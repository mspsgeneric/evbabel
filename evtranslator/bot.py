# evtranslator/bot.py
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

from .config import INTENTS, TEST_GUILD_ID, CONCURRENCY
from .db import init_db
from .webhook import WebhookSender

# Cogs
from .cogs.links import LinksCog
from .relay.cog import RelayCog
from .cogs.events import EventsCog
from .cogs.quota import Quota
from .cogs.clonar import Clonar

log = logging.getLogger(__name__)

class EVTranslatorBot(commands.Bot):
    def __init__(self, db_path: str):
        super().__init__(intents=INTENTS)  # sem command_prefix

        self.db_path = db_path
        self.sem = asyncio.Semaphore(CONCURRENCY)

        self.http_session: Optional[aiohttp.ClientSession] = None
        self.webhooks: Optional[WebhookSender] = None

    async def setup_hook(self) -> None:
        # DB boot
        await init_db(self.db_path)

        # HTTP session com timeout global (evita requests pendurados)
        timeout = aiohttp.ClientTimeout(total=12)
        self.http_session = aiohttp.ClientSession(
            headers={"User-Agent": "EVTranslator/1.0 (+github.com/you)"},
            timeout=timeout,
        )

        # Webhook manager — proteja caso self.user ainda não esteja setado
        bot_user_id = self.user.id if self.user else None  # type: ignore[union-attr]
        self.webhooks = WebhookSender(bot_user_id=bot_user_id)

        # Cogs
        await self.add_cog(LinksCog(self))
        await self.add_cog(RelayCog(self))
        await self.add_cog(EventsCog(self))
        await self.add_cog(Quota(self))
        await self.add_cog(Clonar(self))

        # Slash sync
        try:
            if TEST_GUILD_ID:
                await self.tree.sync(guild=discord.Object(id=int(TEST_GUILD_ID)))
                log.info("Slash sync (guild %s)", TEST_GUILD_ID)
            else:
                await self.tree.sync()
                log.info("Slash sync (global)")
        except Exception as e:
            log.warning("Slash sync failed: %s", e)

    async def on_ready(self):
        # presença/atividade para facilitar diagnóstico
        try:
            await self.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name="traduções (EVtranslator)",
                )
            )
        except Exception:
            pass
        log.info("Logado como %s (%s)", self.user, getattr(self.user, "id", "?"))

        # se inicializou webhooks antes de self.user, atualize o id agora
        if self.webhooks and self.user and self.webhooks.bot_user_id is None:
            self.webhooks.bot_user_id = self.user.id

    async def on_app_command_error(self, interaction: discord.Interaction, error: Exception):
        # handler simples para slash errors (não poluir chat/console)
        log.exception("App command error: %s", error)
        if interaction.response.is_done():
            try:
                await interaction.followup.send("❌ Ocorreu um erro ao processar o comando.", ephemeral=True)
            except Exception:
                pass
        else:
            try:
                await interaction.response.send_message("❌ Ocorreu um erro ao processar o comando.", ephemeral=True)
            except Exception:
                pass

    async def close(self):
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()
