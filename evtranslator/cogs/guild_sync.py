# evtranslator/cogs/guild_sync.py
from __future__ import annotations
import logging
import discord
from discord.ext import commands

from evtranslator.supabase_client import ensure_guild_row  # <- usa seu painel

log = logging.getLogger(__name__)

class GuildSyncCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        # sincroniza todas as guilds ao subir (cobre "entrei mas não gravei")
        for g in self.bot.guilds:
            try:
                ensure_guild_row(g.id, g.name)
            except Exception as e:
                log.warning("[guild_sync] on_ready falhou p/ %s (%s): %s", g.name, g.id, e)
        log.info("[guild_sync] synced %d guild(s) no on_ready", len(self.bot.guilds))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        try:
            ensure_guild_row(guild.id, guild.name)
        except Exception as e:
            log.warning("[guild_sync] on_guild_join falhou p/ %s (%s): %s", guild.name, guild.id, e)
        else:
            log.info("[guild_sync] join: %s (%s)", guild.name, guild.id)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        if before.name != after.name:
            try:
                ensure_guild_row(after.id, after.name)
            except Exception as e:
                log.warning("[guild_sync] rename falhou %s->%s (%s): %s",
                            before.name, after.name, after.id, e)
            else:
                log.info("[guild_sync] rename: %s -> %s (%s)",
                         before.name, after.name, after.id)

async def setup(bot: commands.Bot):
    await bot.add_cog(GuildSyncCog(bot))
