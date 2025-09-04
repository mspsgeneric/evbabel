# evtranslator/relay/send.py
from __future__ import annotations
import re
import discord
from evtranslator.config import TRANSLATED_FLAG, MAX_MSG_LEN
from evtranslator.relay.attachments import (
    split_attachment_urls,
    rewrite_proxied_image_urls_in_text,
    rewrite_links,
)

# detecta se o texto é apenas o(s) nome(s) de arquivo de mídia (ex.: "VID-...mp4")
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

async def send_translation(
    bot, src_msg: discord.Message, target_ch: discord.TextChannel,
    translated_text: str | None, is_proxy_msg: bool
):
    # 1) Texto traduzido + normalização de URLs de mídia “proxied”
    base_text = (translated_text or "").strip()
    if base_text:
        base_text = rewrite_proxied_image_urls_in_text(base_text)

    # 2) Anexos → mídia (img+vídeo) e outros
    media_urls, other_urls = split_attachment_urls(src_msg.attachments or [])
    media_urls = rewrite_links(media_urls)
    other_urls = rewrite_links(other_urls)

    # 2.1) Se houver anexos, não ecoar "VID-....mp4" etc. como texto
    base_text = strip_filename_only_text(base_text, src_msg.attachments or [])

    # 3) Monta fila de mensagens
    msgs: list[str] = []

    if base_text:
        msgs.append(base_text)

    if media_urls:
        msgs.extend(_split_by_limit(media_urls))  # URLs puras → preview grande

    if other_urls:
        lines = ["**Anexos:**"] + [f"• {u}" for u in other_urls]
        msgs.extend(_split_by_limit(lines))

    if not msgs:
        return  # nada para enviar

    # 4) Garante o flag invisível NA ÚLTIMA mensagem (sem duplicar e sem estourar limite)
    if len(msgs[-1]) + len(TRANSLATED_FLAG) <= MAX_MSG_LEN:
        msgs[-1] = msgs[-1] + TRANSLATED_FLAG
    else:
        msgs.append(TRANSLATED_FLAG)

    # 5) Envia na ordem
    for body in msgs:
        await _send(bot, src_msg, target_ch, body, is_proxy_msg)

async def _send(bot, src_msg: discord.Message, target_ch: discord.TextChannel, content: str, is_proxy_msg: bool):
    try:
        if is_proxy_msg:
            username = src_msg.author.name or src_msg.author.display_name
            avatar_url = str(src_msg.author.display_avatar.url) if src_msg.author.display_avatar else None
            await bot.webhooks.send_as_identity(
                target_ch, username, avatar_url, content,
                allowed_mentions=discord.AllowedMentions.none()
            )
        else:
            await bot.webhooks.send_as_member(
                target_ch, src_msg.author, content,
                allowed_mentions=discord.AllowedMentions.none()
            )
    except TypeError:
        # fallback sem webhook
        await target_ch.send(content=content, allowed_mentions=discord.AllowedMentions.none())
