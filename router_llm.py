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
            "  \"intent\": \"LOG
