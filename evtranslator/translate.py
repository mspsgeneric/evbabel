# evtranslator/translate.py
from __future__ import annotations

import asyncio
import aiohttp
import urllib.parse
import random
from typing import Optional

from .config import RETRIES, BACKOFF_BASE, HTTP_TIMEOUT, MAX_MSG_LEN

async def google_web_translate(
    session: aiohttp.ClientSession, text: str, src: str, dest: str
) -> str:
    """
    Usa o endpoint público do Google Translate (não-oficial).
    Retorna a tradução completa ou levanta RuntimeError em falha.
    """

    # ⚠️ corta para não estourar limite do endpoint (~5000 chars)
    if len(text) > 4800:
        text = text[:4800]

    base = "https://translate.googleapis.com/translate_a/single"
    params = {"client": "gtx", "sl": src, "tl": dest, "dt": "t", "q": text}
    url = f"{base}?{urllib.parse.urlencode(params)}"

    for attempt in range(RETRIES):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)) as resp:
                # retry explícito em 429 ou 5xx
                if resp.status == 429 or 500 <= resp.status < 600:
                    delay = BACKOFF_BASE * (2**attempt) + random.uniform(0, 0.3)
                    await asyncio.sleep(delay)
                    continue

                resp.raise_for_status()
                data = await resp.json(content_type=None)

                parts: list[str] = []
                for seg in data[0]:
                    if seg and seg[0]:
                        parts.append(seg[0])
                return "".join(parts)

        except Exception as e:
            delay = BACKOFF_BASE * (2**attempt) + random.uniform(0, 0.2)
            await asyncio.sleep(delay)
            # logging pode ser adicionado aqui se quiser debug detalhado
            continue

    raise RuntimeError("google_web_translate: failed after retries")
