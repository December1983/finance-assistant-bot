import json
import time
from typing import Any, Dict, Optional, Tuple, List

import firebase_admin
from firebase_admin import credentials, firestore


class Storage:
    def __init__(self, service_account_json: str):
        cred_dict = json.loads(service_account_json)
        cred = credentials.Certificate(cred_dict)

        # avoid double init
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)

        self.db = firestore.client()

    def user_ref(self, user_id: int):
        return self.db.collection("users").document(str(user_id))

    def ensure_user(
        self,
        user_id: int,
        username: Optional[str],
        first_name: Optional[str],
        telegram_language_code: Optional[str],
    ) -> Dict[str, Any]:
        ref = self.user_ref(user_id)
        doc = ref.get()
        if doc.exists:
            data = doc.to_dict() or {}
            # store telegram language code for fallback
            if telegram_language_code and data.get("telegram_language_code") != telegram_language_code:
                ref.set({"telegram_language_code": telegram_language_code}, merge=True)
            return data

        data = {
            "telegram_id": user_id,
            "username": username,
            "first_name": first_name,
            "created_at": firestore.SERVER_TIMESTAMP,
            "last_active_at": firestore.SERVER_TIMESTAMP,
            "language": "auto",                 # auto until we detect
            "telegram_language_code": telegram_language_code,
            "base_currency": None,              # ask only when needed
        }
        ref.set(data)
        return data

    def touch_user(self, user_id: int):
        self.user_ref(user_id).set({"last_active_at": firestore.SERVER_TIMESTAMP}, merge=True)

    def set_user_language(self, user_id: int, lang: str):
        self.user_ref(user_id).set({"language": lang}, merge=True)

    def set_user_base_currency(self, user_id: int, currency: str):
        self.user_ref(user_id).set({"base_currency": currency}, merge=True)

    # ---------------- transactions ----------------

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
        ref = self.user_ref(user_id).collection("transactions").document()
        ref.set(
            {
                "type": tx_type,
                "amount": amount,
                "currency": currency,
                "category": category,
                "note": note,
                "original_text": original_text,
                "created_at": firestore.SERVER_TIMESTAMP,
                "created_at_unix": int(time.time()),
            }
        )

    def list_transactions(self, user_id: int, period: Dict[str, Any], limit: int = 50) -> List[Dict[str, Any]]:
        # For now: last N (period filtering can be added next)
        q = (
            self.user_ref(user_id)
            .collection("transactions")
            .order_by("created_at_unix", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        docs = q.get()
        return [d.to_dict() for d in docs]

    def compute_summary(self, user_id: int, period: Dict[str, Any]) -> Dict[str, Any]:
        # For now: summarize last 7 days by unix time if "week", etc.
        ptype = (period or {}).get("type") or "week"
        now = int(time.time())

        if ptype == "day":
            start = now - 86400
        elif ptype == "month":
            start = now - 30 * 86400
        elif ptype == "year":
            start = now - 365 * 86400
        else:  # week default
            start = now - 7 * 86400

        q = (
            self.user_ref(user_id)
            .collection("transactions")
            .where("created_at_unix", ">=", start)
        )
        docs = q.get()

        income = 0.0
        expense = 0.0
        cat_sum: Dict[str, float] = {}

        for d in docs:
            it = d.to_dict() or {}
            t = it.get("type")
            amt = float(it.get("amount") or 0)
            cat = it.get("category") or "other"

            if t == "income":
                income += amt
            elif t == "expense":
                expense += amt
                cat_sum[cat] = cat_sum.get(cat, 0.0) + amt

        tops = sorted(
            [{"category": k, "amount": round(v, 2)} for k, v in cat_sum.items()],
            key=lambda x: x["amount"],
            reverse=True
        )

        return {
            "income": round(income, 2),
            "expense": round(expense, 2),
            "net": round(income - expense, 2),
            "top_expense_categories": tops,
        }

    # ---------------- account delete cooldown ----------------

    def can_delete_account(self, user_id: int) -> Tuple[bool, int]:
        meta_ref = self.user_ref(user_id).collection("meta").document("current")
        doc = meta_ref.get()
        if not doc.exists:
            return True, 0

        data = doc.to_dict() or {}
        last = int(data.get("last_delete_unix") or 0)
        now = int(time.time())
        cooldown = 24 * 3600
        if now - last >= cooldown:
            return True, 0
        return False, cooldown - (now - last)

    def delete_user_everything(self, user_id: int):
        # mark delete time
        meta_ref = self.user_ref(user_id).collection("meta").document("current")
        meta_ref.set({"last_delete_unix": int(time.time())}, merge=True)

        # delete subcollections (transactions)
        tx_docs = self.user_ref(user_id).collection("transactions").limit(500).get()
        for d in tx_docs:
            d.reference.delete()

        # reset user doc (keeps cooldown meta)
        self.user_ref(user_id).set(
            {
                "language": "auto",
                "base_currency": None,
                "created_at": firestore.SERVER_TIMESTAMP,
                "last_active_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
