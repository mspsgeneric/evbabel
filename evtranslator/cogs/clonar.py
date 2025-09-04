# comandos/clonar.py
from __future__ import annotations
import re
import io
from typing import Dict

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

from evtranslator.translate import google_web_translate
from evtranslator.webhook import WebhookSender

MAX_MSGS = 50
MAX_LEN = 1900
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
URL_RE = re.compile(r'(https?://[^\s<>]+)')

def looks_like_image_url(url: str) -> bool:
    u = url.lower()
    return any(u.endswith(ext) for ext in IMG_EXT) or ("media.discordapp.net" in u or "cdn.discordapp.com" in u)

class Clonar(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.webhook_sender = WebhookSender(bot_user_id=None)

    @commands.Cog.listener()
    async def on_ready(self):
        if self.webhook_sender.bot_user_id is None and self.bot.user:
            self.webhook_sender.bot_user_id = self.bot.user.id

    @app_commands.command(
        name="clonar",
        description="Clona este canal (até 50 msgs), traduzindo para EN e preservando imagens/anexos."
    )
    @app_commands.guild_only()  # só em guild
    @app_commands.checks.has_permissions(administrator=True)  # apenas admins
    async def clonar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        canal_src = interaction.channel
        if not isinstance(canal_src, discord.TextChannel):
            return await interaction.followup.send("Use este comando em um canal de texto.", ephemeral=True)

        mensagens = [m async for m in canal_src.history(limit=MAX_MSGS, oldest_first=True)]
        if not mensagens:
            return await interaction.followup.send("Sem mensagens recentes para clonar.", ephemeral=True)

        # --- cria o canal clonado com mesmas permissões/config ---
        name = f"{canal_src.name}-traduzido"[:100]
        overwrites = canal_src.overwrites
        topic = f"Clonado de #{canal_src.name} → EN"
        kwargs = dict(
            name=name,
            overwrites=overwrites,
            topic=topic,
            position=canal_src.position + 1,
            nsfw=canal_src.is_nsfw(),
            slowmode_delay=canal_src.slowmode_delay,
        )
        if canal_src.category:
            canal_dst = await canal_src.category.create_text_channel(**kwargs)
        else:
            canal_dst = await canal_src.guild.create_text_channel(**kwargs)

        # checar permissões essenciais no canal clonado
        me = canal_dst.guild.me
        if me is None and self.bot.user:
            try:
                me = await canal_dst.guild.fetch_member(self.bot.user.id)
            except Exception:
                me = None

        if me is None:
            return await interaction.followup.send(
                "❌ Não consegui identificar meu usuário na guild. Tente novamente.",
                ephemeral=True,
            )

        perms = canal_dst.permissions_for(me)
        if not (perms.view_channel and perms.send_messages and perms.manage_webhooks):
            return await interaction.followup.send(
                "❌ Preciso da permissão **Gerenciar Webhooks** neste canal clonado para postar com o nick do autor.",
                ephemeral=True,
            )

        # criar/pegar webhook e abortar se não conseguir (para garantir nick correto)
        wh = await self.webhook_sender.get_or_create(canal_dst)
        if wh is None:
            return await interaction.followup.send(
                "❌ Não consegui criar/usar o webhook neste canal. Verifique as permissões.",
                ephemeral=True,
            )

        # sessão única para tradução E execução do webhook via URL (com token)
        async with aiohttp.ClientSession() as session:
            # executor tokenizado do webhook (sempre executável)
            exec_wh = discord.Webhook.from_url(wh.url, session=session)



            # cache para resolver User -> Member e garantir nick de servidor
            member_cache: Dict[int, discord.Member] = {}

            for msg in mensagens:
                original_text = msg.content or ""
                attachments = list(msg.attachments)

                # resolver autor para Member (nick de servidor)
                author = msg.author  # User ou Member
                member = None
                if isinstance(author, discord.Member):
                    member = author
                else:
                    mid = author.id
                    member = member_cache.get(mid)
                    if member is None:
                        member = msg.guild.get_member(mid)
                        if member is None:
                            try:
                                member = await msg.guild.fetch_member(mid)
                            except Exception:
                                member = None
                        if member:
                            member_cache[mid] = member

                if member:
                    display_name = member.display_name  # nick de servidor
                    avatar_url = member.display_avatar.url
                else:
                    # fallback: usuário não está mais na guild
                    display_name = getattr(author, "global_name", None) or author.name
                    avatar_url = author.display_avatar.url

                # separar URLs de imagem das demais
                image_urls_in_text = []
                other_urls_in_text = []
                for u in URL_RE.findall(original_text):
                    if looks_like_image_url(u):
                        image_urls_in_text.append(u)
                    else:
                        other_urls_in_text.append(u)

                # texto p/ tradução sem links de imagem
                text_for_translation = original_text
                for u in image_urls_in_text:
                    text_for_translation = text_for_translation.replace(u, "").strip()

                translated = ""
                if text_for_translation:
                    try:
                        translated = await google_web_translate(session, text_for_translation, src="auto", dest="en")
                    except Exception:
                        translated = "[Translation error]"

                # recoloca links não-imagem (se quiser manter)
                for u in other_urls_in_text:
                    translated = (translated + "\n" + u).strip() if translated else u

                if len(translated) > MAX_LEN:
                    translated = translated[:MAX_LEN - 10] + "\n[...]"

                # preparar arquivos (reupload)
                files: list[discord.File] = []
                for a in attachments:
                    try:
                        data = await a.read(use_cached=True)
                        files.append(discord.File(io.BytesIO(data), filename=a.filename))
                    except Exception:
                        translated = (translated + f"\n{a.url}").strip() if translated else a.url

                for url in image_urls_in_text:
                    try:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                            if resp.status == 200 and resp.headers.get("Content-Type", "").startswith("image/"):
                                data = await resp.read()
                                fname = url.split("/")[-1].split("?")[0] or "image.jpg"
                                files.append(discord.File(io.BytesIO(data), filename=fname[:100]))
                            else:
                                translated = (translated + f"\n{url}").strip() if translated else url
                    except Exception:
                        translated = (translated + f"\n{url}").strip() if translated else url

                content_to_send = translated if translated else "\u200b"

                # sempre via webhook executor (com token), sem fallback
                try:
                    await exec_wh.send(
                        content=content_to_send,
                        username=display_name,
                        avatar_url=avatar_url,
                        files=files if files else None,
                        allowed_mentions=discord.AllowedMentions.none(),
                        wait=True,
                    )
                except Exception:
                    # segunda tentativa: texto-apenas (mantém nick)
                    try:
                        await exec_wh.send(
                            content=content_to_send,
                            username=display_name,
                            avatar_url=avatar_url,
                            allowed_mentions=discord.AllowedMentions.none(),
                            wait=True,
                        )
                    except Exception:
                        # sem fallback para channel.send; pula para manter consistência de nome
                        continue

        await interaction.followup.send(f"✅ Clonado com permissões preservadas: {canal_dst.mention}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Clonar(bot))
