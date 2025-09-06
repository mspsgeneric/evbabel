# evtranslator/relay/send.py
from __future__ import annotations
import os
import re
from urllib.parse import urlparse
import discord

from evtranslator.config import TRANSLATED_FLAG, MAX_MSG_LEN
from evtranslator.relay.attachments import (
    split_attachment_urls,
    rewrite_proxied_image_urls_in_text,
    rewrite_links,
)

# ==========================
# Sanitização de invisíveis
# ==========================
ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"
_ZW_TABLE = {ord(c): None for c in ZERO_WIDTH}

def _strip_zw(s: str) -> str:
    return (s or "").translate(_ZW_TABLE)

# ==========================
# Heurísticas de mídia/nomes
# ==========================
_MEDIA_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".webm", ".mkv", ".m4v")
_FILENAME_ONLY_RE = re.compile(r'^[\w\-\s\.\(\)\[\]]+\.[A-Za-z0-9]{2,4}$')

def strip_filename_only_text(text: str, attachments: list[discord.Attachment]) -> str:
    if not text or not attachments:
        return text
    t = text.strip()
    filenames = {(a.filename or "").strip() for a in attachments if a and a.filename}
    if t in filenames:
        return ""
    low = t.lower()
    if _FILENAME_ONLY_RE.match(t) and any(low.endswith(ext) for ext in _MEDIA_EXTS):
        return ""
    parts = [p.strip() for p in t.splitlines() if p.strip()]
    if parts and all(
        (_FILENAME_ONLY_RE.match(p) and any(p.lower().endswith(ext) for ext in _MEDIA_EXTS))
        for p in parts
    ):
        return ""
    return text

def _split_by_limit(lines: list[str]) -> list[str]:
    """Quebra uma lista de linhas em blocos <= MAX_MSG_LEN, preservando quebras."""
    msgs, cur = [], ""
    for line in lines:
        add = (("\n" if cur else "") + line) if line else "\n"
        if len(cur) + len(add) <= MAX_MSG_LEN:
            cur += add
        else:
            if cur:
                msgs.append(cur)
            # se a linha sozinha excede o limite, corta (situação rara p/ URLs)
            cur = line[:MAX_MSG_LEN] if len(line) > MAX_MSG_LEN else line
    if cur:
        msgs.append(cur)
    return msgs

# ==========================
# Helpers de URL
# ==========================
_URL_ONLY_RE = re.compile(r'\s*https?://[^\s\u200b\u200c\u200d\u2060\ufeff]+\s*$')

def _is_pure_url_block(s: str) -> bool:
    return bool(_URL_ONLY_RE.fullmatch(s or ""))

def _one_url(text: str) -> str | None:
    m = re.search(r'https?://[^\s\u200b\u200c\u200d\u2060\ufeff]+', text or "")
    return _strip_zw(m.group(0)) if m else None

def _domain(url: str) -> str:
    try:
        u = _strip_zw(url)
        host = urlparse(u).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""

# ==========================
# Suporte a Imgur (#anchor)
# ==========================
_DEF_DIRECT_RESOLVE_DOMAINS = {"imgur.com"}
_ENV = os.getenv("EV_URL_DIRECT_EMBED_DOMAINS", "")
_DIRECT_EMBED_DOMAINS = (
    {d.strip().lower() for d in _ENV.split(",") if d.strip()} if _ENV.strip() else _DEF_DIRECT_RESOLVE_DOMAINS
)

def _imgur_anchor_id(url: str) -> str | None:
    """
    Para URLs tipo:
      https://imgur.com/gallery/<slug-ou-id>#<imageId>
      https://imgur.com/a/<album>#<imageId>
    retorna <imageId> (alfa-num).
    """
    try:
        frag = _strip_zw(urlparse(_strip_zw(url)).fragment)
        if frag and re.fullmatch(r'[A-Za-z0-9]+', frag):
            return frag
    except Exception:
        pass
    return None

async def _probe_direct_url(session, url: str) -> bool:
    """
    Verifica rapidamente se a URL existe e é imagem/vídeo leve.
    Tenta HEAD; se 405/403, tenta GET com Range pequeno.
    """
    try:
        async with session.head(url, allow_redirects=True) as r:
            if r.status == 200:
                ctype = (r.headers.get("Content-Type") or "").lower()
                return ctype.startswith("image/") or ctype.startswith("video/")
            if r.status in (403, 405):
                headers = {"Range": "bytes=0-0"}
                async with session.get(url, headers=headers, allow_redirects=True) as g:
                    if g.status in (200, 206):
                        ctype = (g.headers.get("Content-Type") or "").lower()
                        return ctype.startswith("image/") or ctype.startswith("video/")
        return False
    except Exception:
        return False

async def _resolve_imgur_direct(session, original_url: str) -> str | None:
    """
    Se for link do Imgur com âncora, tenta resolver para link direto:
      i.imgur.com/<id>.gif|jpg|png|jpeg|mp4
    """
    img_id = _imgur_anchor_id(original_url)
    if not img_id:
        return None
    candidates = [
        f"https://i.imgur.com/{img_id}.gif",
        f"https://i.imgur.com/{img_id}.jpg",
        f"https://i.imgur.com/{img_id}.png",
        f"https://i.imgur.com/{img_id}.jpeg",
        f"https://i.imgur.com/{img_id}.mp4",  # por último
    ]
    for direct in candidates:
        ok = await _probe_direct_url(session, direct)
        if ok:
            return direct
    return None

# ==========================
# Envio via webhook
# ==========================
import logging
log = logging.getLogger(__name__)

async def _send(
    bot, src_msg: discord.Message, target_ch: discord.TextChannel,
    content: str, is_proxy_msg: bool, return_message: bool = False,
    reference: discord.MessageReference | None = None,
    **kwargs
):
    try:
        if reference is None:
            # fluxo normal via webhook (sem reply)
            if is_proxy_msg:
                username = src_msg.author.name or src_msg.author.display_name
                avatar_url = str(src_msg.author.display_avatar.url) if src_msg.author.display_avatar else None
                return await bot.webhooks.send_as_identity(
                    target_ch, username, avatar_url, content,
                    allowed_mentions=discord.AllowedMentions.none(),
                    return_message=return_message,
                    **kwargs,
                )
            else:
                return await bot.webhooks.send_as_member(
                    target_ch, src_msg.author, content,
                    allowed_mentions=discord.AllowedMentions.none(),
                    return_message=return_message,
                    **kwargs,
                )
        else:
            # ✅ Reply "soft inline" com blockquote + inline code + »»
            log.info(
                "reply SOFT-INLINE (blockquote): ref_msg=%s ch=%s proxy=%s",
                getattr(reference, "message_id", None),
                target_ch.id,
                is_proxy_msg,
            )

            ref_author = ""
            excerpt = ""
            try:
                ref_msg = await target_ch.fetch_message(int(reference.message_id))
                ref_author = (getattr(ref_msg.author, "name", "") or getattr(ref_msg.author, "display_name", "") or "").strip()
                raw = (ref_msg.content or "").strip()
                first_line = raw.splitlines()[0] if raw else ""
                excerpt = (first_line[:80] + "…") if len(first_line) > 80 else first_line
            except Exception:
                pass

            # jump link
            jump = None
            try:
                guild_id = target_ch.guild.id if target_ch.guild else 0
                jump = f"https://discord.com/channels/{guild_id}/{target_ch.id}/{int(reference.message_id)}"
            except Exception:
                pass

            # --- sanitize ---
            def _md_sanitize(s: str) -> str:
                if not s:
                    return ""
                s = s.replace("`", "").replace("[", "(").replace("]", ")") \
                     .replace("*", "").replace("_", "")
                return " ".join(s.split())

            safe_author = _md_sanitize(ref_author) or "mensagem"
            safe_excerpt = _md_sanitize(excerpt) or "…"

            # blockquote + inline code com chevron duplo »»
            if jump:
                header = f"> »» [`{safe_author}: {safe_excerpt}`]({jump})"
            else:
                header = f"> »» `{safe_author}: {safe_excerpt}`"

            soft_content = f"{header}\n{content}".strip()

            # envio via webhook spoofando autor
            if is_proxy_msg:
                username = src_msg.author.name or src_msg.author.display_name
                avatar_url = str(src_msg.author.display_avatar.url) if src_msg.author.display_avatar else None
                return await bot.webhooks.send_as_identity(
                    target_ch, username, avatar_url, soft_content,
                    allowed_mentions=discord.AllowedMentions.none(),
                    return_message=return_message,
                    **kwargs,
                )
            else:
                return await bot.webhooks.send_as_member(
                    target_ch, src_msg.author, soft_content,
                    allowed_mentions=discord.AllowedMentions.none(),
                    return_message=return_message,
                    **kwargs,
                )

    except TypeError:
        # fallback extra
        sent = await target_ch.send(
            content=content,
            allowed_mentions=discord.AllowedMentions.none(),
            reference=reference,
            **kwargs,
        )
        if return_message:
            return (sent.id, 0)
        return None













# ==========================
# Função principal
# ==========================
async def send_translation(
    bot, src_msg: discord.Message, target_ch: discord.TextChannel,
    translated_text: str | None, is_proxy_msg: bool,
    reference: discord.MessageReference | None = None,
):
    # Texto base (com URLs do corpo)
    base_text = (translated_text or "").strip()
    if base_text:
        base_text = rewrite_proxied_image_urls_in_text(base_text)

    # URLs de anexos
    media_urls, other_urls = split_attachment_urls(src_msg.attachments or [])
    media_urls = rewrite_links(media_urls)
    other_urls = rewrite_links(other_urls)

    # remove texto que é só nomes de arquivos anexados
    base_text = strip_filename_only_text(base_text, src_msg.attachments or [])

    # Monta blocos
    msgs: list[str] = []
    if base_text:
        msgs.append(base_text)
    if media_urls:
        msgs.extend(_split_by_limit(media_urls))
    if other_urls:
        lines = ["**Anexos:**"] + [f"• {u}" for u in other_urls]
        msgs.extend(_split_by_limit(lines))
    if not msgs:
        return None

    # ===== Caso especial: mensagem é APENAS uma URL =====
    if len(msgs) == 1 and _is_pure_url_block(msgs[0]):
        url = _one_url(msgs[0]) or ""
        dom = _domain(url)

        # só tentamos resolver “direto” para domínios suportados
        if any(dom == d or dom.endswith("." + d) for d in _DIRECT_EMBED_DOMAINS):
            session = getattr(bot, "http_session", None) or getattr(bot.webhooks, "http_session", None)
            direct = None
            if session is not None and dom.endswith("imgur.com"):
                direct = await _resolve_imgur_direct(session, url)

            if direct:
                # mp4: melhor enviar o link direto no conteúdo (player nativo)
                if direct.lower().endswith(".mp4"):
                    
                    return await _send(
                        bot, src_msg, target_ch, direct, is_proxy_msg,
                        return_message=True,
                        reference=reference,  # ✅ novo
                    )
                # imagem: usa embed explícito (mantém identidade do autor)
                emb = discord.Embed(url=url)  # link de referência
                emb.set_image(url=direct)
                return await _send(
                    bot, src_msg, target_ch, "", is_proxy_msg,
                    return_message=True,
                    embeds=emb,
                    reference=reference,  # ✅ novo
                )
        # Se não deu pra resolver direto, seguimos; IMPORTANTE: não aplicar flag em URL pura
        # para não quebrar o preview nativo do Discord.

    # ⚑ FLAG anti-loop: aplique SOMENTE na última mensagem que NÃO seja apenas URL.
    applied = False
    for i in range(len(msgs) - 1, -1, -1):
        if not _is_pure_url_block(msgs[i]):
            if len(msgs[i]) + len(TRANSLATED_FLAG) <= MAX_MSG_LEN:
                msgs[i] = msgs[i] + TRANSLATED_FLAG
            else:
                msgs.append(TRANSLATED_FLAG)
            applied = True
            break
    # Se TODAS forem apenas URL, não aplica flag (pra não matar preview).

    # Envie e capture IDs só do primeiro bloco que seja “conteúdo” (não-URL)
    saved_ids = None
    for i, body in enumerate(msgs):
        want_ids = (i == 0)
        if want_ids:
            ids = await _send(
                bot, src_msg, target_ch, body, is_proxy_msg,
                return_message=True,
                reference=reference,  # ✅ reply só no 1º bloco
            )
        else:
            ids = await _send(
                bot, src_msg, target_ch, body, is_proxy_msg,
                return_message=False,
            )
        if want_ids and ids:
            saved_ids = ids

    return saved_ids

