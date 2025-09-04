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
from evtranslator.relay.quota import ensure_and_snapshot, check_enabled_and_notice, reserve_quota_if_needed, maybe_warn_90pct
from evtranslator.relay.translate_wrap import translate_with_controls
from evtranslator.relay.send import send_translation
from evtranslator.relay.attachments import extract_urls

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
    async def on_message(self, message: discord.Message):
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

        # cooldowns (mantém como você já tem)
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

        snapshot = await ensure_and_snapshot(message.guild.id)
        if not await check_enabled_and_notice(message, snapshot, self.disabled_notice_ts):
            return

        # 🔠 traduz só o que não é URL
        should_translate = len(text_no_urls) >= MIN_MSG_LEN

        if should_translate:
            allowed, _ = await reserve_quota_if_needed(message.guild.id, len(text_no_urls))
            if not allowed:
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
            translated_core = text_no_urls  # vazio ou curtinho → não traduz

        # 🔗 reanexa URLs intactas (uma por linha) ao final do texto traduzido
        if urls_in_text:
            if translated_core:
                translated = translated_core + "\n" + "\n".join(urls_in_text)
            else:
                translated = "\n".join(urls_in_text)
        else:
            translated = translated_core

        await maybe_warn_90pct(message.guild, self.warned_guilds)
        await send_translation(self.bot, message, target_ch, translated, message.webhook_id is not None)

