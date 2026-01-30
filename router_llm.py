import json
import re
from openai import OpenAI


def _safe_json_extract(text: str) -> dict | None:
    """
    Пытаемся достать JSON даже если модель добавила лишний текст.
    """
    if not text:
        return None

    # ищем первый {...}
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None

    chunk = m.group(0)
    try:
        return json.loads(chunk)
    except Exception:
        return None


def route_message(openai_client: OpenAI, user_text: str, lang: str, currency: str | None, user_name: str | None) -> dict:
    """
    Возвращает структуру:
    {
      "intent": "greeting|record|summary|advice|set_language|set_currency|delete_account|other",
      "reply": "text in user's language",
      "record": { "type":"expense|income", "amount": 12.3, "category":"food", "note":"coffee" } | null,
      "summary": { "period":"week|month|day|year", "kind":"expenses|income|all" } | null,
      "set": { "language":"ru" } | { "currency":"USD" } | null,
      "needs": { "currency": true/false }   # если для действия нужна валюта
    }
    """

    sys = (
        "You are a finance notebook assistant inside a Telegram bot.\n"
        "Rules:\n"
        "- Stay inside finance notebook scope (records, summaries, advice, reminders conceptually).\n"
        "- If user asks about weather/politics/random, politely redirect back to finance tasks.\n"
        "- Always respond in user's language.\n"
        "- Output ONLY valid JSON. No markdown. No extra text.\n"
        "\n"
        "If user message is a greeting (hi/привет/куку/ау), intent=greeting.\n"
        "If user wants to record something like 'coffee 5', intent=record.\n"
        "If user asks 'my expenses last week', intent=summary.\n"
        "If user says 'speak Russian / по-русски', intent=set_language.\n"
        "If user says 'currency USD / валюта рубли', intent=set_currency.\n"
        "If user says 'delete my account / удали всё', intent=delete_account.\n"
        "\n"
        "Categories (suggest one): food, fuel, car, health, home, debt, entertainment, other.\n"
        "Amounts: number only.\n"
    )

    # язык
    if lang in ["auto", None, ""]:
        lang = "ru"

    user_context = {
        "user_name": user_name,
        "language": lang,
        "currency": currency,
    }

    prompt = {
        "user_context": user_context,
        "message": user_text
    }

    resp = openai_client.responses.create(
        model="gpt-4o-mini",
        instructions=sys,
        input=json.dumps(prompt, ensure_ascii=False),
    )

    out = getattr(resp, "output_text", "") or ""
    data = _safe_json_extract(out)

    # fallback, если модель вдруг вернула не-JSON
    if not isinstance(data, dict):
        data = {
            "intent": "other",
            "reply": ("Я тебя понял. Что хочешь сделать: записать расход/доход, получить сводку или совет?"
                      if lang.startswith("ru")
                      else "Got it. What would you like to do: record expense/income, get a summary, or advice?"),
            "record": None,
            "summary": None,
            "set": None,
            "needs": {"currency": False},
        }

    # нормализуем поля
    data.setdefault("intent", "other")
    data.setdefault("reply", "")
    data.setdefault("record", None)
    data.setdefault("summary", None)
    data.setdefault("set", None)
    data.setdefault("needs", {"currency": False})

    return data
