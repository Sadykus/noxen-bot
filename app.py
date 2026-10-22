"""
Noxen Board — MVP backend
=========================
One process, two jobs:
  1. A real Telegram bot (python-telegram-bot) that receives messages
     and updates the "board state".
  2. A tiny Flask web server that serves:
       GET /            -> a live flip-board display page (the "device")
       GET /board       -> current board state as JSON (polled by the page)

This is intentionally simple — a single global state, no database —
so it's easy to deploy and easy to understand. Good enough to prove
the whole pipeline works end to end: Telegram message -> board updates
on screen within a few seconds.

Once this is proven, the next step is: real device via HTTP instead
of this HTML page, and a proper database instead of a global variable.
"""

import io
import os
import json
import threading
import asyncio
import time
from datetime import datetime

from flask import Flask, jsonify, Response

import quotes as Q
import screen
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ─────────────────────────────────────────────────────────────────
# Config — the bot token comes from an environment variable.
# NEVER hardcode it in the file — set it in Railway's dashboard instead.
# ─────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set. "
        "Add it in Railway → your project → Variables."
    )

# ─────────────────────────────────────────────────────────────────
# Shared state — the current text on the "board".
# A lock keeps it safe if the bot and the web server touch it
# at the same time.
# ─────────────────────────────────────────────────────────────────
_state_lock = threading.Lock()
board_state = {
    "text": "NOXEN BOARD",
    "sender": "System",
    "updated_at": datetime.utcnow().isoformat(),
}


def set_board_text(text: str, sender: str = "Telegram"):
    with _state_lock:
        board_state["text"] = text[:200]  # sanity limit
        board_state["sender"] = sender
        board_state["updated_at"] = datetime.utcnow().isoformat()


def get_board_state():
    with _state_lock:
        return dict(board_state)


# ─────────────────────────────────────────────────────────────────
# Telegram bot handlers
# ─────────────────────────────────────────────────────────────────
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я Noxen Bot 🖤\n\n"
        "Напиши мне что угодно — сообщение появится на виртуальном табло.\n\n"
        "Команды табло:\n"
        "/clear — очистить табло\n"
        "/status — текущее состояние табло\n\n"
        "Экран сна ридера:\n"
        "/quotes — список фраз\n"
        "/quote N — поставить фразу №N\n"
        "/addquote Текст — Автор — добавить свою\n"
        "/delquote N — удалить фразу №N\n"
        "/daily — менять фразу каждый день\n"
        "/screen — показать, как выглядит экран сейчас"
    )


async def clear_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender = update.effective_user.first_name or "Telegram"
    set_board_text(" ", sender)
    await update.message.reply_text("🗑 Табло очищено")


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_board_state()
    await update.message.reply_text(
        f"📋 Текущее сообщение: «{s['text']}»\n"
        f"От: {s['sender']}\n"
        f"Обновлено: {s['updated_at']} UTC"
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    sender = update.effective_user.first_name or "Telegram"
    set_board_text(text, sender)
    await update.message.reply_text(f"✓ Отправлено на табло:\n«{text}»")


# ── Фразы для экрана сна ──────────────────────────────────────────
OWNER_ID = os.environ.get("OWNER_ID")  # если задан — менять фразы может только владелец


def _allowed(update: Update) -> bool:
    return not OWNER_ID or str(update.effective_user.id) == OWNER_ID


def _format_list(data):
    lines = []
    for i, q in enumerate(data["quotes"], 1):
        mark = "▶️" if data.get("mode") != "daily" and i - 1 == data.get("current", 0) else f"{i}."
        author = f" — {q['author']}" if q.get("author") else ""
        lines.append(f"{mark} {q['text']}{author}")
    mode = "каждый день новая" if data.get("mode") == "daily" else "фиксированная"
    return "\n\n".join(lines) + f"\n\nРежим: {mode}"


async def quotes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_format_list(Q.load()))


async def quote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return await update.message.reply_text("⛔ Нет доступа")
    data = Q.load()
    try:
        n = int(context.args[0])
        assert 1 <= n <= len(data["quotes"])
    except (IndexError, ValueError, AssertionError):
        return await update.message.reply_text(f"Напиши номер от 1 до {len(data['quotes'])}, например: /quote 2")
    data["current"], data["mode"] = n - 1, "fixed"
    Q.save(data)
    q = data["quotes"][n - 1]
    await update.message.reply_text(f"✓ На экране будет:\n«{q['text']}»\nОбновится при следующем пробуждении ридера.")


async def addquote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return await update.message.reply_text("⛔ Нет доступа")
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        return await update.message.reply_text("Формат: /addquote Текст фразы — Автор")
    text, author = Q.parse_new(raw)
    data = Q.load()
    data["quotes"].append({"text": text[:220], "author": author[:80]})
    Q.save(data)
    await update.message.reply_text(f"✓ Добавлено под №{len(data['quotes'])}. Поставить: /quote {len(data['quotes'])}")


async def delquote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return await update.message.reply_text("⛔ Нет доступа")
    data = Q.load()
    try:
        n = int(context.args[0])
        assert 1 <= n <= len(data["quotes"]) and len(data["quotes"]) > 1
    except (IndexError, ValueError, AssertionError):
        return await update.message.reply_text("Формат: /delquote N (последнюю фразу удалить нельзя)")
    data["quotes"].pop(n - 1)
    if data.get("current", 0) >= len(data["quotes"]):
        data["current"] = 0
    Q.save(data)
    await update.message.reply_text("🗑 Удалено.\n\n" + _format_list(data))


async def daily_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return await update.message.reply_text("⛔ Нет доступа")
    data = Q.load()
    data["mode"] = "daily"
    Q.save(data)
    await update.message.reply_text("✓ Теперь фраза меняется каждый день по списку. Вернуть одну: /quote N")


async def screen_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Рисую экран…")
    png = await asyncio.to_thread(render_screen, "PNG")
    await update.message.reply_photo(png, caption="Так сейчас выглядит экран сна")


def build_telegram_app():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("clear", clear_handler))
    application.add_handler(CommandHandler("status", status_handler))
    application.add_handler(CommandHandler("quotes", quotes_handler))
    application.add_handler(CommandHandler("quote", quote_handler))
    application.add_handler(CommandHandler("addquote", addquote_handler))
    application.add_handler(CommandHandler("delquote", delquote_handler))
    application.add_handler(CommandHandler("daily", daily_handler))
    application.add_handler(CommandHandler("screen", screen_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    return application


def run_bot_in_background():
    """Runs the Telegram bot's polling loop in its own thread with its own
    asyncio event loop, so it doesn't block the Flask web server."""
    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        application = build_telegram_app()
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        loop.run_until_complete(application.updater.start_polling())
        loop.run_forever()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()


# ─────────────────────────────────────────────────────────────────
# Flask web server — serves the board state + a live display page
# ─────────────────────────────────────────────────────────────────
flask_app = Flask(__name__)


# ── Экран сна: ридер скачивает GET /sleep.bmp ─────────────────────
_data_cache = {"at": 0.0, "data": None}
_data_lock = threading.Lock()


def render_screen(fmt="BMP") -> bytes:
    """Погода и курсы кешируются на 30 минут, фраза берётся всегда свежая."""
    with _data_lock:
        if _data_cache["data"] is None or time.time() - _data_cache["at"] > 1800:
            _data_cache["data"] = screen.collect()
            _data_cache["at"] = time.time()
        data = _data_cache["data"]
    text, author = Q.today_quote()
    img = screen.render(data, quote=text, author=author)
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


@flask_app.route("/sleep.bmp")
def sleep_bmp():
    return Response(render_screen("BMP"), mimetype="image/bmp",
                    headers={"Cache-Control": "no-store"})


@flask_app.route("/sleep.png")
def sleep_png():
    return Response(render_screen("PNG"), mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


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


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_bot_in_background()
    # give the bot a moment to initialize before Flask takes over the main thread
    time.sleep(1)
    flask_app.run(host="0.0.0.0", port=PORT)
