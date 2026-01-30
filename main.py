import os
import json
import tempfile

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

import firebase_admin
from firebase_admin import credentials, firestore
from openai import OpenAI

from brain import Brain


BOT_TOKEN = os.getenv("BOT_TOKEN")
FIREBASE_SERVICE_ACCOUNT = os.getenv("FIREBASE_SERVICE_ACCOUNT")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not FIREBASE_SERVICE_ACCOUNT:
    raise RuntimeError("FIREBASE_SERVICE_ACCOUNT is missing")


cred = credentials.Certificate(json.loads(FIREBASE_SERVICE_ACCOUNT))
firebase_admin.initialize_app(cred)
db = firestore.client()

openai_client = OpenAI()

brain = Brain(db=db, openai_client=openai_client)


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
        return (text or "").strip()
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Just greet. No currency trap.
    reply = "Привет 🙂 Я твоя финансовая записная книжка. Что хочешь сделать: записать расход/доход, посмотреть сводку или спросить совет?"
    await update.message.reply_text(reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()
    if not text:
        return

    reply = brain.handle(user.id, user.username, user.first_name, text)
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
        await update.message.reply_text("Не разобрал голос. Попробуй ещё раз.")
        return

    reply = brain.handle(user.id, user.username, user.first_name, text)
    await update.message.reply_text(reply)


app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))

print("Bot started")
app.run_polling()
