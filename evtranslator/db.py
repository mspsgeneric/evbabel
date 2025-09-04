# evtranslator/db.py
from __future__ import annotations
import aiosqlite
from typing import Optional, Tuple, List

async def init_db(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        # 🔧 PRAGMAs de desempenho/concorrência (idempotentes)
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS links (
                guild_id INTEGER NOT NULL,
                ch_a     INTEGER NOT NULL,
                lang_a   TEXT    NOT NULL CHECK (lang_a IN ('pt','en')),
                ch_b     INTEGER NOT NULL,
                lang_b   TEXT    NOT NULL CHECK (lang_b IN ('pt','en')),
                PRIMARY KEY (guild_id, ch_a, ch_b)
            );
            """
        )

        # 📇 Índices para consultas mais rápidas (idempotentes)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_links_guild ON links (guild_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_links_guild_cha ON links (guild_id, ch_a);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_links_guild_chb ON links (guild_id, ch_b);")

        await db.commit()

async def link_pair(db_path: str, guild_id: int, ch_pt: int, ch_en: int):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM links WHERE guild_id=? AND (ch_a IN (?,?) OR ch_b IN (?,?))",
            (guild_id, ch_pt, ch_en, ch_pt, ch_en),
        )
        await db.execute(
            "INSERT OR REPLACE INTO links (guild_id, ch_a, lang_a, ch_b, lang_b) VALUES (?, ?, 'pt', ?, 'en')",
            (guild_id, ch_pt, ch_en),
        )
        await db.execute(
            "INSERT OR REPLACE INTO links (guild_id, ch_a, lang_a, ch_b, lang_b) VALUES (?, ?, 'en', ?, 'pt')",
            (guild_id, ch_en, ch_pt),
        )
        await db.commit()

async def unlink_pair(db_path: str, guild_id: int, ch1: int, ch2: int):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM links WHERE guild_id=? AND ((ch_a=? AND ch_b=?) OR (ch_a=? AND ch_b=?))",
            (guild_id, ch1, ch2, ch2, ch1),
        )
        await db.commit()

async def unlink_all(db_path: str, guild_id: int):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM links WHERE guild_id=?", (guild_id,))
        await db.commit()

async def get_link_info(db_path: str, guild_id: int, ch_id: int) -> Optional[Tuple[int, str, str]]:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT lang_a, ch_b, lang_b FROM links WHERE guild_id=? AND ch_a=?",
            (guild_id, ch_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        src_lang, ch_b, tgt_lang = row
        return (int(ch_b), str(src_lang), str(tgt_lang))

async def list_links(db_path: str, guild_id: int) -> List[Tuple[int, str, int, str]]:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT ch_a, lang_a, ch_b, lang_b FROM links WHERE guild_id=?",
            (guild_id,),
        )
        rows = await cur.fetchall()
        seen = set()
        out: List[Tuple[int, str, int, str]] = []
        for a, la, b, lb in rows:
            key = tuple(sorted([int(a), int(b)]))
            if key in seen:
                continue
            seen.add(key)
            out.append((int(a), str(la), int(b), str(lb)))
        return out

# ✅ Helper opcional: remove qualquer link que envolva um canal (src OU dst)
async def unlink_any_for_channel(db_path: str, guild_id: int, channel_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM links WHERE guild_id=? AND (ch_a=? OR ch_b=?)",
            (guild_id, channel_id, channel_id),
        )
        await db.commit()
        return cur.rowcount or 0
