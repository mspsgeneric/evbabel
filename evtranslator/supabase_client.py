# evtranslator/supabase_client.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Tuple, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

# Carrega .env do diretório atual e da raiz do projeto (sem sobrescrever env do SO)
load_dotenv()  # CWD
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)  # raiz do projeto

# Timeout padrão (pode ajustar via SUPABASE_TIMEOUT=10)
_DEFAULT_TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "10"))
_SUPABASE_DEBUG = os.getenv("SUPABASE_DEBUG", "false").lower() == "true"

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
        msg = f"RPC {name} falhou: {e} | status={r.status_code} body={r.text[:500]}"
        raise RuntimeError(msg) from None

    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"RPC {name} retornou payload não-JSON: {r.text[:200]}")

    return _ensure_row_list(data)

# === API pública ===

def consume_chars(guild_id: int | str, amount: int) -> tuple[bool, int]:
    rows = _rpc("rpc_translator_consume_chars", {
        "p_guild_id": str(guild_id),
        "p_amount": int(amount),
    })
    if not rows:
        # Sem linha retornada: trate como não permitido
        return False, 0
    row = rows[0]
    return bool(row.get("allowed", False)), int(row.get("remaining", 0) or 0)

def get_quota(guild_id: int | str) -> dict:
    rows = _rpc("rpc_translator_get_quota", {"p_guild_id": str(guild_id)})
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

# --- helpers REST para nomes/linhas ---

def _post_upsert_emails_translator(payload: dict) -> requests.Response:
    base, key = _get_env()
    url = f"{base}/rest/v1/emails_translator?on_conflict=guild_id"
    headers = _headers(key) | {"Prefer": "resolution=merge-duplicates"}
    r = _get_session().post(url, json=payload, headers=headers, timeout=_DEFAULT_TIMEOUT)
    if _SUPABASE_DEBUG:
        print("POST upsert emails_translator", r.status_code, r.text[:300])
    return r

def _patch_emails_translator(guild_id: int | str, patch: dict) -> requests.Response:
    base, key = _get_env()
    url = f"{base}/rest/v1/emails_translator?guild_id=eq.{guild_id}"
    headers = _headers(key) | {"Prefer": "return=representation"}
    r = _get_session().patch(url, json=patch, headers=headers, timeout=_DEFAULT_TIMEOUT)
    if _SUPABASE_DEBUG:
        print("PATCH emails_translator", r.status_code, r.text[:300])
    return r

def set_guild_name_force(guild_id: int | str, guild_name: str) -> bool:
    """
    Força atualização do nome da guild via PATCH direto na linha.
    Retorna True se alterou com sucesso (2xx).
    """
    r = _patch_emails_translator(guild_id, {"guild_name": str(guild_name)})
    if 200 <= r.status_code < 300:
        return True
    # Se coluna não existir ou outra falha, levanta só se debug desativado; senão, dá contexto
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"set_guild_name_force falhou: {e} | status={r.status_code} body={r.text[:300]}") from None
    return False

def ensure_guild_row(guild_id: int | str, guild_name: Optional[str] = None) -> None:
    """
    Upsert idempotente do registro na tabela do tradutor e,
    se guild_name vier, aplica PATCH para gravar/atualizar o nome.
    """
    # 1) garante linha pelo ID (não depende do schema de colunas adicionais)
    r = _post_upsert_emails_translator({"guild_id": str(guild_id)})
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"ensure_guild_row (upsert id) falhou: {e} | status={r.status_code} body={r.text[:300]}") from None

    # 2) se veio nome, tenta PATCH dedicado (mais confiável que on_conflict para atualizar campos)
    if guild_name:
        try:
            set_guild_name_force(guild_id, guild_name)
        except Exception as e:
            # Não quebra o fluxo de quem chamou; apenas informa se DEBUG
            if _SUPABASE_DEBUG:
                print("ensure_guild_row: PATCH nome falhou:", e)



# --- Presença no painel ---

def guild_exists(guild_id: int | str) -> bool:
    """
    True se a guild existir na tabela emails_translator (painel).
    Usado para decidir se o bot deve sair do servidor.
    """
    base, key = _get_env()
    url = f"{base}/rest/v1/emails_translator?select=guild_id&guild_id=eq.{guild_id}&limit=1"
    r = _get_session().get(url, headers=_headers(key), timeout=_DEFAULT_TIMEOUT)
    # 200 com [] = não existe; >=1 item = existe
    if r.status_code != 200:
        # Em caso de erro transitório, considere 'existe' para não sair por engano
        return True
    try:
        data = r.json()
    except ValueError:
        return True
    return bool(data)


# --- Helpers administrativos opcionais (use se precisar no futuro) ---

def set_translate_enabled(guild_id: int | str, enabled: bool) -> dict:
    rows = _rpc("rpc_translator_set_enabled", {
        "p_guild_id": str(guild_id),
        "p_enabled": bool(enabled),
    })
    return rows[0] if rows else {}

def set_char_limit(guild_id: int | str, limit: int) -> dict:
    rows = _rpc("rpc_translator_set_limit", {
        "p_guild_id": str(guild_id),
        "p_limit": int(limit),
    })
    return rows[0] if rows else {}

def set_cycle_tz(guild_id: int | str, tz: str) -> dict:
    rows = _rpc("rpc_translator_set_tz", {
        "p_guild_id": str(guild_id),
        "p_tz": tz,
    })
    return rows[0] if rows else {}

def set_billing_day(guild_id: int | str, day: int) -> dict:
    rows = _rpc("rpc_translator_set_billing_day", {
        "p_guild_id": str(guild_id),
        "p_day": int(day),
    })
    return rows[0] if rows else {}
