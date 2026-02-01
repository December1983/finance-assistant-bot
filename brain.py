import re
import time
from datetime import datetime, timedelta, timezone

from firebase_admin import firestore


RU_HINT = (
    "Примеры:\n"
    "• «кофе 5»\n"
    "• «запиши 8 на долг»\n"
    "• «покажи расходы за неделю»\n"
    "• «покажи доходы за месяц»\n"
    "• «сводка за сегодня»"
)

CATEGORY_MAP_RU = {
    "долг": "debt",
    "долги": "debt",
    "кредит": "debt",
    "кредиты": "debt",
    "еда": "food",
    "кафе": "food",
    "кофе": "food",
    "топливо": "fuel",
    "бензин": "fuel",
    "ремонт": "maintenance",
    "страховка": "insurance",
    "связь": "phone",
    "интернет": "internet",
}


def _now_utc():
    return datetime.now(timezone.utc)


def _ts_to_dt(ts):
    # Firestore Timestamp -> datetime
    try:
        return ts.to_datetime()
    except Exception:
        return ts


def _normalize_text(t: str) -> str:
    t = (t or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _detect_ru(text: str) -> bool:
    return bool(re.search(r"[а-яА-ЯёЁ]", text))


def _parse_amount(text: str):
    # ищем первое число: 8, 8.5, 8,50
    m = re.search(r"(?<!\d)(\d+(?:[.,]\d{1,2})?)(?!\d)", text)
    if not m:
        return None
    s = m.group(1).replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _parse_currency(text: str):
    t = text.lower()
    if "$" in t or "usd" in t or "доллар" in t or "бакс" in t:
        return "USD"
    if "eur" in t or "евро" in t:
        return "EUR"
    if "gbp" in t or "фунт" in t:
        return "GBP"
    if "kzt" in t or "тенге" in t:
        return "KZT"
    if "rub" in t or "руб" in t:
        return "RUB"
    return None


def _parse_period(text: str):
    t = text.lower()

    # сегодня
    if any(x in t for x in ["сегодня", "за сегодня", "today"]):
        start = _now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start, end, "today"

    # вчера
    if any(x in t for x in ["вчера", "за вчера", "yesterday"]):
        end = _now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)
        return start, end, "yesterday"

    # неделя / 7 дней
    if any(x in t for x in ["недел", "7 дней", "last week", "past week"]):
        end = _now_utc()
        start = end - timedelta(days=7)
        return start, end, "week"

    # месяц / 30 дней
    if any(x in t for x in ["месяц", "30 дней", "last month", "past month"]):
        end = _now_utc()
        start = end - timedelta(days=30)
        return start, end, "month"

    # по умолчанию неделя
    end = _now_utc()
    start = end - timedelta(days=7)
    return start, end, "week"


def _is_show_intent(text: str):
    t = text.lower()
    show_words = ["покажи", "показать", "выведи", "дай", "show", "list"]
    if not any(w in t for w in show_words):
        return False
    return True


def _want_expenses(text: str):
    t = text.lower()
    return any(x in t for x in ["расход", "траты", "потрат", "expense", "spent"])


def _want_income(text: str):
    t = text.lower()
    return any(x in t for x in ["доход", "пришло", "заработ", "income", "got paid", "paid"])


def _want_summary(text: str):
    t = text.lower()
    return any(x in t for x in ["сводк", "итого", "summary", "total"])


def _guess_category(text: str):
    t = text.lower()
    for k, v in CATEGORY_MAP_RU.items():
        if k in t:
            return v
    return "other"


def _guess_type(text: str):
    t = text.lower()

    # явные доходы
    if any(x in t for x in ["доход", "пришло", "получил", "заработ", "income", "got paid"]):
        return "income"

    # явные расходы
    if any(x in t for x in ["расход", "потрат", "купил", "запиши", "списал", "expense", "spent", "coffee", "кофе"]):
        return "expense"

    # по умолчанию расход
    return "expense"


class Brain:
    def __init__(self, db, openai_client):
        self.db = db
        self.openai = openai_client

    # ---------- Firestore helpers ----------
    def _user_ref(self, tg_user_id: int):
        return self.db.collection("users").document(str(tg_user_id))

    def _tx_ref(self, tg_user_id: int):
        return self._user_ref(tg_user_id).collection("transactions")

    def _get_user(self, tg_user_id: int) -> dict:
        doc = self._user_ref(tg_user_id).get()
        return (doc.to_dict() or {}) if doc.exists else {}

    def _set_user(self, tg_user_id: int, data: dict):
        self._user_ref(tg_user_id).set(data, merge=True)

    def _add_tx(self, tg_user_id: int, tx: dict):
        # tx: {type, amount, currency, category, note}
        self._tx_ref(tg_user_id).add({
            "type": tx["type"],
            "amount": float(tx["amount"]),
            "currency": tx["currency"],
            "category": tx["category"],
            "note": tx.get("note", ""),
            "created_at": firestore.SERVER_TIMESTAMP,
        })

    def _query_range(self, tg_user_id: int, start_dt, end_dt):
        # created_at between start_dt and end_dt
        q = (self._tx_ref(tg_user_id)
             .where("created_at", ">=", start_dt)
             .where("created_at", "<", end_dt)
             .order_by("created_at", direction=firestore.Query.ASCENDING))
        return list(q.stream())

    # ---------- Core ----------
    def handle(self, tg_user_id: int, username: str, first_name: str, text: str) -> str:
        text = _normalize_text(text)
        if not text:
            return "Напиши или скажи голосом. " + RU_HINT

        user = self._get_user(tg_user_id)

        # 1) Язык: если русские буквы — запоминаем ru (но не блокируем)
        if _detect_ru(text) and user.get("lang") != "ru":
            self._set_user(tg_user_id, {"lang": "ru"})

        # 2) Если пользователь написал “по русски”
        if text.lower() in ["по русски", "по-русски", "русский", "ru"]:
            self._set_user(tg_user_id, {"lang": "ru"})
            return "Ок, по-русски. Что делаем: расход/доход, сводка или совет? 🙂"

        # 3) ЖЁСТКИЕ КОМАНДЫ “ПОКАЖИ …”
        if _is_show_intent(text) or _want_summary(text):
            start_dt, end_dt, p = _parse_period(text)

            docs = self._query_range(tg_user_id, start_dt, end_dt)

            # фильтр по типу
            want_exp = _want_expenses(text)
            want_inc = _want_income(text)

            items = []
            total_exp = 0.0
            total_inc = 0.0

            for d in docs:
                data = d.to_dict() or {}
                ttype = data.get("type")
                amt = float(data.get("amount") or 0)
                cur = data.get("currency") or user.get("currency") or "USD"
                cat = data.get("category") or "other"
                note = data.get("note") or ""
                created = data.get("created_at")
                dt = _ts_to_dt(created) if created else None
                ds = dt.strftime("%Y-%m-%d") if dt else "?"

                if want_exp and ttype != "expense":
                    continue
                if want_inc and ttype != "income":
                    continue

                if ttype == "expense":
                    total_exp += amt
                elif ttype == "income":
                    total_inc += amt

                # короткая строка записи
                label = "Расход" if ttype == "expense" else "Доход"
                line = f"{ds} • {label}: {amt:.2f} {cur} • {cat}"
                if note:
                    line += f" • {note}"
                items.append(line)

            # Если просили именно расходы — показываем расходы. Если доходы — доходы. Иначе сводка.
            if want_exp and not items:
                return "За выбранный период расходов нет."
            if want_inc and not items:
                return "За выбранный период доходов нет."

            title_map = {
                "today": "за сегодня",
                "yesterday": "за вчера",
                "week": "за 7 дней",
                "month": "за 30 дней",
            }
            title = title_map.get(p, "за период")

            # Сводка
            out = [f"📊 Сводка {title}:"]
            if not want_inc:  # если не только доходы
                out.append(f"• Расход: {total_exp:.2f} {user.get('currency') or 'USD'}")
            if not want_exp:  # если не только расходы
                out.append(f"• Доход: {total_inc:.2f} {user.get('currency') or 'USD'}")
            if not want_exp and not want_inc:
                out.append(f"• Итого: {(total_inc - total_exp):.2f} {user.get('currency') or 'USD'}")

            # Список записей (не бесконечный)
            if items:
                out.append("")
                out.append("🧾 Записи:")
                out.extend(items[-30:])  # последние 30 строк

            return "\n".join(out)

        # 4) ДОБАВИТЬ ЗАПИСЬ (расход/доход) — строго из текста, без “болтовни”
        amt = _parse_amount(text)
        if amt is not None:
            ttype = _guess_type(text)
            cat = _guess_category(text)
            cur = _parse_currency(text) or user.get("currency") or "USD"

            # если валюта ещё не сохранена — сохраняем, но НЕ блокируем работу
            if not user.get("currency"):
                self._set_user(tg_user_id, {"currency": cur})

            note = text
            # чуть почистим note: уберём число из начала, чтобы не было мусора
            note = re.sub(r"(?<!\d)(\d+(?:[.,]\d{1,2})?)(?!\d)", "", note, count=1).strip(" -:;,.")
            if len(note) > 120:
                note = note[:120]

            self._add_tx(tg_user_id, {
                "type": ttype,
                "amount": amt,
                "currency": cur,
                "category": cat,
                "note": note,
            })

            if ttype == "expense":
                return f"✅ Записал расход: {amt:.2f} {cur} • {cat}"
            else:
                return f"✅ Записал доход: {amt:.2f} {cur} • {cat}"

        # 5) Если не распознали как команду — “человеческий” ответ, но с направлением
        # (без тупых вопросов типа “дай мне данные”, потому что это блокнот)
        lang = user.get("lang") or ("ru" if _detect_ru(text) else "en")
        if lang == "ru":
            return (
                "Ок 🙂 Что делаем?\n"
                "1) Записать расход/доход (например: «кофе 5», «запиши 8 на долг»)\n"
                "2) Показать расходы/доходы (например: «покажи расходы за неделю»)\n"
                "3) Сводка (например: «сводка за месяц»)\n"
                "Напиши одним сообщением, что нужно."
            )

        return "Hi 🙂 What do you want to do: add expense/income, show summary, or get advice?"
