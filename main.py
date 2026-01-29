# main/main.py
import os
import json
import tempfile

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import firebase_admin
from firebase_admin import credentials, firestore
from openai import OpenAI

from storage import Storage
from brain import Brain


# -----------------------------
# ENV
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
FIREBASE_SERVICE_ACCOUNT = os.getenv("FIREBASE_SERVICE_ACCOUNT")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in environment variables.")
if not FIREBASE_SERVICE_ACCOUNT:
    raise RuntimeError("FIREBASE_SERVICE_ACCOUNT is missing in environment variables.")


# -----------------------------
# Firebase init
# -----------------------------
cred = credentials.Certificate(json.loads(FIREBASE_SERVICE_ACCOUNT))
firebase_admin.initialize_app(cred)
db = firestore.client()

storage = Storage(db)
brain = Brain(storage)

# -----------------------------
# OpenAI init (voice->text)
# -----------------------------
openai_client = OpenAI()  # берет OPENAI_API_KEY из ENV автоматически


# -----------------------------
# Speech-to-text (voice -> text)
# -----------------------------
async def transcribe_telegram_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    voice = update.message.voice
    tg_file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await tg_file.download_to_drive(custom_path=tmp_path)

        with open(tmp_path, "rb") as f:
            tr = openai_client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=f,
            )

        text = getattr(tr, "text", None)
        if not text:
            text = str(tr)
        return (text or "").strip()

    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# -----------------------------
# Handlers
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage.ensure_user(user)
    await update.message.reply_text(
        "Я финансовый блокнот.\n"
        "Говори или пиши как обычно:\n"
        "• «кофе 5»\n"
        "• «пришло 450»\n"
        "• «покажи за неделю»\n"
        "• «удали всё»"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text or ""
    reply, _ = await brain.handle(user, text)
    await update.message.reply_text(reply)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        text = await transcribe_telegram_voice(update, context)
    except Exception as e:
        await update.message.reply_text("Не смог распознать голос. Попробуй ещё раз.")
        print("STT error:", repr(e))
        return

    if not text:
        await update.message.reply_text("Не разобрал голос (пусто). Попробуй ещё раз.")
        return

    # ВАЖНО: мы НЕ выводим “распознал: ...” — ты это просил убрать
    reply, _ = await brain.handle(user, text)
    await update.message.reply_text(reply)


# -----------------------------
# App
# -----------------------------
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))

print("Bot started")
app.run_polling()
