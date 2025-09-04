# evtranslator/cogs/links.py
from __future__ import annotations
import logging
import discord
from discord.ext import commands
from discord import app_commands
from evtranslator.db import link_pair, unlink_pair, unlink_all, list_links, get_link_info
from evtranslator.config import DB_PATH

log = logging.getLogger(__name__)

# 🔒 limitar seleção a canais de TEXTO
TEXT_ONLY = [discord.ChannelType.text]

class LinksCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="linkar", description="Liga dois canais em PT↔EN (canal_pt ↔ canal_en).")
    @app_commands.describe(canal_pt="Canal em Português", canal_en="Canal em Inglês")
    @app_commands.guild_only()
    async def linkar_cmd(
        self,
        inter: discord.Interaction,
        canal_pt: discord.TextChannel,
        canal_en: discord.TextChannel
    ):
        if not inter.user.guild_permissions.manage_guild:  # type: ignore[union-attr]
            return await inter.response.send_message("🚫 Requer permissão: **Gerenciar Servidor**.", ephemeral=True)

        # ✅ garantir types (evita threads/fórum)
        if canal_pt.type not in TEXT_ONLY or canal_en.type not in TEXT_ONLY:
            return await inter.response.send_message("🚫 Apenas **canais de texto** são suportados.", ephemeral=True)

        # ✅ mesma guild
        if canal_pt.guild.id != inter.guild.id or canal_en.guild.id != inter.guild.id:  # type: ignore[arg-type]
            return await inter.response.send_message("🚫 Selecione canais **desta guild**.", ephemeral=True)

        if canal_pt.id == canal_en.id:
            return await inter.response.send_message("🚫 Escolha **dois canais diferentes**.", ephemeral=True)

        # ✅ checar duplicidade (par direto ou invertido)
        # get_link_info retorna: (target_id, src_lang, tgt_lang) para o canal informado, certo?
        info_a = await get_link_info(DB_PATH, inter.guild.id, canal_pt.id)  # type: ignore[arg-type]
        info_b = await get_link_info(DB_PATH, inter.guild.id, canal_en.id)  # type: ignore[arg-type]
        if info_a or info_b:
            return await inter.response.send_message("ℹ️ Um dos canais **já está linkado**. Remova antes de linkar novamente.", ephemeral=True)

        await link_pair(DB_PATH, inter.guild.id, canal_pt.id, canal_en.id)  # type: ignore[arg-type]
        log.info(f"[links] {inter.guild.id}: link {canal_pt.id}<->{canal_en.id}")
        await inter.response.send_message(
            f"🔗 Link criado: {canal_pt.mention} *(pt)* ⇄ {canal_en.mention} *(en)*",
            ephemeral=True
        )

    @app_commands.command(name="deslinkar", description="Remove o link do canal atual com seu par.")
    @app_commands.guild_only()
    async def deslinkar_cmd(self, inter: discord.Interaction):
        if not inter.user.guild_permissions.manage_guild:  # type: ignore[union-attr]
            return await inter.response.send_message("🚫 Requer permissão: **Gerenciar Servidor**.", ephemeral=True)

        current_ch = inter.channel
        if not isinstance(current_ch, discord.TextChannel) or current_ch.type not in TEXT_ONLY:
            return await inter.response.send_message("🚫 Use em um **canal de texto**.", ephemeral=True)

        info = await get_link_info(DB_PATH, inter.guild.id, current_ch.id)  # type: ignore[arg-type]
        if not info:
            return await inter.response.send_message("ℹ️ Nenhum link encontrado para este canal.", ephemeral=True)

        target_id, src_lang, tgt_lang = info
        await unlink_pair(DB_PATH, inter.guild.id, current_ch.id, target_id)  # type: ignore[arg-type]

        target_ch = inter.guild.get_channel(target_id)
        pair_txt = f"{current_ch.mention} ({src_lang}) ⇄ {target_ch.mention if target_ch else f'#{target_id}'} ({tgt_lang})"
        log.info(f"[links] {inter.guild.id}: unlink {current_ch.id}<->{target_id}")
        return await inter.response.send_message(f"❌ Link removido: {pair_txt}", ephemeral=True)

    @app_commands.command(name="deslinkar_todos", description="Remove todos os links deste servidor.")
    @app_commands.guild_only()
    async def unlink_all_cmd(self, inter: discord.Interaction):
        if not inter.user.guild_permissions.manage_guild:  # type: ignore[union-attr]
            return await inter.response.send_message("🚫 Requer permissão: **Gerenciar Servidor**.", ephemeral=True)

        count = await unlink_all(DB_PATH, inter.guild.id)  # type: ignore[arg-type]
        log.warning(f"[links] {inter.guild.id}: unlink_all ({count} pares)")
        await inter.response.send_message(f"🧹 Todos os links foram removidos. ({count} par(es))", ephemeral=True)



    @app_commands.command(name="links", description="Lista todos os pares de canais linkados neste servidor.")
    async def links_cmd(self, inter: discord.Interaction):
        if not inter.user.guild_permissions.manage_guild:  # type: ignore[union-attr]
            return await inter.response.send_message("🚫 Requer permissão: Gerenciar Servidor.", ephemeral=True)

        pairs = await list_links(DB_PATH, inter.guild.id)  # type: ignore[arg-type]
        if not pairs:
            return await inter.response.send_message("Nenhum link configurado.", ephemeral=True)

        linhas = []
        removed = 0
        skips = 0

        async def resolve(guild: discord.Guild, ch_id: int):
            ch = guild.get_channel(ch_id)
            if ch:
                return ch
            try:
                return await guild.fetch_channel(ch_id)  # 404 se não existe mais
            except discord.NotFound:
                return None

        for a, la, b, lb in pairs:
            ra = await resolve(inter.guild, a)  # type: ignore[arg-type]
            rb = await resolve(inter.guild, b)  # type: ignore[arg-type]

            if ra is None or rb is None:
                await unlink_pair(DB_PATH, inter.guild.id, a, b)  # type: ignore[arg-type]
                removed += 1
                continue

            if not isinstance(ra, discord.TextChannel) or not isinstance(rb, discord.TextChannel):
                # opcional: se quiser limpar tudo que não for TextChannel, descomente:
                # await unlink_pair(DB_PATH, inter.guild.id, a, b)  # type: ignore[arg-type]
                skips += 1
                continue

            linhas.append(f"🔗 {ra.mention} ({la})  ⇄  {rb.mention} ({lb})")

        total = len(linhas)
        msg = "\n".join(linhas) if linhas else "_(sem pares visíveis)_"
        nota = []
        if removed:
            nota.append(f"🧹 {removed} par(es) removido(s) — canal inexistente.")
        if skips:
            nota.append(f"ℹ️ {skips} par(es) ignorado(s) — tipo de canal não suportado.")
        nota_str = ("\n" + "\n".join(nota)) if nota else ""

        await inter.response.send_message(
            f"**Pares de canais linkados ({total}):**\n{msg}{nota_str}",
            ephemeral=True
        )

