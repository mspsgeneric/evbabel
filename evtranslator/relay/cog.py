# evtranslator/relay/cog.py
from __future__ import annotations
import os, time, asyncio, random, logging, discord
from discord.ext import commands

from evtranslator.config import (
    DB_PATH, MIN_MSG_LEN, USER_COOLDOWN_SEC, CHANNEL_COOLDOWN_SEC,
)
from evtranslator.db import get_link_info
from evtranslator.webhook import WebhookSender

from evtranslator.relay.filters import tupperbox_guard, basic_checks, short_text_ok, clamp_text, Dedupe
from evtranslator.relay.ratelimit import TokenBucket
from evtranslator.relay.backoff import BackoffCfg, CircuitBreaker

from evtranslator.relay.translate_wrap import translate_with_controls
from evtranslator.relay.send import send_translation
from evtranslator.relay.attachments import extract_urls


from evtranslator.relay.quota import (
    ensure_and_snapshot,
    check_enabled_and_notice,
    precheck_chars,             # NOVO
    commit_chars,               # NOVO
    maybe_warn_90pct,
)

class RelayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_cooldowns: dict[int,float] = {}
        self.channel_cooldowns: dict[int,float] = {}
        self.warned_guilds: set[int] = set()
        self.disabled_notice_ts: dict[int,float] = {}
        self.event_mode = os.getenv("EV_MODE_EVENT", "false").lower() == "true"
        self.user_cd_event = float(os.getenv("EV_USER_COOLDOWN_SEC", "1.5"))
        self.chan_cd_event = float(os.getenv("EV_CHANNEL_COOLDOWN_SEC", "2.0"))
        rate = float(os.getenv("EV_PROVIDER_RATE_CAP", "12"))
        burst = float(os.getenv("EV_PROVIDER_BURST", "24"))
        self.rate_limiter = TokenBucket(rate, burst)
        self.translate_timeout = float(os.getenv("EV_TRANSLATE_TIMEOUT", "8"))
        self.jitter_ms = int(os.getenv("EV_JITTER_MS", "150"))
        self.backoff_cfg = BackoffCfg(
            attempts=int(os.getenv("EV_RETRY_ATTEMPTS", "3")),
            base=float(os.getenv("EV_RETRY_BASE", "0.3")),
            factor=float(os.getenv("EV_RETRY_FACTOR", "2.0")),
            max_delay=float(os.getenv("EV_RETRY_MAX", "2.0")),
            jitter_ms=int(os.getenv("EV_RETRY_JITTER_MS", "150")),
        )
        self.cb = CircuitBreaker(
            fail_threshold=int(os.getenv("EV_CB_THRESHOLD", "6")),
            cooldown_sec=float(os.getenv("EV_CB_COOLDOWN", "30")),
        )
        self.dedupe = Dedupe(float(os.getenv("EV_DEDUPE_WINDOW_SEC", "3.0")))
        self._rita_cache: dict[int, bool] = {}   # ⬅️ adicione esta linha no __init__
        self._rita_warned: set[int] = set()      # ✅ guard anti-spam de aviso
        self._rita_mutex = asyncio.Lock()  # 🔒 evita aviso duplicado por corrida
        self._guild_snap_ts: dict[int, float] = {}  # throttle de snapshot por guild
        self._guild_snap_interval = 600.0  # 10 minutos


        # === BLOQUEIO DE RITA ===
        # Ativa/desativa via env (padrão: on)
        self.rita_block = os.getenv("EV_BLOCK_RITA", "true").lower() == "true"
        # IDs oficiais (opcional, recomendado). Ex: "123456789012345678,987654321098765432"
        self.known_rita_ids: set[int] = {
            int(x.strip()) for x in os.getenv("EV_RITA_IDS", "").split(",") if x.strip().isdigit()
        }

        # expõe o WebhookSender (bot.bot_user_id é setado em on_ready do bot)
        self.webhook_sender = WebhookSender(bot_user_id=None, default_avatar_bytes=None)
        setattr(self.bot, "webhooks", self.webhook_sender)

    @commands.Cog.listener()
    async def on_ready(self):
        if self.webhook_sender.bot_user_id is None and self.bot.user:
            self.webhook_sender.bot_user_id = self.bot.user.id
        if self.webhook_sender.default_avatar_bytes is None and self.bot.user:
            try:
                self.webhook_sender.default_avatar_bytes = await self.bot.user.display_avatar.read()
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        # Saiu do servidor → limpa caches para permitir novo aviso no futuro
        self._rita_warned.discard(guild.id)
        self._rita_cache.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        # Entrou num servidor novo/antigo → garante que poderá avisar de novo
        self._rita_warned.discard(guild.id)
        self._rita_cache.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        if before.name != after.name:
            try:
                await ensure_and_snapshot(after.id, after.name)
            except Exception:
                pass



    # === RITA: helper de detecção (COLE AQUI, logo abaixo do on_ready) ===
    async def _guild_has_rita(self, guild: discord.Guild) -> bool:
        """
        True se encontrar Rita no servidor.
        Usa cache; se necessário, força chunk ou fetch dos membros uma única vez.
        """
        # cache curto: evita custo por mensagem
        cached = self._rita_cache.get(guild.id)
        if cached is not None:
            return cached
        try:
            # 1) Checa membros já carregados
            def _is_rita(m: discord.Member) -> bool:
                if not m.bot:
                    return False
                if self.known_rita_ids and m.id in self.known_rita_ids:
                    return True
                name = (m.name or "").strip().lower()
                return (name == "rita") or name.startswith("rita ")

            for m in guild.members:
                if _is_rita(m):
                    self._rita_cache[guild.id] = True
                    return True

            # 2) Se a lista parece incompleta, tenta "chunkar"
            if not getattr(guild, "chunked", False):
                try:
                    await guild.chunk()
                except Exception:
                    pass
                for m in guild.members:
                    if _is_rita(m):
                        self._rita_cache[guild.id] = True
                        return True

            # 3) Último recurso: fetch via API (uma vez)
            try:
                async for m in guild.fetch_members(limit=None):
                    if _is_rita(m):
                        self._rita_cache[guild.id] = True
                        return True
            except Exception:
                # Se falhar o fetch, segue sem travar
                pass

            self._rita_cache[guild.id] = False
            return False
        except Exception:
            # Em caso de erro inesperado, não bloquear
            self._rita_cache[guild.id] = False
            return False



    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # === Upsert + snapshot com throttle (evita hits a cada msg) ===
        snapshot = None
        if message.guild is not None:
            gid = message.guild.id
            now = time.time()
            last = self._guild_snap_ts.get(gid, 0.0)
            if now - last > self._guild_snap_interval:
                try:
                    snapshot = await ensure_and_snapshot(gid, message.guild.name)
                except Exception:
                    snapshot = None
                else:
                    self._guild_snap_ts[gid] = now

        # === RITA: checagem e saída imediata com seção crítica ===
        if message.guild and self.rita_block:
            gid = message.guild.id
            async with self._rita_mutex:  # 🔒 evita múltiplos envios simultâneos
                if await self._guild_has_rita(message.guild):
                    if gid not in self._rita_warned:
                        self._rita_warned.add(gid)
                        try:
                            await message.channel.send(
                                "⚠️ Este servidor já possui um bot de tradução comercial. O EVbabel será removido."
                            )
                        except Exception:
                            pass
                    try:
                        await message.guild.leave()
                    finally:
                        return

        # === Filtros originais ===
        if not basic_checks(message):
            return
        if not await tupperbox_guard(message):
            return

        link = await get_link_info(DB_PATH, message.guild.id, message.channel.id)
        if not link:
            return

        target_id, src_lang, tgt_lang = link
        target_ch = message.guild.get_channel(target_id)
        if not isinstance(target_ch, discord.TextChannel) or target_id == message.channel.id:
            return

        text = (message.content or "").strip()
        has_atts = bool(message.attachments)
        # 🔎 separe URLs do texto para que NUNCA sejam traduzidas / corrompidas
        text_no_urls, urls_in_text = extract_urls(text)
        has_url = bool(urls_in_text)

        if not short_text_ok(text_no_urls or text, has_atts, has_url):
            return

        # clamp só no que será traduzido
        text_no_urls = clamp_text(text_no_urls)

        # cooldowns
        now = time.time()
        user_cd = self.user_cd_event if self.event_mode else USER_COOLDOWN_SEC
        if now - self.user_cooldowns.get(message.author.id, 0.0) < user_cd:
            return
        self.user_cooldowns[message.author.id] = now

        chan_cd = self.chan_cd_event if self.event_mode else CHANNEL_COOLDOWN_SEC
        if now - self.channel_cooldowns.get(message.channel.id, 0.0) < chan_cd:
            return
        self.channel_cooldowns[message.channel.id] = now

        if self.event_mode and not self.dedupe.check_and_set(message.channel.id, message.author.id, text):
            return

        # 🔁 Fallback: se o throttle não buscou agora, garanta um snapshot aqui
        if snapshot is None and message.guild is not None:
            try:
                snapshot = await ensure_and_snapshot(message.guild.id, message.guild.name)
            except Exception:
                snapshot = {}

        # ❗️Checagem de habilitação/aviso
        if not await check_enabled_and_notice(message, snapshot or {}, self.disabled_notice_ts):
            return

        # 🔠 traduz só o que não é URL
        should_translate = len(text_no_urls) >= MIN_MSG_LEN

        if should_translate:
            # 1) Pré-checagem: NÃO consome ainda
            n_chars = len(text_no_urls)
            ok, used, cap = await precheck_chars(message.guild.id, n_chars)
            if not ok:
                try:
                    await message.channel.send(
                        "⚠️ A cota de tradução deste servidor está esgotada por enquanto. "
                        "Um admin pode ajustar o limite ou aguardar o reset mensal (`/quota`)."
                    )
                except Exception:
                    pass
                return

            # 2) Traduz com controles (timeout/backoff/circuit/rate)
            translated_core = await translate_with_controls(
                self.bot.http_session, text_no_urls, src_lang, tgt_lang,
                getattr(self.bot, "sem", asyncio.Semaphore(1)),
                self.translate_timeout, self.jitter_ms,
                self.backoff_cfg, self.cb, self.rate_limiter.acquire,
            )
            if translated_core is None:
                # Falhou → não consome
                return
        else:
            translated_core = text_no_urls  # vazio ou curtinho → não traduz

        # 🔗 reanexa URLs intactas (uma por linha) ao final do texto traduzido
        if urls_in_text:
            if translated_core:
                translated = translated_core + "\n" + "\n".join(urls_in_text)
            else:
                translated = "\n".join(urls_in_text)
        else:
            translated = translated_core

        # ✅ Commit da cota só depois que a tradução deu certo
        if should_translate:
            committed = await commit_chars(message.guild.id, len(text_no_urls))
            if not committed:
                try:
                    await message.channel.send(
                        "⚠️ Não foi possível registrar o consumo de cota agora. "
                        "Tente novamente em instantes."
                    )
                except Exception:
                    pass
                return

        await maybe_warn_90pct(message.guild, self.warned_guilds)
        await send_translation(self.bot, message, target_ch, translated, message.webhook_id is not None)





