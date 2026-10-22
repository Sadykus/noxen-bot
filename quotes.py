"""Хранилище фраз для экрана сна.

Файл quotes.json лежит в DATA_DIR (на Railway — подключённый Volume, например /data),
иначе рядом с кодом. Без Volume список сбросится при каждом редеплое.
"""
import datetime as dt
import json
import os
import threading
from zoneinfo import ZoneInfo

DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(DATA_DIR, "quotes.json")
TZ = ZoneInfo("Asia/Almaty")
_lock = threading.Lock()

DEFAULT = {
    "mode": "fixed",      # fixed — одна выбранная фраза; daily — каждый день следующая
    "current": 0,
    "quotes": [
        {"text": "Если не знаешь, к какой гавани плывёшь, ни один ветер не будет попутным.",
         "author": "Сенека, «Письма к Луцилию», LXXI"},
        {"text": "Пока мы откладываем жизнь, она проходит.",
         "author": "Сенека, «Письма к Луцилию», I"},
        {"text": "Любишь жизнь? Тогда не трать время впустую — из него она и состоит.",
         "author": "Бенджамин Франклин"},
        {"text": "Хорошо сделанное лучше хорошо сказанного.",
         "author": "Бенджамин Франклин"},
        {"text": "У кого есть «зачем», тот выдержит почти любое «как».",
         "author": "Фридрих Ницше"},
        {"text": "Главное — не лгите самому себе.",
         "author": "Ф. М. Достоевский, «Братья Карамазовы»"},
        {"text": "Путь в тысячу ли начинается с первого шага.",
         "author": "Лао-цзы"},
        {"text": "То, что мешает делу, становится делом. Препятствие на пути становится путём.",
         "author": "Марк Аврелий, «Размышления», V.20"},
    ],
}


def load():
    with _lock:
        try:
            with open(PATH, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return json.loads(json.dumps(DEFAULT))


def save(data):
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PATH)


def today_quote():
    """Фраза, которая должна стоять на экране сегодня."""
    data = load()
    qs = data["quotes"]
    if not qs:
        return "", ""
    if data.get("mode") == "daily":
        idx = dt.datetime.now(TZ).date().toordinal() % len(qs)
    else:
        idx = min(data.get("current", 0), len(qs) - 1)
    q = qs[idx]
    return q["text"], q.get("author", "")


def parse_new(raw):
    """«Текст — Автор» → (текст, автор). Автор необязателен."""
    for sep in (" — ", " - ", " – "):
        if sep in raw:
            text, author = raw.rsplit(sep, 1)
            return text.strip(), author.strip()
    return raw.strip(), ""
