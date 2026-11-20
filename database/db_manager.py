import os
import sqlite3

DB_DIR = "database"
DB_PATH = os.path.join(DB_DIR, "gisbei_memory.db")


def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_outlooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_str TEXT UNIQUE,
            day_name TEXT,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
            title TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("PRAGMA table_info(processed_news)")
    existing_cols = [row[1] for row in cursor.fetchall()]
    if "title" not in existing_cols:
        cursor.execute("ALTER TABLE processed_news ADD COLUMN title TEXT")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS education_ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_key TEXT,
            title TEXT,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def save_daily_outlook(date_str: str, day_name: str, content: str):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO daily_outlooks (date_str, day_name, content)
        VALUES (?, ?, ?)
        ON CONFLICT(date_str) DO UPDATE SET
            day_name = excluded.day_name,
            content = excluded.content,
            created_at = CURRENT_TIMESTAMP
    """, (date_str, day_name, content))
    conn.commit()
    conn.close()


def get_latest_outlook() -> tuple | None:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT date_str, day_name, content FROM daily_outlooks ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return row


def get_weekly_outlooks(limit: int = 5) -> list:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT date_str, day_name, content FROM daily_outlooks ORDER BY date_str DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return list(reversed(rows))


def is_news_processed(url: str) -> bool:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed_news WHERE url = ?", (url,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def mark_news_processed(url: str, title: str):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO processed_news (url, title) VALUES (?, ?)", (url, title))
    conn.commit()
    conn.close()


def get_past_education_ideas(limit: int = 25) -> list[str]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM education_ideas ORDER BY id DESC LIMIT ?", (limit,))
    rows = [r[0] for r in cursor.fetchall() if r[0]]
    conn.close()
    return rows


def save_education_idea(topic_key: str, title: str, content: str):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO education_ideas (topic_key, title, content) VALUES (?, ?, ?)",
        (topic_key, title, content)
    )
    conn.commit()
    conn.close()