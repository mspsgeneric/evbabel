# evtranslator/cogs/events.py
from __future__ import annotations
import logging
import discord
from discord.ext import commands
from evtranslator.config import DB_PATH
from evtranslator.db import unlink_any_for_channel

log = logging.getLogger(__name__)

class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_guild_channel_delete")
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        # Só trabalhamos com canais de TEXTO (ignora voz, stage, fórum, categorias, etc.)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            removed = await unlink_any_for_channel(DB_PATH, channel.guild.id, channel.id)
            log.info("🧹 Removidos %s link(s) envolvendo canal deletado #%s (%s) em guild %s",
                     removed, channel.name, channel.id, channel.guild.id)
        except Exception as e:
            log.exception("[events] Falha ao limpar links do canal %s em guild %s: %s",
                          getattr(channel, "id", "?"), getattr(channel.guild, "id", "?"), e)
