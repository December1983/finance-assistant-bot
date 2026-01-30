import os
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
from openai import OpenAI

# =========================
# BASIC SETUP
# =========================
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# SIMPLE MEMORY (TEMP)
# =========================
USER_STATE = {}

# =========================
# HELPERS
# =========================
def get_lang(text: str) -> str:
    text = text.lower()
    if any(w in text for w in ["привет", "кофе", "покажи", "расход", "доход"]):
        return "ru"
    return "en"

def human_reply(lang: str) -> str:
    if lang == "ru":
        return "Привет 🙂 Что будем делать? Записать расход, доход, посмотреть сводку или обсудить идею?"
    return "Hi 🙂 What would you like to do? Add an expense, income, get a summary or advice?"

# =========================
# OPENAI CALL
# =========================
def ask_openai(user_text: str, lang: str) -> str:
    try:
        resp = openai_client.responses.create(
            model="gpt-4o-mini",
            instructions=(
                "You are a finance notebook assistant. "
                "You respond like a human, short, clear, no philosophy. "
                "Stay within finance, budgeting, money notes."
            ),
            input=user_text,
        )

        if hasattr(resp, "output_text") and resp.output_text:
            return resp.output_text.strip()

        return "Не смог сформировать ответ."

    except Exception as e:
        # 🔥 ВОТ ГЛАВНОЕ
        print("OPENAI ERROR >>>", repr(e))
        return (
            "Сейчас не могу обратиться к OpenAI. "
            "Посмотри логи Railway — там есть точная ошибка."
            if lang == "ru"
            else "Can't reach OpenAI right now. Check Railway logs for details."
        )

# =========================
# HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.message.text or "")
    await update.message.reply_text(human_reply(lang))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    lang = get_lang(text)

    # Простые приветствия — БЕЗ OpenAI
    if text.lower() in ["привет", "куку", "ау", "hello", "hi"]:
        await update.message.reply_text(human_reply(lang))
        return

    # Всё остальное — через OpenAI
    answer = ask_openai(text, lang)
    await update.message.reply_text(answer)

# =========================
# APP
# =========================
app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

print("Bot is running...")
app.run_polling()
