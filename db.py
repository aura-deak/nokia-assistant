"""SQLite 数据库访问层（保持简单，无 ORM）。"""

import os
import sqlite3
from flask import g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
NOVEL_DIR = os.path.join(DATA_DIR, "novels")
DB_PATH = os.path.join(DATA_DIR, "nokia.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_settings (
    user_id INTEGER PRIMARY KEY,
    base_url TEXT DEFAULT '',
    api_key TEXT DEFAULT '',
    model TEXT DEFAULT '',
    system_prompt TEXT DEFAULT '',
    temperature TEXT DEFAULT '0.7'
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    guest_key TEXT DEFAULT '',
    title TEXT DEFAULT '新会话',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT DEFAULT '',
    model TEXT DEFAULT '',
    status TEXT DEFAULT '',
    started_at REAL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS novels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    code TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    size INTEGER DEFAULT 0,
    char_count INTEGER DEFAULT 0,
    chapters TEXT DEFAULT '[]',
    views INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reading_progress (
    user_id INTEGER NOT NULL,
    novel_id INTEGER NOT NULL,
    ci INTEGER NOT NULL DEFAULT 0,
    page INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, novel_id)
);
"""


def get_db():
    """每个请求一个连接。"""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA busy_timeout=5000")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(NOVEL_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    _migrate(con)
    con.commit()
    con.close()


def _migrate(con):
    """为旧库补充新增列（游客会话、按消息记录模型）。"""
    cols = [r[1] for r in con.execute("PRAGMA table_info(conversations)")]
    if "guest_key" not in cols:
        con.execute("ALTER TABLE conversations ADD COLUMN guest_key TEXT DEFAULT ''")
    cols = [r[1] for r in con.execute("PRAGMA table_info(messages)")]
    if "model" not in cols:
        con.execute("ALTER TABLE messages ADD COLUMN model TEXT DEFAULT ''")
    if "started_at" not in cols:
        con.execute("ALTER TABLE messages ADD COLUMN started_at REAL DEFAULT 0")
