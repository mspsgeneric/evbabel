# evtranslator/relay/cog.py
from __future__ import annotations

import os
import time
import asyncio
import logging
import discord
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
from evtranslator.config import TRANSLATED_FLAG
from .reply import ReplyService

from evtranslator.db import (
    record_translation,
    get_translation_by_src,
    touch_translation_edit,
    purge_xlate_older_than,
    delete_translation_map,
    get_webhook_token_by_id,
)

from evtranslator.relay.quota import (
    ensure_and_snapshot,
    check_enabled_and_notice,
    precheck_chars,
    commit_chars,
    maybe_warn_90pct,
)

log = logging.getLogger(__name__)


class RelayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_cooldowns: dict[int, float] = {}
        self.channel_cooldowns: dict[int, float] = {}
        self.warned_guilds: set[int] = set()
        self.disabled_notice_ts: dict[int, float] = {}

        self.event_mode = os.getenv("EV_MODE_EVENT", "false").lower() == "true"
        self.user_cd_event = float(os.getenv("EV_USER_COOLDOWN_SEC", "1.5"))
        self.chan_cd_event = float(os.getenv("EV_CHANNEL_COOLDOWN_SEC", "2.0"))

        rate = float(os.getenv("EV_PROVIDER_RATE_CAP", "12"))
        burst = float(os.getenv("EV_PROVIDER_BURST", "24"))
        self.rate_limiter = TokenBucket(rate, burst)
        self._own_wh_cache: set[int] = set()  # IDs de webhooks “nossos” (persistidos no DB)


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

        self._rita_cache: dict[int, bool] = {}
        self._rita_warned: set[int] = set()
        self._rita_mutex = asyncio.Lock()

        self._guild_snap_ts: dict[int, float] = {}
        self._guild_snap_interval = 600.0  # 10 min

        self.edit_window_sec = int(os.getenv("EV_EDIT_WINDOW_SEC", "3600"))
        self._xlate_cleanup_interval = int(os.getenv("EV_EDIT_CLEAN_SEC", "600"))
        self._xlate_cleanup_started = False
        self.map_retention_sec = 30 * 24 * 3600  # 30 dias, sem ENV
        self.reply_service = ReplyService(bot)

        # Rita block
        self.rita_block = os.getenv("EV_BLOCK_RITA", "true").lower() == "true"
        self.known_rita_ids: set[int] = {
            int(x.strip()) for x in os.getenv("EV_RITA_IDS", "").split(",") if x.strip().isdigit()
        }

        # Webhook manager (session injetada no on_ready)
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
        if not self._xlate_cleanup_started:
            self._xlate_cleanup_started = True
            asyncio.create_task(self._xlate_cleanup_loop())

        # injeta a http_session do bot no WebhookSender (necessário p/ Webhook.partial)
        self.webhook_sender.http_session = getattr(self.bot, "http_session", None)
        log.info("webhook: http_session injetada = %s", self.webhook_sender.http_session is not None)
        log.info("DB_PATH runtime=%s (cwd=%s)", DB_PATH, os.getcwd())

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        self._rita_warned.discard(guild.id)
        self._rita_cache.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._rita_warned.discard(guild.id)
        self._rita_cache.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        if before.name != after.name:
            try:
                await ensure_and_snapshot(after.id, after.name)
            except Exception:
                pass

    # =======================
    # EDIT: helper reutilizado
    # =======================
    async def _handle_message_edit(self, after: discord.Message):
        # ignora bots/webhooks/DMs
        if after.guild is None or after.author.bot or after.webhook_id is not None:
            return

        # precisa ter link (lado origem)
        link = await get_link_info(DB_PATH, after.guild.id, after.channel.id)
        if not link:
            return
        target_id, src_lang, tgt_lang = link
        target_ch = after.guild.get_channel(target_id)
        if not isinstance(target_ch, discord.TextChannel):
            return

        # consulta mapeamento
        info = await get_translation_by_src(DB_PATH, after.guild.id, after.id)
        if not info:
            log.info("edit: sem vínculo para src_msg=%s (guild=%s)", after.id, after.guild.id)
            return

        src_ch_id, tgt_msg_id, tgt_ch_id, webhook_id, created_at = info
        now = int(time.time())
        if now - int(created_at) > self.edit_window_sec:
            log.info("edit: janela expirada p/ src_msg=%s (age=%ss > %ss)", after.id, now - int(created_at), self.edit_window_sec)
            return

        # reprocessa texto
        text = (after.content or "").strip()
        text_no_urls, urls_in_text = extract_urls(text)
        text_no_urls = clamp_text(text_no_urls)

        # quota + tradução
        if len(text_no_urls) >= MIN_MSG_LEN:
            ok, used, cap = await precheck_chars(after.guild.id, len(text_no_urls))
            if not ok:
                log.info("edit: quota negada p/ guild=%s chars=%s used=%s cap=%s", after.guild.id, len(text_no_urls), used, cap)
                return

            translated_core = await translate_with_controls(
                self.bot.http_session, text_no_urls, src_lang, tgt_lang,
                getattr(self.bot, "sem", asyncio.Semaphore(1)),
                self.translate_timeout, self.jitter_ms,
                self.backoff_cfg, self.cb, self.rate_limiter.acquire,
            )
            if translated_core is None:
                log.info("edit: tradução falhou (None) p/ src_msg=%s", after.id)
                return
        else:
            translated_core = text_no_urls

        translated = (translated_core + ("\n" + "\n".join(urls_in_text) if urls_in_text else "")).strip()

        # preferir o canal salvo no vínculo
        saved_target = after.guild.get_channel(tgt_ch_id)
        if isinstance(saved_target, discord.TextChannel):
            target_ch = saved_target

        try:
            log.info("edit: vínculo tgt_msg_id=%s webhook_id=%s tgt_ch_id=%s", tgt_msg_id, webhook_id, tgt_ch_id)


            if webhook_id == 0:
                # fallback: traduzido via channel.send → editar direto
                tgt_ch = after.guild.get_channel(tgt_ch_id)
                if tgt_ch:
                    try:
                        msg = await tgt_ch.fetch_message(tgt_msg_id)
                        await msg.edit(content=translated, allowed_mentions=discord.AllowedMentions.none())
                        log.info("edit: sucesso via channel.send p/ tgt_msg_id=%s", tgt_msg_id)
                        if len(text_no_urls) >= MIN_MSG_LEN:
                            committed = await commit_chars(after.guild.id, len(text_no_urls))
                        await touch_translation_edit(DB_PATH, after.guild.id, after.id, now)
                    except Exception as e:
                        log.warning("edit: erro ao editar fallback msg=%s: %s", tgt_msg_id, e)
                return

            # tenta com o MESMO webhook persistido
            wh = await self.webhook_sender.get_by_id(int(webhook_id))
            if wh is not None:
                log.info("edit: usando webhook persistido %s", webhook_id)
            else:
                log.info("edit: webhook_id %s não encontrado; usando get_or_create()", webhook_id)
                wh = await self.webhook_sender.get_or_create(target_ch)
                if wh is None:
                    log.warning("edit: get_or_create falhou em #%s", getattr(target_ch, "name", "?"))
                    return

            await wh.edit_message(int(tgt_msg_id), content=translated, allowed_mentions=discord.AllowedMentions.none())
            log.info("edit: sucesso p/ tgt_msg_id=%s", tgt_msg_id)

            if len(text_no_urls) >= MIN_MSG_LEN:
                committed = await commit_chars(after.guild.id, len(text_no_urls))
                log.info("edit: commit_chars=%s guild=%s chars=%s", committed, after.guild.id, len(text_no_urls))
            await touch_translation_edit(DB_PATH, after.guild.id, after.id, now)

        except discord.NotFound:
            # Mensagem original não pode mais ser editada → não repostar; remover vínculo
            try:
                await delete_translation_map(DB_PATH, after.guild.id, after.id)
            except Exception:
                pass
            log.info("edit: NotFound; vínculo desativado p/ src=%s guild=%s", after.id, after.guild.id)

        except Exception as e:
            log.warning("edit: erro ao editar via webhook: %s", e)

    # evento com mensagem no cache
    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        await self._handle_message_edit(after)

    # evento RAW: cobre pós-restart / fora do cache
    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        log.info("edit:RAW payload guild=%s channel=%s msg=%s keys=%s",
                 payload.guild_id, payload.channel_id, payload.message_id, list(payload.data.keys()))
        if payload.guild_id is None or payload.channel_id is None or payload.message_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        ch = guild.get_channel(payload.channel_id)
        if not isinstance(ch, discord.TextChannel):
            return

        try:
            msg = await ch.fetch_message(payload.message_id)
        except discord.NotFound:
            log.info("edit:RAW fetch miss (msg not found)")
            return
        except Exception as e:
            log.info("edit:RAW fetch error: %s", e)
            return

        await self._handle_message_edit(msg)

    # ====== Rita detection helpers ======
    async def _guild_has_rita(self, guild: discord.Guild) -> bool:
        cached = self._rita_cache.get(guild.id)
        if cached is not None:
            return cached
        try:
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

            if not getattr(guild, "chunked", False):
                try:
                    await guild.chunk()
                except Exception:
                    pass
                for m in guild.members:
                    if _is_rita(m):
                        self._rita_cache[guild.id] = True
                        return True

            try:
                async for m in guild.fetch_members(limit=None):
                    if _is_rita(m):
                        self._rita_cache[guild.id] = True
                        return True
            except Exception:
                pass

            self._rita_cache[guild.id] = False
            return False
        except Exception:
            self._rita_cache[guild.id] = False
            return False


    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore mensagens que vieram de WEBHOOKS NOSSOS (evita eco).
        # - Mensagens do Tupperbox também são webhooks, mas NÃO estão na tabela webhook_tokens,
        #   então continuam sendo traduzidas normalmente.
        if message.webhook_id is not None:
            wid = int(message.webhook_id)

            # cache rápido: já marcamos esse webhook como “nosso”
            if wid in self._own_wh_cache:
                return

            # consulta no banco: se temos token salvo, é um webhook “nosso”
            info = None
            try:
                info = await get_webhook_token_by_id(DB_PATH, wid)
            except Exception:
                info = None

            if info is not None:
                # é nosso webhook → não traduzir (evita eco)
                self._own_wh_cache.add(wid)
                return

        # fallback extra: se por algum motivo o conteúdo tiver nossa flag invisível, ignore
        from evtranslator.config import TRANSLATED_FLAG
        if TRANSLATED_FLAG in (message.content or ""):
            return
        # snapshot throttle
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

        # Rita block
        if message.guild and self.rita_block:
            gid = message.guild.id
            async with self._rita_mutex:
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

        # filtros
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
        text_no_urls, urls_in_text = extract_urls(text)
        has_url = bool(urls_in_text)

        # --- DEBUG: checa hosts das URLs do texto
        from urllib.parse import urlparse
        try:
            url_hosts = {(urlparse(u).netloc or "").lower() for u in urls_in_text}
        except Exception:
            url_hosts = set()

        # Gate original
        ok_basic = short_text_ok(text_no_urls or text, has_atts, has_url)

        # Override: se tiver Imgur, passa mesmo sendo "só link" curto
        if not ok_basic and any(h.endswith("imgur.com") for h in url_hosts):
            log.info("override short_text_ok: liberando por conter imgur (hosts=%s)", sorted(url_hosts))
            ok_basic = True

        # Se continuar bloqueado, loga e sai (pra sabermos se o problema estava aqui)
        if not ok_basic:
            log.info(
                "skip short_text_ok: len_no_urls=%s, has_atts=%s, has_url=%s, hosts=%s, preview=%r",
                len(text_no_urls or ""), has_atts, has_url, sorted(url_hosts), (text[:100] if text else "")
            )
            return


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

        # fallback snapshot
        if snapshot is None and message.guild is not None:
            try:
                snapshot = await ensure_and_snapshot(message.guild.id, message.guild.name)
            except Exception:
                snapshot = {}

        if not await check_enabled_and_notice(message, snapshot or {}, self.disabled_notice_ts):
            return

        # traduz apenas o que não é URL
        should_translate = len(text_no_urls) >= MIN_MSG_LEN

        if should_translate:
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

            translated_core = await translate_with_controls(
                self.bot.http_session, text_no_urls, src_lang, tgt_lang,
                getattr(self.bot, "sem", asyncio.Semaphore(1)),
                self.translate_timeout, self.jitter_ms,
                self.backoff_cfg, self.cb, self.rate_limiter.acquire,
            )
            if translated_core is None:
                return
        else:
            translated_core = text_no_urls

        if urls_in_text:
            if translated_core:
                translated = translated_core + "\n" + "\n".join(urls_in_text)
            else:
                translated = "\n".join(urls_in_text)
        else:
            translated = translated_core

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
       
        
        reference, effective_ch = await self.reply_service.resolve_reference(message, target_ch)

        ids = await send_translation(
            self.bot, message, effective_ch, translated, message.webhook_id is not None,
            reference=reference,
        )


        log.info(
            "send_translation ids=%r (guild=%s ch=%s src_msg=%s)",
            ids, message.guild.id, target_ch.id, message.id
        )

        # 3. Grava vínculo no banco
        if ids:
            tgt_msg_id, webhook_id = ids
            try:
                await record_translation(
                    DB_PATH,
                    message.guild.id,
                    message.id,
                    message.channel.id,
                    int(tgt_msg_id),
                    target_ch.id,
                    int(webhook_id),
                    int(time.time()),
                )
            except Exception as e:
                log.warning("record_translation falhou: %s", e)


 
    async def _xlate_cleanup_loop(self):
        while not self.bot.is_closed():
            try:
                now = int(time.time())
                cutoff = now - self.map_retention_sec  # 🔁 mantém pares por 30 dias
                deleted = await purge_xlate_older_than(DB_PATH, cutoff)
                if deleted:
                    log.info(
                        "[xlate] purge: %d vínculos antigos removidos (retenção=%ss)",
                        deleted, self.map_retention_sec
                    )
            except Exception as e:
                log.warning("[xlate] cleanup error: %s", e)
            await asyncio.sleep(self._xlate_cleanup_interval)

