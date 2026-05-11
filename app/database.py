import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.getenv("DATA_DIR", "/app/data")) / "mtv.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS videos (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                filename        TEXT    NOT NULL UNIQUE,
                artist          TEXT    NOT NULL,
                title           TEXT    NOT NULL,
                year            INTEGER,
                genre           TEXT,
                lastfm_art_url  TEXT,
                lastfm_bio      TEXT,
                rating          INTEGER NOT NULL DEFAULT 0,
                play_count      INTEGER NOT NULL DEFAULT 0,
                enriched_at     TIMESTAMP,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS play_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id    INTEGER NOT NULL REFERENCES videos(id),
                played_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_videos_artist ON videos(artist);
        """)
        # Migration: add enriched_at to pre-existing databases
        try:
            conn.execute("ALTER TABLE videos ADD COLUMN enriched_at TIMESTAMP")
        except Exception:
            pass  # column already exists


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
