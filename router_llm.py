import json
from openai import OpenAI

client = OpenAI()

ROUTER_INSTRUCTIONS = """
You are the router for a conversational finance notebook bot.
Return ONLY valid JSON that matches the schema. No extra text.

The user can write in any language.
You must:
1) Detect the language of the user message (ISO code like "ru", "en", "de", "es", "pt-BR"...).
2) Decide exactly ONE intent.
3) Extract structured fields (amount, currency, category, period, dates, etc.) when possible.

Intents:
- set_base_currency: user sets preferred base currency (USD/EUR/etc.)
- set_language: user explicitly requests to switch language or return to auto mode
- log_expense: user reports spending
- log_income: user reports income
- show_summary: totals for a period (day/week/month/year/custom)
- show_category: totals by category for a period
- show_list: list last N records / records in period
- delete_account: wipe ALL user data (requires confirmation phrase)
- smalltalk: user message not about finance
- unknown: cannot classify

Rules:
- Never invent amounts/dates. If unclear, set needs_clarification=true with ONE short question.
- Default currency: null if not specified (the app will fill base currency).
- Category should be a short key: food, fuel, car, rent, utilities, insurance, phone, health, shopping, subscriptions, entertainment, debt, other.
- delete_account: if user is requesting deletion but not confirming, set args.confirm=false and ask for confirmation phrase:
  "DELETE MY ACCOUNT" (exact).
  If the user message contains that exact phrase, set args.confirm=true.
"""

JSON_SCHEMA = {
    "name": "finance_router",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "language": {"type": "string"},
            "intent": {
                "type": "string",
                "enum": [
                    "set_base_currency",
                    "set_language",
                    "log_expense",
                    "log_income",
                    "show_summary",
                    "show_category",
                    "show_list",
                    "delete_account",
                    "smalltalk",
                    "unknown",
                ],
            },
            "args": {"type": "object"},
            "needs_clarification": {"type": "boolean"},
            "clarifying_question": {"type": "string"},
        },
        "required": ["language", "intent", "args", "needs_clarification", "clarifying_question"],
    },
}


def route_message(user_text: str) -> dict:
    resp = client.responses.create(
        model="gpt-4o-mini",
        instructions=ROUTER_INSTRUCTIONS,
        input=user_text,
        response_format={"type": "json_schema", "json_schema": JSON_SCHEMA},
    )
    raw = resp.output_text or "{}"
    try:
        obj = json.loads(raw)
        # basic safety
        if not isinstance(obj, dict):
            return _fallback()
        for k in ["language", "intent", "args", "needs_clarification", "clarifying_question"]:
            if k not in obj:
                return _fallback()
        if not isinstance(obj["args"], dict):
            obj["args"] = {}
        return obj
    except Exception:
        return _fallback()


def _fallback() -> dict:
    return {
        "language": "en",
        "intent": "unknown",
        "args": {},
        "needs_clarification": True,
        "clarifying_question": "I couldn't understand. Please rephrase in one sentence.",
    }
