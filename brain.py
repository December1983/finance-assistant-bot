from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone

from openai import OpenAI

import storage
from router_llm import call_router, normalize_currency, normalize_language_code


def _week_range_utc() -> Tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=7)
    return start, now


def _month_range_utc() -> Tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=30)
    return start, now


class Brain:
    def __init__(self, db, openai_client: OpenAI):
        self.db = db
        self.oa = openai_client

    def handle(self, user_id: int, username: Optional[str], first_name: Optional[str], text: str) -> str:
        user = storage.ensure_user(self.db, user_id, username, first_name)
        settings = storage.get_settings(user)
        pending = storage.get_pending(user)

        # 1) If pending expects something (e.g. currency for a record) we still must respond flexibly.
        # We pass pending into router so LLM can decide: handle pending OR just chat.
        try:
            route = call_router(self.oa, text, settings, pending)
        except Exception as e:
            # OpenAI down: fallback basic behavior
            return self._fallback_reply(settings, pending, text, e)

        intent = (route.get("intent") or "chat").strip()

        # 2) Handle setting changes
        set_obj = route.get("set") or {}
        if isinstance(set_obj, dict) and "language" in set_obj:
            lang = normalize_language_code(str(set_obj.get("language") or ""))
            if lang:
                storage.set_user_language(self.db, user_id, lang)
                storage.set_pending(self.db, user_id, None)
                return route.get("reply") or "✅ Language updated."
        if isinstance(set_obj, dict) and "currency" in set_obj:
            cur = normalize_currency(str(set_obj.get("currency") or ""))
            if cur:
                storage.set_user_currency(self.db, user_id, cur)
                # if we were waiting for currency for an event, try to save it now
                if pending and pending.get("type") == "need_currency_for_event":
                    ev = pending.get("data") or {}
                    ev["currency"] = cur
                    storage.add_event(
                        self.db,
                        user_id,
                        kind=ev.get("kind", "expense"),
                        amount=float(ev.get("amount") or 0),
                        currency=cur,
                        category=ev.get("category"),
                        note=ev.get("note"),
                        raw_text=ev.get("raw_text"),
                    )
                    storage.set_pending(self.db, user_id, None)
                    # confirm in same language via router reply or generic
                    return route.get("reply") or f"✅ Saved: {ev.get('category') or 'item'} {cur} {ev.get('amount')}"
                storage.set_pending(self.db, user_id, None)
                return route.get("reply") or "✅ Currency updated."

        # 3) Delete account
        if intent == "delete_account":
            can, remain = storage.can_delete_account(self.db, user_id)
            if not can:
                # remain in seconds
                hrs = max(0, remain // 3600)
                mins = max(0, (remain % 3600) // 60)
                return f"⛔ You can delete again in ~{hrs}h {mins}m."
            # require confirm from router
            if route.get("delete_confirm") is True:
                storage.wipe_user(self.db, user_id)
                return route.get("reply") or "✅ Account wiped."
            # no confirm yet
            storage.set_pending(self.db, user_id, {"type": "confirm_delete"})
            return route.get("reply") or "Are you sure? Say: delete everything."

        # 4) If pending confirm_delete and user says something else, we should not hang.
        if pending and pending.get("type") == "confirm_delete" and intent != "delete_account":
            # just clear pending and continue normally
            storage.set_pending(self.db, user_id, None)

        # 5) Record event
        if intent == "record_event":
            ev = route.get("event") or {}
            kind = ev.get("kind") or "expense"
            amount = ev.get("amount")
            category = ev.get("category")
            note = ev.get("note")
            currency = ev.get("currency") or (settings.get("currency") if settings else None)

            if amount is None:
                # bot must ask one question
                storage.set_pending(self.db, user_id, None)
                return route.get("reply") or "How much was it?"

            if not currency:
                # ask for currency but do NOT hang: set pending and reply with a question
                storage.set_pending(self.db, user_id, {
                    "type": "need_currency_for_event",
                    "data": {
                        "kind": kind,
                        "amount": float(amount),
                        "category": category,
                        "note": note,
                        "raw_text": text,
                    }
                })
                return route.get("reply") or "Which currency? (USD/EUR/...)"

            storage.add_event(
                self.db,
                user_id,
                kind=kind,
                amount=float(amount),
                currency=str(currency).upper(),
                category=category,
                note=note,
                raw_text=text,
            )
            storage.set_pending(self.db, user_id, None)
            return route.get("reply") or "✅ Saved."

        # 6) Summary
        if intent == "summary":
            cur = settings.get("currency") if settings else None
            if not cur:
                # ask currency but keep it flexible
                storage.set_pending(self.db, user_id, {"type": "need_currency_for_summary", "data": {"raw_text": text}})
                return route.get("reply") or "Which currency should I summarize in? (USD/EUR/...)"

            s = route.get("summary") or {}
            period = (s.get("period") or "week").lower()
            kind = (s.get("kind") or "all").lower()

            if period == "month":
                start, end = _month_range_utc()
            else:
                start, end = _week_range_utc()

            if kind == "expense":
                events = storage.list_events_range(self.db, user_id, start, end, kind="expense")
            elif kind == "income":
                events = storage.list_events_range(self.db, user_id, start, end, kind="income")
            else:
                events = storage.list_events_range(self.db, user_id, start, end, kind=None)

            inc, exp, total = storage.sum_events(events, cur)

            storage.set_pending(self.db, user_id, None)
            # reply text comes from router to keep language, but we ensure numbers
            base_reply = route.get("reply")
            if base_reply and "{INCOME}" in base_reply:
                return (base_reply
                        .replace("{INCOME}", f"{inc:.2f}")
                        .replace("{EXPENSE}", f"{exp:.2f}")
                        .replace("{TOTAL}", f"{total:.2f}")
                        .replace("{CUR}", cur))
            # default
            return f"📊 Summary ({period})\nIncome: {cur} {inc:.2f}\nExpense: {cur} {exp:.2f}\nTotal: {cur} {total:.2f}"

        # 7) Help/chat
        storage.set_pending(self.db, user_id, None if intent != "chat" else pending)
        return route.get("reply") or "What would you like to do?"

    def _fallback_reply(self, settings: Dict[str, Any], pending: Optional[Dict[str, Any]], text: str, e: Exception) -> str:
        # Always respond
        # If user is giving currency and we were waiting, accept it without OpenAI.
        cur = None
        if text:
            t = text.strip().upper()
            if len(t) == 3 and t.isalpha():
                cur = t

        if pending and pending.get("type") == "need_currency_for_event" and cur:
            storage.set_user_currency(self.db, pending.get("user_id", None) or 0, cur)  # not used
            return "✅ Currency saved. Repeat your record."

        # Generic fallback
        return "Сейчас не могу обратиться к OpenAI (ключ/лимит/сеть). Я в упрощённом режиме: могу записывать расходы/доходы простыми фразами и показывать сводку."
