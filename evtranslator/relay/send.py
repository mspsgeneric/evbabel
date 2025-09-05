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

# evtranslator/relay/send.py

async def send_translation(
    bot, src_msg: discord.Message, target_ch: discord.TextChannel,
    translated_text: str | None, is_proxy_msg: bool
):
    # (1) ... código que já existe até montar msgs ...
    base_text = (translated_text or "").strip()
    if base_text:
        base_text = rewrite_proxied_image_urls_in_text(base_text)

    media_urls, other_urls = split_attachment_urls(src_msg.attachments or [])
    media_urls = rewrite_links(media_urls)
    other_urls = rewrite_links(other_urls)
    base_text = strip_filename_only_text(base_text, src_msg.attachments or [])

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

    # Não atrapalhar embeds: evite colar flag em mensagens que são só URL
    def _is_pure_url_block(s: str) -> bool:
        return bool(re.fullmatch(r'\s*https?://\S+\s*', s or ""))

    # tenta colocar a flag na última mensagem que não seja apenas URL
    applied = False
    for i in range(len(msgs) - 1, -1, -1):
        if not _is_pure_url_block(msgs[i]):
            if len(msgs[i]) + len(TRANSLATED_FLAG) <= MAX_MSG_LEN:
                msgs[i] += TRANSLATED_FLAG
            else:
                msgs.append(TRANSLATED_FLAG)
            applied = True
            break

    # se todas as mensagens são só link, manda a flag como mensagem separada
    if not applied:
        msgs.append(TRANSLATED_FLAG)


    # (2) Envia e CAPTURA os IDs só da primeira mensagem de conteúdo (se existir).
    saved_ids = None
    for i, body in enumerate(msgs):
        want_ids = (i == 0 and bool(base_text))  # captura o primeiro "conteúdo"
        ids = await _send(bot, src_msg, target_ch, body, is_proxy_msg, return_message=want_ids)
        if want_ids and ids:
            saved_ids = ids

    return saved_ids  # (msg_id, webhook_id) ou None


async def _send(
    bot, src_msg: discord.Message, target_ch: discord.TextChannel,
    content: str, is_proxy_msg: bool, return_message: bool = False
):
    try:
        if is_proxy_msg:
            username = src_msg.author.name or src_msg.author.display_name
            avatar_url = str(src_msg.author.display_avatar.url) if src_msg.author.display_avatar else None
            return await bot.webhooks.send_as_identity(
                target_ch, username, avatar_url, content,
                allowed_mentions=discord.AllowedMentions.none(),
                return_message=return_message,
            )
        else:
            return await bot.webhooks.send_as_member(
                target_ch, src_msg.author, content,
                allowed_mentions=discord.AllowedMentions.none(),
                return_message=return_message,
            )
    except TypeError:
        # Fallback sem webhook: não retornamos IDs (não dá para editar depois)
        await target_ch.send(content=content, allowed_mentions=discord.AllowedMentions.none())
        return None

