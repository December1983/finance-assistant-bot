import json
from typing import Any, Dict, Optional


SYSTEM_INSTRUCTIONS = """You are a finance-notebook assistant inside a Telegram bot.
Rules:
- Always respond in the same language as the user's message.
- Do NOT engage in long off-topic chats. If user is off-topic, answer briefly and steer back to finance tasks.
- Be flexible like a human assistant. Never "block" conversation just because settings are missing.
- Only ask for missing base currency when user wants calculations or to save an amount.
- Output MUST be valid JSON following the schema below. No markdown.

Schema (JSON object):
{
  "action": "greet|help|offtopic|add_transaction|query_summary|query_list|advice|set_language|set_currency|delete_account|unknown",
  "reply": "string (assistant reply to user, same language as user)",
  "detected_language": "string (e.g., ru, en, de, es, ...)",
  "language_set": "string|null (if user asked to change language)",
  "base_currency_set": "string|null (if user set currency like USD)",
  "transaction": {
     "type": "expense|income",
     "amount": number|null,
     "category": "string|null",
     "note": "string|null"
  },
  "period": {
     "type": "day|week|month|year|custom",
     "from": "YYYY-MM-DD|null",
     "to": "YYYY-MM-DD|null"
  }
}

Notes:
- For add_transaction: if user says "coffee 5" => expense, amount 5, category "coffee" or "food".
- If user explicitly says income ("got paid", "salary", "пришло") => income.
- For queries like "my expenses last week" => query_summary OR query_list depending on wording. Summary by default.
- If user says "show list" => query_list.
- If user says "delete my account" / "удали аккаунт/всё" => delete_account.
- If user says "change language to ..." => set_language and language_set.
- If user says "currency USD" => set_currency and base_currency_set.
"""


def route_message(openai_client: Any, prefs: Dict[str, Any], user_text: str) -> Dict[str, Any]:
    # We keep it cheap: one call.
    # If OpenAI fails, caller will fallback.

    resp = openai_client.responses.create(
        model="gpt-4o-mini",
        instructions=SYSTEM_INSTRUCTIONS,
        input=f"User prefs: {json.dumps(prefs, ensure_ascii=False)}\nUser message: {user_text}",
        # JSON mode: enforce structured output
        response_format={"type": "json_object"},
    )

    txt = getattr(resp, "output_text", "") or ""
    txt = txt.strip()

    data: Dict[str, Any] = {}
    try:
        data = json.loads(txt)
    except Exception:
        # Hard fallback if model returned non-JSON
        data = {
            "action": "unknown",
            "reply": "",
            "detected_language": None,
            "language_set": None,
            "base_currency_set": None,
            "transaction": {"type": None, "amount": None, "category": None, "note": None},
            "period": {"type": None, "from": None, "to": None},
        }

    # Normalize fields
    data.setdefault("action", "unknown")
    data.setdefault("reply", "")
    data.setdefault("detected_language", None)
    data.setdefault("language_set", None)
    data.setdefault("base_currency_set", None)
    data.setdefault("transaction", {"type": None, "amount": None, "category": None, "note": None})
    data.setdefault("period", {"type": None, "from": None, "to": None})

    return data
