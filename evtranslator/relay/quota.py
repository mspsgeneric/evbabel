# evtranslator/relay/quota.py
from __future__ import annotations
import asyncio, logging, discord
from evtranslator.supabase_client import ensure_guild_row, get_quota, consume_chars

async def ensure_and_snapshot(guild_id: int) -> dict:
    try: await asyncio.to_thread(ensure_guild_row, guild_id)
    except Exception: pass
    return await asyncio.to_thread(get_quota, guild_id)

async def check_enabled_and_notice(message: discord.Message, snapshot: dict, last_notice_ts: dict[int,float]) -> bool:
    import time
    enabled = bool(snapshot.get("translate_enabled", False))
    if enabled: return True
    now = time.time()
    last = last_notice_ts.get(message.guild.id, 0.0)
    if now - last > 60:
        try:
            await message.channel.send(
                "🚫 Este servidor **não está habilitado** para tradução no momento. "
                "Entre em contato com o criador/gerente do bot."
            )
        except Exception as e:
            logging.warning("Aviso 'não habilitado' falhou (guild=%s): %s", message.guild.id, e)
        last_notice_ts[message.guild.id] = now
    return False

async def reserve_quota_if_needed(guild_id: int, text_len: int) -> tuple[bool, int]:
    if text_len <= 0:  # anexo/link puro
        return True, 0
    try:
        return await asyncio.to_thread(consume_chars, guild_id, text_len)
    except Exception as e:
        logging.exception("Falha ao consumir cota (guild=%s): %s", guild_id, e)
        return False, 0

async def maybe_warn_90pct(guild: discord.Guild, warned_guilds: set[int]):
    try:
        quota = await asyncio.to_thread(get_quota, guild.id)
        char_limit = quota.get("char_limit") or 0
        used = quota.get("used_chars") or 0
        if char_limit and used >= 0.9 * char_limit and guild.id not in warned_guilds:
            warned_guilds.add(guild.id)
            msg = (f"⚠️ Este servidor já consumiu {used:,} de {char_limit:,} caracteres "
                   f"(90% da cota mensal). Considere ajustar o limite ou aguardar o reset.")
            sent = False
            try:
                if guild.owner:
                    await guild.owner.send(msg); sent = True
            except Exception: pass
            if not sent:
                try:
                    admin = next((m for m in guild.members if m.guild_permissions.administrator and not m.bot), None)
                    if admin: await admin.send(msg)
                except Exception: pass
        if used < 1000 and guild.id in warned_guilds:
            warned_guilds.remove(guild.id)
    except Exception as e:
        logging.exception("Falha ao verificar 90%% cota (guild=%s): %s", guild.id, e)
