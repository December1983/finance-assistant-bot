import os
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import firebase_admin
from firebase_admin import credentials, firestore

# --- Telegram ---
BOT_TOKEN = os.getenv("BOT_TOKEN")

# --- Firebase ---
firebase_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
cred = credentials.Certificate(json.loads(firebase_json))
firebase_admin.initialize_app(cred)
db = firestore.client()

# --- Helpers ---
def user_doc(user_id: int):
    return db.collection("users").document(str(user_id))

def snapshot_doc(user_id: int):
    return user_doc(user_id).collection("financial_snapshot").document("current")

def balance_doc(user_id: int):
    return user_doc(user_id).collection("balance_state").document("current")

async def send_onboarding_question(update: Update, step: str):
    questions = {
        "income": "Расскажи, откуда и примерно сколько ты зарабатываешь.",
        "obligations": "Какие у тебя регулярные обязательные траты? Жильё, кредиты, страховки и т.п.",
        "balance": "Сколько примерно сейчас денег у тебя доступно?",
        "goal": "Есть ли у тебя финансовая цель? (накопить, закрыть долг, стабилизироваться и т.д.)"
    }
    await update.message.reply_text(questions[step])

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref = user_doc(user.id)
    snap = ref.get()

    if not snap.exists:
        # create new user
        ref.set({
            "telegram_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "created_at": firestore.SERVER_TIMESTAMP,
            "last_active_at": firestore.SERVER_TIMESTAMP,
            "onboarding": {
                "done": False,
                "step": "income"
            },
            "settings": {
                "language": "auto",
                "currency": "USD",
                "allow_weekly_nudge": True
            },
            "memory_summary": {
                "text": "Пока нет данных.",
                "updated_at": firestore.SERVER_TIMESTAMP
            }
        })

        await update.message.reply_text("Привет. Давай разберём твою финансовую ситуацию.")
        await send_onboarding_question(update, "income")
        return

    data = snap.to_dict()
    onboarding = data.get("onboarding", {})

    if not onboarding.get("done", False):
        step = onboarding.get("step", "income")
        await send_onboarding_question(update, step)
        return

    await update.message.reply_text("Я помню твою ситуацию. Чем помочь?")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref = user_doc(user.id)
    snap = ref.get()

    if not snap.exists:
        await start(update, context)
        return

    data = snap.to_dict()
    onboarding = data.get("onboarding", {})

    if not onboarding.get("done", False):
        step = onboarding.get("step", "income")
        text = update.message.text.strip()

        if step == "income":
            snapshot_doc(user.id).set({
                "income_notes": text,
                "updated_at": firestore.SERVER_TIMESTAMP
            }, merge=True)
            ref.update({"onboarding.step": "obligations"})
            await send_onboarding_question(update, "obligations")
            return

        if step == "obligations":
            snapshot_doc(user.id).set({
                "obligations_notes": text,
                "updated_at": firestore.SERVER_TIMESTAMP
            }, merge=True)
            ref.update({"onboarding.step": "balance"})
            await send_onboarding_question(update, "balance")
            return

        if step == "balance":
            balance_doc(user.id).set({
                "last_known_balance_raw": text,
                "confidence": "low",
                "updated_at": firestore.SERVER_TIMESTAMP
            }, merge=True)
            ref.update({"onboarding.step": "goal"})
            await send_onboarding_question(update, "goal")
            return

        if step == "goal":
            snapshot_doc(user.id).set({
                "goal_notes": text,
                "updated_at": firestore.SERVER_TIMESTAMP
            }, merge=True)
            ref.update({
                "onboarding.done": True,
                "onboarding.step": "done"
            })
            await update.message.reply_text(
                "Ок, я понял общую картину. Теперь можешь спрашивать что угодно по деньгам."
            )
            return

    # обычный режим (пока просто эхо)
    await update.message.reply_text("Принял. Дальше здесь будет логика консультаций.")

# --- App ---
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

print("Bot started")
app.run_polling()
