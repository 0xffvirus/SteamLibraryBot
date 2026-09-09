# 🎮 Steam Library Bot

A free and open-source Telegram bot to track the shared Steam accounts and game credentials you buy from sellers. Keep game names, store links, usernames, passwords, warranty dates, and notes in one private, per-user library — right inside Telegram.

No subscriptions, no ads, no paid tiers. Just a self-hosted bot and a local SQLite file.

## Features

- **Step-by-step game entry** — Steam store links auto-fill the game name and cover image via the Steam Store API.
- **Quick Add** — paste a seller message block (`Game:`, `User:`, `Pass:`, `Store:`, `Seller:`, `Price:`, `Warranty:`, `Notes:`) and the bot parses it for you.
- **Private library** — every user only ever sees their own games.
- **Stores** — save sellers/stores once, then link games to them.
- **Favorites & recents** — star games and quickly reopen recently viewed ones.
- **One-tap copy** — copy username or password straight to your clipboard.
- **Search** — find games by name, store, seller, username, or notes.
- **Stats** — total games, unique stores, total spent, added this month, and expiring warranties.
- **Warranty reminders** — daily alerts at 09:00 for warranties expiring in 7, 3, or 0 days (toggle with `/reminders`).
- **Import / export** — back up your library as JSON and restore it anytime.
- **Anti-spam** — callback debouncing so double-taps don't create duplicate actions.

## Commands

| Command | Description |
| --- | --- |
| `/start` | Register and show the main menu |
| `/add` | Add a game step-by-step |
| `/quickadd` | Add a game from a pasted block |
| `/mylibrary` | Open your library |
| `/view <id>` | View a game by ID |
| `/search <keyword>` | Search your library |
| `/stats` | Show library statistics |
| `/export` | Download your library as JSON |
| `/import` | Import a previously exported JSON file |
| `/reminders on\|off` | Toggle warranty reminders |
| `/clear` | Clear the chat and reset the menu |
| `/cancel` | Cancel the current conversation |

## Requirements

- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup

1. **Clone the repository**

   ```bash
   git clone <your-repo-url>
   cd SteamLibraryBot
   ```

2. **Create a virtual environment and install dependencies**

   ```bash
   python -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Set your bot token**

   Export the token from @BotFather as an environment variable:

   ```bash
   export BOT_TOKEN="your-token-from-BotFather"   # Windows: set BOT_TOKEN=...
   ```

   The bot reads `BOT_TOKEN` from the environment (`config.py`). Never commit your real token to a public repository.

4. **Run the bot**

   ```bash
   python bot.py
   ```

   The bot runs in long-polling mode. Press `Ctrl+C` to stop.

## Running with Docker

```bash
docker build -t steam-library-bot .
docker run -d --name steam-library-bot \
  -e BOT_TOKEN="your-token-from-BotFather" \
  -v "$(pwd)/game_library.db:/app/game_library.db" \
  steam-library-bot
```

The volume mount keeps your SQLite database on the host so it survives container restarts.

## Data & Privacy

- All data lives in a local SQLite database (`game_library.db`) on the machine running the bot.
- Games are scoped per user via a `user_id` foreign key — no user can access another user's library.
- Passwords are stored as plain text in the database. Protect the host and its backups accordingly. Password encryption at rest is on the roadmap.

## Project Structure

| File | Purpose |
| --- | --- |
| `bot.py` | Telegram handlers, conversation flows, and `main()` |
| `database.py` | All SQLite operations and schema migrations |
| `steam.py` | Fetches game name and cover image from the Steam Store API |
| `quick_add.py` | Parses pasted seller message blocks |
| `config.py` | Reads `BOT_TOKEN` from the environment |

Built with [python-telegram-bot](https://python-telegram-bot.org/) and `httpx`, using raw `sqlite3`. No other frameworks.

## Contributing

Contributions are welcome. Open an issue to report a bug or suggest a feature, then submit a pull request with a clear description of the change.

## Roadmap

- Tags / categories
- PIN lock before showing passwords
- Password encryption at rest (Fernet)
- Library pagination
- Sort options

## License

Released under the [MIT License](LICENSE). Free to use, modify, and distribute.
