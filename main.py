# main.py
from __future__ import annotations
import logging
import signal
import sys
import os
import asyncio
from aiohttp import web

from evtranslator.config import DISCORD_TOKEN, DB_PATH, SUPABASE_URL, SUPABASE_KEY
from evtranslator.bot import EVTranslatorBot

# painel
from painel.routes import setup_painel_routes
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)
logging.getLogger("discord").setLevel(logging.WARNING)

async def start_web_app(loop) -> tuple[web.AppRunner, web.TCPSite]:
    """Sobe o servidor aiohttp do painel."""
    app = web.Application()

    # autenticação básica é aplicada dentro do setup_painel_routes (via middleware), conforme seus arquivos.
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    setup_painel_routes(app, supabase)

    host = os.getenv("PANEL_HOST", "0.0.0.0")
    try:
        port = int(os.getenv("PANEL_PORT", "8080"))
    except ValueError:
        port = 8080

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logging.info("🌐 Painel web rodando em http://%s:%s", host, port)
    return runner, site

async def main():
    bot = EVTranslatorBot(db_path=DB_PATH)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _stop(*_):
        logging.info("📴 Sinal recebido, desligando...")
        stop_event.set()

    # Windows não suporta SIGTERM em add_signal_handler; ignore se der NotImplementedError
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    # inicia web + bot
    runner = None
    try:
        runner, _site = await start_web_app(loop)

        async with bot:
            bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
            await stop_event.wait()

            # encerra bot primeiro
            await bot.close()
            bot_task.cancel()
    finally:
        # encerra web
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception:
                pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
