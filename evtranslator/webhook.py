# evtranslator/webhook.py
from __future__ import annotations
import logging
from typing import Optional

import discord
from discord import AllowedMentions

TARGET_NAME = "EVbabel Relay"  # pode trocar pra "EVtranslator Relay" se quiser
log = logging.getLogger(__name__)

class WebhookSender:
    def __init__(self, bot_user_id: Optional[int], default_avatar_bytes: Optional[bytes] = None):
        self.cache: dict[int, discord.Webhook] = {}
        self.own_webhook_ids: set[int] = set()  # << IDs de webhooks criados/geridos por este bot
        self.bot_user_id = bot_user_id
        self.default_avatar_bytes = default_avatar_bytes  # avatar fixo (bytes) ou None p/ ícone neutro

    async def get_or_create(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        wh = self.cache.get(channel.id)
        if wh:
            return wh
        try:
            hooks = await channel.webhooks()
            target: Optional[discord.Webhook] = None

            # reutiliza APENAS webhooks criados por este bot
            if self.bot_user_id is not None:
                for h in hooks:
                    if h.user and h.user.id == self.bot_user_id:
                        target = h
                        break

            # achou mas sem token → recria
            if target is not None and target.token is None:
                try:
                    await target.delete(reason="Recreating to ensure token")
                except Exception:
                    pass
                target = None

            # não achou válido → cria
            if target is None:
                target = await channel.create_webhook(name=TARGET_NAME)

            # normaliza NOME + AVATAR (neutro ou default fornecido)
            want_avatar = self.default_avatar_bytes  # None => ícone neutro do Discord
            needs_edit = (target.name != TARGET_NAME) or (
                (want_avatar is None and target.avatar is not None) or
                (want_avatar is not None)  # sempre tenta aplicar avatar fixo se fornecido
            )
            if needs_edit:
                # avatar aceita bytes ou None
                await target.edit(name=TARGET_NAME, avatar=want_avatar)

            # registra este webhook como “nosso”
            try:
                if target.id:
                    self.own_webhook_ids.add(int(target.id))
            except Exception:
                pass

            self.cache[channel.id] = target
            return target

        except discord.Forbidden:
            log.warning("Sem permissão para gerenciar webhooks em #%s", getattr(channel, "name", channel.id))
            return None
        except Exception as e:
            log.warning("Falha ao obter/criar webhook em #%s: %s", getattr(channel, "name", channel.id), e)
            return None

    def _norm_kwargs(self, kwargs: dict, default_allowed: AllowedMentions) -> dict:
        if "embeds" in kwargs and isinstance(kwargs["embeds"], discord.Embed):
            kwargs["embeds"] = [kwargs["embeds"]]
        if "text" in kwargs and "content" not in kwargs:
            kwargs["content"] = kwargs.pop("text")
        kwargs.setdefault("allowed_mentions", default_allowed)
        return kwargs

    async def _send_with_retry(self, wh: discord.Webhook, channel: discord.TextChannel, **payload):
        """Tenta enviar; se o webhook sumiu/ficou inválido, recria 1x e tenta de novo."""
        try:
            await wh.send(**payload)
            return
        except discord.NotFound:
            # webhook apagado manualmente → invalida cache e recria
            self.cache.pop(channel.id, None)
            new = await self.get_or_create(channel)
            if not new:
                raise
            await new.send(**payload)
        except Exception:
            raise

    async def send_as_member(self, channel: discord.TextChannel, member: discord.Member, text: str, **kwargs):
        """Envia mensagem imitando um membro humano (apelido + avatar)."""
        default_allowed = AllowedMentions.none()
        kwargs = self._norm_kwargs(kwargs, default_allowed)

        wh = await self.get_or_create(channel)
        if not wh:
            log.warning("Sem webhook em #%s; abortando para evitar mostrar nome do bot.", channel.name)
            return

        display = (member.display_name or member.name or "user").strip()[:80]
        avatar_url = None
        try:
            avatar_url = member.display_avatar.replace(size=128).url
        except Exception:
            avatar_url = None

        payload = dict(
            content=text,
            username=display,
            avatar_url=avatar_url or discord.utils.MISSING,
            wait=False,
            **kwargs,
        )

        try:
            await self._send_with_retry(wh, channel, **payload)
        except Exception as e:
            log.warning("Webhook falhou em #%s: %s", channel.name, e)
            # fallback texto-apenas (mantém username), ainda via webhook
            try:
                payload.pop("avatar_url", None)
                payload["allowed_mentions"] = kwargs.get("allowed_mentions", default_allowed)
                await self._send_with_retry(wh, channel, **payload)
            except Exception as e2:
                log.warning("Webhook texto-apenas falhou em #%s: %s", channel.name, e2)

    async def send_as_identity(
        self,
        channel: discord.TextChannel,
        username: str,
        avatar_url: Optional[str],
        text: str,
        **kwargs,
    ):
        """Envia mensagem com nome/avatar arbitrários."""
        default_allowed = AllowedMentions.none()
        kwargs = self._norm_kwargs(kwargs, default_allowed)

        wh = await self.get_or_create(channel)
        if not wh:
            log.warning("Sem webhook em #%s; abortando para evitar mostrar nome do bot.", channel.name)
            return

        uname = (username or "Proxy").strip()[:80]
        payload = dict(
            content=text,
            username=uname,
            avatar_url=avatar_url or discord.utils.MISSING,
            wait=False,
            **kwargs,
        )

        try:
            await self._send_with_retry(wh, channel, **payload)
        except Exception as e:
            log.warning("Webhook (identity) falhou em #%s: %s", channel.name, e)
            try:
                payload.pop("avatar_url", None)
                payload["allowed_mentions"] = kwargs.get("allowed_mentions", default_allowed)
                await self._send_with_retry(wh, channel, **payload)
            except Exception as e2:
                log.warning("Webhook (identity) texto-apenas falhou em #%s: %s", channel.name, e2)
