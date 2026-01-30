from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from router_llm import route_message
from storage import Storage


@dataclass
class Brain:
    storage: Storage
    openai_client: Any

    async def handle(self, user_id: int, username: Optional[str], first_name: Optional[str], text: str) -> str:
        # Ensure user exists
        user = self.storage.ensure_user(user_id=user_id, username=username, first_name=first_name)

        # /start is treated like "hello + setup check"
        if text.strip().lower() == "/start":
            return self._welcome(user)

        # Route via LLM
        prefs = {
            "language": user.get("language", "auto"),
            "base_currency": user.get("base_currency"),
        }

        routed = route_message(
            openai_client=self.openai_client,
            prefs=prefs,
            user_text=text,
        )

        action = routed.get("action", "unknown")

        # If language is auto and router guessed a language confidently, save it
        if user.get("language", "auto") == "auto" and routed.get("language_set"):
            self.storage.set_user_language(user_id, routed["language_set"])
            user["language"] = routed["language_set"]

        # If base currency missing and router extracted, save it
        if not user.get("base_currency") and routed.get("base_currency_set"):
            self.storage.set_user_base_currency(user_id, routed["base_currency_set"])
            user["base_currency"] = routed["base_currency_set"]

        # Update last active
        self.storage.touch_user(user_id)

        # Handle actions
        if action == "greet":
            # If missing prefs, ask minimal questions
            if not user.get("base_currency"):
                return routed.get("reply") or self._ask_base_currency(user)
            return routed.get("reply") or self._small_help(user)

        if action == "set_language":
            lang = routed.get("language_set")
            if lang:
                self.storage.set_user_language(user_id, lang)
                user["language"] = lang
            return routed.get("reply") or "OK."

        if action == "set_currency":
            cur = routed.get("base_currency_set")
            if cur:
                self.storage.set_user_base_currency(user_id, cur)
                user["base_currency"] = cur
            return routed.get("reply") or "OK."

        if action == "delete_account":
            # 24h limit
            ok, wait_seconds = self.storage.can_delete_account(user_id=user_id)
            if not ok:
                # use router reply if exists, else default
                if routed.get("reply"):
                    return routed["reply"]
                hours = max(1, int(wait_seconds // 3600))
                return f"You can delete your account again in about {hours} hours."

            self.storage.delete_user_everything(user_id=user_id)
            return routed.get("reply") or "Deleted. Your account data has been fully removed."

        if action == "add_transaction":
            # Need base currency
            if not user.get("base_currency"):
                return self._ask_base_currency(user)

            tx = routed.get("transaction") or {}
            tx_type = tx.get("type")  # "expense" | "income"
            amount = tx.get("amount")
            category = tx.get("category") or "other"
            note = tx.get("note") or ""

            if tx_type not in ("expense", "income") or amount is None:
                # Ask clarification (LLM already can produce it)
                return routed.get("reply") or self._clarify(user)

            # store
            self.storage.add_transaction(
                user_id=user_id,
                tx_type=tx_type,
                amount=float(amount),
                currency=user["base_currency"],
                category=category,
                note=note,
                original_text=text,
            )

            return routed.get("reply") or "Saved."

        if action == "query_summary":
            if not user.get("base_currency"):
                return self._ask_base_currency(user)

            period = routed.get("period") or {"type": "week"}
            summary = self.storage.compute_summary(user_id=user_id, period=period)

            # Use LLM to format summary nicely in the user's language
            reply = self._format_summary_via_llm(user, summary, period)
            return reply

        if action == "query_list":
            if not user.get("base_currency"):
                return self._ask_base_currency(user)

            period = routed.get("period") or {"type": "week"}
            items = self.storage.list_transactions(user_id=user_id, period=period, limit=50)
            reply = self._format_list_via_llm(user, items, period)
            return reply

        if action == "add_reminder":
            # Minimal: store reminder, job will deliver
            reminder = routed.get("reminder") or {}
            when_iso = reminder.get("when_iso")
            rtext = reminder.get("text")

            if not when_iso or not rtext:
                return routed.get("reply") or self._clarify(user)

            self.storage.add_reminder(
                user_id=user_id,
                when_iso=when_iso,
                text=rtext,
                language=user.get("language", "auto"),
            )
            return routed.get("reply") or "OK, reminder saved."

        if action == "help":
            return routed.get("reply") or self._small_help(user)

        if action == "offtopic":
            return routed.get("reply") or self._offtopic(user)

        # unknown fallback
        return routed.get("reply") or self._small_help(user)

    def _welcome(self, user: Dict[str, Any]) -> str:
        # If base currency missing, ask it. Language can be auto.
        if not user.get("base_currency"):
            return self._ask_base_currency(user)
        return self._small_help(user)

    def _ask_base_currency(self, user: Dict[str, Any]) -> str:
        # Keep it short; router will keep language most of time
        return "What base currency do you want for all summaries? (e.g., USD, EUR, GBP)"

    def _small_help(self, user: Dict[str, Any]) -> str:
        return (
            "Tell me naturally (text or voice).\n"
            "Examples:\n"
            "• coffee 5\n"
            "• got paid 1200\n"
            "• my expenses last week\n"
            "• show list for January\n"
            "• remind me to pay credit in 3 days\n"
            "• delete my account"
        )

    def _offtopic(self, user: Dict[str, Any]) -> str:
        return "I’m your finance notebook. Tell me a transaction, ask for a summary, or set a reminder."

    def _clarify(self, user: Dict[str, Any]) -> str:
        return "I’m not sure. Is that an expense or income, and what amount?"

    def _format_summary_via_llm(self, user: Dict[str, Any], summary: Dict[str, Any], period: Dict[str, Any]) -> str:
        lang = user.get("language", "auto")
        base = user.get("base_currency", "USD")

        prompt = (
            "You are a finance notebook inside a chat app. "
            "Reply in the user's language. Stay in-app (no off-topic). "
            "Format clean and short.\n\n"
            f"BASE_CURRENCY: {base}\n"
            f"PERIOD: {period}\n"
            f"SUMMARY_JSON: {summary}\n\n"
            "Return a short readable summary with totals and top categories. "
            "If there is no data, say that clearly."
        )

        resp = self.openai_client.responses.create(
            model="gpt-4o-mini",
            input=prompt,
        )
        out = getattr(resp, "output_text", "") or ""
        return out.strip() or "No data."

    def _format_list_via_llm(self, user: Dict[str, Any], items: list, period: Dict[str, Any]) -> str:
        base = user.get("base_currency", "USD")
        prompt = (
            "You are a finance notebook inside a chat app. "
            "Reply in the user's language. Stay in-app (no off-topic). "
            "Show a compact list.\n\n"
            f"BASE_CURRENCY: {base}\n"
            f"PERIOD: {period}\n"
            f"TX_LIST_JSON: {items}\n\n"
            "Return up to 20 lines: date, category, amount, short note. "
            "If empty, say no transactions found."
        )
        resp = self.openai_client.responses.create(
            model="gpt-4o-mini",
            input=prompt,
        )
        out = getattr(resp, "output_text", "") or ""
        return out.strip() or "No transactions found."
