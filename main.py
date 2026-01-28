import os
import json
import tempfile
from datetime import datetime, timezone
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

    resp = openai_client.responses.create(
        model="gpt-4o-mini",
        instructions=instructions,
        input=f"КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:\n{context_text}\n\nЗАПРОС ПОЛЬЗОВАТЕЛЯ:\n{user_text}",
    )

    answer = getattr(resp, "output_text", None)
    if not answer:
        return "Не смог сформировать ответ. Попробуй переформулировать вопрос."
    return answer.strip()

# -----------------------------
# Memory summary (auto)
# -----------------------------
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def build_summary_instructions() -> str:
    return (
        "Ты пишешь краткое финансовое досье пользователя (memory summary) на русском языке.\n"
        "Требования:\n"
        "- Коротко, 6–10 строк, без воды.\n"
        "- Без морали, без угроз.\n"
        "- Не выдумывай цифры: если нет точных чисел, пиши 'примерно/неизвестно'.\n"
        "- Формат строго такой:\n"
        "ПРОФИЛЬ:\n"
        "- Доход: ...\n"
        "- Обязательства: ...\n"
        "- Баланс: ...\n"
        "- Цель: ...\n"
        "- Риски: ...\n"
        "- Что делать дальше (1–2 пункта): ...\n"
        "- Последний запрос: ...\n"
        "- Последний совет: ...\n"
    )

def call_llm_memory_summary(user_id: int, last_user_text: str, last_bot_text: str) -> str:
    context_text = build_context_text(user_id)

    # Подтягиваем старое саммари (если есть), чтобы модель "переписала" аккуратно, а не ломала
    udoc = user_ref(user_id).get()
    old_summary = ""
    if udoc.exists:
        d = udoc.to_dict() or {}
        ms = (d.get("memory_summary") or {}).get("text", "")
        old_summary = ms or ""

    instructions = build_summary_instructions()
    input_text = (
        f"ТЕКУЩИЙ КОНТЕКСТ:\n{context_text}\n\n"
        f"ПРЕДЫДУЩЕЕ САММАРИ (может быть пусто):\n{old_summary}\n\n"
        f"ПОСЛЕДНИЙ ЗАПРОС ПОЛЬЗОВАТЕЛЯ:\n{last_user_text}\n\n"
        f"ПОСЛЕДНИЙ ОТВЕТ АССИСТЕНТА:\n{last_bot_text}\n\n"
        "Сгенерируй обновлённое саммари строго в заданном формате."
    )

    resp = openai_client.responses.create(
        model="gpt-4o-mini",
        instructions=instructions,
        input=input_text,
    )

    text = getattr(resp, "output_text", None)
    return (text or "").strip()

def maybe_update_memory_summary(user_id: int, last_user_text: str, last_bot_text: str):
    """
    Обновляем memory summary не на каждый запрос, чтобы не тратить деньги.
    Правило MVP: обновлять раз в 5 сообщений после онбординга.
    """
    ref = user_ref(user_id)
    doc = ref.get()
    if not doc.exists:
        return

    data = doc.to_dict() or {}
    onboarding = data.get("onboarding", {}) or {}
    if not onboarding.get("done", False):
        return  # пока идёт онбординг — саммари не нужно

    counters = data.get("counters", {}) or {}
    n = int(counters.get("since_summary", 0)) + 1

    # всегда сохраняем последние тексты (это бесплатно, в Firestore)
    ref.set({
        "last_interaction": {
            "user_text": last_user_text[:1500],
            "bot_text": last_bot_text[:1500],
            "at": firestore.SERVER_TIMESTAMP,
        },
        "counters": {"since_summary": n},
    }, merge=True)

    # обновляем саммари только раз в 5 сообщений
    if n < 5:
        return

    try:
        summary = call_llm_memory_summary(user_id, last_user_text, last_bot_text)
        if summary:
            ref.set({
                "memory_summary": {
                    "text": summary[:3000],
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
                "counters": {"since_summary": 0},
            }, merge=True)
    except Exception as e:
        # Не мешаем пользователю, просто лог
        print("MEMORY_SUMMARY error:", repr(e))

# -----------------------------
# Speech-to-text (voice -> text)
# -----------------------------
async def transcribe_telegram_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Скачивает voice (.ogg) от Telegram во временный файл и отправляет на OpenAI transcriptions.
    Возвращает распознанный текст.
    """
    voice = update.message.voice
    file_id = voice.file_id

    tg_file = await context.bot.get_file(file_id)

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
# Core flow: process text regardless of source
# -----------------------------
async def process_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user = update.effective_user
    text = (text or "").strip()
    if not text:
        await update.message.reply_text("Не разобрал сообщение. Попробуй ещё раз.")
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

            # после завершения онбординга — сразу создадим стартовое саммари (1 раз)
            try:
                # считаем, что "последний запрос" = goal, "последний совет" = системная фраза
                last_bot = "Онбординг завершён. Готов отвечать по деньгам."
                maybe_update_memory_summary(user.id, f"(онбординг) Цель: {text}", last_bot)
            except Exception as e:
                print("MEMORY init error:", repr(e))

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

        # обновляем memory summary (раз в 5 сообщений)
        maybe_update_memory_summary(user.id, text, answer)

    except Exception as e:
        await update.message.reply_text("Ошибка при обработке запроса. Попробуй ещё раз чуть позже.")
        print("LLM error:", repr(e))

# -----------------------------
# Handlers
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref = user_ref(user.id)
    doc = ref.get()

    if not doc.exists:
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
            "counters": {"since_summary": 0},
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
    await process_user_text(update, context, update.message.text or "")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1) Распознать голос в текст
    try:
        text = await transcribe_telegram_voice(update, context)
    except Exception as e:
        await update.message.reply_text("Не смог распознать голос. Попробуй ещё раз.")
        print("STT error:", repr(e))
        return

    if not text:
        await update.message.reply_text("Не разобрал голос (пусто). Попробуй ещё раз.")
        return

    # 2) Никаких "Распознал: ..." — сразу в основной поток
    await process_user_text(update, context, text)

# -----------------------------
# App
# -----------------------------
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))

print("Bot started")
app.run_polling()
