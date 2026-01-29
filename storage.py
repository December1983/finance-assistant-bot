# main/storage.py
from __future__ import annotations

from datetime import datetime, date, time
from typing import Any, Dict, List, Optional, Tuple

from firebase_admin import firestore


def _day_bounds(d: date) -> Tuple[datetime, datetime]:
    start = datetime.combine(d, time(0, 0, 0))
    end = datetime.combine(d, time(23, 59, 59))
    return start, end


class Storage:
    def __init__(self, db):
        self.db = db

    # --------------- Refs ---------------
    def user_ref(self, user_id: int):
        return self.db.collection("users").document(str(user_id))

    def tx_col(self, user_id: int):
        return self.user_ref(user_id).collection("transactions")

    # --------------- User init ---------------
    def ensure_user(self, user: Any) -> None:
        ref = self.user_ref(user.id)
        doc = ref.get()
        if doc.exists:
            ref.set({"last_active_at": firestore.SERVER_TIMESTAMP}, merge=True)
            return

        ref.set({
            "telegram_id": user.id,
            "username": getattr(user, "username", None),
            "first_name": getattr(user, "first_name", None),
            "created_at": firestore.SERVER_TIMESTAMP,
            "last_active_at": firestore.SERVER_TIMESTAMP,
            "settings": {
                "currency": "USD",
                "timezone": "America/Los_Angeles",
            },
            "pending": None,  # тут будем хранить незавершённый вопрос-уточнение
        }, merge=True)

    # --------------- Pending clarification ---------------
    def set_pending(self, user_id: int, pending: Optional[Dict[str, Any]]) -> None:
        self.user_ref(user_id).set({"pending": pending}, merge=True)

    def get_pending(self, user_id: int) -> Optional[Dict[str, Any]]:
        doc = self.user_ref(user_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        return data.get("pending")

    # --------------- Transactions ---------------
    def add_transaction(self, user_id: int, tx: Dict[str, Any]) -> str:
        """
        tx fields expected:
          type: 'expense'|'income'
          amount: float
          currency: 'USD'
          category: str
          note: str
        """
        ref = self.tx_col(user_id).document()
        payload = dict(tx)
        payload.update({
            "created_at": firestore.SERVER_TIMESTAMP,
            "ts": firestore.SERVER_TIMESTAMP,  # для сортировки/диапазонов
        })
        ref.set(payload)
        # запомним последнюю запись (на будущее: undo/edit last)
        self.user_ref(user_id).set({"last_transaction_id": ref.id}, merge=True)
        return ref.id

    def delete_all_user_data(self, user_id: int) -> None:
        """
        Полная очистка: удаляем все транзакции и сам документ user.
        Firestore не умеет delete collection одним вызовом — делаем батчами.
        """
        # 1) delete transactions in batches
        col = self.tx_col(user_id)
        while True:
            docs = col.limit(300).get()
            if not docs:
                break
            batch = self.db.batch()
            for d in docs:
                batch.delete(d.reference)
            batch.commit()

        # (на будущее: здесь же будут obligations, debts, goals, limits, assets, reminders)
        # пока чистим только transactions + user doc
        self.user_ref(user_id).delete()

    # --------------- Queries / summaries ---------------
    def query_transactions(self, user_id: int, d1: date, d2: date) -> List[Dict[str, Any]]:
        start_dt = datetime.combine(d1, time(0, 0, 0))
        end_dt = datetime.combine(d2, time(23, 59, 59))

        q = (
            self.tx_col(user_id)
            .where("ts", ">=", start_dt)
            .where("ts", "<=", end_dt)
            .order_by("ts", direction=firestore.Query.ASCENDING)
            .get()
        )

        out: List[Dict[str, Any]] = []
        for doc in q:
            item = doc.to_dict() or {}
            item["_id"] = doc.id
            out.append(item)
        return out

    def summarize(self, user_id: int, d1: date, d2: date, category: Optional[str] = None) -> Dict[str, Any]:
        items = self.query_transactions(user_id, d1, d2)

        income = 0.0
        expense = 0.0
        by_cat: Dict[str, float] = {}

        for tx in items:
            ttype = (tx.get("type") or "expense").lower()
            amt = float(tx.get("amount") or 0.0)
            cat = (tx.get("category") or "other").strip()

            if category and category != "all":
                # очень простой фильтр: точное совпадение
                if cat.lower() != category.lower():
                    continue

            if ttype == "income":
                income += amt
            else:
                expense += amt

            by_cat[cat] = by_cat.get(cat, 0.0) + amt

        net = income - expense
        top = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "income": income,
            "expense": expense,
            "net": net,
            "top_categories": top,
            "count": len(items),
        }

