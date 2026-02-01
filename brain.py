from typing import Optional
from datetime import datetime

from storage import Storage, start_end_for_period
from router_llm import LLMRouter


def fmt_money(amount: float, currency: str) -> str:
    try:
        return f"{amount:.2f} {currency}"
    except Exception:
        return f"{amount} {currency}"


def capabilities_text(lang: str = "ru") -> str:
    # коротко, без простыней
    if lang == "ru":
        return (
            "Я умею:\n"
            "1) Записывать расходы/доходы (голосом или текстом)\n"
            "2) Показывать траты/доходы за период (день/неделя/месяц)\n"
            "3) Делать сводку и итоги\n"
            "4) Давать советы по экономии/целям/долгам\n"
            "5) Менять язык и валюту\n"
            "6) Удалить все данные (с подтверждением)\n\n"
            "Скажи, что хочешь сделать 🙂"
        )
    return "I can log income/expenses, show lists/summaries, give advice, change language/currency, and delete all data (with confirmation)."


class Brain:
    def __init__(self, db, openai_client):
        self.storage = Storage(db)
        self.router = LLMRouter(openai_client)

    def handle(self, user_id: int, username: Optional[str], first_name: Optional[str], text: str) -> str:
        text = (text or "").strip()
        if not text:
            return capabilities_text("ru")

        meta = self.storage.meta_get(user_id)
        user_lang = meta.get("lang")
        user_currency = meta.get("currency")

        # 1) ROUTE via LLM (intent)
        try:
            r = self.router.route(text, user_lang=user_lang, user_currency=user_currency)
        except Exception:
            # аварийный режим: OpenAI упал — но бот НЕ умирает
            lang = user_lang or "ru"
            if lang == "ru":
                return (
                    "Сейчас у меня проблемы с «мозгом» (OpenAI недоступен), но я всё равно могу работать простыми фразами:\n"
                    "• «кофе 5»\n"
                    "• «доход 1200»\n"
                    "• «покажи расходы за неделю»\n"
                    "• «сводка за месяц»\n"
                    "Или напиши «что ты умеешь»."
                )
            return "OpenAI is temporarily unavailable. Try simple phrases like: 'coffee 5', 'income 1200', 'show expenses for week'."

        lang = (r.get("language") or user_lang or "ru").lower()
        # store language if changed/known
        if lang and lang != meta.get("lang"):
            self.storage.meta_set(user_id, {"lang": lang})

        intent = (r.get("intent") or "CHAT").upper()

        # 2) SETTINGS
        if intent == "SETTINGS":
            s = r.get("settings") or {}
            set_lang = s.get("set_language")
            set_cur = s.get("set_currency")
            updates = {}
            if set_lang:
                updates["lang"] = set_lang
            if set_cur:
                updates["currency"] = set_cur
            if updates:
                self.storage.meta_set(user_id, updates)
                if lang == "ru":
                    return f"✅ Ок. Настройки обновлены: {updates}"
                return f"✅ Settings updated: {updates}"
            return capabilities_text(lang)

        # 3) ACCOUNT actions
        if intent == "ACCOUNT":
            acc = r.get("account") or {}
            action = acc.get("action")
            requires = bool(acc.get("requires_confirmation"))

            # delete all with confirmation phrase
            if action == "delete_all":
                # confirmation flow stored in user meta with timestamp
                confirm_word = "ПОДТВЕРЖДАЮ" if lang == "ru" else "CONFIRM"
                if text.strip().upper() == confirm_word:
                    # simple 24h limit (optional). If you want strict, we add later.
                    self.storage.delete_all(user_id)
                    return "✅ Всё удалено. Ты можешь начать с нуля командой /start" if lang == "ru" else "✅ Deleted. Start again with /start"
                return (
                    f"⚠️ Это удалит ВСЕ записи и профиль. Это нельзя отменить.\n"
                    f"Если точно хочешь — напиши одним сообщением: {confirm_word}"
                ) if lang == "ru" else (
                    f"⚠️ This will delete ALL your data. If sure, type: {confirm_word}"
                )

            if action == "export_csv":
                return "Экспорт в CSV добавим следующим шагом." if lang == "ru" else "CSV export will be added next."

            return capabilities_text(lang)

        # 4) LOG transaction
        if intent == "LOG":
            log = r.get("log") or {}
            ttype = (log.get("type") or "expense").lower()
            amount = log.get("amount")
            currency = log.get("currency") or user_currency or "USD"
            category = log.get("category") or "other"
            note = log.get("note") or text

            # if amount missing -> ask ONE question
            if amount is None:
                q = r.get("one_question")
                return q or ("Сколько это было? Напиши сумму (например: 8 или 8.50)." if lang == "ru" else "What amount was it?")

            # store currency if user had none (but NEVER block)
            if not user_currency:
                self.storage.meta_set(user_id, {"currency": currency})

            self.storage.add_tx(user_id, ttype, float(amount), currency, category, note)

            if lang == "ru":
                label = "расход" if ttype == "expense" else "доход"
                extra = "" if user_currency else f"\n(Я записал в {currency}. Если другая валюта — скажи: «валюта EUR».)"
                return f"✅ Записал {label}: {fmt_money(float(amount), currency)} • {category}{extra}"
            else:
                label = "expense" if ttype == "expense" else "income"
                return f"✅ Logged {label}: {fmt_money(float(amount), currency)} • {category}"

        # 5) SHOW list
        if intent == "SHOW":
            show = r.get("show") or {}
            what = show.get("what") or "all"
            period = show.get("period") or "week"
            custom_days = show.get("custom_days")
            category = show.get("category")

            start_dt, end_dt = start_end_for_period(period, custom_days)
            items = self.storage.list_txs(user_id, start_dt, end_dt, what=what, category=category)

            # totals
            total_exp = 0.0
            total_inc = 0.0
            cur = user_currency or "USD"

            lines = []
            for it in items[-30:]:
                ttype = it.get("type")
                amt = float(it.get("amount") or 0)
                cur = it.get("currency") or cur
                cat = it.get("category") or "other"
                note = it.get("note") or ""
                # created_at may be Timestamp; show minimal
                lines.append(f"• {ttype}: {amt:.2f} {cur} • {cat}" + (f" • {note}" if note else ""))

                if ttype == "expense":
                    total_exp += amt
                elif ttype == "income":
                    total_inc += amt

            if lang == "ru":
                head = "🧾 Записи:"
                if not items:
                    return "За этот период записей нет. Хочешь записать расход или доход?" 
                return (
                    f"Итого за период: расход {total_exp:.2f} {cur}, доход {total_inc:.2f} {cur}\n"
                    f"{head}\n" + "\n".join(lines)
                )
            else:
                if not items:
                    return "No records for that period. Want to log an expense or income?"
                return f"Totals: expenses {total_exp:.2f} {cur}, income {total_inc:.2f} {cur}\nRecords:\n" + "\n".join(lines)

        # 6) SUMMARY
        if intent == "SUMMARY":
            summ = r.get("summary") or {}
            period = summ.get("period") or "week"
            custom_days = summ.get("custom_days")
            start_dt, end_dt = start_end_for_period
