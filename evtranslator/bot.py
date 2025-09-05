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
from .cogs.ajuda import AjudaCog
from evtranslator.supabase_client import guild_exists
import os 
from .cogs.guild_sync import GuildSyncCog


log = logging.getLogger(__name__)

class EVTranslatorBot(commands.Bot):
    def __init__(self, db_path: str):
        
        super().__init__(
            command_prefix=commands.when_mentioned,  # só @menção, ignora "!"
            intents=INTENTS,
            help_command=None,                      # sem help de texto
        )

        self.db_path = db_path
        self.sem = asyncio.Semaphore(CONCURRENCY)

        self.http_session: Optional[aiohttp.ClientSession] = None
        self.webhooks: Optional[WebhookSender] = None
        self.reconcile_interval = float(os.getenv("EV_RECONCILE_SEC", "45"))  # segundos
        self.leave_if_missing = os.getenv("EV_LEAVE_IF_MISSING", "false").lower() == "true"
        self._reconcile_task: asyncio.Task | None = None


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
        await self.add_cog(GuildSyncCog(self))
        await self.add_cog(LinksCog(self))
        await self.add_cog(RelayCog(self))
        await self.add_cog(EventsCog(self))
        await self.add_cog(Quota(self))
        await self.add_cog(Clonar(self))
        await self.add_cog(AjudaCog(self))

        self._reconcile_task = asyncio.create_task(self._reconcile_loop())


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
                    name="traduções (EVbabel)",
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



    async def _reconcile_loop(self):
        await self.wait_until_ready()
        log = logging.getLogger(__name__)

        while not self.is_closed():
            try:
                if self.leave_if_missing:
                    # Varre todas as guilds onde o bot está
                    for g in list(self.guilds):
                        ok = await asyncio.to_thread(guild_exists, g.id)
                        if not ok:
                            # Aviso amigável em algum canal onde o bot possa falar
                            try:
                                ch = g.system_channel or next(
                                    (c for c in g.text_channels if c.permissions_for(g.me).send_messages),
                                    None
                                )
                                if ch:
                                    await ch.send("👋 Servidor não autorizado.")
                            except Exception:
                                pass
                            # Sai da guild
                            try:
                                await g.leave()
                            except Exception as e:
                                log.warning("Falha ao sair da guild %s: %s", g.id, e)
            except Exception as e:
                log.warning("reconcile loop error: %s", e)

            await asyncio.sleep(self.reconcile_interval)
