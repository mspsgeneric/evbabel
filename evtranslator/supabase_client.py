# evtranslator/supabase_client.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

# Carrega .env do diretório atual e da raiz do projeto (sem sobrescrever env do SO)
load_dotenv()  # CWD
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)  # raiz do projeto

# Timeout padrão (pode ajustar via SUPABASE_TIMEOUT=10)
_DEFAULT_TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "10"))

# --- Sessão HTTP com retries leves ---
_session: requests.Session | None = None
def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        retry = Retry(
            total=3,
            read=3,
            connect=3,
            backoff_factor=0.3,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["POST", "GET", "PATCH", "DELETE"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        _session = s
    return _session

def _get_env() -> Tuple[str, str]:
    base = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not base or not key:
        raise RuntimeError(
            "Faltam SUPABASE_URL e/ou SUPABASE_KEY. "
            "Defina no .env na raiz do projeto (ou export no ambiente)."
        )
    return base.rstrip("/"), key

def _headers(key: str) -> Dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

def _ensure_row_list(obj: Any) -> list:
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    # Supabase RPC normalmente retorna lista; se vier dict, normaliza
    return [obj]

def _rpc(name: str, payload: dict, timeout: float = _DEFAULT_TIMEOUT) -> list:
    base, key = _get_env()
    url = f"{base}/rest/v1/rpc/{name}"
    r = _get_session().post(url, json=payload, headers=_headers(key), timeout=timeout)

    # Se vier 204 (No Content), retorna lista vazia
    if r.status_code == 204:
        return []

    # Levanta com contexto
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        # inclui corpo da resposta pra log/diagnóstico
        msg = f"RPC {name} falhou: {e} | status={r.status_code} body={r.text[:500]}"
        raise RuntimeError(msg) from None

    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"RPC {name} retornou payload não-JSON: {r.text[:200]}")

    return _ensure_row_list(data)

# === API pública ===

def consume_chars(guild_id: int | str, amount: int) -> tuple[bool, int]:
    rows = _rpc("rpc_emails_consume_chars", {
        "p_guild_id": str(guild_id),
        "p_amount": int(amount),
    })
    if not rows:
        # Sem linha retornada: trate como não permitido
        return False, 0
    row = rows[0]
    return bool(row.get("allowed", False)), int(row.get("remaining", 0) or 0)

def get_quota(guild_id: int | str) -> dict:
    rows = _rpc("rpc_emails_get_quota", {"p_guild_id": str(guild_id)})
    if not rows:
        # Retorno vazio: devolve shape padrão para o /quota não quebrar
        return {
            "translate_enabled": False,
            "char_limit": 0,
            "used_chars": 0,
            "remaining": 0,
            "cycle_start": None,
            "next_reset": None,
            "billing_day": None,
            "cycle_tz": "UTC",
        }
    return rows[0]

def ensure_guild_row(guild_id: int | str) -> None:
    base, key = _get_env()
    url = f"{base}/rest/v1/emails?on_conflict=guild_id"
    headers = _headers(key) | {"Prefer": "resolution=merge-duplicates"}
    payload = {"guild_id": str(guild_id)}
    r = _get_session().post(url, json=payload, headers=headers, timeout=_DEFAULT_TIMEOUT)
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"ensure_guild_row falhou: {e} | status={r.status_code} body={r.text[:500]}") from None

# --- Helpers administrativos opcionais (use se precisar no futuro) ---

def set_translate_enabled(guild_id: int | str, enabled: bool) -> dict:
    rows = _rpc("rpc_emails_set_enabled", {
        "p_guild_id": str(guild_id),
        "p_enabled": bool(enabled),
    })
    return rows[0] if rows else {}

def set_char_limit(guild_id: int | str, limit: int) -> dict:
    rows = _rpc("rpc_emails_set_limit", {
        "p_guild_id": str(guild_id),
        "p_limit": int(limit),
    })
    return rows[0] if rows else {}

def set_cycle_tz(guild_id: int | str, tz: str) -> dict:
    rows = _rpc("rpc_emails_set_tz", {
        "p_guild_id": str(guild_id),
        "p_tz": tz,
    })
    return rows[0] if rows else {}

def set_billing_day(guild_id: int | str, day: int) -> dict:
    rows = _rpc("rpc_emails_set_billing_day", {
        "p_guild_id": str(guild_id),
        "p_day": int(day),
    })
    return rows[0] if rows else {}
