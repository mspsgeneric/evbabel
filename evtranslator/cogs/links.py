# evtranslator/cogs/links.py
from __future__ import annotations
import logging
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, Dict, Tuple, List

from evtranslator.db import (
    link_pair,
    unlink_pair,
    unlink_all,
    list_links,
    get_link_info,
    # chamaremos as versões com owner via getattr para manter compatibilidade
)

from evtranslator.config import DB_PATH

MAX_MSG = 1900  # margem de segurança para não encostar nos 2000 chars


log = logging.getLogger(__name__)

# 🔒 limitar seleção a canais de TEXTO
TEXT_ONLY = [discord.ChannelType.text]


def _has_send(ch: discord.TextChannel, member: discord.Member) -> bool:
    perms = ch.permissions_for(member)
    return perms.view_channel and perms.send_messages

def _chunk_text(text: str, max_len: int = MAX_MSG) -> List[str]:
    if len(text) <= max_len:
        return [text]
    parts, cur = [], []
    cur_len = 0
    for line in text.splitlines():
        if cur_len + len(line) + 1 > max_len:
            parts.append("\n".join(cur))
            cur = [line]
            cur_len = len(line) + 1
        else:
            cur.append(line)
            cur_len += len(line) + 1
    if cur:
        parts.append("\n".join(cur))
    return parts


class LinksCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ========== /linkar ==========
    @app_commands.command(
        name="linkar",
        description="Liga dois canais em PT↔EN (canal_pt ↔ canal_en)."
    )
    @app_commands.describe(canal_pt="Canal em Português", canal_en="Canal em Inglês")
    @app_commands.guild_only()
    async def linkar_cmd(
        self,
        inter: discord.Interaction,
        canal_pt: discord.TextChannel,
        canal_en: discord.TextChannel
    ):
        if inter.guild is None:
            return await inter.response.send_message("Use em um servidor.", ephemeral=True)

        # ✅ garantir types (evita threads/fórum)
        if canal_pt.type not in TEXT_ONLY or canal_en.type not in TEXT_ONLY:
            return await inter.response.send_message("🚫 Apenas **canais de texto** são suportados.", ephemeral=True)

        # ✅ mesma guild
        if canal_pt.guild.id != inter.guild.id or canal_en.guild.id != inter.guild.id:  # type: ignore[arg-type]
            return await inter.response.send_message("🚫 Selecione canais **desta guild**.", ephemeral=True)

        if canal_pt.id == canal_en.id:
            return await inter.response.send_message("🚫 Escolha **dois canais diferentes**.", ephemeral=True)

        user = inter.user
        assert isinstance(user, discord.Member)
        is_admin = user.guild_permissions.administrator or user.guild_permissions.manage_guild

        # 👤 usuário comum precisa poder falar nos dois canais
        if not is_admin:
            if not _has_send(canal_pt, user) or not _has_send(canal_en, user):
                return await inter.response.send_message(
                    "Você precisa ter permissão para falar **no canal PT** e no **canal EN**.",
                    ephemeral=True,
                )

        # 🤖 bot precisa enviar no destino (e ler ambos)
        me = inter.guild.me
        if not me or not canal_pt.permissions_for(me).read_messages or not canal_en.permissions_for(me).send_messages:
            return await inter.response.send_message(
                "Eu preciso poder **ler o canal PT** e **enviar no canal EN**.",
                ephemeral=True,
            )

        # ✅ checar duplicidade (par direto ou invertido)
        info_a = await get_link_info(DB_PATH, inter.guild.id, canal_pt.id)  # type: ignore[arg-type]
        info_b = await get_link_info(DB_PATH, inter.guild.id, canal_en.id)  # type: ignore[arg-type]
        if info_a or info_b:
            return await inter.response.send_message("ℹ️ Um dos canais **já está linkado**. Remova antes de linkar novamente.", ephemeral=True)

        # 📝 criar o link, preferindo versão com owner se existir
        created_by = user.id
        created = False
        try:
            link_with_owner = getattr(__import__("evtranslator.db", fromlist=["link_pair_with_owner"]), "link_pair_with_owner", None)
        except Exception:
            link_with_owner = None

        try:
            if callable(link_with_owner):
                await link_with_owner(DB_PATH, inter.guild.id, canal_pt.id, canal_en.id, created_by)  # type: ignore[arg-type]
                created = True
        except Exception as e:
            log.warning("[links] link_pair_with_owner falhou/indisponível: %s", e)

        if not created:
            # fallback para API antiga
            await link_pair(DB_PATH, inter.guild.id, canal_pt.id, canal_en.id)  # type: ignore[arg-type]

        log.info(f"[links] {inter.guild.id}: link {canal_pt.id}<->{canal_en.id} (by {created_by})")
        await inter.response.send_message(
            f"🔗 Link criado: {canal_pt.mention} *(pt)* ⇄ {canal_en.mention} *(en)*",
            ephemeral=not is_admin  # admin pode querer deixar público
        )

    # ========== /deslinkar ==========
    @app_commands.command(name="deslinkar", description="Remove o link do canal atual com seu par.")
    @app_commands.guild_only()
    async def deslinkar_cmd(self, inter: discord.Interaction):
        if inter.guild is None:
            return await inter.response.send_message("Use em um servidor.", ephemeral=True)

        current_ch = inter.channel
        if not isinstance(current_ch, discord.TextChannel) or current_ch.type not in TEXT_ONLY:
            return await inter.response.send_message("🚫 Use em um **canal de texto**.", ephemeral=True)

        info = await get_link_info(DB_PATH, inter.guild.id, current_ch.id)  # type: ignore[arg-type]
        if not info:
            return await inter.response.send_message("ℹ️ Nenhum link encontrado para este canal.", ephemeral=True)

        target_id, src_lang, tgt_lang = info
        target_ch = inter.guild.get_channel(target_id)

        user = inter.user
        assert isinstance(user, discord.Member)
        is_admin = user.guild_permissions.administrator or user.guild_permissions.manage_guild

        # 🔐 se não for admin, confirmar que o usuário é o criador do link
        if not is_admin:
            owner_id = None
            try:
                get_owner = getattr(__import__("evtranslator.db", fromlist=["get_link_owner"]), "get_link_owner", None)
            except Exception:
                get_owner = None

            try:
                if callable(get_owner):
                    owner_id = await get_owner(DB_PATH, inter.guild.id, current_ch.id)  # type: ignore[arg-type]
            except Exception as e:
                log.warning("[links] get_link_owner falhou/indisponível: %s", e)

            if owner_id is None:
                return await inter.response.send_message(
                    "Este servidor ainda não está com os links no formato novo (com proprietário). "
                    "Peça a um administrador para remover, ou atualize o banco.",
                    ephemeral=True,
                )

            if int(owner_id) != user.id:
                return await inter.response.send_message(
                    "Apenas **administradores** ou o **criador** deste link podem removê-lo.",
                    ephemeral=True,
                )

            # também exige permissão de falar no canal de origem (boa prática)
            if not _has_send(current_ch, user):
                return await inter.response.send_message(
                    "Você precisa ter permissão para falar neste canal.",
                    ephemeral=True,
                )

        await unlink_pair(DB_PATH, inter.guild.id, current_ch.id, target_id)  # type: ignore[arg-type]
        pair_txt = f"{current_ch.mention} ({src_lang}) ⇄ {target_ch.mention if isinstance(target_ch, discord.TextChannel) else f'#{target_id}'} ({tgt_lang})"

        log.info(f"[links] {inter.guild.id}: unlink {current_ch.id}<->{target_id} (by {user.id})")
        return await inter.response.send_message(f"❌ Link removido: {pair_txt}", ephemeral=not is_admin)

    # ========== /deslinkar_todos ==========
    @app_commands.command(name="deslinkar_todos", description="Remove todos os links deste servidor.")
    @app_commands.guild_only()
    async def unlink_all_cmd(self, inter: discord.Interaction):
        user = inter.user
        assert isinstance(user, discord.Member)
        if not (user.guild_permissions.administrator or user.guild_permissions.manage_guild):  # type: ignore[union-attr]
            return await inter.response.send_message("🚫 Requer permissão: **Gerenciar Servidor**.", ephemeral=True)

        count = await unlink_all(DB_PATH, inter.guild.id)  # type: ignore[arg-type]
        log.warning(f"[links] {inter.guild.id}: unlink_all ({count} pares)")
        await inter.response.send_message(f"🧹 Todos os links foram removidos. ({count} par(es))", ephemeral=True)

    
    # ========== /links ==========


    


    async def _resolve_channel(guild: discord.Guild, ch_id: int) -> Optional[discord.abc.GuildChannel]:
        ch = guild.get_channel(ch_id)
        if ch:
            return ch
        try:
            return await guild.fetch_channel(ch_id)  # 404 se não existe mais
        except discord.NotFound:
            return None

    async def _resolve_user_name(bot: discord.Client, user_id: int, cache: Dict[int, str]) -> str:
        if user_id in cache:
            return cache[user_id]
        # tenta resolver como Member (se estiver no guild) ou como User global
        name = f"<@{user_id}>"
        try:
            u = await bot.fetch_user(user_id)
            if u:
                name = f"{u.mention} ({u.name})"
        except Exception:
            pass
        cache[user_id] = name
        return name

    @app_commands.command(name="links", description="Lista os pares de canais linkados neste servidor.")
    @app_commands.guild_only()
    async def links_cmd(self, inter: discord.Interaction):
        if inter.guild is None:
            return await inter.response.send_message("Use em um servidor.", ephemeral=True)

        user = inter.user
        assert isinstance(user, discord.Member)
        is_admin = user.guild_permissions.administrator or user.guild_permissions.manage_guild

        # ✅ SEMPRE ephemeral
        await inter.response.defer(ephemeral=True, thinking=False)

        # preferir versão com owner, se existir
        try:
            list_with_owner = getattr(__import__("evtranslator.db", fromlist=["list_links_with_owner"]),
                                    "list_links_with_owner", None)
        except Exception:
            list_with_owner = None

        # estrutura: (a, la, b, lb, owner_id)
        pairs: Optional[List[Tuple[int, str, int, str, Optional[int]]]] = None
        if callable(list_with_owner):
            try:
                pairs = await list_with_owner(DB_PATH, inter.guild.id)  # type: ignore[arg-type]
            except Exception as e:
                log.warning("[links] list_links_with_owner falhou/indisponível: %s", e)

        if pairs is None:
            raw = await list_links(DB_PATH, inter.guild.id)  # type: ignore[arg-type]
            pairs = [(a, la, b, lb, None) for (a, la, b, lb) in raw]

        # 🔎 filtro de visualização:
        # - Admin: vê todos.
        # - Usuário comum: vê apenas os que criou (owner == user.id).
        visible: List[Tuple[int, str, int, str, Optional[int]]] = []
        mine = 0
        for a, la, b, lb, owner in pairs:
            if is_admin or (owner is not None and int(owner) == user.id):
                visible.append((a, la, b, lb, owner))
                if owner is not None and int(owner) == user.id:
                    mine += 1

        if not visible:
            if is_admin:
                return await inter.followup.send("Nenhum link configurado.", ephemeral=True)
            return await inter.followup.send(
                "Você ainda **não criou** nenhum link. Crie com `/linkar` ou peça a um administrador.",
                ephemeral=True
            )

        # ordena por nome do canal A (fallback por ID)
        # primeiro vamos resolver canais e limpar órfãos
        linhas: List[str] = []
        removed = 0
        skips = 0
        resolved_rows: List[Tuple[discord.TextChannel, str, discord.TextChannel, str, Optional[int]]] = []

        for a, la, b, lb, owner in visible:
            ra = await _resolve_channel(inter.guild, a)  # type: ignore[arg-type]
            rb = await _resolve_channel(inter.guild, b)  # type: ignore[arg-type]

            if ra is None or rb is None:
                await unlink_pair(DB_PATH, inter.guild.id, a, b)  # type: ignore[arg-type]
                removed += 1
                continue

            if not isinstance(ra, discord.TextChannel) or not isinstance(rb, discord.TextChannel):
                # opcional: limpar tudo que não for TextChannel
                # await unlink_pair(DB_PATH, inter.guild.id, a, b)  # type: ignore[arg-type]
                skips += 1
                continue

            resolved_rows.append((ra, la, rb, lb, owner))

        resolved_rows.sort(key=lambda row: (row[0].name or "", row[0].id))

        # resolve nomes dos criadores (com cache) — só se admin
        owner_cache: Dict[int, str] = {}
        for ra, la, rb, lb, owner in resolved_rows:
            if is_admin and owner:
                owner_name = await _resolve_user_name(inter.client, int(owner), owner_cache)  # type: ignore[arg-type]
                owner_txt = f" • criador: {owner_name}"
            else:
                owner_txt = ""
            linhas.append(f"🔗 {ra.mention} ({la})  ⇄  {rb.mention} ({lb}){owner_txt}")

        total = len(linhas)
        msg = "\n".join(linhas) if linhas else "_(sem pares visíveis)_"
        nota = []
        if removed:
            nota.append(f"🧹 {removed} par(es) removido(s) — canal inexistente.")
        if skips:
            nota.append(f"ℹ️ {skips} par(es) ignorado(s) — tipo de canal não suportado.")
        if not is_admin:
            nota.append(f"👤 Mostrando **apenas os seus** links. ({mine})")
        nota_str = ("\n" + "\n".join(nota)) if nota else ""

        # paginação simples para não estourar 2000 chars
        header = f"**Pares de canais linkados ({total}):**\n"
        chunks = _chunk_text(header + msg + nota_str, MAX_MSG)
        for i, part in enumerate(chunks, 1):
            suffix = f" (página {i}/{len(chunks)})" if len(chunks) > 1 else ""
            await inter.followup.send(part + suffix, ephemeral=True)


