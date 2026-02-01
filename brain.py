from __future__ import annotations
from dataclasses import dataclass

from storage import Storage, Tx
from router_fallback import FallbackRouter
from router_llm import LLMRouter
from utils import now_utc, fmt_money, detect_lang_ru, clean_text

@dataclass
class UserCtx:
    pending_delete: bool = False

class Brain:
    def __init__(self, db, openai_client):
        self.storage = Storage(db)
        self.fallback = FallbackRouter()
        self.llm = LLMRouter(openai_client=openai_client)
        self._mem: dict[int, UserCtx] = {}

    def _ctx(self, uid: int) -> UserCtx:
        if uid not in self._mem:
            self._mem[uid] = UserCtx()
        return self._mem[uid]

    def help_text(self) -> str:
        return (
            "Ок 🙂 Я — финансовая записная книжка.\n\n"
            "Что я умею:\n"
            "1) Записывать расход/доход/долг:\n"
            "   • «кофе 5»\n"
            "   • «потратил 20 на бензин»\n"
            "   • «доход 1200»\n"
            "   • «запиши 8 на долг» / «оплатил долг 30»\n\n"
            "2) Показать записи:\n"
            "   • «покажи расходы за неделю»\n"
            "   • «покажи доходы за 30 дней»\n\n"
            "3) Сводка:\n"
            "   • «сводка за неделю» / «итоги за месяц»\n\n"
            "4) Совет:\n"
            "   • «как экономить?» / «дай совет по бюджету»\n\n"
            "5) Удалить все данные:\n"
            "   • «сотри мои данные» (потребую подтверждение)\n\n"
            "Валюта по умолчанию: USD. Можно сменить фразой: «валюта EUR»."
        )

    def handle(self, uid: int, username: str | None, first_name: str | None, text: str, openai_ok: bool) -> str:
        t = clean_text(text)
        tl = t.lower()
        ctx = self._ctx(uid)

        # Получаем профиль
        profile = self.storage.get_profile(uid)
        currency = (profile.get("currency") or "USD").upper()

        # Команда смены валюты (без залипания)
        if tl.startswith("валюта ") or tl.startswith("currency "):
            cur = tl.split(" ", 1)[1].strip().upper()
            if len(cur) <= 5:
                self.storage.set_profile(uid, {"currency": cur})
                return f"Ок. Базовая валюта теперь: {cur}."
            return "Напиши валюту так: «валюта USD» или «валюта EUR»."

        # Подтверждение удаления
        if ctx.pending_delete:
            if "удалить всё" in tl or "удалить все" in tl or "delete all" in tl:
                self.storage.delete_all_user_data(uid)
                ctx.pending_delete = False
                return "Готово. Я удалил все твои данные."
            # если человек передумал / что-то другое
            ctx.pending_delete = False
            return "Ок, не удаляю. Что делаем дальше? Напиши «что ты умеешь»."

        # HELP если привет/что умеешь
        if any(x in tl for x in ["что ты умеешь", "помощь", "help", "команды", "что можешь"]):
            return self.help_text()

        if any(x in tl for x in ["привет", "ку", "куку", "hi", "hello", "как дела", "ау"]):
            return "Привет 🙂 Чем займёмся? Могу записать расход/доход, показать за период, сделать сводку или дать совет. Напиши «что ты умеешь»."

        # 1) пытаемся LLM если доступен
        routed = None
        if openai_ok:
            try:
                routed = self.llm.route(t)
            except Exception:
                routed = None

        # 2) если LLM нет — fallback
        if not routed:
            r = self.fallback.route(t)
            intent = r.intent
            kind = r.kind
            amount = r.amount
            days = r.days
            note = r.note
        else:
            intent = routed.intent
            kind = routed.kind
            amount = routed.amount
            days = routed.days
            note = routed.note

        # Выполняем
        if intent == "DELETE_DATA":
            ctx.pending_delete = True
            return (
                "Понял. Ты хочешь удалить ВСЕ данные.\n"
                "Это необратимо.\n\n"
                "Чтобы подтвердить — напиши точную фразу: **УДАЛИТЬ ВСЁ**.\n"
                "Если передумал — напиши любой другой текст."
            )

        if intent == "LOG":
            if amount is None or kind is None:
                return "Я понял, что ты хочешь записать, но не вижу сумму. Скажи так: «кофе 5» или «доход 1200»."
            self.storage.add_tx(uid, Tx(
                ts=now_utc(),
                kind=kind,
                amount=float(amount),
                note=(note or t)[:300],
                currency=currency,
            ))
            kind_ru = {
                "expense": "Расход",
                "income": "Доход",
                "debt": "Долг (взял/добавил)",
                "pay_debt": "Оплата долга",
            }.get(kind, kind)
            return f"{kind_ru}: {fmt_money(float(amount), currency)} ✅"

        if intent == "SHOW":
            d = int(days or 7)
            rows = self.storage.list_tx(uid, days=d)
            if not rows:
                return f"За последние {d} дней записей нет."
            lines = [f"Записи за {d} дней (последние сверху):"]
            # показываем максимум 20 строк
            for r in rows[:20]:
                k = r.get("kind")
                a = float(r.get("amount") or 0)
                cur = (r.get("currency") or currency).upper()
                ts = r.get("ts")
                date_s = ts.strftime("%Y-%m-%d") if ts else ""
                k_ru = {"expense":"расход","income":"доход","debt":"долг","pay_debt":"оплата долга"}.get(k, k)
                lines.append(f"• {date_s} — {k_ru}: {fmt_money(a, cur)} — {str(r.get('note') or '')[:60]}")
            if len(rows) > 20:
                lines.append(f"…и ещё {len(rows)-20} записей.")
            return "\n".join(lines)

        if intent == "SUMMARY":
            d = int(days or 7)
            s = self.storage.summary(uid, days=d)
            cur = (s.get("currency") or currency).upper()
            income = float(s.get("income") or 0)
            expense = float(s.get("expense") or 0)
            debt_added = float(s.get("debt_added") or 0)
            debt_paid = float(s.get("debt_paid") or 0)
            net = income - expense
            return (
                f"📊 Сводка за {d} дней:\n"
                f"Доход: {fmt_money(income, cur)}\n"
                f"Расход: {fmt_money(expense, cur)}\n"
                f"Итого: {fmt_money(net, cur)}\n"
                f"Долг добавлен: {fmt_money(debt_added, cur)}\n"
                f"Долг погашен: {fmt_money(debt_paid, cur)}\n"
                f"Записей: {int(s.get('count') or 0)}"
            )

        if intent == "ADVICE":
            # если openai недоступен — даём базовый совет
            if not openai_ok:
                return (
                    "Пока OpenAI недоступен — дам базовый совет:\n"
                    "1) Пиши расходы сразу (кофе/бензин/еда) — это даст картину.\n"
                    "2) Раздели фиксированные и переменные траты.\n"
                    "3) Цель: урезать 1–2 самые большие переменные категории.\n"
                    "Хочешь — напиши «сводка за неделю», и я подскажу по цифрам."
                )

            # если openai есть — делаем умный совет на основе сводки
            s = self.storage.summary(uid, days=30)
            cur = (s.get("currency") or currency).upper()
            prompt = (
                f"Сделай короткий практичный совет по экономии.\n"
                f"Данные за 30 дней:\n"
                f"Доход={s.get('income')}, Расход={s.get('expense')}, Долг добавлен={s.get('debt_added')}, Долг погашен={s.get('debt_paid')}\n"
                f"Валюта={cur}\n"
                f"Ответ: 5–7 пунктов, без воды, по-русски."
            )
            try:
                # вызываем LLM напрямую тем же роутером
                from openai import OpenAI
                # у нас client уже внутри llm; проще — сделать короткий запрос
                # Но чтобы не плодить лишнее: используем llm.client
                resp = self.llm.client.chat.completions.create(
                    model=self.llm.model,
                    temperature=0.4,
                    messages=[
                        {"role": "system", "content": "Ты — финансовый помощник. Пиши по-русски, коротко и по делу."},
                        {"role": "user", "content": prompt},
                    ],
                    timeout=20,
                )
                return (resp.choices[0].message.content or "").strip() or "Могу дать совет, но сейчас ответ пустой. Попробуй ещё раз."
            except Exception:
                return "Не смог дать совет сейчас. Попробуй ещё раз через минуту."

        if intent == "HELP":
            return self.help_text()

        # UNKNOWN
        return (
            "Я не до конца понял.\n"
            "Напиши одной фразой, что нужно:\n"
            "• «кофе 5» / «доход 1200» / «запиши 8 на долг»\n"
            "• «покажи расходы за неделю»\n"
            "• «сводка за месяц»\n"
            "• «сотри мои данные»\n"
            "Или «что ты умеешь»."
        )
