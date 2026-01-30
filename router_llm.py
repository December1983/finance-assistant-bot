import re
from typing import Any, Dict, Optional

from openai import OpenAI


SYSTEM_INSTRUCTIONS = """
You are a conversational finance notebook bot inside Telegram.

Goals:
- Always respond (never hang).
- Operate within finance notebook scope: record income/expense, show summaries, reminders, advice.
- If user is off-topic, gently redirect to finance.

Language:
- Reply in the same language as the user.
- If user asks to change language, comply and confirm.

Behavior:
- If user greets: greet back and ask what they want to do.
- If user message can be a finance record or query, decide and act.

IMPORTANT:
Return ONLY valid JSON object.
No markdown.
"""

# Router output schema:
# {
#   "intent": "chat"|"record_event"|"summary"|"set_language"|"set_currency"|"delete_account"|"help",
#   "reply": "string",
#   "event": {"kind":"expense|income","amount":123.45,"currency":null|"...","category":null|"...","note":null|"..."} | null,
#   "summary": {"period":"week|month|day|custom","kind":"all|expense|income","category":null|"..."} | null,
#   "set": {"language": "ru|en|de|..."} | {"currency":"USD"} | null,
#   "delete_confirm": true|false
# }

def call_router(
    client: OpenAI,
    user_text: str,
    user_settings: Dict[str, Any],
    pending: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    lang = (user_settings or {}).get("language")
    currency = (user_settings or {}).get("currency")

    context = {
        "known_language": lang,
        "known_currency": currency,
        "pending": pending,
    }

    resp = client.responses.create(
        model="gpt-4o-mini",
        instructions=SYSTEM_INSTRUCTIONS,
        input=[
            {"role": "user", "content": f"USER_SETTINGS_AND_STATE: {context}\n\nUSER_MESSAGE: {user_text}"}
        ],
        response_format={"type": "json_object"},
    )
    txt = getattr(resp, "output_text", None)
    if not txt:
        return {"intent": "help", "reply": "I had trouble. Try again.", "event": None, "summary": None, "set": None}

    # safety: ensure dict
    import json
    try:
        data = json.loads(txt)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return {"intent": "help", "reply": "I had trouble. Try again.", "event": None, "summary": None, "set": None}


def normalize_currency(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip().upper()
    if re.fullmatch(r"[A-Z]{3}", s):
        return s
    return None


def normalize_language_code(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip().lower()
    # accept things like "ru", "en", "de", "es", "fr", "pt-br"
    if re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", s):
        return s
    return None
