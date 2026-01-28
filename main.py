import os
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import firebase_admin
from firebase_admin import credentials, firestore

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Firebase
firebase_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
cred = credentials.Certificate(json.loads(firebase_json))
firebase_admin.initialize_app(cred)
db = firestore.client()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    db.collection("users").document(str(user.id)).set({
        "telegram_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "created_at": firestore.SERVER_TIMESTAMP
    }, merge=True)

    await update.message.reply_text(
        "Привет. Бот запущен и подключён к Firestore."
    )

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))

print("Bot started")

app.run_polling()
