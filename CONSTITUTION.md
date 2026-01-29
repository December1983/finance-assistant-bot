# Finance Notebook Bot — Constitution (v1)

## 0) Core Idea
This is a conversational financial notebook.
No menus, no buttons, no forms. The interface is plain chat (text + voice).
The bot stores all user financial records and can answer questions instantly.

## 1) Supported Languages
- Language is detected at onboarding (/start) and saved as the user's default.
- The bot replies in the user's saved language by default.
- The user can change language anytime by saying/writing it (e.g., “switch to German”, “сменить язык на русский”).
- Minimum supported: RU / EN / DE. (Extendable to any.)

## 2) Input Style
User can write or speak in natural language.
The bot must:
- Understand whether the user is logging a record, asking for a report, setting a reminder, etc.
- Confirm every saved record: “✅ Saved …”
- If unclear, ask ONE clarifying question (no drafts).

## 3) Data the Bot Stores (v1, must exist from day 1)
- Transactions: incomes and expenses
- Categories (auto-guessed, user can override)
- Notes (original message)
- Timestamps (UTC + user timezone for reporting)
- Regular payments (optional in v1.1 if needed)
- Debts (optional in v1.1 if needed)
- Goals (optional in v1.1 if needed)
- Reminders (v1.1 if needed)
- Export to CSV (required)

## 4) What the Bot Can Do
### 4.1 Log
- Log expense: “coffee 5”, “заправка 80”
- Log income: “got paid 1200”, “пришло 450”

### 4.2 Show reports (numbers must be exact)
- Summary for: day / week / month / year / custom period
- Category totals: “how much on gas last week”
- List records: “show last 20”
- Search: “show all burgers in January”

### 4.3 Reminders (v1.1)
- User sets reminders in natural language:
  “remind me about car payment on Feb 5: 7 days, 3 days, 1 day before”
- Repeat rules: once / weekly / monthly / custom

### 4.4 Advisor mode (optional)
User can ask: “Should I buy X?” / “Can I afford Y?”
Bot should use stored data + request more info if needed.
Bot must not invent facts.

## 5) Behavior Rules
- Finance-only scope: if user chats off-topic, gently redirect to finance.
- No moralizing. Calm, practical tone.
- No invented data.
- If uncertainty: ask 1 question max.
- Keep replies short and actionable.

## 6) Privacy & Control
- User can delete last record (v1.1).
- User can delete ALL records and account (must require confirmation phrase).
- Basic rate limits per user/day (v1.1).
- Store only what is necessary for the finance notebook.

## 7) Architecture Decision (Important)
We DO NOT use keyword parsers.
Every message goes through OpenAI “router” which returns strict JSON:
- intent (action type)
- extracted fields (amount, category, dates, language, etc.)
Code executes actions against Firestore as the single source of truth.

## 8) Defaults
- Currency: USD unless user says otherwise
- Timezone: America/Los_Angeles unless user changes
- Language: detected at /start and saved; changes only by explicit user request
