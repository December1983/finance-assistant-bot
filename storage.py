import json
import os
import firebase_admin
from firebase_admin import credentials, firestore

BOT_TZ = os.getenv("BOT_TZ", "America/New_York")

FIREBASE_SERVICE_ACCOUNT = os.getenv("FIREBASE_SERVICE_ACCOUNT")
if not FIREBASE_SERVICE_ACCOUNT:
    raise RuntimeError("FIREBASE_SERVICE_ACCOUNT is missing in environment variables.")

_cred = credentials.Certificate(json.loads(FIREBASE_SERVICE_ACCOUNT))
if not firebase_admin._apps:
    firebase_admin.initialize_app(_cred)

db = firestore.client()


def user_ref(user_id: int):
    return db.collection("users").document(str(user_id))


def ensure_user(user_id: int, username: str | None, first_name: str | None, tg_lang: str | None):
    ref = user_ref(user_id)
    doc = ref.get()
    if doc.exists:
        return

    # tg_lang может быть 'ru', 'en', 'de', etc.
    ref.set({
        "telegram_id": user_id,
        "username": username,
        "first_name": first_name,
        "created_at": firestore.SERVER_TIMESTAMP,
        "last_active_at": firestore.SERVER_TIMESTAMP,
        "settings": {
            "language": (tg_lang or "auto"),
            "currency": None,  # будет установлено пользователем
        },
        "state": {
            "pending": None,  # например: {"type":"need_currency_for_record","payload":{...}}
            "last_delete_at": None,
        }
    })


def touch_user(user_id: int):
    user_ref(user_id).set({"last_active_at": firestore.SERVER_TIMESTAMP}, merge=True)


def get_settings(user_id: int) -> dict:
    doc = user_ref(user_id).get()
    if not doc.exists:
        return {"language": "auto", "currency": None}
    data = doc.to_dict() or {}
    return (data.get("settings") or {"language": "auto", "currency": None})


def set_language(user_id: int, lang: str):
    user_ref(user_id).set({"settings": {"language": lang}}, merge=True)


def set_currency(user_id: int, currency: str):
    user_ref(user_id).set({"settings": {"currency": currency}}, merge=True)


def get_state(user_id: int) -> dict:
    doc = user_ref(user_id).get()
    if not doc.exists:
        return {"pending": None, "last_delete_at": None}
    data = doc.to_dict() or {}
    return data.get("state") or {"pending": None, "last_delete_at": None}


def set_pending(user_id: int, pending: dict | None):
    user_ref(user_id).set({"state": {"pending": pending}}, merge=True)


def get_last_delete_at(user_id: int):
    doc = user_ref(user_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    state = data.get("state") or {}
    return state.get("last_delete_at")


def set_last_delete_now(user_id: int):
    user_ref(user_id).set({"state": {"last_delete_at": firestore.SERVER_TIMESTAMP}}, merge=True)


def add_event(user_id: int, event: dict):
    """
    event example:
    {
      "type":"expense|income",
      "amount": 5.0,
      "currency": "USD",
      "category":"food",
      "note":"coffee",
      "ts": firestore.SERVER_TIMESTAMP,
      "raw":"Кофе 5"
    }
    """
    user_ref(user_id).collection("events").add({
        **event,
        "ts": firestore.SERVER_TIMESTAMP
    })


def delete_user_everything(user_id: int):
    """
    Удаляем:
    - users/{id}
    - users/{id}/events/*
    """
    ref = user_ref(user_id)

    # delete subcollection events
    events = ref.collection("events").stream()
    for d in events:
        d.reference.delete()

    ref.delete()
