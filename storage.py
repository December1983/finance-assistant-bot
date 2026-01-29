from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from firebase_admin import firestore


class Storage:
    def __init__(self, db):
        self.db = db

    # -----------------------------
    # refs
    # -----------------------------
    def user_ref(self, user_id: int):
        return self.db.collection("users").document(str(user_id))

    def tx_col(self, user_id: int):
        return self.user_ref(user_id).collection("transactions")

    # cooldown survives account deletion
    def delete_cooldown_ref(self, user_id: int):
        return self.db.collection("deletion_cooldowns").document(str(user_id))

    # -----------------------------
    # user
    # -----------------------------
    def ensure_user(self, user: Any) -> None:
        ref = self.user_ref(user.id)
        doc = ref.get()
        if doc.exists:
            ref.set({"last_active_at": firestore.SERVER_TIMESTAMP}, merge=True)
            return

        ref.set(
            {
                "telegram_id": user.id,
                "username": getattr(user, "username", None),
                "first_name": getattr(user, "first_name", None),
                "created_at": firestore.SERVER_TIMESTAMP,
                "last_active_at": firestore.SERVER_TIMESTAMP,
                "settings": {
                    "language_mode": "auto",      # auto or fixed
                    "language_fixed": None,       # e.g. "de"
                    "base_currency": None,        # e.g. "USD" (asked on start)
                    "timezone": "America/Los_Angeles",
                },
                "pending": None,                 # confirmations
            },
            merge=True,
        )

    def get_user_settings(self, user_id: int) -> Dict[str, Any]:
        doc = self.user_ref(user_id).get()
        if not doc.exists:
            return {}
        data = doc.to_dict() or {}
        return data.get("settings", {}) or {}

    def set_language_fixed(self, user_id: int, lang: Optional[str], mode: str) -> None:
        self.user_ref(user_id).set(
            {"settings": {"language_mode": mode, "language_fixed": lang}},
            merge=True,
        )

    def set_base_currency(self, user_id: int, cur: str) -> None:
        self.user_ref(user_id).set({"settings": {"base_currency": cur}}, merge=True)

    def get_pending(self, user_id: int) -> Optional[Dict[str, Any]]:
        doc = self.user_ref(user_id).get()
        if not doc.exists:
            return None
        return (doc.to_dict() or {}).get("pending")

    def set_pending(self, user_id: int, pending: Optional[Dict[str, Any]]) -> None:
        self.user_ref(user_id).set({"pending": pending}, merge=True)

    # -----------------------------
    # transactions
    # -----------------------------
    def add_transaction(self, user_id: int, payload: Dict[str, Any]) -> str:
        ref = self.tx_col(user_id).document()
        data = dict(payload)
        data["created_at"] = firestore.SERVER_TIMESTAMP
        data["ts"] = firestore.SERVER_TIMESTAMP
        ref.set(data)
        self.user_ref(user_id).set({"last_active_at": firestore.SERVER_TIMESTAMP}, merge=True)
        return ref.id

    def get_last_transaction(self, user_id: int) -> Optional[Tuple[str, Dict[str, Any]]]:
        q = self.tx_col(user_id).order_by("ts", direction=firestore.Query.DESCENDING).limit(1).get()
        if not q:
            return None
        doc = q[0]
        return doc.id, (doc.to_dict() or {})

    def delete_last_transaction(self, user_id: int) -> bool:
        last = self.get_last_transaction(user_id)
        if not last:
            return False
        doc_id, _ = last
        self.tx_col(user_id).document(doc_id).delete()
        return True

    def list_transactions(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        q = self.tx_col(user_id).order_by("ts", direction=firestore.Query.DESCENDING).limit(limit).get()
        out = []
        for d in q:
            item = d.to_dict() or {}
            item["_id"] = d.id
            out.append(item)
        return out

    # For reporting we fetch by ts range (simple).
    # NOTE: Firestore "ts" is server timestamp. That’s OK for MVP.
    def query_by_ts_range(self, user_id: int, start_dt: datetime, end_dt: datetime) -> List[Dict[str, Any]]:
        q = (
            self.tx_col(user_id)
            .where("ts", ">=", start_dt)
            .where("ts", "<=", end_dt)
            .order_by("ts", direction=firestore.Query.ASCENDING)
            .get()
        )
        out = []
        for d in q:
            item = d.to_dict() or {}
            item["_id"] = d.id
            out.append(item)
        return out

    # -----------------------------
    # delete account with cooldown
    # -----------------------------
    def can_delete_account_now(self, user_id: int) -> Tuple[bool, Optional[datetime]]:
        doc = self.delete_cooldown_ref(user_id).get()
        if not doc.exists:
            return True, None
        data = doc.to_dict() or {}
        until = data.get("cooldown_until")
        if not until:
            return True, None

        # until may be Firestore Timestamp -> has .to_datetime()
        try:
            until_dt = until.to_datetime()
        except Exception:
            until_dt = None

        if not until_dt:
            return True, None

        now = datetime.now(timezone.utc)
        if now >= until_dt:
            return True, None
        return False, until_dt

    def set_delete_cooldown(self, user_id: int, hours: int = 24) -> None:
        now = datetime.now(timezone.utc)
        until = now + timedelta(hours=hours)
        self.delete_cooldown_ref(user_id).set(
            {"cooldown_until": until, "updated_at": firestore.SERVER_TIMESTAMP},
            merge=True,
        )

    def delete_account_everything(self, user_id: int) -> None:
        # delete subcollections in batches
        self._delete_collection_in_batches(self.tx_col(user_id), batch_size=300)

        # user doc
        self.user_ref(user_id).delete()

    def _delete_collection_in_batches(self, col_ref, batch_size: int = 300) -> None:
        while True:
            docs = col_ref.limit(batch_size).get()
            if not docs:
                break
            batch = self.db.batch()
            for d in docs:
                batch.delete(d.reference)
            batch.commit()
