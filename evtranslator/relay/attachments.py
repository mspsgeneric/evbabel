# evtranslator/relay/attachments.py
from __future__ import annotations
import re
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

# Regex de URL usada em todo o módulo
_URL_RE = re.compile(r'(https?://\S+)', re.IGNORECASE)

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

def rewrite_proxied_image_urls_in_text(text: str) -> str:
    """
    Em texto, troca URLs de mídia proxied pela origem real (sem mudar outras).
    """
    if not text:
        return text
    def _repl(m: re.Match) -> str:
        url = m.group(1)
        base = url.split('?', 1)[0].lower()
        if any(base.endswith(ext) for ext in (*_IMG_EXTS, *_VID_EXTS)):
            return _unproxy_cdn_url(url)
        return url
    return _URL_RE.sub(_repl, text)

def rewrite_links(urls: list[str]) -> list[str]:
    """
    Para listas de URLs (de anexos), aplica _unproxy_cdn_url apenas nas que parecem mídia.
    """
    out: list[str] = []
    for u in urls:
        base = u.split('?', 1)[0].lower()
        if any(base.endswith(ext) for ext in (*_IMG_EXTS, *_VID_EXTS)):
            out.append(_unproxy_cdn_url(u))
        else:
            out.append(u)
    return out

def extract_urls(text: str) -> tuple[str, list[str]]:
    """
    Remove URLs do texto, retornando (texto_sem_urls, lista_de_urls_encontradas).
    Preserva ordem; não altera as URLs capturadas.
    """
    if not text:
        return "", []
    urls = [m.group(1) for m in _URL_RE.finditer(text)]
    stripped = _URL_RE.sub("", text).strip()
    return stripped, urls
