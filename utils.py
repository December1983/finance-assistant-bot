import re
from datetime import datetime, timezone

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def clean_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def detect_lang_ru(text: str) -> bool:
    # если есть кириллица — почти точно русский
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))

def parse_days(text: str) -> int | None:
    t = (text or "").lower()
    # 7 дней / неделя
    if "недел" in t or "week" in t:
        return 7
    if "месяц" in t or "month" in t:
        return 30
    m = re.search(r"(\d{1,3})\s*(дн|дня|дней|day|days)", t)
    if m:
        try:
            return max(1, min(365, int(m.group(1))))
        except:
            return None
    return None

def parse_amount(text: str) -> float | None:
    # ищем число: 8 / 8.5 / 8,5 / $8
    t = (text or "").replace(",", ".")
    m = re.search(r"(?<!\d)(\d{1,9}(?:\.\d{1,2})?)(?!\d)", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except:
        return None

def is_income_phrase(t: str) -> bool:
    t = t.lower()
    keys = ["доход", "income", "получил", "заработал", "paid", "got paid"]
    return any(k in t for k in keys)

def is_expense_phrase(t: str) -> bool:
    t = t.lower()
    keys = ["расход", "потрат", "spent", "expense", "купил", "buy", "coffee", "кофе", "бензин", "gas", "diesel"]
    return any(k in t for k in keys)

def is_debt_phrase(t: str) -> bool:
    t = t.lower()
    keys = ["долг", "debt", "на долг", "в долг", "loan"]
    return any(k in t for k in keys)

def is_pay_debt_phrase(t: str) -> bool:
    t = t.lower()
    keys = ["оплат", "погас", "вернул", "paid debt", "pay debt", "закрыл долг"]
    return any(k in t for k in keys)

def fmt_money(amount: float, currency: str) -> str:
    cur = (currency or "USD").upper().strip()
    symbol = "$" if cur == "USD" else (cur + " ")
    # 2 знака только если нужно
    if abs(amount - int(amount)) < 1e-9:
        return f"{symbol}{int(amount)}"
    return f"{symbol}{amount:.2f}"
