import os
import json
import tempfile
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from openai import OpenAI

from storage import Storage
from brain import Brain


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("finance-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
FIREBASE_SERVICE_ACCOUNT = os.getenv("FIREBASE_SERVICE_ACCOUNT")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in environment variables.")
if not FIREBASE_SERVICE_ACCOUNT:
    raise RuntimeError("FIREBASE_SERVICE_ACCOUNT is missing in environment variables.")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing in environment variables.")


# Init clients
openai_client = OpenAI(api_key=OPENAI_API_KEY)
storage = Storage(service_account_json=FIREBASE_SERVICE_ACCOUNT)
brain = Brain(storage=storage, openai_client=openai_client)


# -----------------------------
# Voice -> text
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
        return (text or "").strip()

    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# -----------------------------
# Telegram handlers
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    reply = await brain.handle(user_id=user.id, username=user.username, first_name=user.first_name, text="/start")
    await update.message.reply_text(reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()
    if not text:
        return

    reply = await brain.handle(user_id=user.id, username=user.username, first_name=user.first_name, text=text)
    if reply:
        await update.message.reply_text(reply)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    try:
        text = await transcribe_telegram_voice(update, context)
    except Exception as e:
        logger.exception("STT error: %s", e)
        await update.message.reply_text("Sorry, I couldn't recognize that voice message. Please try again.")
        return

    if not text:
        await update.message.reply_text("Sorry, I couldn't recognize that voice message (empty). Please try again.")
        return

    reply = await brain.handle(user_id=user.id, username=user.username, first_name=user.first_name, text=text)
    if reply:
        await update.message.reply_text(reply)


# -----------------------------
# Reminder polling job
# -----------------------------
async def reminders_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every minute: finds due reminders and sends messages.
    """
    try:
        due = storage.fetch_due_reminders(limit=25)
        if not due:
            return

        for item in due:
            user_id = item["user_id"]
            reminder_id = item["reminder_id"]
            text = item["text"]
            lang = item.get("language") or "auto"

            # send
            try:
                await context.bot.send_message(chat_id=int(user_id), text=text)
            except Exception:
                logger.exception("Failed to send reminder to user_id=%s", user_id)

            # mark done
            storage.mark_reminder_done(user_id=user_id, reminder_id=reminder_id)

    except Exception:
        logger.exception("reminders_job failed")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # JobQueue reminders
    if app.job_queue:
        app.job_queue.run_repeating(reminders_job, interval=60, first=10)

    logger.info("Bot started")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
