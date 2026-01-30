from __future__ import annotations

from typing import Any, Dict

ROUTER_MODEL = "gpt-4o-mini"


def route_message(openai_client: Any, prefs: Dict[str, Any], user_text: str) -> Dict[str, Any]:
    """
    Single LLM router: decides what the user meant in the context of the finance notebook.
    Returns a strict JSON object matching the schema below.
    """

    schema = {
        "name": "finance_notebook_router",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "greet",
                        "help",
                        "set_language",
                        "set_currency",
                        "add_transaction",
                        "query_summary",
                        "query_list",
                        "add_reminder",
                        "delete_account",
                        "offtopic",
                        "unknown",
                    ],
                },
                "reply": {"type": "string"},
                "language_set": {"type": ["string", "null"], "description": "BCP-47 if possible (e.g., en, ru, de, fr, es)."},
                "base_currency_set": {"type": ["string", "null"], "description": "ISO code like USD/EUR/GBP."},
                "period": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "properties": {
                        "type": {"type": "string", "enum": ["day", "week", "month", "year", "custom"]},
                        "start_iso": {"type": ["string", "null"]},
                        "end_iso": {"type": ["string", "null"]},
                    },
                },
                "transaction": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "properties": {
                        "type": {"type": ["string", "null"], "enum": ["expense", "income", None]},
                        "amount": {"type": ["number", "null"]},
                        "category": {"type": ["string", "null"]},
                        "note": {"type": ["string", "null"]},
                    },
                },
                "reminder": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "properties": {
                        "when_iso": {"type": ["string", "null"], "description": "UTC ISO datetime."},
                        "text": {"type": ["string", "null"]},
                    },
                },
            },
            "required": ["action", "reply", "language_set", "base_currency_set", "period", "transaction", "reminder"],
        },
    }

    instructions = (
        "You are an intent router for a finance notebook bot.\n"
        "Rules:\n"
        "- Stay strictly in the finance notebook app context.\n"
        "- If user is off-topic (politics, jokes, random chat), set action='offtopic' and reply briefly redirecting.\n"
        "- If user says hello/hi, set action='greet' and reply politely + ask what to do.\n"
        "- If user wants to change language: action='set_language' and language_set to the requested language.\n"
        "- If user wants to change currency or says 'USD/EUR...' as answer: action='set_currency' and base_currency_set.\n"
        "- If user provides a transaction like 'coffee 5' or 'spent 20 on gas': action='add_transaction' and fill transaction fields.\n"
        "- If user asks for totals/summaries: action='query_summary' and period (week/month/year/custom if specified).\n"
        "- If user asks to show the list/details: action='query_list' and period.\n"
        "- If user sets a reminder: action='add_reminder' and reminder.when_iso in UTC ISO format.\n"
        "- If user asks to delete everything/account: action='delete_account'.\n"
        "- Always produce a helpful reply in the user's language.\n"
        "- If you are not sure, ask ONE short clarification question in reply and set action='unknown'.\n"
        "\n"
        f"User preferences: language={prefs.get('language','auto')}, base_currency={prefs.get('base_currency')}\n"
        "If language is auto, detect from the user's message and set language_set.\n"
        "If base_currency is missing and user seems to specify currency, set base_currency_set.\n"
    )

    resp = openai_client.responses.create(
        model=ROUTER_MODEL,
        instructions=instructions,
        input=user_text,
        response_format={"type": "json_schema", "json_schema": schema},
    )

    txt = getattr(resp, "output_text", None)
    if not txt:
        # fallback
        return {
            "action": "unknown",
            "reply": "I’m not sure. Do you want to add an expense/income, or see a summary?",
            "language_set": None,
            "base_currency_set": None,
            "period": None,
            "transaction": None,
            "reminder": None,
        }

    import json
    try:
        data = json.loads(txt)
        return data
    except Exception:
        return {
            "action": "unknown",
            "reply": "I’m not sure. Do you want to add an expense/income, or see a summary?",
            "language_set": None,
            "base_currency_set": None,
            "period": None,
            "transaction": None,
            "reminder": None,
        }
