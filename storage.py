import uuid
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta, timezone

from firebase_admin import firestore


def user_ref(db, user_id: int):
    return db.collection("users").document(str(user_id))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_user(db, user_id: int) -> Dict[str, Any]:
    doc = user_ref(db, user_id).get()
    return doc.to_dict() if doc.exists else {}


def ensure_user(db, user_id: int, username: Optional[str], first_name: Optional[str]) -> Dict[str, Any]:
    ref = user_ref(db, user_id)
    doc = ref.get()
    if doc.exists:
        ref.update({"last_active_at": firestore.SERVER_TIMESTAMP})
        return doc.to_dict() or {}

    ref.set({
        "telegram_id": user_id,
        "username": username,
        "first_name": first_name,
        "created_at": firestore.SERVER_TIMESTAMP,
        "last_active_at": firestore.SERVER_TIMESTAMP,
        "settings": {
            "language": None,
            "currency": None,
            "timezone": None,
        },
        "state": {
            "pending": None
        },
        "delete_guard": {
            "last_deleted_at": None
        }
    })
    return get_user(db, user_id)


def set_user_language(db, user_id: int, lang: str):
    user_ref(db, user_id).update({"settings.language": lang, "last_active_at": firestore.SERVER_TIMESTAMP})


def set_user_currency(db, user_id: int, currency: str):
    user_ref(db, user_id).update({"settings.currency": currency, "last_active_at": firestore.SERVER_TIMESTAMP})


def get_settings(user: Dict[str, Any]) -> Dict[str, Any]:
    return (user or {}).get("settings", {}) or {}


def get_pending(user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return ((user or {}).get("state", {}) or {}).get("pending")


def set_pending(db, user_id: int, pending: Optional[Dict[str, Any]]):
    user_ref(db, user_id).update({"state.pending": pending, "last_active_at": firestore.SERVER_TIMESTAMP})


def add_event(
    db,
    user_id: int,
    kind: str,
    amount: float,
    currency: str,
    category: Optional[str],
    note: Optional[str],
    raw_text: Optional[str],
) -> str:
    event_id = str(uuid.uuid4())
    ref = user_ref(db, user_id).collection("events").document(event_id)
    ref.set({
        "ts": firestore.SERVER_TIMESTAMP,
        "kind": kind,
        "amount": float(amount),
        "currency": currency,
        "category": category,
        "note": note,
        "raw_text": raw_text,
    })
    user_ref(db, user_id).update({"last_active_at": firestore.SERVER_TIMESTAMP})
    return event_id


def _to_ts(dt: datetime):
    # Firestore python accepts datetime with tzinfo
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def list_events_range(
    db,
    user_id: int,
    start_dt: datetime,
    end_dt: datetime,
    kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    col = user_ref(db, user_id).collection("events")
    q = col.where("ts", ">=", _to_ts(start_dt)).where("ts", "<", _to_ts(end_dt))
    if kind:
        q = q.where("kind", "==", kind)
    docs = q.stream()
    out = []
    for d in docs:
        item = d.to_dict() or {}
        item["id"] = d.id
        out.append(item)
    return out


def sum_events(
    events: List[Dict[str, Any]],
    currency: str,
) -> Tuple[float, float, float]:
    income = 0.0
    expense = 0.0
    for e in events:
        if (e.get("currency") or "").upper() != currency.upper():
            # пока не конвертируем, просто игнорим другие валюты
            continue
        amt = float(e.get("amount") or 0)
        if e.get("kind") == "income":
            income += amt
        elif e.get("kind") == "expense":
            expense += amt
    total = income - expense
    return income, expense, total


def can_delete_account(db, user_id: int) -> Tuple[bool, Optional[int]]:
    doc = user_ref(db, user_id).get()
    if not doc.exists:
        return True, None
    data = doc.to_dict() or {}
    last = ((data.get("delete_guard") or {}).get("last_deleted_at"))
    if not last:
        return True, None

    # last can be Firestore Timestamp or datetime
    try:
        last_dt = last.datetime if hasattr(last, "datetime") else last
    except Exception:
        last_dt = last

    if not isinstance(last_dt, datetime):
        return True, None

    now = now_utc()
    delta = now - last_dt.replace(tzinfo=timezone.utc) if last_dt.tzinfo is None else now - last_dt
    if delta.total_seconds() >= 24 * 3600:
        return True, None
    remain = int(24 * 3600 - delta.total_seconds())
    return False, remain


def wipe_user(db, user_id: int):
    # delete subcollections
    u = user_ref(db, user_id)
    for sub in ["events", "reminders"]:
        col = u.collection(sub).stream()
        for d in col:
            u.collection(sub).document(d.id).delete()

    # mark deletion time and reset doc
    u.set({
        "telegram_id": user_id,
        "created_at": firestore.SERVER_TIMESTAMP,
        "last_active_at": firestore.SERVER_TIMESTAMP,
        "settings": {"language": None, "currency": None, "timezone": None},
        "state": {"pending": None},
        "delete_guard": {"last_deleted_at": firestore.SERVER_TIMESTAMP},
    }, merge=False)
