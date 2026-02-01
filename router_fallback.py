from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from utils import clean_text, parse_days, parse_amount, is_income_phrase, is_expense_phrase, is_debt_phrase, is_pay_debt_phrase

Intent = Literal["LOG", "SHOW", "SUMMARY", "ADVICE", "DELETE_DATA", "HELP", "UNKNOWN"]

@dataclass
class Route:
    intent: Intent
    kind: str | None = None
    amount: float | None = None
    days: int | None = None
    note: str | None = None

class FallbackRouter:
    def route(self, text: str) -> Route:
        t = clean_text(text)
        tl = t.lower()

        # HELP / greetings
        if any(x in tl for x in ["что ты умеешь", "помощь", "help", "команды", "что можешь", "как дела", "привет", "hello", "hi", "ку", "куку", "ау"]):
            return Route(intent="HELP")

        # DELETE
        if any(x in tl for x in ["удали", "удалить", "стер", "сотри", "delete", "remove", "очисти", "wipe", "erase"]) and any(x in tl for x in ["данные", "всё", "аккаунт", "account", "data", "history"]):
            return Route(intent="DELETE_DATA")

        # SHOW / SUMMARY
        if any(x in tl for x in ["покажи", "показать", "show", "list", "выведи", "посмотреть", "какие расходы", "какие доходы"]):
            days = parse_days(t) or 7
            return Route(intent="SHOW", days=days)

        if any(x in tl for x in ["сводка", "итого", "итоги", "summary", "total", "отчет", "отчёт"]):
            days = parse_days(t) or 7
            return Route(intent="SUMMARY", days=days)

        # ADVICE
        if any(x in tl for x in ["совет", "как экономить", "как сэкономить", "подскажи", "advice", "tips", "бюджет"]):
            return Route(intent="ADVICE")

        # LOG
        amt = parse_amount(t)
        if amt is not None:
            # определяем тип
            if is_pay_debt_phrase(t):
                return Route(intent="LOG", kind="pay_debt", amount=amt, note=t)
            if is_debt_phrase(t):
                return Route(intent="LOG", kind="debt", amount=amt, note=t)
            if is_income_phrase(t):
                return Route(intent="LOG", kind="income", amount=amt, note=t)
            if is_expense_phrase(t):
                return Route(intent="LOG", kind="expense", amount=amt, note=t)
            # если просто "кофе 5" — это расход
            return Route(intent="LOG", kind="expense", amount=amt, note=t)

        return Route(intent="UNKNOWN")
