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
# OpenAI init (voice->text only here)
# -----------------------------
openai_client = OpenAI()  # OPENAI_API_KEY из ENV


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

    # Никаких меню. Просто коротко объясняем.
    # Язык определится автоматически при первом реальном сообщении (auto),
    # но базовую валюту спросим.
    await update.message.reply_text(
        "Hi! I'm your finance notebook.\n"
        "Just text or speak normally: “coffee 5”, “got paid 1200”, “my expenses last week”.\n"
        "First question: what base currency do you want for all summaries? (e.g., USD, EUR, GBP)"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text or ""
    reply = await brain.handle_message(user, text)
    await update.message.reply_text(reply)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        text = await transcribe_telegram_voice(update, context)
    except Exception as e:
        await update.message.reply_text("I couldn't transcribe the voice message. Please try again.")
        print("STT error:", repr(e))
        return

    if not text:
        await update.message.reply_text("I couldn't hear anything clearly. Please try again.")
        return

    reply = await brain.handle_message(user, text)
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
