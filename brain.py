import time
from typing import Optional


class Brain:
    def __init__(self, db, openai_client):
        self.db = db
        self.openai = openai_client

    def _user_doc(self, tg_user_id: int):
        return self.db.collection("users").document(str(tg_user_id))

    def _get_user(self, tg_user_id: int) -> dict:
        doc = self._user_doc(tg_user_id).get()
        if doc.exists:
            return doc.to_dict() or {}
        return {}

    def _set_user(self, tg_user_id: int, data: dict):
        self._user_doc(tg_user_id).set(data, merge=True)

    def _safe_err(self, e: Exception) -> str:
        # Вытащим максимум информации, но без утечки секретов
        err_type = type(e).__name__
        msg = str(e)[:600]

        # OpenAI SDK часто кладёт детали в e.response / e.status_code
        status = getattr(e, "status_code", None)
        if status is None:
            resp = getattr(e, "response", None)
            status = getattr(resp, "status_code", None)

        parts = [f"OpenAI error: {err_type}"]
        if status:
            parts.append(f"status={status}")
        if msg:
            parts.append(f"details={msg}")

        hint = []
        if status == 401:
            hint.append("Похоже на неверный/пустой OPENAI_API_KEY.")
        elif status == 429:
            hint.append("Похоже на лимит/квоту (429). Проверь billing/limits.")
        elif status == 404:
            hint.append("Похоже на неверную модель (model not found).")
        elif status in (500, 502, 503, 504):
            hint.append("Похоже на проблему на стороне сервиса/сети. Повтори позже.")
        else:
            # частые кейсы по тексту
            low = msg.lower()
            if "api key" in low or "authentication" in low:
                hint.append("Похоже на проблему с ключом OPENAI_API_KEY.")
            if "model" in low and ("not found" in low or "does not exist" in low):
                hint.append("Похоже на неправильное имя модели.")
            if "ssl" in low or "certificate" in low:
                hint.append("Похоже на SSL/сеть в Railway.")
            if "timeout" in low:
                hint.append("Похоже на таймаут сети.")

        if hint:
            parts.append("hint=" + " ".join(hint))

        return " | ".join(parts)

    def handle(self, tg_user_id: int, username: Optional[str], first_name: Optional[str], text: str) -> str:
        text = (text or "").strip()
        if not text:
            return "Напиши текстом: расход, доход, сводка или вопрос."

        # Примитивный быстрый “оффлайн” ответ без LLM — чтобы бот не был “немым”
        # Но если OpenAI доступен — уйдём в LLM.
        user = self._get_user(tg_user_id)

        try:
            # ВАЖНО: именно здесь твоя ошибка. Если OpenAI падает — покажем реальную причину.
            system = (
                "You are a financial notebook assistant inside a Telegram app.\n"
                "Rules:\n"
                "- Stay in personal finance/budgeting/income/expenses/summaries/advice.\n"
                "- If user says hi or off-topic, reply politely and ask what they want to do in the app.\n"
                "- Do NOT get stuck waiting for currency/language. Ask only when needed.\n"
                "- Reply in the user's language.\n"
            )

            # Небольшой контекст о пользователе (язык/валюта), если есть
            lang = user.get("lang")
            currency = user.get("currency")
            meta = []
            if lang:
                meta.append(f"user_lang={lang}")
            if currency:
                meta.append(f"user_currency={currency}")
            meta_str = ("; ".join(meta)) if meta else "none"

            resp = self.openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"User meta: {meta_str}\nUser message: {text}"},
                ],
                temperature=0.4,
            )

            content = resp.choices[0].message.content if resp and resp.choices else ""
            content = (content or "").strip()
            if not content:
                return "Я здесь. Что хочешь сделать: расход/доход, сводка или совет?"
            return content

        except Exception as e:
            # ВОТ ТУТ: вместо “ключ/лимит/сеть” теперь будет реальная причина
            return self._safe_err(e)
