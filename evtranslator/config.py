# evtranslator/config.py
from __future__ import annotations

import os
import logging
from pathlib import Path

import discord
from dotenv import load_dotenv

# Carrega .env (prioriza o CWD; fallback raiz do projeto)
load_dotenv()
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

# --- Helpers para tipos ---
def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        logging.warning("⚠️ %s=%r inválido, usando default %s", name, val, default)
        return default

def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        logging.warning("⚠️ %s=%r inválido, usando default %s", name, val, default)
        return default

# --- Discord ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise SystemExit("❌ Missing DISCORD_TOKEN in env")

# Banco de dados local (SQLite)
DB_PATH = os.getenv("EVBABEL_DB", "evlogger_links.sqlite")

# --- Supabase ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise SystemExit("❌ Missing SUPABASE_URL / SUPABASE_KEY in env")

# --- Tunables ---
CONCURRENCY = _get_int("CONCURRENCY", 6)
HTTP_TIMEOUT = _get_float("HTTP_TIMEOUT", 15.0)
RETRIES = _get_int("RETRIES", 4)
BACKOFF_BASE = _get_float("BACKOFF_BASE", 0.5)
CHANNEL_COOLDOWN_SEC = _get_float("CHANNEL_COOLDOWN", 0.15)
USER_COOLDOWN_SEC = _get_float("USER_COOLDOWN", 2.0)

TEST_GUILD_ID: int | None = None
if os.getenv("TEST_GUILD_ID"):
    try:
        TEST_GUILD_ID = int(os.getenv("TEST_GUILD_ID", ""))
    except ValueError:
        logging.warning("⚠️ TEST_GUILD_ID inválido, ignorando.")

# --- Constantes de tradução ---
TRANSLATED_FLAG = "\u200b"  # marcador invisível para evitar loops
MIN_MSG_LEN = 4
MAX_MSG_LEN = 2000  # limite hard do Discord

# --- Intents mínimos ---
INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.message_content = True
