from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import firebase_admin
from firebase_admin import credentials, firestore


class Storage:
    def __init__(self, service_account_json: str):
        cred_dict = json.loads(service_account_json)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        self.db = firestore.client()

    # -----------------------------
    # User
    # -----------------------------
    def user_ref(self, user_id: int):
        return self.db.collection("users").document(str(user_id))

    def ensure_user(self, user_id: int, username: Optional[str], first_name: Optional[str]) -> Dict[str, Any]:
        ref = self.user_ref(user_id)
        doc = ref.get()
        if doc.exists:
            data = doc.to_dict() or {}
            # keep username fresh
            ref.set(
                {
                    "telegram_id": user_id,
                    "username": username,
                    "first_name": first_name,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            return data

        data = {
            "telegram_id": user_id,
            "username": username,
            "first_name": first_name,
            "language": "auto",
            "base_currency": None,
            "created_at": firestore.SERVER_TIMESTAMP,
            "last_active_at": firestore.SERVER_TIMESTAMP,
            "last_delete_at": None,
        }
        ref.set(data, merge=True)
        return data

    def touch_user(self, user_id: int):
        self.user_ref(user_id).set({"last_active_at": firestore.SERVER_TIMESTAMP}, merge=True)

    def set_user_language(self, user_id: int, lang: str):
        self.user_ref(user_id).set({"language": lang, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)

    def set_user_base_currency(self, user_id: int, cur: str):
        self.user_ref(user_id).set({"base_currency": cur.upper(), "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)

    # -----------------------------
    # Transactions
    # -----------------------------
    def add_transaction(
        self,
        user_id: int,
        tx_type: str,
        amount: float,
        currency: str,
        category: str,
        note: str,
        original_text: str,
    ):
        ref = self.user_ref(user_id).collection("tx").document()
        ref.set(
            {
                "type": tx_type,
                "amount": amount,
                "currency": currency,
                "category": category,
                "note": note,
                "original_text": original_text,
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def _period_range(self, period: Dict[str, Any]) -> Tuple[datetime, datetime]:
        now = datetime.now(timezone.utc)

        ptype = (period or {}).get("type", "week")
        if ptype == "day":
            start = now - timedelta(days=1)
            end = now
            return start, end

        if ptype == "week":
            start = now - timedelta(days=7)
            end = now
            return start, end

        if ptype == "month":
            start = now - timedelta(days=30)
            end = now
            return start, end

        if ptype == "year":
            start = now - timedelta(days=365)
            end = now
            return start, end

        if ptype == "custom":
            # Expect ISO strings
            import dateutil.parser
            s = period.get("start_iso")
            e = period.get("end_iso")
            if s and e:
                start = dateutil.parser.isoparse(s).astimezone(timezone.utc)
                end = dateutil.parser.isoparse(e).astimezone(timezone.utc)
                return start, end

        # fallback
        start = now - timedelta(days=7)
        end = now
        return start, end

    def list_transactions(self, user_id: int, period: Dict[str, Any], limit: int = 50) -> List[Dict[str, Any]]:
        start, end = self._period_range(period)

        q = (
            self.user_ref(user_id)
            .collection("tx")
            .where("created_at", ">=", start)
            .where("created_at", "<=", end)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )

        out = []
        for d in q.stream():
            item = d.to_dict() or {}
            item["id"] = d.id

            # created_at can be Firestore Timestamp
            ca = item.get("created_at")
            try:
                if hasattr(ca, "to_datetime"):
                    ca = ca.to_datetime().replace(tzinfo=timezone.utc)
                elif isinstance(ca, datetime) and ca.tzinfo is None:
                    ca = ca.replace(tzinfo=timezone.utc)
                item["created_at_iso"] = ca.astimezone(timezone.utc).isoformat() if isinstance(ca, datetime) else None
            except Exception:
                item["created_at_iso"] = None

            out.append(item)

        return out

    def compute_summary(self, user_id: int, period: Dict[str, Any]) -> Dict[str, Any]:
        items = self.list_transactions(user_id=user_id, period=period, limit=500)

        income = 0.0
        expense = 0.0
        by_cat = {}

        for it in items:
            t = it.get("type")
            amt = float(it.get("amount") or 0)
            cat = (it.get("category") or "other").lower()

            if t == "income":
                income += amt
            elif t == "expense":
                expense += amt

            by_cat.setdefault(cat, 0.0)
            if t == "expense":
                by_cat[cat] += amt

        top = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "income": round(income, 2),
            "expense": round(expense, 2),
            "net": round(income - expense, 2),
            "top_expense_categories": [{"category": c, "amount": round(a, 2)} for c, a in top if a > 0],
            "count": len(items),
        }

    # -----------------------------
    # Reminders
    # -----------------------------
    def add_reminder(self, user_id: int, when_iso: str, text: str, language: str = "auto"):
        import dateutil.parser

        when_dt = dateutil.parser.isoparse(when_iso)
        if when_dt.tzinfo is None:
            when_dt = when_dt.replace(tzinfo=timezone.utc)
        when_dt = when_dt.astimezone(timezone.utc)

        ref = self.user_ref(user_id).collection("reminders").document()
        ref.set(
            {
                "when": when_dt,
                "text": text,
                "language": language,
                "status": "pending",
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def fetch_due_reminders(self, limit: int = 25) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)

        # Collection group query across all users
        q = (
            self.db.collection_group("reminders")
            .where("status", "==", "pending")
            .where("when", "<=", now)
            .order_by("when", direction=firestore.Query.ASCENDING)
            .limit(limit)
        )

        out = []
        for doc in q.stream():
            data = doc.to_dict() or {}
            # Path: users/{uid}/reminders/{rid}
            parts = doc.reference.path.split("/")
            user_id = parts[1]
            reminder_id = parts[3]
            out.append(
                {
                    "user_id": user_id,
                    "reminder_id": reminder_id,
                    "text": data.get("text", ""),
                    "language": data.get("language", "auto"),
                }
            )
        return out

    def mark_reminder_done(self, user_id: str, reminder_id: str):
        self.db.collection("users").document(str(user_id)).collection("reminders").document(reminder_id).set(
            {"status": "done", "done_at": firestore.SERVER_TIMESTAMP},
            merge=True,
        )

    # -----------------------------
    # Delete account (24h limit)
    # -----------------------------
    def can_delete_account(self, user_id: int) -> Tuple[bool, int]:
        doc = self.user_ref(user_id).get()
        data = doc.to_dict() or {}
        last = data.get("last_delete_at")

        # If never deleted -> ok
        if not last:
            return True, 0

        try:
            if hasattr(last, "to_datetime"):
                last_dt = last.to_datetime().replace(tzinfo=timezone.utc)
            elif isinstance(last, datetime):
                last_dt = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            else:
                return True, 0
        except Exception:
            return True, 0

        now = datetime.now(timezone.utc)
        diff = now - last_dt
        if diff >= timedelta(hours=24):
            return True, 0

        wait = int((timedelta(hours=24) - diff).total_seconds())
        return False, wait

    def delete_user_everything(self, user_id: int):
        # Mark delete time first
        self.user_ref(user_id).set({"last_delete_at": firestore.SERVER_TIMESTAMP}, merge=True)

        # Delete subcollections: tx, reminders
        self._delete_collection(self.user_ref(user_id).collection("tx"), batch_size=200)
        self._delete_collection(self.user_ref(user_id).collection("reminders"), batch_size=200)

        # Reset user doc (keep deletion timestamp to enforce 24h)
        self.user_ref(user_id).set(
            {
                "telegram_id": user_id,
                "language": "auto",
                "base_currency": None,
                "created_at": firestore.SERVER_TIMESTAMP,
                "last_active_at": firestore.SERVER_TIMESTAMP,
                "last_delete_at": firestore.SERVER_TIMESTAMP,
            },
            merge=False,
        )

    def _delete_collection(self, col_ref, batch_size: int = 200):
        docs = col_ref.limit(batch_size).stream()
        deleted = 0
        batch = self.db.batch()

        for doc in docs:
            batch.delete(doc.reference)
            deleted += 1

        if deleted > 0:
            batch.commit()
            return self._delete_collection(col_ref, batch_size)

        return
