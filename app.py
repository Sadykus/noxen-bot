"""
Noxen Frame — backend
=====================
Один процесс, две задачи:
  1. Telegram-бот: личный кабинет каждого пользователя (город, Instagram, фразы, устройства)
     и старое табло (любое текстовое сообщение → на табло).
  2. Flask-сервер:
       GET /s/<token>.bmp  -> экран сна 480x800 для ридера (свой у каждого устройства)
       GET /s/<token>.png  -> то же в PNG, посмотреть в браузере
       GET /               -> живое табло
       GET /board          -> состояние табло JSON

Переменные окружения:
  BOT_TOKEN   — токен бота (обязательно)
  PUBLIC_URL  — внешний адрес сервиса, например https://noxen.up.railway.app (для ссылок в боте)
  DATA_DIR    — папка для базы, на Railway — подключённый Volume (/data)
"""

import asyncio
import io
import os
import threading
import time
from datetime import datetime

from flask import Flask, Response, abort, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import db
import screen

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/") or (
    "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"] if os.environ.get("RAILWAY_PUBLIC_DOMAIN") else "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set. Add it in Railway → Variables.")

db.init()

# ─────────────────────────────────────────────────────────────────
# Табло (как было)
# ─────────────────────────────────────────────────────────────────
_state_lock = threading.Lock()
board_state = {"text": "NOXEN BOARD", "sender": "System", "updated_at": datetime.utcnow().isoformat()}


def set_board_text(text: str, sender: str = "Telegram"):
    with _state_lock:
        board_state["text"] = text[:200]
        board_state["sender"] = sender
        board_state["updated_at"] = datetime.utcnow().isoformat()


def get_board_state():
    with _state_lock:
        return dict(board_state)


# ─────────────────────────────────────────────────────────────────
# Экран сна: данные кешируются (погода по городу, курсы общие) на 30 минут
# ─────────────────────────────────────────────────────────────────
_cache = {}
_cache_lock = threading.Lock()
TTL = 1800


def _cached(key, fn):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (time.time(), val)
    return val


def render_for(user, fmt="BMP") -> bytes:
    today, monday = screen.week_bounds()
    lat, lon = round(user["lat"], 2), round(user["lon"], 2)
    wx = _cached(("wx", lat, lon, monday), lambda: screen.weather_for(lat, lon))
    mk = _cached(("market", today), screen.market)
    data = dict(today=today, monday=monday, wx=wx, city=user["city"], **mk)
    text, author = db.current_quote(user)
    img = screen.render(data, quote=text, author=author, instagram=user["instagram"])
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def screen_url(token, ext="bmp"):
    return f"{PUBLIC_URL}/s/{token}.{ext}" if PUBLIC_URL else f"/s/{token}.{ext}"


# ─────────────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────────────
HELP = (
    "Экран сна ридера:\n"
    "/screen — как выглядит экран сейчас\n"
    "/quotes — список фраз\n"
    "/quote N — поставить фразу №N\n"
    "/addquote Текст — Автор — добавить свою\n"
    "/delquote N — удалить фразу №N\n"
    "/daily — менять фразу каждый день\n"
    "/city Алматы — город для погоды\n"
    "/instagram @ник — подпись внизу (/instagram - убрать)\n"
    "/devices — мои ридеры и их ссылки\n"
    "/newdevice — добавить ещё один ридер\n\n"
    "Табло:\n"
    "любой текст — на табло, /clear — очистить, /status — что на табло"
)


def _user(update: Update):
    u = update.effective_user
    user, _ = db.get_or_create_user(u.id, u.first_name or "")
    return user


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user, created = db.get_or_create_user(u.id, u.first_name or "")
    dev = db.list_devices(u.id)[0]
    hello = "Привет! Я создал тебе кабинет Noxen Frame 🖤" if created else "С возвращением 🖤"
    await update.message.reply_text(
        f"{hello}\n\nГород: {user['city']}\nСсылка экрана для ридера:\n{screen_url(dev['token'])}\n\n{HELP}")


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


def _format_quotes(user):
    qs = db.list_quotes(user["tg_id"])
    daily = user["quote_mode"] == "daily"
    lines = []
    for i, q in enumerate(qs):
        mark = "▶️" if not daily and i == min(user["quote_pos"], len(qs) - 1) else f"{i + 1}."
        author = f" — {q['author']}" if q["author"] else ""
        lines.append(f"{mark} {q['text']}{author}")
    mode = "каждый день новая" if daily else "фиксированная"
    return "\n\n".join(lines) + f"\n\nРежим: {mode}"


async def quotes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_format_quotes(_user(update)))


async def quote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    qs = db.list_quotes(user["tg_id"])
    try:
        n = int(context.args[0])
        assert 1 <= n <= len(qs)
    except (IndexError, ValueError, AssertionError):
        return await update.message.reply_text(f"Напиши номер от 1 до {len(qs)}, например: /quote 2")
    db.update_user(user["tg_id"], quote_pos=n - 1, quote_mode="fixed")
    await update.message.reply_text(f"✓ На экране будет:\n«{qs[n - 1]['text']}»\nОбновится при следующем пробуждении ридера.")


async def addquote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        return await update.message.reply_text("Формат: /addquote Текст фразы — Автор")
    text, author = db.parse_new(raw)
    n = db.add_quote(user["tg_id"], text[:220], author[:80])
    await update.message.reply_text(f"✓ Добавлено под №{n}. Поставить на экран: /quote {n}")


async def delquote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    try:
        ok = db.delete_quote(user["tg_id"], int(context.args[0]) - 1)
    except (IndexError, ValueError):
        ok = False
    if not ok:
        return await update.message.reply_text("Формат: /delquote N (последнюю фразу удалить нельзя)")
    await update.message.reply_text("🗑 Удалено.\n\n" + _format_quotes(db.get_user(user["tg_id"])))


async def daily_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    db.update_user(user["tg_id"], quote_mode="daily")
    await update.message.reply_text("✓ Теперь фраза меняется каждый день по списку. Вернуть одну: /quote N")


async def city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    name = " ".join(context.args).strip()
    if not name:
        return await update.message.reply_text(f"Сейчас: {user['city']}. Сменить: /city Алматы")
    try:
        found = await asyncio.to_thread(screen.geocode, name)
    except Exception:
        found = None
    if not found:
        return await update.message.reply_text("Не нашёл такой город, попробуй написать иначе.")
    lat, lon, city = found
    db.update_user(user["tg_id"], city=city, lat=lat, lon=lon)
    await update.message.reply_text(f"✓ Погода теперь для: {city}")


async def instagram_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    raw = " ".join(context.args).strip()
    if not raw:
        cur = user["instagram"] or "не указан"
        return await update.message.reply_text(f"Сейчас: {cur}. Сменить: /instagram @ник, убрать: /instagram -")
    handle = "" if raw == "-" else "@" + raw.lstrip("@")[:30]
    db.update_user(user["tg_id"], instagram=handle)
    await update.message.reply_text(f"✓ Подпись внизу: {handle or 'убрана'}")


async def devices_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    lines = []
    for d in db.list_devices(user["tg_id"]):
        seen = d["last_seen"][:16].replace("T", " ") if d["last_seen"] else "ещё не подключался"
        lines.append(f"📟 {d['name']}\n{screen_url(d['token'])}\nПоследний раз: {seen}")
    await update.message.reply_text("\n\n".join(lines) + "\n\nДобавить ещё: /newdevice")


async def newdevice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    token = db.add_device(user["tg_id"])
    await update.message.reply_text(f"✓ Новый ридер добавлен. Его ссылка:\n{screen_url(token)}")


async def screen_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    await update.message.reply_text("Рисую экран…")
    png = await asyncio.to_thread(render_for, user, "PNG")
    await update.message.reply_photo(png, caption="Так сейчас выглядит экран сна")


async def clear_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_board_text(" ", update.effective_user.first_name or "Telegram")
    await update.message.reply_text("🗑 Табло очищено")


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_board_state()
    await update.message.reply_text(
        f"📋 Текущее сообщение: «{s['text']}»\nОт: {s['sender']}\nОбновлено: {s['updated_at']} UTC")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    set_board_text(text, update.effective_user.first_name or "Telegram")
    await update.message.reply_text(f"✓ Отправлено на табло:\n«{text}»")


def build_telegram_app():
    app = Application.builder().token(BOT_TOKEN).build()
    for name, fn in [("start", start_handler), ("help", help_handler), ("screen", screen_handler),
                     ("quotes", quotes_handler), ("quote", quote_handler), ("addquote", addquote_handler),
                     ("delquote", delquote_handler), ("daily", daily_handler), ("city", city_handler),
                     ("instagram", instagram_handler), ("devices", devices_handler),
                     ("newdevice", newdevice_handler), ("clear", clear_handler), ("status", status_handler)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    return app


def run_bot_in_background():
    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        application = build_telegram_app()
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        loop.run_until_complete(application.updater.start_polling())
        loop.run_forever()

    threading.Thread(target=_runner, daemon=True).start()


# ─────────────────────────────────────────────────────────────────
# Flask
# ─────────────────────────────────────────────────────────────────
flask_app = Flask(__name__)


@flask_app.route("/s/<token>.<ext>")
def device_screen(token, ext):
    if ext not in ("bmp", "png"):
        abort(404)
    user = db.user_by_token(token)
    if not user:
        abort(404)
    data = render_for(user, "BMP" if ext == "bmp" else "PNG")
    return Response(data, mimetype=f"image/{ext}", headers={"Cache-Control": "no-store"})


@flask_app.route("/health")
def health():
    return jsonify(ok=True, users=db.count_users())


@flask_app.route("/board")
def board_endpoint():
    return jsonify(get_board_state())


BOARD_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Noxen Board — Live</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0a0a0a; min-height: 100vh; display: flex;
    align-items: center; justify-content: center; flex-direction: column;
    font-family: 'Courier New', monospace; gap: 24px;
  }
  .board {
    background: #141414; border-radius: 12px; padding: 28px;
    border: 1px solid #2a2a2a; box-shadow: 0 24px 70px rgba(0,0,0,0.6);
    display: grid; grid-template-columns: repeat(10, 40px); gap: 4px;
  }
  .tile {
    width: 40px; height: 54px; background: #0a0a0a; border-radius: 4px;
    border: 1px solid #1e1e1e; display: flex; align-items: center;
    justify-content: center; font-size: 20px; font-weight: 700; color: #E8E8E8;
    transition: transform 0.15s;
  }
  .tile.flip { transform: rotateX(90deg); opacity: 0.3; }
  .meta { color: #555; font-size: 12px; text-align: center; }
  .meta b { color: #888; }
  .status { display: flex; align-items: center; gap: 6px; color: #4ade80; font-size: 11px; }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: #4ade80; }
</style>
</head>
<body>
  <div class="board" id="board"></div>
  <div class="meta">От: <b id="sender">—</b> · Обновлено: <b id="time">—</b></div>
  <div class="status"><span class="dot"></span>Live · опрос каждые 2 сек</div>

<script>
const ROWS = 4, COLS = 10;
const boardEl = document.getElementById('board');
let tiles = [];

for (let i = 0; i < ROWS * COLS; i++) {
  const t = document.createElement('div');
  t.className = 'tile';
  boardEl.appendChild(t);
  tiles.push(t);
}

let lastText = '';

async function poll() {
  try {
    const res = await fetch('/board');
    const data = await res.json();
    document.getElementById('sender').textContent = data.sender;
    document.getElementById('time').textContent = new Date(data.updated_at).toLocaleTimeString('ru-RU');

    if (data.text !== lastText) {
      lastText = data.text;
      renderText(data.text);
    }
  } catch (e) {
    console.error('poll failed', e);
  }
  setTimeout(poll, 2000);
}

function renderText(text) {
  const padded = text.toUpperCase().padEnd(ROWS * COLS, ' ').slice(0, ROWS * COLS);
  padded.split('').forEach((ch, i) => {
    const tile = tiles[i];
    tile.classList.add('flip');
    setTimeout(() => {
      tile.textContent = ch === ' ' ? '' : ch;
      tile.classList.remove('flip');
    }, 150);
  });
}

poll();
</script>
</body>
</html>
"""


@flask_app.route("/")
def index():
    return Response(BOARD_HTML, mimetype="text/html")


if __name__ == "__main__":
    run_bot_in_background()
    time.sleep(1)
    flask_app.run(host="0.0.0.0", port=PORT)
