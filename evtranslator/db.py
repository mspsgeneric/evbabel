# evtranslator/db.py
from __future__ import annotations
import aiosqlite
from typing import Optional, Tuple, List, Any

# ============== utilidades internas ==============

async def _table_has_column(db: aiosqlite.Connection, table: str, column: str) -> bool:
    cur = await db.execute(f"PRAGMA table_info({table})")
    cols = await cur.fetchall()
    return any((c[1] == column) for c in cols)  # c[1] = name

async def _ensure_created_by_column(db: aiosqlite.Connection) -> None:
    """Garante que links.created_by exista. Idempotente."""
    has = await _table_has_column(db, "links", "created_by")
    if not has:
        # SQLite não tem ALTER TABLE IF NOT EXISTS; então só fazemos se faltar.
        await db.execute("ALTER TABLE links ADD COLUMN created_by BIGINT")
        # valor default para registros antigos
        await db.execute("UPDATE links SET created_by = 0 WHERE created_by IS NULL")

# ============== boot ==============

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

        # ✅ garante a coluna created_by (se ainda não existir)
        await _ensure_created_by_column(db)

        await db.commit()

# ============== API básica (retrocompatível) ==============

async def link_pair(db_path: str, guild_id: int, ch_pt: int, ch_en: int):
    """Cria par pt<->en (sem owner). Mantida por retrocompatibilidade."""
    async with aiosqlite.connect(db_path) as db:
        await _ensure_created_by_column(db)

        # Remove qualquer relacionamento existente envolvendo esses canais
        await db.execute(
            "DELETE FROM links WHERE guild_id=? AND (ch_a IN (?,?) OR ch_b IN (?,?))",
            (guild_id, ch_pt, ch_en, ch_pt, ch_en),
        )
        # Insere os dois sentidos, sem owner explícito (vai como 0)
        await db.execute(
            "INSERT OR REPLACE INTO links (guild_id, ch_a, lang_a, ch_b, lang_b, created_by) "
            "VALUES (?, ?, 'pt', ?, 'en', COALESCE(created_by, 0))",
            (guild_id, ch_pt, ch_en),
        )
        await db.execute(
            "INSERT OR REPLACE INTO links (guild_id, ch_a, lang_a, ch_b, lang_b, created_by) "
            "VALUES (?, ?, 'en', ?, 'pt', COALESCE(created_by, 0))",
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
    """Para o canal ch_id (lado A), retorna (target_id, src_lang, tgt_lang)."""
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
    """Lista pares únicos no formato (ch_a, lang_a, ch_b, lang_b)."""
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

# ============== API com proprietário (novo) ==============

async def link_pair_with_owner(db_path: str, guild_id: int, ch_pt: int, ch_en: int, created_by: int):
    """
    Cria par pt<->en registrando o criador (created_by).
    Se a coluna não existir por algum motivo, cai no link_pair antigo.
    """
    async with aiosqlite.connect(db_path) as db:
        try:
            await _ensure_created_by_column(db)
        except Exception:
            # fallback extremo: usa API antiga
            await link_pair(db_path, guild_id, ch_pt, ch_en)
            return

        # Remove qualquer relacionamento existente envolvendo esses canais
        await db.execute(
            "DELETE FROM links WHERE guild_id=? AND (ch_a IN (?,?) OR ch_b IN (?,?))",
            (guild_id, ch_pt, ch_en, ch_pt, ch_en),
        )

        # Insere os dois sentidos com o mesmo owner
        await db.execute(
            "INSERT OR REPLACE INTO links (guild_id, ch_a, lang_a, ch_b, lang_b, created_by) "
            "VALUES (?, ?, 'pt', ?, 'en', ?)",
            (guild_id, ch_pt, ch_en, created_by),
        )
        await db.execute(
            "INSERT OR REPLACE INTO links (guild_id, ch_a, lang_a, ch_b, lang_b, created_by) "
            "VALUES (?, ?, 'en', ?, 'pt', ?)",
            (guild_id, ch_en, ch_pt, created_by),
        )
        await db.commit()

async def get_link_owner(db_path: str, guild_id: int, ch_a: int) -> Optional[int]:
    """
    Retorna o created_by do registro onde ch_a = canal de ORIGEM.
    Se a coluna não existir ou estiver vazia, retorna None.
    """
    async with aiosqlite.connect(db_path) as db:
        # se tabela não tem a coluna, None
        if not await _table_has_column(db, "links", "created_by"):
            return None
        cur = await db.execute(
            "SELECT created_by FROM links WHERE guild_id=? AND ch_a=?",
            (guild_id, ch_a),
        )
        row = await cur.fetchone()
        if not row:
            return None
        owner = row[0]
        if owner is None:
            return None
        try:
            return int(owner)
        except (TypeError, ValueError):
            return None

async def list_links_with_owner(db_path: str, guild_id: int) -> List[Tuple[int, str, int, str, Optional[int]]]:
    """
    Lista pares únicos com owner: (ch_a, lang_a, ch_b, lang_b, created_by).
    Tenta escolher um owner não-nulo dentre os dois sentidos; se ambos nulos, devolve None.
    """
    async with aiosqlite.connect(db_path) as db:
        has_col = await _table_has_column(db, "links", "created_by")
        # inclui created_by somente se existir
        if has_col:
            cur = await db.execute(
                "SELECT ch_a, lang_a, ch_b, lang_b, created_by FROM links WHERE guild_id=?",
                (guild_id,),
            )
            rows = await cur.fetchall()
            seen = set()
            out: List[Tuple[int, str, int, str, Optional[int]]] = []
            # para deduplicar, guardamos o par ordenado e retemos algum owner não nulo
            for a, la, b, lb, owner in rows:
                key = tuple(sorted([int(a), int(b)]))
                if key in seen:
                    # se já vimos, mas o owner anterior era None e agora temos um válido, podemos atualizar em memória;
                    # porém, como estamos só retornando a lista, manteremos o primeiro visto (suficiente para UI).
                    continue
                seen.add(key)
                out.append((int(a), str(la), int(b), str(lb), int(owner) if owner is not None else None))
            return out
        else:
            # fallback: sem coluna, retorna sem owner
            cur = await db.execute(
                "SELECT ch_a, lang_a, ch_b, lang_b FROM links WHERE guild_id=?",
                (guild_id,),
            )
            rows = await cur.fetchall()
            seen = set()
            out: List[Tuple[int, str, int, str, Optional[int]]] = []
            for a, la, b, lb in rows:
                key = tuple(sorted([int(a), int(b)]))
                if key in seen:
                    continue
                seen.add(key)
                out.append((int(a), str(la), int(b), str(lb), None))
            return out
