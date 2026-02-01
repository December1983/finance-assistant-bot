from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from firebase_admin import firestore


def now_utc():
    return datetime.now(timezone.utc)


def start_end_for_period(period: str, custom_days: Optional[int] = None) -> Tuple[datetime, datetime]:
    end = now_utc()

    if period == "today":
        start = end.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)

    if period == "yesterday":
        today_start = end.replace(hour=0, minute=0, second=0, microsecond=0)
        return today_start - timedelta(days=1), today_start

    if period == "month":
        return end - timedelta(days=30), end

    if period == "year":
        return end - timedelta(days=365), end

    if period == "custom" and custom_days:
        return end - timedelta(days=int(custom_days)), end

    # default week
    return end - timedelta(days=7), end


class Storage:
    def __init__(self, db):
        self.db = db

    def user_ref(self, user_id: int):
        return self.db.collection("users").document(str(user_id))

    def tx_col(self, user_id: int):
        return self.user_ref(user_id).collection("transactions")

    def meta_get(self, user_id: int) -> Dict[str, Any]:
        doc = self.user_ref(user_id).get()
        return (doc.to_dict() or {}) if doc.exists else {}

    def meta_set(self, user_id: int, data: Dict[str, Any]):
        self.user_ref(user_id).set(data, merge=True)

    def add_tx(self, user_id: int, ttype: str, amount: float, currency: str, category: str, note: str):
        self.tx_col(user_id).add({
            "type": ttype,
            "amount": float(amount),
            "currency": currency,
            "category": category,
            "note": note or "",
            "created_at": firestore.SERVER_TIMESTAMP,
        })

    def list_txs(self, user_id: int, start_dt, end_dt, what: str = "all", category: Optional[str] = None) -> List[Dict[str, Any]]:
        q = (self.tx_col(user_id)
             .where("created_at", ">=", start_dt)
             .where("created_at", "<", end_dt)
             .order_by("created_at", direction=firestore.Query.ASCENDING))

        docs = list(q.stream())
        out = []
        for d in docs:
            data = d.to_dict() or {}
            ttype = data.get("type")
            if what == "expenses" and ttype != "expense":
                continue
            if what == "income" and ttype != "income":
                continue
            if category and (data.get("category") != category):
                continue
            out.append(data)
        return out

    def delete_all(self, user_id: int):
        # delete txs
        docs = list(self.tx_col(user_id).stream())
        for d in docs:
            d.reference.delete()
        # delete user doc
        self.user_ref(user_id).delete()
