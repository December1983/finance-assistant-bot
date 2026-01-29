# Firestore Data Model (v1)

Collection: users/{userId}

## Document: users/{userId}
Fields:
- telegram_id: number
- username: string | null
- first_name: string | null
- created_at: server_timestamp
- last_active_at: server_timestamp
- settings:
  - language: "ru" | "en" | "de" | "auto"
  - currency: "USD" | "EUR" | etc
  - timezone: "America/Los_Angeles" | etc
- safety:
  - daily_llm_count: number (v1.1)
  - daily_llm_date: "YYYY-MM-DD" (v1.1)
- pending:
  - type: string | null
  - payload: map | null
  (Used for confirmations, e.g., delete_all confirmation)

Subcollection: users/{userId}/events/{eventId}

## Document: users/{userId}/events/{eventId}
Fields:
- type: "expense" | "income"
- amount: number
- currency: string (default "USD")
- category: string (e.g., "gas", "food", "rent", "other")
- note: string (original user message, as-is)
- language: "ru" | "en" | "de" (language at the time of logging, optional)
- event_date: "YYYY-MM-DD" (user-local date for reporting)
- ts: server_timestamp (ordering / filtering)
- created_at: server_timestamp

Indexes:
- events by ts (ASC)
- events by event_date (ASC) (optional)
- events by category + ts (optional)

Subcollection: users/{userId}/reminders/{reminderId} (v1.1)

## Document: users/{userId}/reminders/{reminderId}
Fields:
- text: string
- due_date: "YYYY-MM-DD"
- notify_offsets_days: array<number> (e.g., [7,3,1,0])
- repeat: "none" | "weekly" | "monthly" | "custom"
- is_active: boolean
- created_at: server_timestamp
- updated_at: server_timestamp

Export:
- CSV export is generated from events and delivered to user on request (v1.1)
