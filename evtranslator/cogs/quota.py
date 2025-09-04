# evtranslator/cogs/quota.py
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands, Interaction
from discord.ext import commands

from evtranslator.supabase_client import get_quota  # síncrona? ver nota abaixo

def fmt_int(n) -> str:
    try:
        return f"{int(n):,}".replace(",", ".")
    except Exception:
        return str(n)

def pct(used: int, limit: int) -> float:
    if not limit:
        return 0.0
    x = max(0.0, min(1.0, used / limit))
    return x

def mk_bar(ratio: float, width: int = 20) -> str:
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)

class Quota(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)  # ou administrator=True, se preferir
    @app_commands.command(name="quota", description="Mostra a cota de tradução do servidor.")
    async def quota(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True, thinking=False)

        guild_id = interaction.guild_id
        if guild_id is None:
            return  # guild_only já cobre, mas por garantia

        try:
            # Se get_quota for síncrona (provável), não bloqueie o loop:
            q = await asyncio.to_thread(get_quota, guild_id)
            # Se for assíncrona, troque a linha acima por:
            # q = await get_quota(guild_id)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao consultar a cota: {e}", ephemeral=True)
            return

        # Valores com defaults seguros
        enabled = bool(q.get("translate_enabled", False))
        limit = int(q.get("char_limit", 0) or 0)
        used = int(q.get("used_chars", 0) or 0)
        remaining = max(0, limit - used)
        tz = (q.get("cycle_tz") or "UTC").strip() or "UTC"
        billing_day = q.get("billing_day", "—")

        # Datas
        cycle_start_raw = str(q.get("cycle_start", "") or "")
        next_reset_raw = str(q.get("next_reset", "") or "")

        try:
            dt_start = datetime.fromisoformat(cycle_start_raw.replace("Z", "+00:00"))
            cycle_start_str = dt_start.strftime("%Y-%m-%d %H:%M")
        except Exception:
            cycle_start_str = cycle_start_raw[:19].replace("T", " ") if cycle_start_raw else "—"

        try:
            dt_reset = datetime.fromisoformat(next_reset_raw.replace("Z", "+00:00"))
            next_reset_local = dt_reset.astimezone(ZoneInfo(tz))
            next_reset_str = next_reset_local.strftime("%Y-%m-%d %H:%M")
        except Exception:
            next_reset_str = next_reset_raw[:19].replace("T", " ") if next_reset_raw else "—"

        # UI
        ratio = pct(used, limit)
        bar = mk_bar(ratio, 24)
        percent = int(round(ratio * 100))

        # cor por severidade
        if not enabled:
            color = discord.Color.dark_grey()
        elif percent < 70:
            color = discord.Color.green()
        elif percent < 90:
            color = discord.Color.orange()
        else:
            color = discord.Color.red()

        enabled_txt = "Sim ✅" if enabled else "Não ❌"

        embed = discord.Embed(
            title="EVbabel — Cota do Servidor",
            color=color,
            description=(
                f"**Habilitado:** {enabled_txt}\n"
                f"**Dia do reset:** {billing_day} *(fuso {tz})*"
            ),
        )
        embed.add_field(name="Limite (mês)", value=fmt_int(limit), inline=True)
        embed.add_field(name="Usado", value=f"{fmt_int(used)}  ({percent}%)", inline=True)
        embed.add_field(name="Restante", value=fmt_int(remaining), inline=True)

        embed.add_field(name="Consumo", value=f"`{bar}`", inline=False)
        embed.add_field(name="Último reset", value=cycle_start_str, inline=True)
        embed.add_field(name="Próximo reset", value=next_reset_str, inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Quota(bot))
