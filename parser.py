# main/parser.py
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Any, Tuple

MONEY_RE = re.compile(r"(?P<cur>\$)?\s*(?P<num>\d+(?:[.,]\d{1,2})?)")

INCOME_WORDS = [
    "пришло", "получил", "получила", "заработал", "заработала",
    "доход", "поступило", "оплатили", "перевели", "платеж пришел"
]
EXPENSE_WORDS = [
    "потратил", "потратила", "купил", "купила", "заплатил", "заплатила",
    "расход", "оплатил", "оплатила", "списали"
]

SHOW_WORDS = ["покажи", "показать", "сводка", "итоги", "сколько", "посчитай", "дай"]
DELETE_ALL_WORDS = ["удали всё", "удали все", "удалить всё", "удалить все", "очисти всё", "очисти все", "удалить аккаунт", "стереть всё", "стереть все"]

CATEGORY_HINTS = {
    "топливо": ["топливо", "бенз", "бензин", "дизел", "дизель", "fuel", "gas"],
    "еда": ["еда", "кафе", "бургер", "бургеры", "мак", "mcdonald", "restaurant", "ресторан", "coffee", "кофе"],
    "коммуналка": ["счет", "счёт", "bill", "water", "electric", "gas bill", "интернет", "phone", "телефон", "вода", "электр"],
    "кредит": ["кредит", "loan", "платеж", "платёж", "car payment", "машина в кредит"],
    "страховка": ["страхов", "insurance"],
}

@dataclass
class Intent:
    name: str
    payload: Dict[str, Any]
    needs_clarification: bool = False
    clarification_question: str = ""

def _today() -> date:
    return datetime.now().date()

def _extract_amount(text: str) -> Optional[float]:
    m = MONEY_RE.search(text)
    if not m:
        return None
    raw = m.group("num").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None

def _guess_type(text: str) -> str:
    t = text.lower()
    if any(w in t for w in INCOME_WORDS):
        return "income"
    if any(w in t for w in EXPENSE_WORDS):
        return "expense"
    # по умолчанию расход (как блокнот)
    return "expense"

def _guess_category(text: str) -> str:
    t = text.lower()
    for cat, hints in CATEGORY_HINTS.items():
        if any(h in t for h in hints):
            return cat
    # fallback: короткая “метка” по словам
    cleaned = re.sub(r"[\$]?\d+(?:[.,]\d{1,2})?", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned:
        words = cleaned.split(" ")
        # убираем служебные слова "покажи", "сколько" и т.п.
        stop = set(SHOW_WORDS + ["за", "на", "по", "в", "во", "этот", "эту", "эти", "прошлый", "прошлую", "прошлое"])
        words = [w for w in words if w.lower() not in stop]
        if words:
            return " ".join(words[:3])
    return "other"

def _parse_period(text: str) -> Optional[Tuple[date, date, str]]:
    """
    Возвращает (d1, d2, label)
    """
    t = text.lower()
    d0 = _today()

    if "сегодня" in t:
        return d0, d0, "сегодня"
    if "вчера" in t:
        d = d0 - timedelta(days=1)
        return d, d, "вчера"

    # неделя (последние 7 дней)
    if "за неделю" in t or "последнюю неделю" in t or "последние 7" in t or "за 7" in t:
        d1 = d0 - timedelta(days=6)
        return d1, d0, "за неделю"

    # месяц (последние 30 дней)
    if "за месяц" in t or "последний месяц" in t or "последние 30" in t or "за 30" in t:
        d1 = d0 - timedelta(days=29)
        return d1, d0, "за месяц"

    # конкретная дата YYYY-MM-DD
    m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", t)
    if m:
        y, mo, da = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            d = date(y, mo, da)
            return d, d, f"{y}-{mo:02d}-{da:02d}"
        except ValueError:
            return None

    # месяц YYYY-MM
    m2 = re.search(r"\b(20\d{2})-(\d{2})\b", t)
    if m2:
        y, mo = int(m2.group(1)), int(m2.group(2))
        try:
            d1 = date(y, mo, 1)
            if mo == 12:
                d2 = date(y, 12, 31)
            else:
                d2 = date(y, mo + 1, 1) - timedelta(days=1)
            return d1, d2, f"{y}-{mo:02d}"
        except ValueError:
            return None

    # если спросили "за год"
    if "за год" in t or "за этот год" in t:
        y = d0.year
        d1 = date(y, 1, 1)
        d2 = date(y, 12, 31)
        return d1, d2, f"{y} год"

    return None

def parse_intent(text: str) -> Intent:
    text = (text or "").strip()
    t = text.lower()

    # 1) delete all
    if any(w in t for w in DELETE_ALL_WORDS):
        return Intent(
            name="delete_all_request",
            payload={},
            needs_clarification=True,
            clarification_question="⚠️ Это удалит ВСЕ твои записи и настройки без восстановления. Напиши: «ДА, УДАЛИ ВСЁ» для подтверждения."
        )

    if "да, удали всё" in t or "да, удали все" in t:
        return Intent(name="delete_all_confirmed", payload={})

    # 2) show/summary
    if any(w in t for w in SHOW_WORDS):
        period = _parse_period(text)
        cat = _guess_category(text)
        payload = {"category": cat}
        if period:
            d1, d2, label = period
            payload.update({"date_from": d1, "date_to": d2, "period_label": label})
        else:
            # если период не сказан — по умолчанию неделя
            d1 = _today() - timedelta(days=6)
            d2 = _today()
            payload.update({"date_from": d1, "date_to": d2, "period_label": "за неделю"})
        return Intent(name="show_summary", payload=payload)

    # 3) transaction add (расход/доход)
    amount = _extract_amount(text)
    if amount is not None:
        # если человек прислал только "100" — неясно что это
        cleaned = re.sub(r"[\$]?\d+(?:[.,]\d{1,2})?", "", text).strip()
        if not cleaned:
            return Intent(
                name="clarify_transaction",
                payload={"amount": float(amount)},
                needs_clarification=True,
                clarification_question="Это расход или доход? Скажи одним словом: «расход» или «доход»."
            )

        tx_type = _guess_type(text)
        cat = _guess_category(text)
        return Intent(
            name="add_transaction",
            payload={
                "type": tx_type,
                "amount": float(amount),
                "currency": "USD",
                "category": cat,
                "note": text,
            }
        )

    # 4) unknown / offtopic or unclear
    return Intent(
        name="unknown",
        payload={},
        needs_clarification=False,
        clarification_question=""
    )
