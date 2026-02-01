from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Literal

from prompts import SYSTEM_PROMPT_RU
from utils import clean_text

Intent = Literal["LOG", "SHOW", "SUMMARY", "ADVICE", "DELETE_DATA", "HELP", "UNKNOWN"]

@dataclass
class LLMResult:
    intent: Intent
    kind: str | None = None
    amount: float | None = None
    days: int | None = None
    note: str | None = None
    confidence: float | None = None

class LLMRouter:
    def __init__(self, openai_client, model: str = "gpt-4o-mini"):
        self.client = openai_client
        self.model = model

    def route(self, text: str) -> LLMResult:
        t = clean_text(text)

        # ВАЖНО: просим строго JSON, без болтовни
        user_prompt = f"""
Определи намерение и верни СТРОГО JSON без markdown.

Формат:
{{
  "intent": "LOG|SHOW|SUMMARY|ADVICE|DELETE_DATA|HELP|UNKNOWN",
  "kind": "expense|income|debt|pay_debt|null",
  "amount": number|null,
  "days": number|null,
  "note": string|null,
  "confidence": number
}}

Текст пользователя: {t}
"""

        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_RU},
                {"role": "user", "content": user_prompt},
            ],
            timeout=20,
        )

        content = (resp.choices[0].message.content or "").strip()
        # иногда модель может добавить мусор — пробуем вытащить JSON
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return LLMResult(intent="UNKNOWN", note=t, confidence=0.0)

        data = json.loads(content[start:end+1])
        intent = data.get("intent") or "UNKNOWN"
        kind = data.get("kind")
        amount = data.get("amount")
        days = data.get("days")
        note = data.get("note")
        conf = data.get("confidence")

        if kind == "null":
            kind = None
        if note == "null":
            note = None
        if amount == "null":
            amount = None
        if days == "null":
            days = None

        return LLMResult(
            intent=intent,
            kind=kind,
            amount=amount,
            days=days,
            note=note,
            confidence=conf,
        )
