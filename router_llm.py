import json
import re
from typing import Any, Dict, Optional


PERIOD_DEFAULT = "week"  # last 7 days


def _guess_lang(text: str) -> str:
    # very light detection
    if re.search(r"[а-яА-ЯёЁ]", text):
        return "ru"
    return "en"


def _safe_json_load(s: str) -> Optional[dict]:
    s = (s or "").strip()
    if not s:
        return None
    # try to extract first {...}
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        s = m.group(0)
    try:
        return json.loads(s)
    except Exception:
        return None


class LLMRouter:
    def __init__(self, openai_client):
        self.client = openai_client

    def route(self, user_text: str, user_lang: Optional[str] = None, user_currency: Optional[str] = None) -> Dict[str, Any]:
        user_text = (user_text or "").strip()
        lang = user_lang or _guess_lang(user_text)

        # We force JSON output. If model fails, Brain will fallback.
        system = (
            "You are an intent router for a personal finance notebook bot.\n"
            "Return ONLY valid JSON, no markdown, no extra text.\n"
            "The bot scope is personal finance notebook: log income/expense/debt payments, show lists, summaries, advice, settings, delete/export.\n"
            "If user is off-topic, set intent=CHAT and provide a short in-scope reply and suggested next actions.\n"
            "\n"
            "Intents:\n"
            "LOG, SHOW, SUMMARY, ADVICE, SETTINGS, ACCOUNT, CHAT\n"
            "\n"
            "Output JSON schema:\n"
            "{\n"
            "  \"intent\": \"LOG|SHOW|SUMMARY|ADVICE|SETTINGS|ACCOUNT|CHAT\",\n"
            "  \"language\": \"<iso like ru/en/de/...>\",\n"
            "  \"confidence\": 0.0-1.0,\n"
            "  \"log\": {\"type\":\"expense|income\", \"amount\":number|null, \"currency\":\"string|null\", \"category\":\"string|null\", \"note\":\"string|null\"},\n"
            "  \"show\": {\"what\":\"expenses|income|all\", \"period\":\"today|yesterday|week|month|year|custom\", \"custom_days\":number|null, \"category\":\"string|null\"},\n"
            "  \"summary\": {\"period\":\"today|yesterday|week|month|year|custom\", \"custom_days\":number|null},\n"
            "  \"advice\": {\"topic\":\"save|budget|goal|purchase|debt|general\", \"details\":\"string|null\"},\n"
            "  \"settings\": {\"set_language\":\"string|null\", \"set_currency\":\"string|null\"},\n"
            "  \"account\": {\"action\":\"delete_all|export_csv|privacy\", \"requires_confirmation\":true|false},\n"
            "  \"reply\": \"string\", \n"
            "  \"one_question\": \"string|null\"\n"
            "}\n"
            "\n"
            "Rules:\n"
            "- If intent LOG but amount is missing, ask ONE question in one_question.\n"
            "- If delete_all requested, requires_confirmation must be true.\n"
            "- Always set language to user's language.\n"
        )

        user_meta = f"user_lang={user_lang or ''}; user_currency={user_currency or ''}".strip("; ")
        prompt = f"User meta: {user_meta}\nUser: {user_text}"

        # Use Responses API (preferred). If your environment is older, it still usually works.
        resp = self.client.responses.create(
            model="gpt-4o-mini",
            instructions=system,
            input=prompt,
        )

        out = getattr(resp, "output_text", "") or ""
        data = _safe_json_load(out)

        if not data:
            # Minimal fallback JSON if model returned garbage
            return {
                "intent": "CHAT",
                "language": lang,
                "confidence": 0.0,
                "log": {"type": None, "amount": None, "currency": None, "category": None, "note": None},
                "show": {"what": "all", "period": PERIOD_DEFAULT, "custom_days": None, "category": None},
                "summary": {"period": PERIOD_DEFAULT, "custom_days": None},
                "advice": {"topic": "general", "details": None},
                "settings": {"set_language": None, "set_currency": None},
                "account": {"action": None, "requires_confirmation": False},
                "reply": "Я здесь 🙂 Что хочешь сделать: записать расход/доход, показать траты или сводку?",
                "one_question": None,
            }

        return data
