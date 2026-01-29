# main/brain.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Optional, Tuple

from rules import should_answer_offtopic, normalize_text
from parser import parse_intent, Intent
from storage import Storage


def fmt_money(x: float) -> str:
    if abs(x - int(x)) < 1e-9:
        return f"${int(x)}"
    return f"${x:.2f}"


def confirm_text(tx: Dict[str, Any]) -> str:
    # Вариант A — коротко
    label = tx.get("category") or "other"
    amt = float(tx.get("amount") or 0.0)
    ttype = tx.get("type") or "expense"
    if ttype == "income":
        return f"✅ Записал: {label} {fmt_money(amt)} (доход)"
    return f"✅ Записал: {label} {fmt_money(amt)}"


def summary_text(payload: Dict[str, Any], result: Dict[str, Any]) -> str:
    label = payload.get("period_label", "")
    cat = payload.get("category", "all")

    income = result["income"]
    expense = result["expense"]
    net = result["net"]
    top = result["top_categories"]

    header = "📊 Сводка"
    if label:
        header += f" {label}"
    if cat and cat != "all" and cat != "other":
        header += f" • {cat}"

    lines = [
        header,
        f"Доход: {fmt_money(income)}",
        f"Расход: {fmt_money(expense)}",
        f"Итого: {fmt_money(net)}",
    ]

    if top:
        lines.append("Топ категорий:")
        for c, s in top:
            lines.append(f"- {c}: {fmt_money(s)}")

    return "\n".join(lines)


class Brain:
    def __init__(self, storage: Storage):
        self.storage = storage

    def _parse_yes_no(self, text: str) -> Optional[bool]:
        t = text.lower().strip()
        if t in ["да", "ага", "yes", "y"]:
            return True
        if t in ["нет", "no", "n"]:
            return False
        return None

    async def handle(self, user: Any, text: str) -> Tuple[str, bool]:
        """
        Returns: (reply_text, did_write_anything)
        """
        text = normalize_text(text)

        # 0) Ensure user exists
        self.storage.ensure_user(user)

        # 1) Offtopic / too long
        block, reply = should_answer_offtopic(text)
        if block:
            return reply, False

        # 2) Pending clarification?
        pending = self.storage.get_pending(user.id)
        if pending:
            ptype = pending.get("type")

            # 2.1) pending: clarify expense/income for "100"
            if ptype == "clarify_tx_type":
                yn = None
                t = text.lower()
                if "расход" in t:
                    chosen = "expense"
                elif "доход" in t:
                    chosen = "income"
                else:
                    # всё ещё неясно — просим ещё раз (но коротко)
                    return "Скажи одним словом: «расход» или «доход».", False

                tx = pending.get("tx") or {}
                tx["type"] = chosen
                # category мог быть unknown — оставим "other"
                if not tx.get("category"):
                    tx["category"] = "other"

                self.storage.add_transaction(user.id, tx)
                self.storage.set_pending(user.id, None)
                return confirm_text(tx), True

            # 2.2) pending: delete all confirmation
            if ptype == "delete_all_confirm":
                # требуем точную фразу, чтобы не удалить случайно
                if text.lower().strip() in ["да, удали всё", "да, удали все", "да удали всё", "да удали все"]:
                    self.storage.delete_all_user_data(user.id)
                    return "🗑️ Готово. Я полностью удалил твой аккаунт и все записи.", True
                else:
                    self.storage.set_pending(user.id, None)
                    return "Ок, не удаляю. Продолжаем.", False

        # 3) Parse intent
        intent: Intent = parse_intent(text)

        # 3.1) delete all request
        if intent.name == "delete_all_request":
            self.storage.set_pending(user.id, {"type": "delete_all_confirm"})
            return intent.clarification_question, False

        if intent.name == "delete_all_confirmed":
            # на случай, если парсер поймал прямое подтверждение без pending
            self.storage.delete_all_user_data(user.id)
            return "🗑️ Готово. Я полностью удалил твой аккаунт и все записи.", True

        # 3.2) show summary
        if intent.name == "show_summary":
            d1: date = intent.payload["date_from"]
            d2: date = intent.payload["date_to"]
            cat = intent.payload.get("category", "all")

            result = self.storage.summarize(user.id, d1, d2, category=cat)
            return summary_text(intent.payload, result), False

        # 3.3) add transaction
        if intent.name == "add_transaction":
            tx = dict(intent.payload)

            # если категория получилась "other" и текст слишком общий — уточним (без записи)
            # Пример "вода 100" — двусмысленно: покупка или water bill.
            # Здесь делаем минимальную эвристику:
            note = (tx.get("note") or "").lower()
            cat = (tx.get("category") or "other").lower()

            ambiguous = False
            if "вода" in note and cat in ["вода", "other", "коммуналка"]:
                ambiguous = True

            if ambiguous:
                # НЕ записываем. Уточняем.
                # Храним pending с исходной транзакцией (без записи).
                self.storage.set_pending(user.id, {
                    "type": "clarify_water_100",
                    "raw": tx,
                })
                return "Ты про «воду» как покупку (бутылки) или счёт за воду (water bill)?", False

            # всё ок — записываем
            self.storage.add_transaction(user.id, tx)
            return confirm_text(tx), True

        # 3.4) clarify transaction (например только "100")
        if intent.name == "clarify_transaction":
            amount = intent.payload.get("amount")
            tx = {
                "type": None,
                "amount": float(amount),
                "currency": "USD",
                "category": "other",
                "note": text,
            }
            self.storage.set_pending(user.id, {"type": "clarify_tx_type", "tx": tx})
            return intent.clarification_question, False

        # 4) Unknown
        return (
            "Я не понял, это запись или вопрос.\n"
            "Примеры:\n"
            "• «кофе 5»\n"
            "• «пришло 450»\n"
            "• «покажи за неделю»\n"
            "• «удали всё»",
            False
        )

