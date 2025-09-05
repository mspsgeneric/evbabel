# evtranslator/relay/attachments.py
from __future__ import annotations
import re
from urllib.parse import urlparse
import discord

# Extensões consideradas "mídia" (preview do Discord)
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
_VID_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".m4v")

def _is_image(att: discord.Attachment) -> bool:
    if att.content_type and att.content_type.startswith("image/"):
        return True
    name = (att.filename or "").lower()
    return name.endswith(_IMG_EXTS)

def _is_video(att: discord.Attachment) -> bool:
    if att.content_type and att.content_type.startswith("video/"):
        return True
    name = (att.filename or "").lower()
    return name.endswith(_VID_EXTS)

def split_attachment_urls(atts: list[discord.Attachment]) -> tuple[list[str], list[str]]:
    """
    Retorna (media_urls, other_urls):
      - media_urls: imagens/vídeos sem spoiler (URL pura para preview grande)
      - other_urls: anexos não-mídia ou com spoiler (spoiler fica envolto em || ||)
    """
    media_urls: list[str] = []
    other_urls: list[str] = []
    for a in atts or []:
        url = a.url
        if a.is_spoiler():
            other_urls.append(f"||{url}||")
            continue
        if _is_image(a) or _is_video(a):
            media_urls.append(url)
        else:
            other_urls.append(url)
    return media_urls, other_urls

# ====== Sanitização de caracteres invisíveis (ZWSP/BOM etc.) ======
ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"
_ZW_TABLE = {ord(c): None for c in ZERO_WIDTH}

def _strip_zw(s: str) -> str:
    return (s or "").translate(_ZW_TABLE)

# Regex de URL em texto:
# - NÃO permite espaços nem os invisíveis (zero-width) dentro da URL
_URL_RE = re.compile(r'(https?://[^\s\u200b\u200c\u200d\u2060\ufeff]+)', re.IGNORECASE)

# --------- Helpers de normalização ---------

def _unproxy_cdn_url(u: str) -> str:
    """
    Desfaz URLs proxied (ex.: i#.wp.com/...) para a origem real quando possível.
    Mantém outras URLs como estão.
    """
    m = re.match(r'https?://i\d+\.wp\.com/([^?\s]+)', u, re.IGNORECASE)
    if not m:
        return u
    target = m.group(1)
    if target.startswith(('http://', 'https://')):
        return target
    return 'https://' + target

# Imgur → direto
_IMGUR_HOSTS = {"imgur.com", "www.imgur.com", "m.imgur.com", "i.imgur.com"}
_IMGUR_ID_RE = re.compile(r"^/([A-Za-z0-9]{5,8})(?:\..+)?$")          # /abc123  ou /abc123.jpg
_IMGUR_ALBUM_RE = re.compile(r"^/(?:gallery|a)/([^/?#]+)")            # /gallery/slug  ou /a/slug

def _imgur_to_direct(url: str) -> str:
    """Converte páginas do Imgur em asset direto do CDN quando possível."""
    try:
        p = urlparse(url)
    except Exception:
        return url
    host = (p.netloc or "").lower()
    if host == "i.imgur.com":
        return url  # já é direto
    if host not in _IMGUR_HOSTS:
        return url

    # /gallery/<slug>#<ID>  ou  /a/<album>#<ID>  → usar fragmento como media id
    if _IMGUR_ALBUM_RE.match(p.path or "") and p.fragment:
        media_id = p.fragment.split("/")[0]
        if re.fullmatch(r"[A-Za-z0-9]{5,8}", media_id or ""):
            return f"https://i.imgur.com/{media_id}.mp4"

    # /<ID> simples (post único)
    m = _IMGUR_ID_RE.match(p.path or "")
    if m:
        media_id = m.group(1)
        return f"https://i.imgur.com/{media_id}.mp4"

    return url

def _unwrap_spoiler(u: str) -> tuple[str, bool]:
    if u.startswith("||") and u.endswith("||"):
        return u[2:-2], True
    return u, False

def _rewrite_any_url(u: str) -> str:
    # ordem: unproxy → imgur direto
    u = _unproxy_cdn_url(u)
    u = _imgur_to_direct(u)
    return u

# --------- Reescritas públicas ---------

def rewrite_proxied_image_urls_in_text(text: str) -> str:
    """
    Em texto, reescreve URLs quando possível (unproxy + imgur direto).
    Mantém URLs que não se encaixam nas regras.
    """
    if not text:
        return text
    t = _strip_zw(text)

    def _repl(m: re.Match) -> str:
        url = m.group(1)
        url = _strip_zw(url)
        return _rewrite_any_url(url)

    return _URL_RE.sub(_repl, t)

def rewrite_links(urls: list[str]) -> list[str]:
    """
    Aplica normalizações em listas de URLs (anexos ou links puros):
      - preserva ||spoiler||
      - remove invisíveis
      - unproxy i#.wp.com
      - imgur.com → i.imgur.com/<id>.mp4 quando possível
    """
    out: list[str] = []
    for raw in urls or []:
        core, is_spoiler = _unwrap_spoiler(_strip_zw(raw))
        core = _rewrite_any_url(core)
        out.append(f"||{core}||" if is_spoiler else core)
    return out

def extract_urls(text: str) -> tuple[str, list[str]]:
    """
    Remove URLs do texto, retornando (texto_sem_urls, lista_de_urls_encontradas).
    Preserva ordem; limpa caracteres invisíveis.
    """
    if not text:
        return "", []
    t = _strip_zw(text)
    urls = [m.group(1) for m in _URL_RE.finditer(t)]
    stripped = _URL_RE.sub("", t).strip()
    return stripped, urls
