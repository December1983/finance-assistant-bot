from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

@dataclass
class Tx:
    ts: datetime
    kind: str          # expense | income | debt | pay_debt
    amount: float
    note: str
    currency: str

class Storage:
    def __init__(self, db):
        self.db = db

    def user_doc(self, uid: int):
        return self.db.collection("users").document(str(uid))

    def get_profile(self, uid: int) -> dict[str, Any]:
        doc = self.user_doc(uid).get()
        if doc.exists:
            return doc.to_dict() or {}
        return {}

    def set_profile(self, uid: int, data: dict[str, Any]) -> None:
        self.user_doc(uid).set(data, merge=True)

    def add_tx(self, uid: int, tx: Tx) -> str:
        ref = self.user_doc(uid).collection("tx").document()
        ref.set({
            "ts": tx.ts,
            "kind": tx.kind,
            "amount": tx.amount,
            "note": tx.note,
            "currency": tx.currency,
        })
        return ref.id

    def list_tx(self, uid: int, days: int = 7, kind: str | None = None) -> list[dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        q = self.user_doc(uid).collection("tx").where("ts", ">=", since).order_by("ts", direction="DESCENDING")
        if kind:
            q = q.where("kind", "==", kind)
        docs = q.stream()
        out = []
        for d in docs:
            row = d.to_dict() or {}
            row["_id"] = d.id
            out.append(row)
        return out

    def summary(self, uid: int, days: int = 7) -> dict[str, Any]:
        rows = self.list_tx(uid, days=days)
        income = 0.0
        expense = 0.0
        debt = 0.0
        pay_debt = 0.0
        currency = None
        for r in rows:
            currency = currency or r.get("currency")
            k = r.get("kind")
            a = float(r.get("amount") or 0)
            if k == "income":
                income += a
            elif k == "expense":
                expense += a
            elif k == "debt":
                debt += a
            elif k == "pay_debt":
                pay_debt += a
        return {
            "income": income,
            "expense": expense,
            "debt_added": debt,
            "debt_paid": pay_debt,
            "currency": currency or "USD",
            "count": len(rows),
        }

    def delete_all_user_data(self, uid: int) -> None:
        # удаляем tx
        tx_ref = self.user_doc(uid).collection("tx")
        docs = list(tx_ref.stream())
        for d in docs:
            d.reference.delete()
        # удаляем профиль
        self.user_doc(uid).delete()
