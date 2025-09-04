# evtranslator/relay/translate_wrap.py
from __future__ import annotations
import asyncio, random, logging, aiohttp
from evtranslator.relay.backoff import BackoffCfg, ExponentialBackoff, CircuitBreaker
from evtranslator.translate import google_web_translate

async def translate_with_controls(
    session: aiohttp.ClientSession,
    text: str, src_lang: str, tgt_lang: str,
    sem: asyncio.Semaphore,
    timeout_sec: float,
    jitter_ms: int,
    backoff: BackoffCfg,
    cb: CircuitBreaker,
    rate_acquire,
) -> str | None:
    if cb.is_open:
        logging.warning("CB open: segurando traduções por curto período")
        return None
    if jitter_ms > 0:
        await asyncio.sleep(random.uniform(0, jitter_ms / 1000.0))
    await rate_acquire()

    bo = ExponentialBackoff(backoff)
    last_err = None
    for attempt in range(backoff.attempts):
        try:
            async with sem:
                return await asyncio.wait_for(
                    google_web_translate(session, text, src_lang, tgt_lang),
                    timeout=timeout_sec,
                )
        except asyncio.TimeoutError as e:
            last_err = e; cb.on_failure()
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "429" in msg or "too many" in msg or "rate" in msg or msg.startswith("5"):
                cb.on_failure()
            else:
                logging.exception("Erro não recuperável na tradução: %r", e)
                break
        if attempt < backoff.attempts - 1:
            await asyncio.sleep(bo.next_delay())
    logging.warning("Tradução falhou após %d tentativas. Último erro=%r", backoff.attempts, last_err)
    return None
