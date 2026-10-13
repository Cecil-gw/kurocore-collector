import hashlib
import json
import threading
import sqlite3
from pathlib import Path


_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT UNIQUE NOT NULL,
    searched_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS illusts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kurocore_id TEXT UNIQUE NOT NULL,
    pixiv_id TEXT,
    title TEXT DEFAULT '',
    author_name TEXT DEFAULT '',
    author_pixiv_id TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    page_count INTEGER DEFAULT 1,
    release_date TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    pixiv_url TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    file_hash TEXT UNIQUE,
    status TEXT DEFAULT 'pending',
    downloaded_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def exists_by_id(conn: sqlite3.Connection, kurocore_id: str) -> bool:
    with _lock:
        row = conn.execute(
            "SELECT 1 FROM illusts WHERE kurocore_id = ? AND status = 'completed'",
            (kurocore_id,),
        ).fetchone()
        return row is not None


def exists_by_hash(conn: sqlite3.Connection, file_hash: str) -> bool:
    with _lock:
        row = conn.execute(
            "SELECT 1 FROM illusts WHERE file_hash = ?",
            (file_hash,),
        ).fetchone()
        return row is not None


def upsert(conn: sqlite3.Connection, detail) -> None:
    with _lock:
        conn.execute(
        """INSERT INTO illusts
           (kurocore_id, pixiv_id, title, author_name, author_pixiv_id,
            tags, page_count, release_date, source_url, pixiv_url, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
           ON CONFLICT(kurocore_id) DO NOTHING""",
        (
            detail.illust_id,
            detail.author_pixiv_id,
            detail.title,
            detail.author_name,
            detail.author_pixiv_id,
            json.dumps(detail.tags, ensure_ascii=False),
            len(detail.image_urls),
            detail.release_date,
            detail.kurocore_url,
            detail.pixiv_url,
        ),
    )
        conn.commit()


def mark_completed(conn, kurocore_id: str, file_path: str, file_hash: str) -> None:
    with _lock:
        conn.execute(
            """UPDATE illusts
               SET status = 'completed', file_path = ?, file_hash = ?,
                   downloaded_at = datetime('now')
               WHERE kurocore_id = ?""",
            (file_path, file_hash, kurocore_id),
        )
        conn.commit()


def mark_failed(conn, kurocore_id: str) -> None:
    with _lock:
        conn.execute(
            "UPDATE illusts SET status = 'failed' WHERE kurocore_id = ?",
            (kurocore_id,),
        )
        conn.commit()


def get_pending(conn) -> list:
    rows = conn.execute(
        "SELECT * FROM illusts WHERE status = 'pending' ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def get_stats(conn) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM illusts").fetchone()[0]
    completed = conn.execute(
        "SELECT COUNT(*) FROM illusts WHERE status = 'completed'"
    ).fetchone()[0]
    failed = conn.execute(
        "SELECT COUNT(*) FROM illusts WHERE status = 'failed'"
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM illusts WHERE status = 'pending'"
    ).fetchone()[0]
    return {"total": total, "completed": completed, "failed": failed, "pending": pending}


def save_search(conn, query: str) -> None:
    with _lock:
        conn.execute(
            "INSERT INTO search_history (query) VALUES (?) ON CONFLICT(query) DO UPDATE SET searched_at = datetime('now')",
            (query,),
        )
        conn.commit()


def get_history(conn, limit: int = 20) -> list[str]:
    with _lock:
        rows = conn.execute(
            "SELECT query FROM search_history ORDER BY searched_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [r["query"] for r in rows]
