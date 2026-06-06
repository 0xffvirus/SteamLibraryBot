# database.py
import sqlite3
from datetime import datetime, timedelta

DB_NAME = "game_library.db"

GAME_COLUMNS = [
    "id", "user_id", "game_name", "store_url", "username", "password",
    "image_url", "purchase_date", "notes", "seller_contact", "price_paid",
    "currency", "warranty_until", "is_favorite", "last_viewed",
]


def _get_columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column_if_missing(conn, table, column, definition):
    if column not in _get_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_db(conn):
    games_cols = _get_columns(conn, "games")

    if "user_id" not in games_cols:
        conn.execute("ALTER TABLE games ADD COLUMN user_id INTEGER")
        conn.execute(
            """
            UPDATE games SET user_id = (
                SELECT telegram_id FROM users ORDER BY first_seen LIMIT 1
            ) WHERE user_id IS NULL
            """
        )

    _add_column_if_missing(conn, "games", "seller_contact", "TEXT")
    _add_column_if_missing(conn, "games", "price_paid", "REAL")
    _add_column_if_missing(conn, "games", "currency", "TEXT DEFAULT 'USD'")
    _add_column_if_missing(conn, "games", "warranty_until", "TEXT")
    _add_column_if_missing(conn, "games", "is_favorite", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "games", "last_viewed", "TEXT")
    _add_column_if_missing(conn, "users", "reminders_enabled", "INTEGER DEFAULT 1")


def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            first_seen TEXT NOT NULL,
            reminders_enabled INTEGER DEFAULT 1
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_name TEXT NOT NULL,
            store_url TEXT NOT NULL,
            username TEXT NOT NULL,
            password TEXT,
            image_url TEXT,
            purchase_date TEXT NOT NULL,
            notes TEXT,
            seller_contact TEXT,
            price_paid REAL,
            currency TEXT DEFAULT 'USD',
            warranty_until TEXT,
            is_favorite INTEGER DEFAULT 0,
            last_viewed TEXT,
            FOREIGN KEY (user_id) REFERENCES users(telegram_id)
        )
        """
    )

    _migrate_db(conn)
    conn.commit()
    conn.close()


def _row_to_dict(row):
    return dict(row) if row else None


def _connect():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def register_user(telegram_id, first_name, username):
    conn = _connect()
    conn.execute(
        "INSERT OR IGNORE INTO users (telegram_id, first_name, username, first_seen) VALUES (?,?,?,?)",
        (telegram_id, first_name, username, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def add_game(
    user_id,
    game_name,
    store_url,
    username,
    password,
    image_url,
    notes,
    seller_contact=None,
    price_paid=None,
    currency="USD",
    warranty_until=None,
):
    conn = _connect()
    conn.execute(
        """
        INSERT INTO games (
            user_id, game_name, store_url, username, password, image_url,
            purchase_date, notes, seller_contact, price_paid, currency, warranty_until
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            user_id, game_name, store_url, username, password, image_url,
            datetime.now().isoformat(), notes, seller_contact, price_paid,
            currency, warranty_until,
        ),
    )
    conn.commit()
    conn.close()


def update_game(user_id, game_id, **fields):
    allowed = {
        "game_name", "store_url", "username", "password", "image_url", "notes",
        "seller_contact", "price_paid", "currency", "warranty_until",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [user_id, game_id]
    conn = _connect()
    conn.execute(
        f"UPDATE games SET {set_clause} WHERE user_id = ? AND id = ?",
        values,
    )
    conn.commit()
    conn.close()
    return True


def toggle_favorite(user_id, game_id):
    conn = _connect()
    conn.execute(
        """
        UPDATE games SET is_favorite = CASE WHEN is_favorite = 1 THEN 0 ELSE 1 END
        WHERE user_id = ? AND id = ?
        """,
        (user_id, game_id),
    )
    conn.commit()
    cursor = conn.execute(
        "SELECT is_favorite FROM games WHERE user_id = ? AND id = ?",
        (user_id, game_id),
    )
    row = cursor.fetchone()
    conn.close()
    return bool(row["is_favorite"]) if row else False


def touch_last_viewed(user_id, game_id):
    conn = _connect()
    conn.execute(
        "UPDATE games SET last_viewed = ? WHERE user_id = ? AND id = ?",
        (datetime.now().isoformat(), user_id, game_id),
    )
    conn.commit()
    conn.close()


def get_all_games(user_id, favorites_only=False):
    conn = _connect()
    if favorites_only:
        query = """
            SELECT id, game_name, store_url, username, purchase_date, is_favorite
            FROM games WHERE user_id = ? AND is_favorite = 1
            ORDER BY purchase_date DESC
        """
    else:
        query = """
            SELECT id, game_name, store_url, username, purchase_date, is_favorite
            FROM games WHERE user_id = ?
            ORDER BY is_favorite DESC, purchase_date DESC
        """
    games = conn.execute(query, (user_id,)).fetchall()
    conn.close()
    return [tuple(g) for g in games]


def get_recent_games(user_id, limit=5):
    conn = _connect()
    rows = conn.execute(
        """
        SELECT id, game_name, store_url, username, purchase_date, is_favorite
        FROM games WHERE user_id = ? AND last_viewed IS NOT NULL
        ORDER BY last_viewed DESC LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def get_game_by_id(user_id, game_id):
    conn = _connect()
    row = conn.execute(
        f"SELECT {', '.join(GAME_COLUMNS)} FROM games WHERE user_id = ? AND id = ?",
        (user_id, game_id),
    ).fetchone()
    conn.close()
    return _row_to_dict(row)


def search_games(user_id, keyword):
    conn = _connect()
    pattern = f"%{keyword}%"
    rows = conn.execute(
        """
        SELECT id, game_name, store_url, username
        FROM games WHERE user_id = ? AND (
            game_name LIKE ? OR store_url LIKE ? OR username LIKE ?
            OR notes LIKE ? OR seller_contact LIKE ?
        )
        ORDER BY is_favorite DESC, purchase_date DESC
        """,
        (user_id, pattern, pattern, pattern, pattern, pattern),
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def get_games_by_store(user_id, store_keyword):
    conn = _connect()
    pattern = f"%{store_keyword}%"
    rows = conn.execute(
        """
        SELECT id, game_name, store_url, username, purchase_date, is_favorite
        FROM games WHERE user_id = ? AND store_url LIKE ?
        ORDER BY purchase_date DESC
        """,
        (user_id, pattern),
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def delete_game(user_id, game_id):
    conn = _connect()
    conn.execute("DELETE FROM games WHERE user_id = ? AND id = ?", (user_id, game_id))
    conn.commit()
    conn.close()


def get_stats(user_id):
    conn = _connect()
    total = conn.execute(
        "SELECT COUNT(*) FROM games WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    stores = conn.execute(
        "SELECT COUNT(DISTINCT store_url) FROM games WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    conn.close()
    return total, stores


def get_extended_stats(user_id):
    conn = _connect()
    total = conn.execute(
        "SELECT COUNT(*) FROM games WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    stores = conn.execute(
        "SELECT COUNT(DISTINCT store_url) FROM games WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    total_spent = conn.execute(
        "SELECT COALESCE(SUM(price_paid), 0) FROM games WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    expiring_soon = conn.execute(
        """
        SELECT COUNT(*) FROM games WHERE user_id = ? AND warranty_until IS NOT NULL
        AND date(warranty_until) BETWEEN date('now') AND date('now', '+30 days')
        """,
        (user_id,),
    ).fetchone()[0]
    added_this_month = conn.execute(
        """
        SELECT COUNT(*) FROM games WHERE user_id = ?
        AND strftime('%Y-%m', purchase_date) = strftime('%Y-%m', 'now')
        """,
        (user_id,),
    ).fetchone()[0]
    top_stores = conn.execute(
        """
        SELECT store_url, COUNT(*) as cnt FROM games WHERE user_id = ?
        GROUP BY store_url ORDER BY cnt DESC LIMIT 3
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return {
        "total": total,
        "stores": stores,
        "total_spent": total_spent or 0,
        "expiring_soon": expiring_soon,
        "added_this_month": added_this_month,
        "top_stores": [(r["store_url"], r["cnt"]) for r in top_stores],
    }


def get_reminders_enabled(user_id):
    conn = _connect()
    row = conn.execute(
        "SELECT reminders_enabled FROM users WHERE telegram_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return bool(row["reminders_enabled"]) if row else True


def set_reminders_enabled(user_id, enabled):
    conn = _connect()
    conn.execute(
        "UPDATE users SET reminders_enabled = ? WHERE telegram_id = ?",
        (1 if enabled else 0, user_id),
    )
    conn.commit()
    conn.close()


def get_warranty_reminders():
    """Games with warranty expiring in 7, 3, or 0 days."""
    conn = _connect()
    rows = conn.execute(
        """
        SELECT user_id, id, game_name, seller_contact, warranty_until
        FROM games WHERE warranty_until IS NOT NULL
        AND date(warranty_until) >= date('now')
        AND date(warranty_until) <= date('now', '+7 days')
        """
    ).fetchall()
    conn.close()

    today = datetime.now().date()
    reminders = []
    for row in rows:
        try:
            expiry = datetime.fromisoformat(row["warranty_until"]).date()
        except ValueError:
            expiry = datetime.strptime(row["warranty_until"][:10], "%Y-%m-%d").date()
        days_left = (expiry - today).days
        if days_left in (0, 3, 7):
            reminders.append({
                "user_id": row["user_id"],
                "game_id": row["id"],
                "game_name": row["game_name"],
                "seller_contact": row["seller_contact"],
                "warranty_until": row["warranty_until"],
                "days_left": days_left,
            })
    return reminders


def export_user_games(user_id):
    conn = _connect()
    rows = conn.execute(
        f"""
        SELECT game_name, store_url, username, password, image_url, purchase_date,
               notes, seller_contact, price_paid, currency, warranty_until, is_favorite
        FROM games WHERE user_id = ? ORDER BY id
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def import_user_games(user_id, games, merge=True):
    if not merge:
        conn = _connect()
        conn.execute("DELETE FROM games WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    count = 0
    for g in games:
        add_game(
            user_id,
            g.get("game_name", "Unknown"),
            g.get("store_url", ""),
            g.get("username", ""),
            g.get("password"),
            g.get("image_url"),
            g.get("notes"),
            g.get("seller_contact"),
            g.get("price_paid"),
            g.get("currency", "USD"),
            g.get("warranty_until"),
        )
        count += 1
    return count
