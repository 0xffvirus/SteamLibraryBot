# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
pip install -r requirements.txt
python bot.py
```

No build step. The bot runs in polling mode — `Ctrl+C` to stop.

## Architecture

Five modules, no frameworks beyond python-telegram-bot:

- **`bot.py`** — all Telegram handlers, conversation flows, and `main()`. This is where everything wires together.
- **`database.py`** — every SQLite operation. Uses raw `sqlite3` with `row_factory = sqlite3.Row`. `init_db()` runs migrations on startup via `_migrate_db()`.
- **`steam.py`** — single async function that hits the Steam Store API to pull game name and header image from an app ID.
- **`quick_add.py`** — regex parser that extracts game fields from a pasted seller message block.
- **`config.py`** — only holds `BOT_TOKEN`.

### Conversation state machine

`bot.py` uses three `ConversationHandler`s registered with the `Application`:
- **`add_conv`** — 9-state flow (GAME_NAME → STORE_URL → USERNAME → PASSWORD → IMAGE_URL → SELLER → PRICE → WARRANTY → NOTES). States 0–8.
- **`quick_add_conv`** — 2-state flow (QUICK_ADD_INPUT → QUICK_ADD_CONFIRM). States 10–11.
- **`edit_conv`** — 1-state flow (EDIT_VALUE, state 9). Entry point is `editf_*` callback data.
- **`search_conv`** — 1-state flow (SEARCH_MODE, string key).

All conversations share `menu_fallbacks()` which lets the user navigate away without cancelling explicitly.

### Database schema

Two tables in `game_library.db` (SQLite):
- `users` — `telegram_id`, `first_name`, `username`, `first_seen`, `reminders_enabled`
- `games` — per-user credential records with `user_id` FK; columns defined in `GAME_COLUMNS` list at top of `database.py`

Schema migrations are additive-only via `_add_column_if_missing()` in `_migrate_db()` — always use this pattern when adding columns, never drop or rename.

### Photo storage

Game cover images are stored either as a Telegram `file_id` (prefixed `tgfile:`) or a plain URL. `photo_source()` in `bot.py` strips the prefix before sending. Steam auto-fill sets a direct HTTPS URL; user-uploaded photos use the `tgfile:` prefix.

### Anti-spam

`is_spam_action()` / `debounce_query()` guard every callback handler with a 2-second debounce keyed on `context.user_data["_debounce"]`. Always wrap new callback handlers with `debounce_query`.

### Warranty reminders

A `JobQueue` daily job (`warranty_reminder_job`) fires at 09:00 and queries games expiring in exactly 0, 3, or 7 days, then sends Telegram messages. Users can opt out via `/reminders off`.

## Pending roadmap items (Tier 4)

These are not yet built — tags/categories, PIN lock before showing passwords, password encryption at rest (Fernet), library pagination, and sort options.
