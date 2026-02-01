import os
import json
import tempfile
import traceback

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

import firebase_admin
from firebase_admin import credentials, firestore
from openai import OpenAI

from brain import Brain
from utils import clean_text


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
FIREBASE_SERVICE_ACCOUNT = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not FIREBASE_SERVICE_ACCOUNT:
    raise RuntimeError("FIREBASE_SERVICE_ACCOUNT is missing")
if not OPENAI_API_KEY:
    # бот будет работать без OpenAI, но голос и “мозг” будут ограничены
    print("WARNING: OPENAI_API_KEY is missing. Bot will run in fallback-only mode.")

# Firebase
cred = credentials.Certificate(json.loads(FIREBASE_SERVICE_ACCOUNT))
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# OpenAI
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

brain = Brain(db=db, openai_client=openai_client if openai_client else OpenAI(api_key="DUMMY"))


async def is_openai_ok() -> bool:
    # если ключа нет — точно нет
    if not openai_client:
        return False
    try:
        # быстрый “пинг” через очень дешёвый запрос
        _ = openai_client.models.list()
        return True
    except Exception as e:
        print("OpenAI check failed:", repr(e))
        return False


async def transcribe_telegram_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    if not openai_client:
        raise RuntimeError("OpenAI client not configured")

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
        return clean_text(text or "")
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет 🙂 Я твоя финансовая записная книжка.\n"
        "Напиши «что ты умеешь» — покажу все функции."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # просто вызываем через brain HELP
    user = update.effective_user
    ok = await is_openai_ok()
    reply = brain.handle(user.id, user.username, user.first_name, "что ты умеешь", openai_ok=ok)
    await update.message.reply_text(reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = clean_text(update.message.text or "")
    if not text:
        return

    ok = await is_openai_ok()
    reply = brain.handle(user.id, user.username, user.first_name, text, openai_ok=ok)
    await update.message.reply_text(reply)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    ok = await is_openai_ok()
    if not ok:
        await update.message.reply_text(
            "Сейчас OpenAI недоступен, поэтому я не могу расшифровать голос.\n"
            "Пожалуйста, напиши текстом — я всё равно могу записывать и показывать сводку."
        )
        return

    try:
        text = await transcribe_telegram_voice(update, context)
    except Exception as e:
        print("Voice STT error:", repr(e))
        traceback.print_exc()
        await update.message.reply_text("Не смог распознать голос. Попробуй ещё раз или напиши текстом.")
        return

    if not text:
        await update.message.reply_text("Не разобрал голос. Попробуй ещё раз или напиши текстом.")
        return

    reply = brain.handle(user.id, user.username, user.first_name, text, openai_ok=True)
    await update.message.reply_text(reply)


async def on_startup(app):
    # КРИТИЧНО: убираем webhook, чтобы polling не конфликтовал
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        print("Webhook deleted (drop pending updates).")
    except Exception as e:
        print("delete_webhook error:", repr(e))


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    print("Bot started")
    # allowed_updates=None → по умолчанию все нужные
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
