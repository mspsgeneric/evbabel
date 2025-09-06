# evtranslator/relay/reply.py
from __future__ import annotations
import os
import asyncio
from typing import Optional
import discord

from evtranslator.config import DB_PATH, MIN_MSG_LEN
from evtranslator.db import get_translation_by_src, record_translation, get_link_info

from evtranslator.relay.translate_wrap import translate_with_controls
from evtranslator.relay.attachments import extract_urls
from evtranslator.relay.filters import clamp_text
from evtranslator.relay.ratelimit import TokenBucket
from evtranslator.relay.backoff import BackoffCfg, CircuitBreaker
from evtranslator.relay.quota import precheck_chars, commit_chars

from . import send


class ReplyService:
    """Resolve referências de reply para que a tradução mantenha encadeamento."""

    def __init__(self, bot):
        self.bot = bot

        # configs locais (espelham as do Cog) — lidas do env
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
        rate = float(os.getenv("EV_PROVIDER_RATE_CAP", "12"))
        burst = float(os.getenv("EV_PROVIDER_BURST", "24"))
        self.rate_limiter = TokenBucket(rate, burst)

    async def resolve_reference(
        self,
        src_msg: discord.Message,
        target_ch: discord.TextChannel,
    ) -> tuple[Optional[discord.MessageReference], discord.TextChannel]:
        """
        Dada uma mensagem que contém reply (src_msg.reference),
        retorna (MessageReference para a TRADUÇÃO alvo, canal_efetivo_de_envio).
        Se a mensagem alvo nunca foi traduzida, traduz uma vez e cria o par.
        """
        ref = src_msg.reference
        if not ref or not ref.message_id:
            return None, target_ch

        # 0) Obter idiomas pela ligação (mesmo canal/origem)
        link = await get_link_info(DB_PATH, src_msg.guild.id, src_msg.channel.id)
        if not link:
            return None, target_ch
        _target_id, src_lang, tgt_lang = link  # target_id não precisa aqui; usamos target_ch recebido

        # 1) Verifica se já existe mapeamento no banco
        tgt_pair = await get_translation_by_src(DB_PATH, src_msg.guild.id, ref.message_id)
        if tgt_pair:
            _src_ch_id, tgt_msg_id, tgt_ch_id, _tgt_wh_id, _created_at = tgt_pair
            # usar o canal onde a tradução alvo realmente está
            eff_ch = src_msg.guild.get_channel(int(tgt_ch_id)) or target_ch
            reference = discord.MessageReference(
                message_id=int(tgt_msg_id),
                channel_id=int(tgt_ch_id),
                guild_id=src_msg.guild.id if src_msg.guild else None,
                fail_if_not_exists=False,
            )
            return reference, eff_ch

        # 2) Caso não exista: busca a mensagem original
        try:
            src_ref_msg = await src_msg.channel.fetch_message(ref.message_id)
        except Exception:
            return None, target_ch

        # 3) Montar texto (separando URLs e aplicando clamp como no fluxo principal)
        text = (src_ref_msg.content or "").strip()
        text_no_urls, urls_in_text = extract_urls(text)
        text_no_urls = clamp_text(text_no_urls)

        # 3.1) Checar cota quando houver texto a traduzir
        should_translate = len(text_no_urls) >= MIN_MSG_LEN
        if should_translate:
            ok, used, cap = await precheck_chars(src_msg.guild.id, len(text_no_urls))
            if not ok:
                # Sem cota → não cria pré-tradução da referência; segue sem reply encadeado
                return None, target_ch

            translated_core = await translate_with_controls(
                self.bot.http_session,
                text_no_urls,
                src_lang, tgt_lang,
                getattr(self.bot, "sem", asyncio.Semaphore(1)),
                self.translate_timeout, self.jitter_ms,
                self.backoff_cfg, self.cb, self.rate_limiter.acquire,
            )
            if translated_core is None:
                return None, target_ch
        else:
            translated_core = text_no_urls  # curto ou vazio

        # 3.2) Reposicionar URLs ao final (mesma convenção do on_message)
        if urls_in_text:
            translated_text = (
                translated_core + ("\n" + "\n".join(urls_in_text) if translated_core else "\n".join(urls_in_text))
            ).strip()
        else:
            translated_text = translated_core

        # 3.3) Commit de cota (se traduziu)
        if should_translate:
            committed = await commit_chars(src_msg.guild.id, len(text_no_urls))
            if not committed:
                return None, target_ch

        # 4) Publica a tradução da referência no canal de destino (sem reference, raiz)
        ids = await send.send_translation(
            self.bot,
            src_ref_msg,
            target_ch,
            translated_text,
            is_proxy_msg=False,
            reference=None,  # raiz
        )
        if not ids:
            return None, target_ch

        tgt_msg_id, tgt_wh_id = ids

        # 5) Grava mapeamento no banco
        try:
            await record_translation(
                DB_PATH,
                src_msg.guild.id,
                src_ref_msg.id,
                src_msg.channel.id,
                int(tgt_msg_id),
                target_ch.id,
                int(tgt_wh_id),
                int(src_msg.created_at.timestamp()) if src_msg.created_at else 0,
            )
        except Exception:
            # Mesmo sem persistir, ainda podemos retornar a referência para este envio
            pass

        # 6) Retorna referência para esta tradução (para encadear o reply atual) + canal efetivo
        reference = discord.MessageReference(
            message_id=int(tgt_msg_id),
            channel_id=target_ch.id,
            guild_id=src_msg.guild.id if src_msg.guild else None,
            fail_if_not_exists=False,
        )
        return reference, target_ch
