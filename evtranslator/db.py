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



        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS xlate_msgs (
                guild_id     INTEGER NOT NULL,
                src_msg_id   INTEGER NOT NULL,
                src_ch_id    INTEGER NOT NULL,
                tgt_msg_id   INTEGER NOT NULL,
                tgt_ch_id    INTEGER NOT NULL,
                webhook_id   INTEGER NOT NULL,
                created_at   INTEGER NOT NULL,  -- epoch seconds
                last_edit_at INTEGER,
                PRIMARY KEY (guild_id, src_msg_id)
            );
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_xlate_created ON xlate_msgs(created_at);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_xlate_tgt ON xlate_msgs(tgt_msg_id);")


        # === Tokens de webhooks por canal (permitir editar pós-restart) ===
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_tokens (
                guild_id   INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                webhook_id INTEGER NOT NULL PRIMARY KEY,
                token      TEXT    NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_webhook_tokens_channel ON webhook_tokens(channel_id);")


        
        # === Glossário EN->PT (fechado, só painel edita) ===
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS glossario (
                id         INTEGER PRIMARY KEY,
                termo_src  TEXT    NOT NULL,  -- termo origem (ex.: inglês)
                termo_dst  TEXT    NOT NULL,  -- termo destino (ex.: português)
                enabled    INTEGER NOT NULL DEFAULT 1,
                priority   INTEGER NOT NULL DEFAULT 100,  -- maior casa primeiro
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                updated_by BIGINT
            );
            """
        )
        # índices para consultas rápidas e dedupe
        await db.execute("CREATE INDEX IF NOT EXISTS idx_gloss_enabled ON glossario (enabled);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_gloss_priority ON glossario (priority DESC);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_gloss_src_nocase ON glossario (termo_src COLLATE NOCASE);")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_gloss_src_nocase ON glossario(termo_src COLLATE NOCASE);")




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
        
# ============== Mapeamento de mensagens traduzidas ==============

async def record_translation(db_path: str, guild_id: int, src_msg_id: int, src_ch_id: int,
                             tgt_msg_id: int, tgt_ch_id: int, webhook_id: int, created_at: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO xlate_msgs (guild_id, src_msg_id, src_ch_id, tgt_msg_id, tgt_ch_id, webhook_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, src_msg_id, src_ch_id, tgt_msg_id, tgt_ch_id, webhook_id, created_at)
        )
        await db.commit()

async def get_translation_by_src(db_path: str, guild_id: int, src_msg_id: int):
    """Retorna (src_ch_id, tgt_msg_id, tgt_ch_id, webhook_id, created_at) ou None."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT src_ch_id, tgt_msg_id, tgt_ch_id, webhook_id, created_at "
            "FROM xlate_msgs WHERE guild_id=? AND src_msg_id=?",
            (guild_id, src_msg_id)
        )
        row = await cur.fetchone()
        return tuple(map(int, row)) if row else None  # type: ignore[return-value]

async def touch_translation_edit(db_path: str, guild_id: int, src_msg_id: int, ts: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE xlate_msgs SET last_edit_at=? WHERE guild_id=? AND src_msg_id=?",
            (ts, guild_id, src_msg_id)
        )
        await db.commit()

async def purge_xlate_older_than(db_path: str, cutoff_epoch: int) -> int:
    """Apaga vínculos antigos; retorna quantos deletou."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("DELETE FROM xlate_msgs WHERE created_at < ?", (cutoff_epoch,))
        await db.commit()
        return cur.rowcount or 0
    
async def delete_translation_map(db_path: str, guild_id: int, src_msg_id: int) -> int:
    """Remove o vínculo de edição para uma mensagem original. Retorna quantas linhas removeu."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM xlate_msgs WHERE guild_id=? AND src_msg_id=?",
            (guild_id, src_msg_id),
        )
        await db.commit()
        return cur.rowcount or 0
    


# ============== Webhook tokens (persistência) ==============

async def upsert_webhook_token(db_path: str, guild_id: int, channel_id: int, webhook_id: int, token: str, created_at: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        # PRIMARY KEY(webhook_id) → substitui token se recriar o mesmo id (raro), mantém 1 linha por webhook
        await db.execute(
            "INSERT INTO webhook_tokens (guild_id, channel_id, webhook_id, token, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(webhook_id) DO UPDATE SET token=excluded.token, channel_id=excluded.channel_id, guild_id=excluded.guild_id",
            (guild_id, channel_id, webhook_id, token, created_at)
        )
        await db.commit()

async def get_webhook_token_by_id(db_path: str, webhook_id: int) -> Optional[tuple[int, int, str]]:
    """Retorna (guild_id, channel_id, token) ou None."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT guild_id, channel_id, token FROM webhook_tokens WHERE webhook_id=?",
            (webhook_id,)
        )
        row = await cur.fetchone()
        return (int(row[0]), int(row[1]), str(row[2])) if row else None

async def get_webhook_for_channel(db_path: str, channel_id: int) -> Optional[tuple[int, str]]:
    """Retorna (webhook_id, token) recente para um canal, se houver."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT webhook_id, token FROM webhook_tokens WHERE channel_id=? ORDER BY created_at DESC LIMIT 1",
            (channel_id,)
        )
        row = await cur.fetchone()
        return (int(row[0]), str(row[1])) if row else None

async def delete_webhook_token(db_path: str, webhook_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("DELETE FROM webhook_tokens WHERE webhook_id=?", (webhook_id,))
        await db.commit()
        return cur.rowcount or 0



# ============== Glossário (painel fechado) ==============

async def list_glossario(db_path: str, only_enabled: bool = True) -> List[tuple]:
    """
    Retorna linhas ordenadas para exibição no painel:
    (id, termo_src, termo_dst, enabled, priority, updated_at, updated_by)
    """
    async with aiosqlite.connect(db_path) as db:
        if only_enabled:
            cur = await db.execute(
                "SELECT id, termo_src, termo_dst, enabled, priority, updated_at, updated_by "
                "FROM glossario WHERE enabled=1 "
                "ORDER BY priority DESC, LENGTH(termo_src) DESC"
            )
        else:
            cur = await db.execute(
                "SELECT id, termo_src, termo_dst, enabled, priority, updated_at, updated_by "
                "FROM glossario "
                "ORDER BY enabled DESC, priority DESC, LENGTH(termo_src) DESC"
            )
        return await cur.fetchall()

async def get_gloss_rows_for_cache(db_path: str):
    """
    Retorna [(termo_src, termo_dst, enabled, priority)] já ordenados.
    """
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT termo_src, termo_dst, enabled, priority "
            "FROM glossario "
            "WHERE enabled IN (0,1) "
            "ORDER BY priority DESC, LENGTH(termo_src) DESC"
        )
        rows = await cur.fetchall()
        # garante tipos corretos
        return [(str(a), str(b), int(c), int(d)) for (a, b, c, d) in rows]


async def upsert_glossario(db_path: str, termo_src: str, termo_dst: str,
                           enabled: int = 1, priority: int = 100, updated_by: Optional[int] = None) -> int:
    """
    Insere ou atualiza por termo_src (case-insensitive). Retorna id da linha.
    """
    termo_src = termo_src.strip()
    termo_dst = termo_dst.strip()
    async with aiosqlite.connect(db_path) as db:
        # tenta achar existente (NOCASE)
        cur = await db.execute(
            "SELECT id FROM glossario WHERE termo_src = ? COLLATE NOCASE LIMIT 1",
            (termo_src,)
        )
        row = await cur.fetchone()
        if row:
            g_id = int(row[0])
            await db.execute(
                "UPDATE glossario "
                "SET termo_dst=?, enabled=?, priority=?, updated_by=?, updated_at=strftime('%s','now') "
                "WHERE id=?",
                (termo_dst, int(enabled), int(priority), updated_by, g_id)
            )
            await db.commit()
            return g_id
        else:
            cur2 = await db.execute(
                "INSERT INTO glossario (termo_src, termo_dst, enabled, priority, updated_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (termo_src, termo_dst, int(enabled), int(priority), updated_by)
            )
            await db.commit()
            return cur2.lastrowid

async def set_gloss_enabled(db_path: str, gloss_id: int, enabled: bool, updated_by: Optional[int] = None) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "UPDATE glossario SET enabled=?, updated_by=?, updated_at=strftime('%s','now') WHERE id=?",
            (1 if enabled else 0, updated_by, gloss_id)
        )
        await db.commit()
        return cur.rowcount or 0

async def delete_glossario(db_path: str, gloss_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("DELETE FROM glossario WHERE id=?", (gloss_id,))
        await db.commit()
        return cur.rowcount or 0
