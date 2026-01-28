import os
import json
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

# -----------------------------
# OpenAI init
# -----------------------------
openai_client = OpenAI()  # берет OPENAI_API_KEY из ENV автоматически

# -----------------------------
# Firestore helpers
# -----------------------------
def user_ref(user_id: int):
    return db.collection("users").document(str(user_id))

def snapshot_ref(user_id: int):
    return user_ref(user_id).collection("financial_snapshot").document("current")

def balance_ref(user_id: int):
    return user_ref(user_id).collection("balance_state").document("current")

# -----------------------------
# Onboarding questions
# -----------------------------
ONBOARDING_QUESTIONS = {
    "income": "Расскажи, откуда и примерно сколько ты зарабатываешь.",
    "obligations": "Какие у тебя регулярные обязательные траты? Жильё, кредиты, страховки и т.п.",
    "balance": "Сколько примерно сейчас денег у тебя доступно?",
    "goal": "Есть ли у тебя финансовая цель? (накопить, закрыть долг, стабилизироваться и т.д.)",
}

async def ask_onboarding(update: Update, step: str):
    q = ONBOARDING_QUESTIONS.get(step, ONBOARDING_QUESTIONS["income"])
    await update.message.reply_text(q)

# -----------------------------
# LLM consultation
# -----------------------------
def build_context_text(user_id: int) -> str:
    snap_doc = snapshot_ref(user_id).get()
    bal_doc = balance_ref(user_id).get()

    snap = snap_doc.to_dict() if snap_doc.exists else {}
    bal = bal_doc.to_dict() if bal_doc.exists else {}

    # В MVP храним "notes" как текст, чтобы не усложнять
    income_notes = snap.get("income_notes", "unknown")
    obligations_notes = snap.get("obligations_notes", "unknown")
    goal_notes = snap.get("goal_notes", "unknown")

    last_balance_raw = bal.get("last_known_balance_raw", "unknown")
    confidence = bal.get("confidence", "unknown")

    return (
        f"INCOME_NOTES: {income_notes}\n"
        f"OBLIGATIONS_NOTES: {obligations_notes}\n"
        f"GOAL_NOTES: {goal_notes}\n"
        f"LAST_BALANCE_RAW: {last_balance_raw}\n"
        f"BALANCE_CONFIDENCE: {confidence}\n"
    )

def build_instructions() -> str:
    # Это “конституция” для консультаций (коротко и по делу)
    return (
        "Ты финансовый ассистент-консультант. Говоришь спокойно, без морали.\n"
        "Правила:\n"
        "1) НЕ выдумывай факты. Если данных недостаточно — задай 1–2 уточняющих вопроса.\n"
        "2) НЕ рекомендуй траты, которые могут сорвать обязательные платежи.\n"
        "3) Если видишь риск — прямо пометь словом 'риск'.\n"
        "4) Ответ короткий: рекомендация + почему + (если надо) вопросы.\n"
        "5) Не требуй таблиц и ежедневных отчетов.\n"
    )

def call_llm_advice(user_id: int, user_text: str) -> str:
    context_text = build_context_text(user_id)
    instructions = build_instructions()

    # Используем Responses API через SDK openai
    resp = openai_client.responses.create(
        model="gpt-4o-mini",
        instructions=instructions,
        input=f"КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:\n{context_text}\n\nЗАПРОС ПОЛЬЗОВАТЕЛЯ:\n{user_text}",
    )

    # В Python SDK у responses есть output_text
    answer = getattr(resp, "output_text", None)
    if not answer:
        return "Не смог сформировать ответ. Попробуй переформулировать вопрос."
    return answer.strip()

# -----------------------------
# Handlers
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref = user_ref(user.id)
    doc = ref.get()

    if not doc.exists:
        # Create new user doc
        ref.set({
            "telegram_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "created_at": firestore.SERVER_TIMESTAMP,
            "last_active_at": firestore.SERVER_TIMESTAMP,
            "onboarding": {"done": False, "step": "income"},
            "settings": {
                "language": "auto",
                "currency": "USD",
                "allow_weekly_nudge": True,
            },
            "memory_summary": {"text": "Пока нет данных.", "updated_at": firestore.SERVER_TIMESTAMP},
        })

        await update.message.reply_text("Привет. Давай разберём твою финансовую ситуацию.")
        await ask_onboarding(update, "income")
        return

    data = doc.to_dict() or {}
    onboarding = data.get("onboarding", {})

    ref.update({"last_active_at": firestore.SERVER_TIMESTAMP})

    if not onboarding.get("done", False):
        step = onboarding.get("step", "income")
        await ask_onboarding(update, step)
        return

    await update.message.reply_text("Я помню твою ситуацию. Чем помочь?")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()
    if not text:
        return

    ref = user_ref(user.id)
    doc = ref.get()

    # Если юзера нет — запускаем /start-логику
    if not doc.exists:
        await start(update, context)
        return

    data = doc.to_dict() or {}
    onboarding = data.get("onboarding", {})
    ref.update({"last_active_at": firestore.SERVER_TIMESTAMP})

    # -----------------------------
    # Onboarding flow
    # -----------------------------
    if not onboarding.get("done", False):
        step = onboarding.get("step", "income")

        if step == "income":
            snapshot_ref(user.id).set(
                {"income_notes": text, "updated_at": firestore.SERVER_TIMESTAMP},
                merge=True,
            )
            ref.update({"onboarding.step": "obligations"})
            await ask_onboarding(update, "obligations")
            return

        if step == "obligations":
            snapshot_ref(user.id).set(
                {"obligations_notes": text, "updated_at": firestore.SERVER_TIMESTAMP},
                merge=True,
            )
            ref.update({"onboarding.step": "balance"})
            await ask_onboarding(update, "balance")
            return

        if step == "balance":
            # В MVP сохраняем "как есть" (raw), потом научим парсить сумму
            balance_ref(user.id).set(
                {
                    "last_known_balance_raw": text,
                    "confidence": "low",
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            ref.update({"onboarding.step": "goal"})
            await ask_onboarding(update, "goal")
            return

        if step == "goal":
            snapshot_ref(user.id).set(
                {"goal_notes": text, "updated_at": firestore.SERVER_TIMESTAMP},
                merge=True,
            )
            ref.update({"onboarding.done": True, "onboarding.step": "done"})
            await update.message.reply_text(
                "Ок, я понял общую картину. Теперь можешь спрашивать что угодно по деньгам."
            )
            return

        # fallback
        ref.update({"onboarding.step": "income"})
        await ask_onboarding(update, "income")
        return

    # -----------------------------
    # Consultation mode (LLM)
    # -----------------------------
    try:
        answer = call_llm_advice(user.id, text)
        await update.message.reply_text(answer)
    except Exception as e:
        # Не раскрываем детали пользователю, но даём понятный ответ
        await update.message.reply_text("Ошибка при обработке запроса. Попробуй ещё раз чуть позже.")
        # Лог в Railway
        print("LLM error:", repr(e))

# -----------------------------
# App
# -----------------------------
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

print("Bot started")
app.run_polling()
