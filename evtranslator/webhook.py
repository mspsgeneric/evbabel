# evtranslator/webhook.py
from __future__ import annotations

import logging
import time
from typing import Optional

import aiohttp
import discord
from discord import AllowedMentions

from evtranslator.config import DB_PATH
from evtranslator.db import (
    upsert_webhook_token,
    get_webhook_for_channel,
    get_webhook_token_by_id,
)

TARGET_NAME = "EVbabel Relay"  # nome do webhook criado pelo bot
log = logging.getLogger(__name__)


class WebhookSender:
    """
    Utilitário para enviar mensagens via webhook (imitando usuário ou identidade custom).
    Com persistência de (webhook_id, token) para permitir edição pós-restart.
    """

    def __init__(self, bot_user_id: Optional[int], default_avatar_bytes: Optional[bytes] = None):
        self.cache: dict[int, discord.Webhook] = {}
        self.own_webhook_ids: set[int] = set()  # IDs de webhooks geridos por este bot (opcional)
        self.bot_user_id = bot_user_id
        self.default_avatar_bytes = default_avatar_bytes  # avatar fixo (bytes) ou None
        # Deve ser preenchida pelo Cog: self.webhook_sender.http_session = self.bot.http_session
        self.http_session: Optional[aiohttp.ClientSession] = None

    async def _is_ours(self, wh: discord.Webhook) -> bool:
        """Retorna True se o webhook for nosso (criado pelo bot) ou tiver o nome padrão."""
        try:
            # tentar usar o usuário criador do webhook (melhor sinal)
            u = getattr(wh, "user", None)
            if u and u.id and self.bot_user_id and u.id == self.bot_user_id:
                return True
        except Exception:
            pass
        # fallback: nome padrão
        try:
            if (wh.name or "").strip() == TARGET_NAME:
                return True
        except Exception:
            pass
        return False

    async def get_by_id(self, webhook_id: int) -> Optional[discord.Webhook]:
        """
        Reconstrói um webhook a partir de (id, token) persistidos no banco.
        Retorna None se não houver token salvo ou se não houver http_session disponível.
        """
        info = await get_webhook_token_by_id(DB_PATH, int(webhook_id))
        if not info or self.http_session is None:
            return None
        _guild_id, _channel_id, token = info
        try:
            wh = discord.Webhook.partial(id=int(webhook_id), token=token, session=self.http_session)
            # tentar validar “se é nosso” via fetch (quando possível)
            try:
                full = await wh.fetch()
                if not await self._is_ours(full):
                    return None
            except Exception:
                # se não deu pra fetch, segue com o parcial (melhor do que nada),
                # pois a linha vem do nosso DB e em geral foi criada por nós.
                pass
            return wh
        except Exception:
            return None

    async def get_or_create(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        """
        Obtém um webhook utilizável para o canal, na ordem:
        1) cache em memória
        2) token persistido no DB para este canal (mas só se for “nosso”)
        3) algum webhook existente no canal (somente se for “nosso” e tiver token)
        4) cria um novo webhook e persiste token
        """
        # 1) cache
        wh = self.cache.get(channel.id)
        if wh:
            return wh

        # 2) DB → token salvo para este canal?
        try:
            saved = await get_webhook_for_channel(DB_PATH, channel.id)
            if saved and self.http_session is not None:
                wid, token = saved
                try:
                    wh = discord.Webhook.partial(id=int(wid), token=token, session=self.http_session)
                    # tenta validar “nosso” via fetch; se não der, confia no DB
                    try:
                        full = await wh.fetch()
                        if not await self._is_ours(full):
                            wh = None
                    except Exception:
                        pass
                    if wh is not None:
                        self.cache[channel.id] = wh
                        return wh
                except Exception:
                    pass
        except Exception:
            pass

        # 3) procurar existentes via API (se o bot tiver permissão) — usa apenas se for “nosso”
        try:
            hooks = await channel.webhooks()
            for h in hooks:
                if not h.token:
                    continue
                if not await self._is_ours(h):
                    continue  # NÃO usar/salvar webhooks alheios (ex.: Tupperbox)

                self.cache[channel.id] = h
                try:
                    await upsert_webhook_token(
                        DB_PATH, channel.guild.id, channel.id, int(h.id), str(h.token), int(time.time())
                    )
                except Exception:
                    pass
                return h
        except Exception:
            pass

        # 4) criar um novo (garante token e edição pós-restart)
        try:
            wh = await channel.create_webhook(name=TARGET_NAME, reason="Proxy de tradução")
            self.cache[channel.id] = wh
            try:
                await upsert_webhook_token(
                    DB_PATH, channel.guild.id, channel.id, int(wh.id), str(wh.token), int(time.time())
                )
            except Exception:
                pass
            return wh
        except Exception as e:
            log.warning("Falha ao criar webhook em #%s: %s", channel.name, e)
            return None

    def _norm_kwargs(self, kwargs: dict, default_allowed: AllowedMentions) -> dict:
        # normaliza embeds únicos
        if "embeds" in kwargs and isinstance(kwargs["embeds"], discord.Embed):
            kwargs["embeds"] = [kwargs["embeds"]]
        # alias "text" → "content"
        if "text" in kwargs and "content" not in kwargs:
            kwargs["content"] = kwargs.pop("text")
        kwargs.setdefault("allowed_mentions", default_allowed)
        return kwargs

    async def _send_with_retry(self, wh: discord.Webhook, channel: discord.TextChannel, **payload):
        """
        Envia com retry: se o webhook sumiu/ficou inválido, recria 1x e retorna o resultado.
        Importante: retorna o objeto Message quando wait=True.
        """
        try:
            return await wh.send(**payload)
        except discord.NotFound:
            # webhook inválido → limpa cache e tenta outro
            self.cache.pop(channel.id, None)
            new = await self.get_or_create(channel)
            if not new:
                raise
            return await new.send(**payload)
        except Exception:
            raise

    async def send_as_member(
        self,
        channel: discord.TextChannel,
        member: discord.Member,
        text: str,
        return_message: bool = False,
        **kwargs,
    ):
        """
        Envia imitando um membro humano.
        Se return_message=True, retorna (msg_id, webhook_id); caso contrário, None.
        """
        default_allowed = AllowedMentions.none()
        kwargs = self._norm_kwargs(kwargs, default_allowed)

        wh = await self.get_or_create(channel)
        if not wh:
            log.warning("Sem webhook em #%s; abortando para evitar mostrar nome do bot.", channel.name)
            return None

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
            wait=return_message,  # necessário pra obter o Message de retorno
            **kwargs,
        )

        try:
            result = await self._send_with_retry(wh, channel, **payload)
        except Exception as e:
            log.warning("Webhook falhou em #%s: %s", channel.name, e)
            # tenta novamente sem avatar_url (alguns CDNs/formatos podem falhar)
            try:
                payload.pop("avatar_url", None)
                payload["wait"] = return_message
                payload["allowed_mentions"] = kwargs.get("allowed_mentions", default_allowed)
                result = await self._send_with_retry(wh, channel, **payload)
            except Exception as e2:
                log.warning("Webhook texto-apenas falhou em #%s: %s", channel.name, e2)
                return None

        if return_message and result is not None:
            # tenta refletir o webhook efetivamente usado (após retry, cache aponta pro atual)
            final_wh = self.cache.get(channel.id) or wh
            try:
                return (int(result.id), int(final_wh.id))
            except Exception:
                return None
        return None

    async def send_as_identity(
        self,
        channel: discord.TextChannel,
        username: str,
        avatar_url: Optional[str],
        text: str,
        return_message: bool = False,
        **kwargs,
    ):
        """
        Envia com nome/avatar arbitrários.
        Se return_message=True, retorna (msg_id, webhook_id); caso contrário, None.
        """
        default_allowed = AllowedMentions.none()
        kwargs = self._norm_kwargs(kwargs, default_allowed)

        wh = await self.get_or_create(channel)
        if not wh:
            log.warning("Sem webhook em #%s; abortando para evitar mostrar nome do bot.", channel.name)
            return None

        uname = (username or "Proxy").strip()[:80]
        payload = dict(
            content=text,
            username=uname,
            avatar_url=avatar_url or discord.utils.MISSING,
            wait=return_message,
            **kwargs,
        )

        try:
            result = await self._send_with_retry(wh, channel, **payload)
        except Exception as e:
            log.warning("Webhook (identity) falhou em #%s: %s", channel.name, e)
            try:
                payload.pop("avatar_url", None)
                payload["wait"] = return_message
                payload["allowed_mentions"] = kwargs.get("allowed_mentions", default_allowed)
                result = await self._send_with_retry(wh, channel, **payload)
            except Exception as e2:
                log.warning("Webhook (identity) texto-apenas falhou em #%s: %s", channel.name, e2)
                return None

        if return_message and result is not None:
            final_wh = self.cache.get(channel.id) or wh
            try:
                return (int(result.id), int(final_wh.id))
            except Exception:
                return None
        return None
