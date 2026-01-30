from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Dict

from storage import Storage
from router_llm import route_message


@dataclass
class Brain:
    storage: Storage
    openai_client: Any

    async def handle(
        self,
        user_id: int,
        username: Optional[str],
        first_name: Optional[str],
        telegram_language_code: Optional[str],
        text: str,
    ) -> str:
        # 1) Ensure user exists
        user = self.storage.ensure_user(
            user_id=user_id,
            username=username,
            first_name=first_name,
            telegram_language_code=telegram_language_code,
        )
        self.storage.touch_user(user_id)

        t = (text or "").strip()
        if not t:
            return ""

        # 2) /start is not a blocker, just a friendly intro
        if t.lower() == "/start":
            return self._intro(user)

        # 3) Route everything through LLM (NO hard parser)
        prefs = {
            "language": user.get("language", "auto"),
            "base_currency": user.get("base_currency"),  # can be None
        }

        try:
            routed = route_message(
                openai_client=self.openai_client,
                prefs=prefs,
                user_text=t,
            )
        except Exception:
            # Never be silent
            lang = user.get("language") or telegram_language_code or "ru"
            return self._t(lang, "openai_down")

        # 4) Apply settings updates (language/currency) if LLM detected them
        if routed.get("language_set"):
            self.storage.set_user_language(user_id, routed["language_set"])
            user["language"] = routed["language_set"]

        if routed.get("base_currency_set"):
            self.storage.set_user_base_currency(user_id, routed["base_currency_set"])
            user["base_currency"] = routed["base_currency_set"]

        lang = (user.get("language") if user.get("language") != "auto" else routed.get("detected_language")) \
               or telegram_language_code or "ru"

        action = routed.get("action", "unknown")

        # 5) Actions

        if action == "greet":
            return routed.get("reply") or self._t(lang, "greet_menu")

        if action == "help":
            return routed.get("reply") or self._t(lang, "help")

        if action == "offtopic":
            # Answer politely, but steer back
            return routed.get("reply") or self._t(lang, "offtopic")

        if action == "set_language":
            return routed.get("reply") or self._t(lang, "ok")

        if action == "set_currency":
            return routed.get("reply") or self._t(lang, "ok")

        if action == "delete_account":
            ok, wait_seconds = self.storage.can_delete_account(user_id)
            if not ok:
                hours = max(1, int(wait_seconds // 3600))
                return self._t(lang, "delete_cooldown").format(hours=hours)
            self.storage.delete_user_everything(user_id)
            return self._t(lang, "deleted")

        if action == "add_transaction":
            tx = routed.get("transaction") or {}
            tx_type = tx.get("type")
            amount = tx.get("amount")
            category = tx.get("category") or "other"
            note = tx.get("note") or ""

            # If amount present but currency missing -> ask currency NOW (not blocking everything)
            if amount is not None and not user.get("base_currency"):
                return self._t(lang, "need_currency_for_amount")

            if tx_type not in ("expense", "income"):
                return routed.get("reply") or self._t(lang, "need_expense_or_income")

            if amount is None:
                return routed.get("reply") or self._t(lang, "need_amount")

            self.storage.add_transaction(
                user_id=user_id,
                tx_type=tx_type,
                amount=float(amount),
                currency=user.get("base_currency") or "USD",
                category=category,
                note=note,
                original_text=t,
            )
            return routed.get("reply") or self._t(lang, "saved").format(
                kind=("расход" if tx_type == "expense" else "доход"),
                amount=amount,
                currency=(user.get("base_currency") or "USD"),
                category=category
            )

        if action == "query_summary":
            if not user.get("base_currency"):
                return self._t(lang, "need_currency_for_summary")

            period = routed.get("period") or {"type": "week"}
            summary = self.storage.compute_summary(user_id=user_id, period=period)
            return self._format_summary(summary, user["base_currency"], lang)

        if action == "query_list":
            if not user.get("base_currency"):
                return self._t(lang, "need_currency_for_summary")

            period = routed.get("period") or {"type": "week"}
            items = self.storage.list_transactions(user_id=user_id, period=period, limit=50)
            return self._format_list(items, user["base_currency"], lang)

        if action == "advice":
            # Advice doesn't strictly require currency; it uses what we have
            return routed.get("reply") or self._t(lang, "advice_fallback")

        # 6) Fallback: always answer something sensible
        return routed.get("reply") or self._t(lang, "fallback")

    def _intro(self, user: Dict[str, Any]) -> str:
        lang = user.get("language") if user.get("language") != "auto" else (user.get("telegram_language_code") or "ru")
        return self._t(lang, "intro")

    def _format_summary(self, summary: Dict[str, Any], cur: str, lang: str) -> str:
        income = summary.get("income", 0)
        expense = summary.get("expense", 0)
        net = summary.get("net", 0)
        tops = summary.get("top_expense_categories", [])

        lines = [
            self._t(lang, "summary_title"),
            self._t(lang, "summary_income").format(cur=cur, val=income),
            self._t(lang, "summary_expense").format(cur=cur, val=expense),
            self._t(lang, "summary_net").format(cur=cur, val=net),
        ]
        if tops:
            lines.append("")
            lines.append(self._t(lang, "summary_top"))
            for t in tops[:5]:
                lines.append(f"• {t['category']}: {cur} {t['amount']}")
        return "\n".join(lines)

    def _format_list(self, items: list, cur: str, lang: str) -> str:
        if not items:
            return self._t(lang, "no_items")
        lines = [self._t(lang, "list_title")]
        for it in items[:20]:
            t = it.get("type", "")
            amt = it.get("amount", 0)
            cat = it.get("category", "other")
            note = it.get("note", "")
            sign = "-" if t == "expense" else "+"
            lines.append(f"{sign}{cur} {amt} • {cat}" + (f" • {note}" if note else ""))
        return "\n".join(lines)

    def _t(self, lang: str, key: str) -> str:
        # Minimal i18n: Russian default + fallback to EN
        # (LLM will generate replies in user's language anyway; this is for safety/fallback.)
        lang = (lang or "ru").lower()

        RU = {
            "intro": "Привет 🙂 Я твоя финансовая записная книжка. Пиши или говори как хочешь: «кофе 5», «пришло 1200», «сколько потратил на прошлой неделе», «удали аккаунт».",
            "greet_menu": "Привет 🙂 Что делаем?\n• записать расход/доход\n• показать сводку\n• показать список операций\n• напоминания (скоро)\n• совет по цели/покупке",
            "help": "Можно писать свободно:\n• «кофе 5»\n• «заправка 70»\n• «пришло 1200»\n• «мои расходы за неделю»\n• «покажи список за месяц»\n• «удали аккаунт»",
            "offtopic": "Я отвечаю только в рамках финансовой темы 🙂 Можешь записать расход/доход или попросить сводку.",
            "fallback": "Ок. Ты хочешь записать расход/доход, посмотреть сводку, или спросить совет?",
            "ok": "Ок.",
            "openai_down": "Сейчас не могу обратиться к OpenAI (ключ/лимит/сеть). Проверь OPENAI_API_KEY и баланс.",
            "need_currency_for_amount": "Ок, записать могу. Скажи, пожалуйста, базовую валюту для сумм (например: USD, EUR, GBP).",
            "need_currency_for_summary": "Чтобы посчитать сводку, мне нужна базовая валюта (например: USD). Скажи валюту одним словом.",
            "need_expense_or_income": "Это расход или доход?",
            "need_amount": "Какая сумма?",
            "saved": "✅ Записал: {kind} {currency} {amount} • {category}",
            "summary_title": "📊 Сводка",
            "summary_income": "Доход: {cur} {val}",
            "summary_expense": "Расход: {cur} {val}",
            "summary_net": "Итого: {cur} {val}",
            "summary_top": "Топ расходов:",
            "no_items": "За этот период записей нет.",
            "list_title": "🧾 Последние записи:",
            "advice_fallback": "Ок. Скажи цель (что купить/на что накопить) и срок — я прикину план.",
            "delete_cooldown": "Удалять аккаунт можно не чаще 1 раза в 24 часа. Попробуй примерно через {hours} ч.",
            "deleted": "✅ Готово. Все данные удалены.",
        }

        EN = {
            "intro": "Hi 🙂 I'm your finance notebook. Talk naturally: “coffee 5”, “got paid 1200”, “my expenses last week”, “delete account”.",
            "greet_menu": "Hi 🙂 What do you want to do?\n• add expense/income\n• show summary\n• show transactions list\n• reminders (soon)\n• advice for a goal/purchase",
            "help": "You can type freely:\n• “coffee 5”\n• “gas 70”\n• “got paid 1200”\n• “my expenses last week”\n• “show list for month”\n• “delete account”",
            "offtopic": "I only answer within finance context 🙂 Add an expense/income or ask for a summary.",
            "fallback": "Ok. Do you want to add an expense/income, see a summary, or get advice?",
            "ok": "Ok.",
            "openai_down": "I can't reach OpenAI right now (key/limit/network). Check OPENAI_API_KEY and balance.",
            "need_currency_for_amount": "Ok. What base currency should I use? (e.g., USD, EUR, GBP)",
            "need_currency_for_summary": "To calculate summaries I need a base currency (e.g., USD). Send the currency code.",
            "need_expense_or_income": "Is it an expense or income?",
            "need_amount": "What amount?",
            "saved": "✅ Saved: {kind} {currency} {amount} • {category}",
            "summary_title": "📊 Summary",
            "summary_income": "Income: {cur} {val}",
            "summary_expense": "Expense: {cur} {val}",
            "summary_net": "Net: {cur} {val}",
            "summary_top": "Top expenses:",
            "no_items": "No entries for this period.",
            "list_title": "🧾 Recent entries:",
            "advice_fallback": "Ok. Tell me your goal and timeline — I’ll estimate a plan.",
            "delete_cooldown": "You can delete your account at most once per 24 hours. Try again in about {hours}h.",
            "deleted": "✅ Done. All your data was deleted.",
        }

        # Choose pack
        pack = RU if lang.startswith("ru") else EN
        return pack.get(key, RU.get(key, ""))
