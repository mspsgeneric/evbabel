# evtranslator/cogs/ajuda.py
from __future__ import annotations
import discord
from discord.ext import commands
from discord import app_commands

class AjudaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ajuda", description="Mostra os comandos do EVbabel e como usar.")
    @app_commands.guild_only()
    async def ajuda_cmd(self, inter: discord.Interaction):
        if inter.guild is None:
            return await inter.response.send_message("Use este comando em um servidor.", ephemeral=True)

        user = inter.user
        assert isinstance(user, discord.Member)
        is_admin = user.guild_permissions.administrator or user.guild_permissions.manage_guild

        # ── seção para todos os usuários ────────────────────────────────────────────
        linhas = [
            "## 📘 EVbabel — Ajuda",
            "",
            "O EVbabel traduz automaticamente **mensagens** entre canais linkados.",
            "Links preservam **URLs** (não são traduzidas).",
            "",
            "### 👤 Comandos para todos",
            "- **/linkar** — cria um link entre um canal em **PT** e um canal em **EN**.",
            "  • Se você **não for admin**, precisa ter permissão de **falar nos dois canais**.",
            "- **/deslinkar** — remove o link do **canal atual**.",
            "  • Se você **não for admin**, só remove **links que você criou**.",
            "- **/links** — lista **seus** links (admins veem todos).",
            "- **/quota** — mostra a cota de caracteres do servidor *(somente admin pode usar)*.",
        ]

        # ── seção adicional para admins ────────────────────────────────────────────
        if is_admin:
            linhas += [
                "",
                "### 🛡️ Comandos de admin",
                "- **/links** — lista **todos** os links e mostra quem criou.",
                "- **/deslinkar_todos** — remove todos os links do servidor.",
                "- **/clonar** — clona o canal atual (até 50 msgs) traduzindo para EN, preservando anexos.",
                "- **/quota** — exibe uso e limite mensal (com barra de progresso).",
                "- **/event_mode** — *(se disponível)* ativa/desativa modo de evento (cooldowns/limites especiais).",
                "",
                "### 🔐 Regras de permissão",
                "- Admin pode criar/remover **qualquer** link.",
                "- Usuários comuns só podem:",
                "  • criar link se puderem **falar nos dois canais**;",
                "  • **remover** apenas o link que **criaram**.",
            ]



        await inter.response.send_message("\n".join(linhas), ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(AjudaCog(bot))
