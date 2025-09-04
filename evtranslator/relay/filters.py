# evtranslator/relay/filters.py
from __future__ import annotations
import asyncio, time, discord
from evtranslator.config import MIN_MSG_LEN, MAX_MSG_LEN, TRANSLATED_FLAG

class Dedupe:
    def __init__(self, window_sec: float):
        self.window = window_sec
        self.last: dict[tuple[int,int], tuple[str,float]] = {}
    def check_and_set(self, channel_id: int, user_id: int, text: str) -> bool:
        norm = " ".join(text.split())[:140]
        ts = time.monotonic()
        key = (channel_id, user_id)
        prev = self.last.get(key)
        if norm and prev and prev[0] == norm and (ts - prev[1]) < self.window:
            return False
        self.last[key] = (norm, ts)
        return True

async def tupperbox_guard(message: discord.Message) -> bool:
    """Retorna False se a msg foi proxied (apagada e re-postada por webhook)."""
    if message.webhook_id is not None:  # já é proxy
        return True
    if not message.author.bot:
        await asyncio.sleep(0.7)
        try:
            await message.channel.fetch_message(message.id)
        except discord.NotFound:
            return False
    return True

def basic_checks(message: discord.Message) -> bool:
    if not message.guild:
        return False

    # Ignora mensagens GERADAS pelo nosso próprio webhook
    own_ids = set()
    try:
        bot_client = message.guild._state._get_client()  # discord.Client/Bot
        wh_mgr = getattr(bot_client, "webhooks", None)
        own_ids = getattr(wh_mgr, "own_webhook_ids", set()) or set()
    except Exception:
        own_ids = set()

    if message.webhook_id and int(message.webhook_id) in own_ids:
        return False

    # Evita reprocessar qualquer saída nossa marcada com FLAG (cinto e suspensório)
    txt = (message.content or "").strip()
    if txt.endswith(TRANSLATED_FLAG):
        return False

    # Bots “normais” são ignorados; webhooks de terceiros (ex.: Tupperbox) passam
    if message.author.bot and message.webhook_id is None:
        return False

    # Apenas canais de texto
    if not isinstance(message.channel, discord.TextChannel):
        return False

    return True

def short_text_ok(text: str, has_atts: bool, has_url: bool) -> bool:
    if len(text) >= MIN_MSG_LEN: return True
    return has_atts or has_url

def clamp_text(text: str) -> str:
    if text and len(text) > MAX_MSG_LEN:
        return text[:MAX_MSG_LEN] + " (…)"; 
    return text
