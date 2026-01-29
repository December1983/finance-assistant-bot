from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from openai import OpenAI

from router_llm import route_message
from storage import Storage

client = OpenAI()


# -----------------------------
# helpers
# -----------------------------
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_currency(cur: str) -> str:
    cur = (cur or "").strip().upper()
    # very small mapping (MVP)
    mapping = {
        "ДОЛЛАР": "USD",
        "ДОЛЛАРЫ": "USD",
        "USD": "USD",
        "EUR": "EUR",
        "ЕВРО": "EUR",
        "GBP": "GBP",
        "РУБ": "RUB",
        "RUB": "RUB",
        "ТЕНГЕ": "KZT",
        "KZT": "KZT",
    }
    return mapping.get(cur, cur if cur else "USD")


def _format_money(amount: float, cur: str) -> str:
    # no fancy locales
    if abs(amount - int(amount)) < 1e-9:
        return f"{cur} {int(amount)}"
    return f"{cur} {amount:.2f}"


def _period_to_range(period: str) -> Tuple[datetime, datetime, str]:
    now = _utc_now()
    end = now
    label = period

    period = (period or "").lower().strip()

    if period == "day":
        start = now - timedelta(days=1)
        label = "last day"
    elif period == "week":
        start = now - timedelta(days=7)
        label = "last week"
    elif period == "month":
        start = now - timedelta(days=30)
        label = "last month"
    elif period == "year":
        start = now - timedelta(days=365)
        label = "last year"
    else:
        # fallback: week
        start = now - timedelta(days=7)
        label = "last week"

    return start, end, label


# -----------------------------
# LLM render (multilingual replies)
# -----------------------------
RENDER_INSTRUCTIONS = """
You are the assistant that writes the final user-facing reply for a finance notebook bot.

You will be given:
- target_language (ISO code)
- action name (what happened)
- structured data (numbers, categories, dates)
Write a short, clear message in the target language.
No extra suggestions unless action is "smalltalk" or "unknown".
Do NOT invent numbers; only use given data.

Keep it concise.
"""

RENDER_SCHEMA = {
    "name": "render_reply",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string"},
        },
        "required": ["text"],
    },
}


def render_reply(language: str, action: str, data: Dict[str, Any]) -> str:
    payload = {
        "target_language": language,
        "action": action,
        "data": data,
    }
    resp = client.responses.create(
        model="gpt-4o-mini",
        instructions=RENDER_INSTRUCTIONS,
        input=str(payload),
        response_format={"type": "json_schema", "json_schema": RENDER_SCHEMA},
    )
    txt = (resp.output_text or "").strip()
    # output_text is already JSON text, parse quickly
    try:
        import json
        obj = json.loads(txt)
        return (obj.get("text") or "").strip() or "OK."
    except Exception:
        return "OK."


class Brain:
    def __init__(self, storage: Storage):
        self.storage = storage

    async def handle_message(self, user: Any, text: str) -> str:
        self.storage.ensure_user(user)

        settings = self.storage.get_user_settings(user.id)
        language_mode = (settings.get("language_mode") or "auto").strip().lower()
        language_fixed = settings.get("language_fixed")
        base_currency = settings.get("base_currency")

        # Router decides language + intent
        route = route_message(text)
        detected_lang = (route.get("language") or "en").strip()
        intent = route.get("intent") or "unknown"
        args = route.get("args") or {}
        needs_clar = bool(route.get("needs_clarification"))
        clar_q = route.get("clarifying_question") or ""

        # Choose reply language
        reply_lang = language_fixed if (language_mode == "fixed" and language_fixed) else detected_lang

        # Pending confirmations (delete_account)
        pending = self.storage.get_pending(user.id)
        if pending and pending.get("type") == "delete_account_wait_phrase":
            # user must send exact phrase
            if "DELETE MY ACCOUNT" in (text or ""):
                # check cooldown
                ok, until_dt = self.storage.can_delete_account_now(user.id)
                if not ok and until_dt:
                    remaining = until_dt - _utc_now()
                    hours = int(remaining.total_seconds() // 3600)
                    mins = int((remaining.total_seconds() % 3600) // 60)
                    self.storage.set_pending(user.id, None)
                    return render_reply(reply_lang, "delete_account_blocked", {"hours": hours, "minutes": mins})

                # set cooldown FIRST (survives deletion)
                self.storage.set_delete_cooldown(user.id, hours=24)

                # delete everything
                self.storage.delete_account_everything(user.id)

                # clear pending (doc is deleted anyway, but safe)
                return render_reply(reply_lang, "delete_account_done", {})

            else:
                # not confirmed -> keep pending, but remind
                return render_reply(reply_lang, "delete_account_need_phrase", {})

        # If base currency not set -> we prioritize asking/setting it
        if not base_currency:
            # If user message is a currency answer, router should return set_base_currency
            if intent != "set_base_currency":
                # ask again in the detected language
                return render_reply(reply_lang, "ask_base_currency", {})
            # else continue to handle set_base_currency below

        # Handle: set_language
        if intent == "set_language":
            # args: {language_mode: "auto"/"fixed", language: "de"/"ru"/"auto"}
            mode = (args.get("language_mode") or "").strip().lower()
            lang = (args.get("language") or "").strip()

            if mode == "auto" or lang.lower() == "auto":
                self.storage.set_language_fixed(user.id, None, "auto")
                return render_reply(detected_lang, "language_set_auto", {})

            # fixed
            if not lang:
                return render_reply(reply_lang, "need_language_name", {})

            self.storage.set_language_fixed(user.id, lang, "fixed")
            return render_reply(lang, "language_set_fixed", {"language": lang})

        # Handle: set_base_currency
        if intent == "set_base_currency":
            cur = _normalize_currency(args.get("currency") or args.get("base_currency") or text)
            if not cur or len(cur) < 3:
                return render_reply(reply_lang, "need_currency", {})
            self.storage.set_base_currency(user.id, cur)
            return render_reply(reply_lang, "base_currency_set", {"base_currency": cur})

        # Handle: delete_account (first step)
        if intent == "delete_account":
            confirm = bool((args.get("confirm") or False))
            if confirm:
                # If user already typed phrase but router marked confirm true:
                ok, until_dt = self.storage.can_delete_account_now(user.id)
                if not ok and until_dt:
                    remaining = until_dt - _utc_now()
                    hours = int(remaining.total_seconds() // 3600)
                    mins = int((remaining.total_seconds() % 3600) // 60)
                    return render_reply(reply_lang, "delete_account_blocked", {"hours": hours, "minutes": mins})

                self.storage.set_delete_cooldown(user.id, hours=24)
                self.storage.delete_account_everything(user.id)
                return render_reply(reply_lang, "delete_account_done", {})

            # ask for phrase and set pending
            self.storage.set_pending(user.id, {"type": "delete_account_wait_phrase"})
            return render_reply(reply_lang, "delete_account_confirm", {})

        # Clarification from router
        if needs_clar:
            return clar_q or render_reply(reply_lang, "clarify_generic", {})

        # Ensure base_currency after possible set
        settings = self.storage.get_user_settings(user.id)
        base_currency = settings.get("base_currency") or "USD"

        # Handle: log_expense / log_income
        if intent in ("log_expense", "log_income"):
            amt = args.get("amount")
            cur = _normalize_currency(args.get("currency") or base_currency)
            cat = (args.get("category") or "other").strip().lower()
            desc = (args.get("description") or args.get("note") or text).strip()

            if amt is None:
                return render_reply(reply_lang, "need_amount", {})

            # MVP currency conversion:
            # If currency differs from base, we store original and mark base conversion as None (not implemented yet).
            # Later we can plug in FX API and fill amount_in_base.
            amount_in_base = float(amt) if cur == base_currency else None

            payload = {
                "type": "expense" if intent == "log_expense" else "income",
                "amount": float(amt),
                "currency": cur,
                "base_currency": base_currency,
                "amount_in_base": amount_in_base,  # None if not base currency
                "category": cat,
                "description": desc,
                "language": reply_lang,
            }
            self.storage.add_transaction(user.id, payload)

            return render_reply(reply_lang, "logged", {
                "type": payload["type"],
                "amount": payload["amount"],
                "currency": payload["currency"],
                "category": payload["category"],
                "base_currency": base_currency,
                "has_conversion": (amount_in_base is not None),
            })

        # Handle: show_summary
        if intent == "show_summary":
            period = (args.get("period") or "week").strip().lower()
            start_dt, end_dt, label = _period_to_range(period)

            items = self.storage.query_by_ts_range(user.id, start_dt, end_dt)

            inc = 0.0
            exp = 0.0
            excluded = {}  # currency -> sum

            for tx in items:
                ttype = (tx.get("type") or "expense").lower()
                a_base = tx.get("amount_in_base")
                cur = tx.get("currency") or base_currency
                amt = float(tx.get("amount") or 0.0)

                if a_base is None:
                    excluded[cur] = excluded.get(cur, 0.0) + amt
                    continue

                if ttype == "income":
                    inc += float(a_base)
                else:
                    exp += float(a_base)

            net = inc - exp

            return render_reply(reply_lang, "summary", {
                "label": label,
                "base_currency": base_currency,
                "income": inc,
                "expense": exp,
                "net": net,
                "excluded": excluded,  # show that some amounts are not converted yet
            })

        # Handle: show_category
        if intent == "show_category":
            period = (args.get("period") or "week").strip().lower()
            cat = (args.get("category") or "other").strip().lower()
            kind = (args.get("kind") or "expense").strip().lower()  # expense/income/all

            start_dt, end_dt, label = _period_to_range(period)
            items = self.storage.query_by_ts_range(user.id, start_dt, end_dt)

            total = 0.0
            excluded = {}  # currency -> sum

            for tx in items:
                ttype = (tx.get("type") or "expense").lower()
                tx_cat = (tx.get("category") or "other").strip().lower()
                a_base = tx.get("amount_in_base")
                cur = tx.get("currency") or base_currency
                amt = float(tx.get("amount") or 0.0)

                if tx_cat != cat:
                    continue
                if kind in ("expense", "income") and ttype != kind:
                    continue

                if a_base is None:
                    excluded[cur] = excluded.get(cur, 0.0) + amt
                    continue

                total += float(a_base)

            return render_reply(reply_lang, "category_total", {
                "label": label,
                "category": cat,
                "kind": kind,
                "base_currency": base_currency,
                "total": total,
                "excluded": excluded,
            })

        # Handle: show_list
        if intent == "show_list":
            limit = int(args.get("limit") or 20)
            limit = max(1, min(limit, 50))
            items = self.storage.list_transactions(user.id, limit=limit)

            # We will pass raw data and ask LLM to format in the right language.
            # Keep minimal fields to avoid token bloat.
            simplified = []
            for tx in items:
                simplified.append({
                    "type": tx.get("type"),
                    "amount": tx.get("amount"),
                    "currency": tx.get("currency"),
                    "category": tx.get("category"),
                    "description": tx.get("description"),
                })

            return render_reply(reply_lang, "list", {"items": simplified, "limit": limit})

        # smalltalk / unknown
        if intent == "smalltalk":
            return render_reply(reply_lang, "smalltalk", {})
        if intent == "unknown":
            return render_reply(reply_lang, "unknown", {})

        # fallback
        return render_reply(reply_lang, "unknown", {})
