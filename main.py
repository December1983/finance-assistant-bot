import os
import tempfile
import logging

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
logger = logging.getLogger("finance-notebook-bot")


BOT_TOKEN = os.getenv("BOT_TOKEN")
FIREBASE_SERVICE_ACCOUNT = os.getenv("FIREBASE_SERVICE_ACCOUNT")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not FIREBASE_SERVICE_ACCOUNT:
    raise RuntimeError("FIREBASE_SERVICE_ACCOUNT is missing")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing")


openai_client = OpenAI(api_key=OPENAI_API_KEY)
storage = Storage(service_account_json=FIREBASE_SERVICE_ACCOUNT)
brain = Brain(storage=storage, openai_client=openai_client)


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
        text = getattr(tr, "text", "") or ""
        return text.strip()
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_lang = getattr(user, "language_code", None)

    try:
        reply = await brain.handle(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            telegram_language_code=user_lang,
            text="/start",
        )
        await update.message.reply_text(reply)
    except Exception as e:
        logger.exception("START error: %s", e)
        await update.message.reply_text("Ошибка на сервере. Открой Railway Logs и пришли верхние 10 строк.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_lang = getattr(user, "language_code", None)
    text = (update.message.text or "").strip()
    if not text:
        return

    try:
        reply = await brain.handle(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            telegram_language_code=user_lang,
            text=text,
        )
        if reply:
            await update.message.reply_text(reply)
    except Exception as e:
        logger.exception("TEXT error: %s", e)
        await update.message.reply_text(
            "Упало при обработке сообщения. Открой Railway Logs и пришли верхние 10 строк ошибки."
        )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_lang = getattr(user, "language_code", None)

    try:
        text = await transcribe_telegram_voice(update, context)
    except Exception as e:
        logger.exception("STT error: %s", e)
        await update.message.reply_text("Не смог распознать голос. Проверь OPENAI_API_KEY и баланс.")
        return

    if not text:
        await update.message.reply_text("Не разобрал голос (пусто). Попробуй ещё раз.")
        return

    try:
        reply = await brain.handle(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            telegram_language_code=user_lang,
            text=text,
        )
        if reply:
            await update.message.reply_text(reply)
    except Exception as e:
        logger.exception("VOICE error: %s", e)
        await update.message.reply_text(
            "Ошибка при обработке голоса. Открой Railway Logs и пришли верхние 10 строк."
        )


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    logger.info("Bot started")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
