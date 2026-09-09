# bot.py
import json
import io
import time
from datetime import time as dt_time

DEBOUNCE_SECONDS = 2.0

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    CopyTextButton,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import BOT_TOKEN
import database as db
import steam as steam_api
from quick_add import parse_quick_add, format_quick_add_preview

TG_FILE_PREFIX = "tgfile:"

MENU_ADD = "➕ Add Game"
MENU_QUICK_ADD = "⚡ Quick Add"
MENU_LIBRARY = "📚 My Library"
MENU_SEARCH = "🔍 Search"
MENU_STORES = "🏪 Stores"
MENU_STATS = "📊 My Stats"
MENU_HELP = "❓ Help"
MENU_HOME = "🏠 Menu"
MENU_CANCEL = "❌ Cancel"

# Add-game conversation states
(
    GAME_NAME, STORE_URL, USERNAME, PASSWORD, IMAGE_URL,
    SELLER, PRICE, WARRANTY, NOTES,
) = range(9)

# Edit-game conversation
(EDIT_VALUE,) = range(9, 10)

# Quick-add conversation
(QUICK_ADD_INPUT, QUICK_ADD_CONFIRM) = range(10, 12)

# Store picker (within add-game flow)
STORE_PICK = 12

# Add-store conversation (standalone + sub-flow inside add-game)
(STORE_NAME, STORE_URL_INPUT, STORE_CONTACT) = range(13, 16)

# Edit-store conversation
(STORE_EDIT_VALUE,) = range(16, 17)


def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [MENU_ADD, MENU_QUICK_ADD],
            [MENU_LIBRARY, MENU_SEARCH],
            [MENU_STORES, MENU_STATS],
            [MENU_HELP, MENU_HOME],
            [MENU_CANCEL],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def menu_fallbacks():
    return [
        MessageHandler(filters.Regex(f"^{MENU_LIBRARY}$"), handle_menu_library),
        MessageHandler(filters.Regex(f"^{MENU_STORES}$"), handle_menu_stores),
        MessageHandler(filters.Regex(f"^{MENU_STATS}$"), handle_menu_stats),
        MessageHandler(filters.Regex(f"^{MENU_HELP}$"), handle_menu_help),
        MessageHandler(filters.Regex(f"^{MENU_HOME}$"), handle_menu_home),
        MessageHandler(filters.Text([MENU_CANCEL]), cancel_conversation),
        CommandHandler("cancel", cancel_conversation),
    ]


def is_spam_action(context: ContextTypes.DEFAULT_TYPE, key: str) -> bool:
    """True if the same action was triggered again within the debounce window."""
    now = time.monotonic()
    debounce = context.user_data.setdefault("_debounce", {})
    last = debounce.get(key, 0.0)
    if now - last < DEBOUNCE_SECONDS:
        return True
    debounce[key] = now
    return False


async def debounce_query(query, context: ContextTypes.DEFAULT_TYPE, key: str) -> bool:
    """Answer callback and return True if the action should be skipped."""
    if is_spam_action(context, key):
        await query.answer()
        return True
    await query.answer()
    return False


async def send_or_edit_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action_key: str,
    text: str,
    reply_markup=None,
    parse_mode="Markdown",
) -> bool:
    """Update the last bot panel in chat, or send a new one. Returns False if debounced."""
    if is_spam_action(context, action_key):
        return False

    chat = update.effective_chat
    bot = context.bot
    msg_id = context.user_data.get("_panel_msg_id")

    if msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat.id,
                message_id=msg_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return True
        except BadRequest:
            try:
                await bot.delete_message(chat.id, msg_id)
            except BadRequest:
                pass

    sent = await update.message.reply_text(
        text, reply_markup=reply_markup, parse_mode=parse_mode
    )
    context.user_data["_panel_msg_id"] = sent.message_id
    return True


def photo_source(image_url):
    if not image_url:
        return None
    if image_url.startswith(TG_FILE_PREFIX):
        return image_url[len(TG_FILE_PREFIX):]
    return image_url


def format_warranty_status(warranty_until):
    if not warranty_until:
        return None
    try:
        from datetime import datetime
        expiry = datetime.fromisoformat(warranty_until).date()
    except ValueError:
        from datetime import datetime
        expiry = datetime.strptime(warranty_until[:10], "%Y-%m-%d").date()
    today = datetime.now().date()
    days_left = (expiry - today).days
    if days_left < 0:
        return "Expired"
    if days_left == 0:
        return "Expires today"
    return f"Active ({days_left} days left)"


def format_store_details(store, games):
    lines = [
        f"🏪 **{store['name']}**",
        "━━━━━━━━━━━━━━━",
        f"🔗 **URL:** {store['url']}",
    ]
    if store.get("contact_info"):
        lines.append(f"📞 **Contact:** {store['contact_info']}")
    lines.append("")
    lines.append(f"🎮 **Linked Games ({len(games)}):**")
    if games:
        for g in games:
            lines.append(f"   • {g['game_name']}")
    else:
        lines.append("   • None yet")
    return "\n".join(lines)


def build_store_detail_keyboard(store):
    store_id = store["id"]
    rows = []
    if store["url"].startswith("http"):
        rows.append([InlineKeyboardButton("🔗 Open Store", url=store["url"])])
    rows.append([
        InlineKeyboardButton("✏️ Edit", callback_data=f"store_edit_{store_id}"),
        InlineKeyboardButton("🗑️ Delete", callback_data=f"store_del_{store_id}"),
    ])
    rows.append([InlineKeyboardButton("🔙 Back to Stores", callback_data="stores_list")])
    return InlineKeyboardMarkup(rows)


def format_game_details(game):
    purchase_date = game.get("purchase_date") or "Unknown"
    warranty = format_warranty_status(game.get("warranty_until"))
    price_line = ""
    if game.get("price_paid"):
        currency = game.get("currency") or "USD"
        price_line = f"\n💰 **Price:** {game['price_paid']} {currency}"
    seller_line = ""
    if game.get("seller_contact"):
        seller_line = f"\n🛒 **Seller:** {game['seller_contact']}"
    warranty_line = f"\n🛡️ **Warranty:** {warranty}" if warranty else ""

    message = f"""🎮 **{game['game_name']}**{' ⭐' if game.get('is_favorite') else ''}
━━━━━━━━━━━━━━━

🔗 **Store:** {game['store_url']}
👤 **Username:** `{game['username']}`
🔑 **Password:** `{game['password'] if game['password'] else 'Not provided'}`{price_line}{seller_line}{warranty_line}

📅 **Purchased:** {purchase_date[:10]}

📝 **Notes:** {game['notes'] if game['notes'] else 'None'}

━━━━━━━━━━━━━━━
💡 *Use /mylibrary to see all games*"""
    return message, game["id"]


def build_game_detail_keyboard(game):
    store_url = game.get("store_url") or ""
    store_btn = []
    if store_url.startswith("http"):
        store_btn = [InlineKeyboardButton("🔗 Open Store", url=store_url)]

    copy_row = []
    if game.get("username"):
        copy_row.append(
            InlineKeyboardButton(
                "📋 Copy User",
                copy_text=CopyTextButton(text=game["username"]),
            )
        )
    if game.get("password"):
        copy_row.append(
            InlineKeyboardButton(
                "📋 Copy Pass",
                copy_text=CopyTextButton(text=game["password"]),
            )
        )

    fav_label = "⭐ Unfavorite" if game.get("is_favorite") else "☆ Favorite"
    rows = []
    if copy_row:
        rows.append(copy_row)
    if store_btn:
        rows.append(store_btn)
    rows.append([
        InlineKeyboardButton("✏️ Edit", callback_data=f"edit_{game['id']}"),
        InlineKeyboardButton(fav_label, callback_data=f"fav_{game['id']}"),
    ])
    rows.append([InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_{game['id']}")])
    rows.append([
        InlineKeyboardButton("🔙 Back to Library", callback_data="list"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="menu"),
    ])
    return InlineKeyboardMarkup(rows)


def build_library_inline_keyboard(games, list_mode="all"):
    keyboard = [
        [
            InlineKeyboardButton("⭐ Favorites", callback_data="list_fav"),
            InlineKeyboardButton("🕐 Recent", callback_data="list_recent"),
            InlineKeyboardButton("📚 All", callback_data="list_all"),
        ],
    ]
    for game in games:
        game_id = game[0]
        game_name = game[1]
        is_fav = game[5] if len(game) > 5 else 0
        prefix = "⭐ " if is_fav else "🎮 "
        keyboard.append([
            InlineKeyboardButton(f"{prefix}{game_name}", callback_data=f"view_{game_id}")
        ])
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")])
    return InlineKeyboardMarkup(keyboard)


def format_stats_message(user_id):
    stats = db.get_extended_stats(user_id)
    store_lines = ""
    for store, cnt in stats["top_stores"]:
        store_lines += f"\n   • {store} ({cnt})"
    if not store_lines:
        store_lines = "\n   • None yet"

    return (
        f"📊 **Your Library Stats**\n\n"
        f"🎮 **Total Games:** {stats['total']}\n"
        f"🏪 **Unique Stores:** {stats['stores']}\n"
        f"💰 **Total Spent:** {stats['total_spent']:.2f}\n"
        f"📅 **Added This Month:** {stats['added_this_month']}\n"
        f"🛡️ **Warranties Expiring (30d):** {stats['expiring_soon']}\n\n"
        f"🏪 **Top Stores:**{store_lines}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💡 /reminders on|off — warranty alerts"
    )


async def safe_edit_or_send(query, text, reply_markup=None, parse_mode="Markdown"):
    try:
        if query.message.photo:
            await query.message.delete()
            await query.message.chat.send_message(
                text, reply_markup=reply_markup, parse_mode=parse_mode
            )
        else:
            await query.edit_message_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode
            )
    except BadRequest:
        await query.message.reply_text(
            text, reply_markup=reply_markup, parse_mode=parse_mode
        )


async def safe_send_game_details(query, game, reply_markup=None):
    if reply_markup is None:
        reply_markup = build_game_detail_keyboard(game)
    message, _ = format_game_details(game)
    photo = photo_source(game.get("image_url"))

    if photo:
        try:
            if query.message.photo:
                await query.edit_message_caption(
                    caption=message, reply_markup=reply_markup, parse_mode="Markdown"
                )
            else:
                await query.message.delete()
                await query.message.chat.send_photo(
                    photo=photo,
                    caption=message,
                    reply_markup=reply_markup,
                    parse_mode="Markdown",
                )
        except BadRequest:
            await query.message.reply_photo(
                photo=photo,
                caption=message,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
    else:
        await safe_edit_or_send(query, message, reply_markup)


# ─── Start & menu ───────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.register_user(user.id, user.first_name, user.username)

    await update.message.reply_text(
        f"🎮 **Welcome {user.first_name}!**\n\n"
        f"Your personal game library bot.\n"
        f"Track all your purchased shared Steam accounts.\n\n"
        f"Use the menu below — it stays open at all times:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id

    if await debounce_query(query, context, f"cb_{query.data}"):
        return ConversationHandler.END

    if query.data == "add":
        await query.edit_message_text("🎮 Send the **game name** or paste a **Steam store link**:")
        return GAME_NAME
    if query.data == "list":
        await show_library(query, context, user_id, "all")
        return ConversationHandler.END
    if query.data == "search":
        await query.edit_message_text("🔍 Send a **keyword** (game, store, seller, notes):")
        return "SEARCH_MODE"
    if query.data == "stats":
        await show_stats(query, user_id)
        return ConversationHandler.END
    if query.data == "help":
        await show_help(query)
        return ConversationHandler.END
    return ConversationHandler.END


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await debounce_query(query, context, "cb_menu"):
        return
    inline = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Game", callback_data="add")],
        [InlineKeyboardButton("📚 My Library", callback_data="list")],
        [InlineKeyboardButton("🔍 Search", callback_data="search")],
        [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ])
    await safe_edit_or_send(
        query,
        "🎮 **My Game Library Bot**\n\nWhat would you like to do?",
        reply_markup=inline,
    )


# ─── Add game flow ──────────────────────────────────────────────────────────

async def prompt_add_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_spam_action(context, "menu_add"):
        return ConversationHandler.END
    await _delete_menu_message(update)
    await update.message.reply_text(
        "🎮 Send the **game name** or paste a **Steam store link**:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return GAME_NAME


async def prompt_after_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip cover image step when Steam already provided one. Skip seller when store is linked."""
    if context.user_data.get("image_url"):
        if context.user_data.get("store_id"):
            await update.message.reply_text(
                "📝 **Notes** (2FA info, etc.) or /skip:",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown",
            )
            return NOTES
        await update.message.reply_text(
            "🛒 **Seller contact** (Telegram @, Discord, etc.) or /skip:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return SELLER
    await update.message.reply_text(
        "🖼️ Send a **photo** or image URL (or /skip):",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return IMAGE_URL


async def add_game_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    app_id = steam_api.extract_steam_app_id(text)

    if app_id:
        try:
            details = await steam_api.fetch_steam_app_details(app_id)
            if details and details.get("name"):
                context.user_data["game_name"] = details["name"]
                context.user_data["from_steam"] = True
                if details.get("header_image"):
                    context.user_data["image_url"] = details["header_image"]
                filled = [f"🎮 **{details['name']}**"]
                if context.user_data.get("image_url"):
                    filled.append("🖼️ Cover image loaded")
                await update.message.reply_text(
                    "✅ Found on Steam:\n" + "\n".join(filled),
                    reply_markup=main_menu_keyboard(),
                    parse_mode="Markdown",
                )
        except Exception:
            pass

    if not context.user_data.get("game_name"):
        context.user_data.pop("from_steam", None)
        context.user_data["game_name"] = text

    return await _prompt_store_picker(update, context)


async def _prompt_store_picker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stores = db.get_all_stores(user_id)

    keyboard = []
    for store in stores:
        keyboard.append([InlineKeyboardButton(
            f"🏪 {store['name']}",
            callback_data=f"pick_store_{store['id']}",
        )])
    keyboard.append([
        InlineKeyboardButton("➕ New Store", callback_data="pick_store_new"),
        InlineKeyboardButton("✏️ Manual", callback_data="pick_store_manual"),
    ])

    header = "🏪 **Where did you buy it?**"
    body = "\n\n_(No stores saved yet)_" if not stores else "\n\nPick a store or add one:"
    await update.message.reply_text(
        header + body,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return STORE_PICK


async def store_pick_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "pick_store_manual":
        await query.edit_message_text(
            "🔗 Send the **store URL** (where you bought it):",
            parse_mode="Markdown",
        )
        return STORE_URL

    if query.data == "pick_store_new":
        await query.edit_message_text(
            "🏪 **New Store** — send the store **name**:",
            parse_mode="Markdown",
        )
        return STORE_NAME

    store_id = int(query.data.rsplit("_", 1)[1])
    store = db.get_store_by_id(update.effective_user.id, store_id)
    if not store:
        await query.edit_message_text("❌ Store not found. Try again.")
        return STORE_PICK

    context.user_data["store_url"] = store["url"]
    context.user_data["seller_contact"] = store.get("contact_info")
    context.user_data["store_id"] = store["id"]

    await query.edit_message_text(
        f"✅ Store: **{store['name']}**\n\n👤 Send the **Steam username or email**:",
        parse_mode="Markdown",
    )
    return USERNAME


# New-store sub-flow within the add-game conversation

async def game_add_new_store_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_ns_name"] = update.message.text.strip()
    await update.message.reply_text(
        "🔗 Send the store **URL**:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return STORE_URL_INPUT


async def game_add_new_store_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_ns_url"] = update.message.text.strip()
    await update.message.reply_text(
        "📞 **Contact info** (Telegram @, Discord, etc.) or /skip:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return STORE_CONTACT


async def game_add_new_store_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_ns_contact"] = update.message.text.strip()
    return await _finish_game_add_store(update, context)


async def game_add_new_store_skip_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_ns_contact"] = None
    return await _finish_game_add_store(update, context)


async def _finish_game_add_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = context.user_data.pop("_ns_name")
    url = context.user_data.pop("_ns_url")
    contact = context.user_data.pop("_ns_contact", None)

    store_id = db.add_store(user_id, name, url, contact)
    context.user_data["store_url"] = url
    context.user_data["seller_contact"] = contact
    context.user_data["store_id"] = store_id

    await update.message.reply_text(
        f"✅ Store **{name}** saved!\n\n👤 Send the **Steam username or email**:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return USERNAME


async def add_store_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["store_url"] = update.message.text
    await update.message.reply_text(
        "👤 Send the **Steam username or email**:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return USERNAME


async def add_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["username"] = update.message.text
    await update.message.reply_text(
        "🔑 Send the **password** (or /skip):",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return PASSWORD


async def add_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["password"] = update.message.text
    return await prompt_after_password(update, context)


async def skip_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["password"] = None
    return await prompt_after_password(update, context)


async def add_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        context.user_data["image_url"] = f"{TG_FILE_PREFIX}{update.message.photo[-1].file_id}"
    else:
        context.user_data["image_url"] = update.message.text
    if context.user_data.get("store_id"):
        await update.message.reply_text(
            "📝 **Notes** (2FA info, etc.) or /skip:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return NOTES
    await update.message.reply_text(
        "🛒 **Seller contact** (Telegram @, Discord, etc.) or /skip:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return SELLER


async def skip_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("image_url", None)
    if context.user_data.get("store_id"):
        await update.message.reply_text(
            "📝 **Notes** or /skip:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return NOTES
    await update.message.reply_text(
        "🛒 **Seller contact** or /skip:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return SELLER


async def add_seller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["seller_contact"] = update.message.text
    await update.message.reply_text(
        "📝 **Notes** (2FA info, etc.) or /skip:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return NOTES


async def skip_seller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("seller_contact"):
        context.user_data["seller_contact"] = None
    await update.message.reply_text(
        "📝 **Notes** or /skip:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return NOTES


async def add_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["notes"] = update.message.text
    await save_game(update, context)
    return ConversationHandler.END


async def skip_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["notes"] = None
    await save_game(update, context)
    return ConversationHandler.END


async def save_game(update: Update, context):
    user_id = update.effective_user.id
    ud = context.user_data
    db.add_game(
        user_id,
        ud["game_name"],
        ud["store_url"],
        ud["username"],
        ud.get("password"),
        ud.get("image_url"),
        ud.get("notes"),
        ud.get("seller_contact"),
        ud.get("price_paid"),
        ud.get("currency", "USD"),
        ud.get("warranty_until"),
        ud.get("store_id"),
    )
    await update.message.reply_text(
        f"✅ **Game added!**\n\n🎮 {ud['game_name']}\n"
        f"Tap **My Library** to view it.",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


# ─── Quick add ──────────────────────────────────────────────────────────────

async def prompt_quick_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_spam_action(context, "menu_quick_add"):
        return ConversationHandler.END
    await _delete_menu_message(update)
    await update.message.reply_text(
        "⚡ **Quick Add** — paste a block like:\n\n"
        "```\n"
        "Game: Elden Ring\n"
        "User: foo@email.com\n"
        "Pass: bar123\n"
        "Store: g2g.com/...\n"
        "Seller: @vendor\n"
        "Price: 15.99\n"
        "Warranty: 2026-07-01\n"
        "Notes: ask seller for 2FA\n"
        "```",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return QUICK_ADD_INPUT


async def quick_add_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed = parse_quick_add(update.message.text)
    if not parsed.get("game_name") or not parsed.get("username"):
        await update.message.reply_text(
            "❌ Need at least **Game** and **User** fields. Try again or /cancel.",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return QUICK_ADD_INPUT

    context.user_data["quick_add_data"] = parsed
    preview = format_quick_add_preview(parsed)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="quickadd_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="quickadd_cancel"),
        ],
    ])
    await update.message.reply_text(
        f"📋 **Preview:**\n\n{preview}\n\nSave this game?",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return QUICK_ADD_CONFIRM


async def quick_add_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "quickadd_cancel":
        await query.answer()
        await query.edit_message_text("❌ Quick add cancelled.")
        return ConversationHandler.END

    if await debounce_query(query, context, "quickadd_confirm"):
        return QUICK_ADD_CONFIRM

    data = context.user_data.get("quick_add_data", {})
    user_id = update.effective_user.id
    db.add_game(
        user_id,
        data.get("game_name", "Unknown"),
        data.get("store_url", "N/A"),
        data["username"],
        data.get("password"),
        None,
        data.get("notes"),
        data.get("seller_contact"),
        data.get("price_paid"),
        "USD",
        data.get("warranty_until"),
    )
    await query.edit_message_text(f"✅ **{data['game_name']}** added via Quick Add!")
    return ConversationHandler.END


# ─── Edit game ──────────────────────────────────────────────────────────────

EDIT_FIELDS = {
    "name": ("game_name", "game name"),
    "store": ("store_url", "store URL"),
    "user": ("username", "username"),
    "pass": ("password", "password"),
    "photo": ("image_url", "photo or image URL"),
    "seller": ("seller_contact", "seller contact"),
    "price": ("price_paid", "price (number)"),
    "warranty": ("warranty_until", "warranty date YYYY-MM-DD"),
    "notes": ("notes", "notes"),
}


async def edit_pick_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    game_id = int(query.data.split("_")[1])
    if await debounce_query(query, context, f"cb_edit_{game_id}"):
        return
    context.user_data["edit_game_id"] = game_id

    keyboard = [
        [
            InlineKeyboardButton("Name", callback_data="editf_name"),
            InlineKeyboardButton("Store", callback_data="editf_store"),
            InlineKeyboardButton("User", callback_data="editf_user"),
        ],
        [
            InlineKeyboardButton("Password", callback_data="editf_pass"),
            InlineKeyboardButton("Photo", callback_data="editf_photo"),
            InlineKeyboardButton("Seller", callback_data="editf_seller"),
        ],
        [
            InlineKeyboardButton("Price", callback_data="editf_price"),
            InlineKeyboardButton("Warranty", callback_data="editf_warranty"),
            InlineKeyboardButton("Notes", callback_data="editf_notes"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data=f"view_{game_id}")],
    ]
    await safe_edit_or_send(
        query,
        "✏️ **What do you want to edit?**",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def edit_start_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    field_key = query.data.replace("editf_", "")
    if await debounce_query(query, context, f"cb_editf_{field_key}"):
        return ConversationHandler.END
    db_field, label = EDIT_FIELDS[field_key]
    context.user_data["edit_field"] = db_field
    context.user_data["edit_field_key"] = field_key

    if field_key == "photo":
        await query.message.reply_text(
            f"Send new **{label}** (or /skip to remove):",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
    else:
        await query.message.reply_text(
            f"Send new **{label}**:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
    return EDIT_VALUE


async def edit_skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["edit_field_key"] = "photo"
    context.user_data["edit_field"] = "image_url"
    user_id = update.effective_user.id
    game_id = context.user_data["edit_game_id"]
    db.update_game(user_id, game_id, image_url=None)
    game = db.get_game_by_id(user_id, game_id)
    await update.message.reply_text(
        "✅ Photo removed.",
        reply_markup=main_menu_keyboard(),
    )
    message, _ = format_game_details(game)
    markup = build_game_detail_keyboard(game)
    await update.message.reply_text(message, reply_markup=markup, parse_mode="Markdown")
    return ConversationHandler.END


async def edit_receive_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    game_id = context.user_data["edit_game_id"]
    field = context.user_data["edit_field"]
    field_key = context.user_data.get("edit_field_key")

    if field_key == "photo":
        if update.message.photo:
            value = f"{TG_FILE_PREFIX}{update.message.photo[-1].file_id}"
        elif update.message.text and update.message.text.strip() == "/skip":
            value = None
        else:
            value = update.message.text
    elif field == "price_paid":
        try:
            value = float(update.message.text.replace("$", "").strip())
        except ValueError:
            await update.message.reply_text("❌ Invalid price. Send a number.")
            return EDIT_VALUE
    else:
        value = update.message.text

    db.update_game(user_id, game_id, **{field: value})
    game = db.get_game_by_id(user_id, game_id)
    await update.message.reply_text(
        f"✅ Updated **{EDIT_FIELDS[field_key][1]}**!",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    message, _ = format_game_details(game)
    photo = photo_source(game.get("image_url"))
    markup = build_game_detail_keyboard(game)
    if photo:
        await update.message.reply_photo(
            photo=photo, caption=message, reply_markup=markup, parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(message, reply_markup=markup, parse_mode="Markdown")
    return ConversationHandler.END


# ─── Library & game view ────────────────────────────────────────────────────

def _library_title(games, list_mode):
    labels = {"all": "Your Library", "fav": "Favorites", "recent": "Recently Viewed"}
    return f"📚 **{labels.get(list_mode, 'Your Library')}** ({len(games)} games)\n\nTap a game:"


async def reply_library(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id, list_mode="all"):
    if list_mode == "fav":
        games = db.get_all_games(user_id, favorites_only=True)
    elif list_mode == "recent":
        games = db.get_recent_games(user_id)
    else:
        games = db.get_all_games(user_id)

    action_key = f"library_{list_mode}"

    if not games:
        msg = "📭 **Nothing here yet.**" if list_mode != "all" else "📭 **Your library is empty.**"
        if not await send_or_edit_panel(
            update, context, action_key,
            f"{msg}\n\nTap **Add Game** to get started.",
        ):
            return
        return

    await send_or_edit_panel(
        update, context, action_key,
        _library_title(games, list_mode),
        reply_markup=build_library_inline_keyboard(games, list_mode),
    )


async def show_library(query, context, user_id, list_mode="all"):
    if list_mode == "fav":
        games = db.get_all_games(user_id, favorites_only=True)
    elif list_mode == "recent":
        games = db.get_recent_games(user_id)
    else:
        games = db.get_all_games(user_id)

    empty_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Game", callback_data="add")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")],
    ])

    if not games:
        msg = "📭 **Nothing here yet.**" if list_mode != "all" else "📭 **Your library is empty.**"
        await safe_edit_or_send(query, f"{msg}\n\nTap **Add Game** to get started.", empty_markup)
        return

    await safe_edit_or_send(
        query,
        _library_title(games, list_mode),
        build_library_inline_keyboard(games, list_mode),
    )


async def library_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    mode = query.data.replace("list_", "")
    if await debounce_query(query, context, f"cb_list_{mode}"):
        return
    await show_library(query, context, update.effective_user.id, mode)


async def view_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    game_id = int(query.data.split("_")[1])
    if await debounce_query(query, context, f"cb_view_{game_id}"):
        return
    user_id = update.effective_user.id
    game = db.get_game_by_id(user_id, game_id)

    if not game:
        await safe_edit_or_send(query, "❌ Game not found.")
        return

    db.touch_last_viewed(user_id, game_id)
    game = db.get_game_by_id(user_id, game_id)
    await safe_send_game_details(query, game)


async def toggle_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    game_id = int(query.data.split("_")[1])
    if await debounce_query(query, context, f"cb_fav_{game_id}"):
        return
    user_id = update.effective_user.id
    db.toggle_favorite(user_id, game_id)
    game = db.get_game_by_id(user_id, game_id)
    await safe_send_game_details(query, game)


async def delete_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    game_id = int(query.data.split("_")[1])
    if await debounce_query(query, context, f"cb_delete_{game_id}"):
        return
    user_id = update.effective_user.id
    db.delete_game(user_id, game_id)
    await show_library(query, context, user_id)


# ─── Search ─────────────────────────────────────────────────────────────────

async def prompt_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_spam_action(context, "menu_search"):
        return ConversationHandler.END
    await _delete_menu_message(update)
    await update.message.reply_text(
        "🔍 Send a **keyword** (game, store, seller, username, notes):",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return "SEARCH_MODE"


async def search_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyword = update.message.text
    games = db.search_games(user_id, keyword)

    if not games:
        await update.message.reply_text(
            f"🔍 No games found matching '{keyword}'",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    message = f"🔍 **Results for '{keyword}':**\n\n"
    for game_id, game_name, store_url, username in games:
        message += f"🎮 **{game_name}**\n   👤 {username}\n   🔗 {store_url}\n   /view_{game_id}\n\n"

    await update.message.reply_text(
        message, reply_markup=main_menu_keyboard(), parse_mode="Markdown"
    )
    return ConversationHandler.END


# ─── Stats & help ───────────────────────────────────────────────────────────

async def show_stats(query, user_id):
    message = format_stats_message(user_id)
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")],
    ])
    await safe_edit_or_send(query, message, markup)


async def reply_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
    await send_or_edit_panel(
        update, context, "menu_stats",
        format_stats_message(user_id),
    )


async def show_help(query):
    help_text = (
        "❓ **How to use this bot**\n\n"
        "➕ **Add Game** — step-by-step entry (Steam links auto-fill)\n"
        "⚡ **Quick Add** — paste seller message block\n"
        "📚 **My Library** — Favorites / Recent / All filters\n"
        "✏️ **Edit** — update any field without re-adding\n"
        "📋 **Copy User/Pass** — one-tap on game details\n\n"
        "**Commands:**\n"
        "/start /add /mylibrary /view /search /stats\n"
        "/export /import /reminders on|off /clear\n\n"
        "⚠️ Your data is private per user."
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")],
    ])
    await safe_edit_or_send(query, help_text, markup)


async def reply_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_or_edit_panel(
        update, context, "menu_help",
        "❓ Tap **Help** in inline menu or use /start for full guide.",
    )


async def reply_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_or_edit_panel(
        update, context, "menu_home",
        "🎮 **My Game Library Bot**\n\nUse the menu below.",
    )


async def _delete_menu_message(update: Update):
    try:
        await update.message.delete()
    except BadRequest:
        pass


async def handle_menu_library(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _delete_menu_message(update)
    await reply_library(update, context, update.effective_user.id)
    return ConversationHandler.END


async def handle_menu_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _delete_menu_message(update)
    await reply_stats(update, context, update.effective_user.id)
    return ConversationHandler.END


async def handle_menu_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _delete_menu_message(update)
    await reply_help(update, context)
    return ConversationHandler.END


async def handle_menu_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _delete_menu_message(update)
    await reply_menu(update, context)
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _delete_menu_message(update)
    await update.message.reply_text(
        "❌ Cancelled.", reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END


# ─── Stores ──────────────────────────────────────────────────────────────────

def _stores_keyboard(stores, user_id=None):
    keyboard = []
    for store in stores:
        keyboard.append([InlineKeyboardButton(f"🏪 {store['name']}", callback_data=f"store_view_{store['id']}")])
    keyboard.append([InlineKeyboardButton("➕ Add Store", callback_data="store_add")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")])
    return InlineKeyboardMarkup(keyboard)


def _stores_text(stores):
    if stores:
        return f"🏪 **Your Stores** ({len(stores)} saved)\n\nTap a store to manage it:"
    return "🏪 **No stores saved yet.**\n\nAdd your first store:"


async def show_stores(query, context, user_id):
    stores = db.get_all_stores(user_id)
    await safe_edit_or_send(query, _stores_text(stores), _stores_keyboard(stores))


async def reply_stores(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
    stores = db.get_all_stores(user_id)
    await send_or_edit_panel(
        update, context, "menu_stores",
        _stores_text(stores),
        reply_markup=_stores_keyboard(stores),
    )


async def handle_menu_stores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _delete_menu_message(update)
    await reply_stores(update, context, update.effective_user.id)
    return ConversationHandler.END


async def stores_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await debounce_query(query, context, "cb_stores_list"):
        return
    await show_stores(query, context, update.effective_user.id)


async def view_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    store_id = int(query.data.rsplit("_", 1)[1])
    if await debounce_query(query, context, f"cb_store_view_{store_id}"):
        return
    user_id = update.effective_user.id
    store = db.get_store_by_id(user_id, store_id)
    if not store:
        await safe_edit_or_send(query, "❌ Store not found.")
        return
    games = db.get_games_by_store_id(user_id, store_id)
    await safe_edit_or_send(query, format_store_details(store, games), build_store_detail_keyboard(store))


# ─── Store add (standalone) ───────────────────────────────────────────────────

async def store_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await debounce_query(query, context, "cb_store_add"):
        return ConversationHandler.END
    await query.edit_message_text("🏪 **New Store** — send the store **name**:", parse_mode="Markdown")
    return STORE_NAME


async def store_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_ns_name"] = update.message.text.strip()
    await update.message.reply_text(
        "🔗 Send the store **URL**:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return STORE_URL_INPUT


async def store_add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_ns_url"] = update.message.text.strip()
    await update.message.reply_text(
        "📞 **Contact info** (Telegram @, Discord, etc.) or /skip:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return STORE_CONTACT


async def store_add_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_ns_contact"] = update.message.text.strip()
    return await _save_new_store(update, context)


async def store_add_skip_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_ns_contact"] = None
    return await _save_new_store(update, context)


async def _save_new_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = context.user_data.pop("_ns_name")
    url = context.user_data.pop("_ns_url")
    contact = context.user_data.pop("_ns_contact", None)
    db.add_store(user_id, name, url, contact)
    await update.message.reply_text(
        f"✅ **{name}** added to your stores!\n\nTap **Stores** to manage them.",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ─── Store edit ───────────────────────────────────────────────────────────────

STORE_EDIT_FIELDS = {
    "name": ("name", "store name"),
    "url": ("url", "store URL"),
    "contact": ("contact_info", "contact info"),
}


async def store_edit_pick_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    store_id = int(query.data.rsplit("_", 1)[1])
    if await debounce_query(query, context, f"cb_store_edit_{store_id}"):
        return
    context.user_data["edit_store_id"] = store_id
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Name", callback_data="store_editf_name"),
            InlineKeyboardButton("URL", callback_data="store_editf_url"),
            InlineKeyboardButton("Contact", callback_data="store_editf_contact"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data=f"store_view_{store_id}")],
    ])
    await safe_edit_or_send(query, "✏️ **What do you want to edit?**", keyboard)


async def store_edit_start_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    field_key = query.data.replace("store_editf_", "")
    if await debounce_query(query, context, f"cb_store_editf_{field_key}"):
        return ConversationHandler.END
    _, label = STORE_EDIT_FIELDS[field_key]
    context.user_data["edit_store_field_key"] = field_key
    await query.message.reply_text(
        f"Send new **{label}**:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return STORE_EDIT_VALUE


async def store_edit_receive_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    store_id = context.user_data["edit_store_id"]
    field_key = context.user_data["edit_store_field_key"]
    db_field, label = STORE_EDIT_FIELDS[field_key]
    db.update_store(user_id, store_id, **{db_field: update.message.text.strip()})
    store = db.get_store_by_id(user_id, store_id)
    games = db.get_games_by_store_id(user_id, store_id)
    await update.message.reply_text(
        f"✅ Updated **{label}**!",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    await update.message.reply_text(
        format_store_details(store, games),
        reply_markup=build_store_detail_keyboard(store),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ─── Store delete ─────────────────────────────────────────────────────────────

async def delete_store_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    store_id = int(query.data.rsplit("_", 1)[1])
    if await debounce_query(query, context, f"cb_store_del_{store_id}"):
        return
    user_id = update.effective_user.id
    store = db.get_store_by_id(user_id, store_id)
    linked = db.get_games_by_store_id(user_id, store_id)
    warning = f"\n\n⚠️ **{len(linked)} game(s)** will be unlinked." if linked else ""
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, delete", callback_data=f"store_delok_{store_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"store_view_{store_id}"),
    ]])
    await safe_edit_or_send(query, f"🗑️ Delete **{store['name']}**?{warning}", keyboard)


async def delete_store_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    store_id = int(query.data.rsplit("_", 1)[1])
    if await debounce_query(query, context, f"cb_store_delok_{store_id}"):
        return
    db.delete_store(update.effective_user.id, store_id)
    await show_stores(query, context, update.effective_user.id)


# ─── Commands ─────────────────────────────────────────────────────────────────

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg_id = context.user_data.get("_panel_msg_id")
    if msg_id:
        try:
            await context.bot.delete_message(chat_id, msg_id)
        except BadRequest:
            pass
    try:
        await update.message.delete()
    except BadRequest:
        pass
    context.user_data.clear()
    user = update.effective_user
    await context.bot.send_message(
        chat_id,
        f"🎮 **Welcome back, {user.first_name}!**\n\nChat cleared. Use the menu below:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


async def mylibrary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_library(update, context, update.effective_user.id)


async def view_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "Usage: `/view <game_id>`",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return

    try:
        game_id = int(context.args[0])
        game = db.get_game_by_id(user_id, game_id)
        if not game:
            await update.message.reply_text(
                "❌ Game not found.", reply_markup=main_menu_keyboard()
            )
            return

        db.touch_last_viewed(user_id, game_id)
        message, _ = format_game_details(game)
        markup = build_game_detail_keyboard(game)
        photo = photo_source(game.get("image_url"))
        if photo:
            await update.message.reply_photo(
                photo=photo, caption=message, reply_markup=markup, parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                message, reply_markup=markup, parse_mode="Markdown"
            )
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid ID.", reply_markup=main_menu_keyboard()
        )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "Usage: `/search <keyword>`",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return

    keyword = " ".join(context.args)
    games = db.search_games(user_id, keyword)
    if not games:
        await update.message.reply_text(
            f"🔍 No results for '{keyword}'",
            reply_markup=main_menu_keyboard(),
        )
        return

    message = f"🔍 **Results for '{keyword}':**\n\n"
    for game_id, game_name, store_url, username in games:
        message += f"🎮 `{game_id}` - **{game_name}**\n   👤 {username}\n\n"
    await update.message.reply_text(
        message, reply_markup=main_menu_keyboard(), parse_mode="Markdown"
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_stats(update, context, update.effective_user.id)


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    games = db.export_user_games(user_id)
    if not games:
        await update.message.reply_text(
            "📭 Nothing to export.", reply_markup=main_menu_keyboard()
        )
        return

    data = json.dumps(games, indent=2, ensure_ascii=False)
    bio = io.BytesIO(data.encode("utf-8"))
    bio.name = "my_game_library.json"
    await update.message.reply_document(
        document=bio,
        caption="⚠️ This file contains your passwords. Keep it private!",
        reply_markup=main_menu_keyboard(),
    )


async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📥 Send a **.json** file exported from this bot.\n"
        "Games will be **added** to your library (not replaced).",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    context.user_data["awaiting_import"] = True


async def handle_import_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_import"):
        return

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".json"):
        await update.message.reply_text(
            "❌ Send a .json file.", reply_markup=main_menu_keyboard()
        )
        return

    file = await doc.get_file()
    content = await file.download_as_bytearray()
    try:
        games = json.loads(content.decode("utf-8"))
        if not isinstance(games, list):
            raise ValueError("Expected a JSON array")
        count = db.import_user_games(update.effective_user.id, games, merge=True)
        await update.message.reply_text(
            f"✅ Imported **{count}** game(s)!",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
    except (json.JSONDecodeError, ValueError) as e:
        await update.message.reply_text(
            f"❌ Invalid file: {e}", reply_markup=main_menu_keyboard()
        )
    finally:
        context.user_data.pop("awaiting_import", None)


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args or context.args[0].lower() not in ("on", "off"):
        enabled = db.get_reminders_enabled(user_id)
        await update.message.reply_text(
            f"Warranty reminders are **{'ON' if enabled else 'OFF'}**.\n"
            f"Use `/reminders on` or `/reminders off`",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return

    enabled = context.args[0].lower() == "on"
    db.set_reminders_enabled(user_id, enabled)
    await update.message.reply_text(
        f"✅ Warranty reminders **{'enabled' if enabled else 'disabled'}**.",
        reply_markup=main_menu_keyboard(),
    )


# ─── Warranty reminders job ───────────────────────────────────────────────────

async def warranty_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    reminders = db.get_warranty_reminders()
    for item in reminders:
        if not db.get_reminders_enabled(item["user_id"]):
            continue
        days = item["days_left"]
        if days == 0:
            when = "**today**"
        else:
            when = f"in **{days} days**"
        seller = item["seller_contact"] or "Not set"
        text = (
            f"🛡️ Warranty for **{item['game_name']}** expires {when}!\n"
            f"Seller: {seller}\n"
            f"Ends: {item['warranty_until'][:10]}"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("View Game", callback_data=f"view_{item['game_id']}")],
        ])
        try:
            await context.bot.send_message(
                item["user_id"], text, reply_markup=markup, parse_mode="Markdown"
            )
        except Exception:
            pass


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "❌ BOT_TOKEN is not set. Export it before running:\n"
            "   export BOT_TOKEN=\"your-token-from-BotFather\""
        )
    db.init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(button_handler, pattern="^add$"),
            CommandHandler("add", prompt_add_game),
            MessageHandler(filters.Regex(f"^{MENU_ADD}$"), prompt_add_game),
        ],
        states={
            GAME_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_game_name)],
            STORE_PICK: [CallbackQueryHandler(store_pick_cb, pattern="^pick_store")],
            STORE_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_store_url)],
            # New-store sub-flow within the add-game conversation
            STORE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, game_add_new_store_name)],
            STORE_URL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, game_add_new_store_url)],
            STORE_CONTACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, game_add_new_store_contact),
                CommandHandler("skip", game_add_new_store_skip_contact),
            ],
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_username)],
            PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_password),
                CommandHandler("skip", skip_password),
            ],
            IMAGE_URL: [
                MessageHandler(filters.PHOTO, add_image),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_image),
                CommandHandler("skip", skip_image),
            ],
            SELLER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_seller),
                CommandHandler("skip", skip_seller),
            ],
            NOTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_notes),
                CommandHandler("skip", skip_notes),
            ],
        },
        fallbacks=menu_fallbacks(),
    )

    quick_add_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{MENU_QUICK_ADD}$"), prompt_quick_add),
            CommandHandler("quickadd", prompt_quick_add),
        ],
        states={
            QUICK_ADD_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, quick_add_input)
            ],
            QUICK_ADD_CONFIRM: [
                CallbackQueryHandler(
                    quick_add_confirm_cb, pattern="^quickadd_(confirm|cancel)$"
                ),
            ],
        },
        fallbacks=menu_fallbacks(),
    )

    edit_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_start_field, pattern="^editf_"),
        ],
        states={
            EDIT_VALUE: [
                MessageHandler(filters.PHOTO, edit_receive_value),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_receive_value),
                CommandHandler("skip", edit_skip_photo),
            ],
        },
        fallbacks=menu_fallbacks(),
    )

    search_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(button_handler, pattern="^search$"),
            MessageHandler(filters.Regex(f"^{MENU_SEARCH}$"), prompt_search),
        ],
        states={
            "SEARCH_MODE": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_mode)
            ],
        },
        fallbacks=menu_fallbacks(),
    )

    add_store_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(store_add_entry, pattern="^store_add$"),
        ],
        states={
            STORE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, store_add_name)],
            STORE_URL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, store_add_url)],
            STORE_CONTACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, store_add_contact),
                CommandHandler("skip", store_add_skip_contact),
            ],
        },
        fallbacks=menu_fallbacks(),
    )

    edit_store_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(store_edit_start_field, pattern="^store_editf_"),
        ],
        states={
            STORE_EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, store_edit_receive_value),
            ],
        },
        fallbacks=menu_fallbacks(),
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("mylibrary", mylibrary_command))
    app.add_handler(CommandHandler("view", view_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("import", import_command))
    app.add_handler(CommandHandler("reminders", reminders_command))
    app.add_handler(add_conv)
    app.add_handler(quick_add_conv)
    app.add_handler(edit_conv)
    app.add_handler(search_conv)
    app.add_handler(add_store_conv)
    app.add_handler(edit_store_conv)
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(list|stats|help)$"))
    app.add_handler(CallbackQueryHandler(menu, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(library_list_handler, pattern="^list_(fav|recent|all)$"))
    app.add_handler(CallbackQueryHandler(view_game, pattern="^view_\\d+$"))
    app.add_handler(CallbackQueryHandler(toggle_favorite, pattern="^fav_\\d+$"))
    app.add_handler(CallbackQueryHandler(edit_pick_field, pattern="^edit_\\d+$"))
    app.add_handler(CallbackQueryHandler(delete_game, pattern="^delete_\\d+$"))
    app.add_handler(CallbackQueryHandler(stores_list_handler, pattern="^stores_list$"))
    app.add_handler(CallbackQueryHandler(view_store, pattern="^store_view_\\d+$"))
    app.add_handler(CallbackQueryHandler(store_edit_pick_field, pattern="^store_edit_\\d+$"))
    app.add_handler(CallbackQueryHandler(delete_store_prompt, pattern="^store_del_\\d+$"))
    app.add_handler(CallbackQueryHandler(delete_store_confirm, pattern="^store_delok_\\d+$"))
    app.add_handler(MessageHandler(filters.Regex(f"^{MENU_LIBRARY}$"), handle_menu_library))
    app.add_handler(MessageHandler(filters.Regex(f"^{MENU_STORES}$"), handle_menu_stores))
    app.add_handler(MessageHandler(filters.Regex(f"^{MENU_STATS}$"), handle_menu_stats))
    app.add_handler(MessageHandler(filters.Regex(f"^{MENU_HELP}$"), handle_menu_help))
    app.add_handler(MessageHandler(filters.Regex(f"^{MENU_HOME}$"), handle_menu_home))
    app.add_handler(MessageHandler(filters.Text([MENU_CANCEL]), cancel_conversation))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_import_file))

    if app.job_queue:
        app.job_queue.run_daily(warranty_reminder_job, time=dt_time(hour=9, minute=0))

    print("🤖 Steam Library Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
