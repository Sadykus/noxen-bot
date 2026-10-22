"""Хранилище пользователей, устройств и фраз (SQLite).

Файл noxen.db лежит в DATA_DIR. На Railway подключите Volume и задайте DATA_DIR=/data,
иначе база сотрётся при каждом редеплое.
"""
import datetime as dt
import os
import secrets
import sqlite3
import threading
from zoneinfo import ZoneInfo

DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(DATA_DIR, "noxen.db")
TZ = ZoneInfo("Asia/Almaty")
_lock = threading.Lock()

DEFAULT_QUOTES = [
    ("Если не знаешь, к какой гавани плывёшь, ни один ветер не будет попутным.", "Сенека, «Письма к Луцилию», LXXI"),
    ("Пока мы откладываем жизнь, она проходит.", "Сенека, «Письма к Луцилию», I"),
    ("Любишь жизнь? Тогда не трать время впустую — из него она и состоит.", "Бенджамин Франклин"),
    ("Хорошо сделанное лучше хорошо сказанного.", "Бенджамин Франклин"),
    ("У кого есть «зачем», тот выдержит почти любое «как».", "Фридрих Ницше"),
    ("Главное — не лгите самому себе.", "Ф. М. Достоевский, «Братья Карамазовы»"),
    ("Путь в тысячу ли начинается с первого шага.", "Лао-цзы"),
    ("То, что мешает делу, становится делом. Препятствие на пути становится путём.", "Марк Аврелий, «Размышления», V.20"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id      INTEGER PRIMARY KEY,
    name       TEXT,
    city       TEXT    DEFAULT 'Астана',
    lat        REAL    DEFAULT 51.17,
    lon        REAL    DEFAULT 71.45,
    instagram  TEXT    DEFAULT '',
    quote_mode TEXT    DEFAULT 'fixed',
    quote_pos  INTEGER DEFAULT 0,
    created    TEXT
);
CREATE TABLE IF NOT EXISTS quotes (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id  INTEGER NOT NULL,
    text   TEXT    NOT NULL,
    author TEXT    DEFAULT ''
);
CREATE TABLE IF NOT EXISTS devices (
    token     TEXT PRIMARY KEY,
    tg_id     INTEGER NOT NULL,
    name      TEXT,
    created   TEXT,
    last_seen TEXT
);
"""


def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.executescript(SCHEMA)


def _now():
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


# ── пользователи ─────────────────────────────────────────────────
def get_or_create_user(tg_id, name):
    """Возвращает (user, created). Новому пользователю — стартовые фразы и первое устройство."""
    with _lock, _conn() as c:
        u = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if u:
            return dict(u), False
        c.execute("INSERT INTO users (tg_id, name, created) VALUES (?,?,?)", (tg_id, name, _now()))
        c.executemany("INSERT INTO quotes (tg_id, text, author) VALUES (?,?,?)",
                      [(tg_id, t, a) for t, a in DEFAULT_QUOTES])
        c.execute("INSERT INTO devices (token, tg_id, name, created) VALUES (?,?,?,?)",
                  (secrets.token_urlsafe(9), tg_id, "Ридер 1", _now()))
        return dict(c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()), True


def get_user(tg_id):
    with _lock, _conn() as c:
        u = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return dict(u) if u else None


def update_user(tg_id, **fields):
    allowed = {"city", "lat", "lon", "instagram", "quote_mode", "quote_pos", "name"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with _lock, _conn() as c:
        c.execute(f"UPDATE users SET {sets} WHERE tg_id=?", (*fields.values(), tg_id))


def count_users():
    with _lock, _conn() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# ── фразы ────────────────────────────────────────────────────────
def list_quotes(tg_id):
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM quotes WHERE tg_id=? ORDER BY id", (tg_id,))]


def add_quote(tg_id, text, author):
    with _lock, _conn() as c:
        c.execute("INSERT INTO quotes (tg_id, text, author) VALUES (?,?,?)", (tg_id, text, author))
        return c.execute("SELECT COUNT(*) FROM quotes WHERE tg_id=?", (tg_id,)).fetchone()[0]


def delete_quote(tg_id, pos):
    qs = list_quotes(tg_id)
    if not (0 <= pos < len(qs)) or len(qs) <= 1:
        return False
    with _lock, _conn() as c:
        c.execute("DELETE FROM quotes WHERE id=?", (qs[pos]["id"],))
    u = get_user(tg_id)
    if u["quote_pos"] >= len(qs) - 1:
        update_user(tg_id, quote_pos=0)
    return True


def current_quote(user):
    qs = list_quotes(user["tg_id"])
    if not qs:
        return "", ""
    if user["quote_mode"] == "daily":
        idx = dt.datetime.now(TZ).date().toordinal() % len(qs)
    else:
        idx = min(user["quote_pos"], len(qs) - 1)
    return qs[idx]["text"], qs[idx]["author"]


def parse_new(raw):
    """«Текст — Автор» → (текст, автор). Автор необязателен."""
    for sep in (" — ", " - ", " – "):
        if sep in raw:
            text, author = raw.rsplit(sep, 1)
            return text.strip(), author.strip()
    return raw.strip(), ""


# ── устройства ───────────────────────────────────────────────────
def list_devices(tg_id):
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM devices WHERE tg_id=? ORDER BY created", (tg_id,))]


def add_device(tg_id):
    n = len(list_devices(tg_id)) + 1
    token = secrets.token_urlsafe(9)
    with _lock, _conn() as c:
        c.execute("INSERT INTO devices (token, tg_id, name, created) VALUES (?,?,?,?)",
                  (token, tg_id, f"Ридер {n}", _now()))
    return token


def user_by_token(token):
    """Находит владельца устройства и отмечает время последнего обращения."""
    with _lock, _conn() as c:
        d = c.execute("SELECT tg_id FROM devices WHERE token=?", (token,)).fetchone()
        if not d:
            return None
        c.execute("UPDATE devices SET last_seen=? WHERE token=?", (_now(), token))
        return dict(c.execute("SELECT * FROM users WHERE tg_id=?", (d["tg_id"],)).fetchone())
