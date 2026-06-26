# quick_add.py
import re

FIELD_PATTERNS = {
    "game_name": re.compile(
        r"(?:game|title|name)\s*[:=]\s*(.+)", re.IGNORECASE | re.MULTILINE
    ),
    "username": re.compile(
        r"(?:user(?:name)?|login|email|steam\s*user)\s*[:=]\s*(.+)",
        re.IGNORECASE | re.MULTILINE,
    ),
    "password": re.compile(
        r"(?:pass(?:word)?|pwd)\s*[:=]\s*(.+)", re.IGNORECASE | re.MULTILINE
    ),
    "store_url": re.compile(
        r"(?:store|url|link|site)\s*[:=]\s*(.+)", re.IGNORECASE | re.MULTILINE
    ),
    "seller_contact": re.compile(
        r"(?:seller|contact|vendor)\s*[:=]\s*(.+)", re.IGNORECASE | re.MULTILINE
    ),
    "notes": re.compile(
        r"(?:notes?|note)\s*[:=]\s*(.+)", re.IGNORECASE | re.MULTILINE
    ),
}


def parse_quick_add(text):
    result = {}
    for field, pattern in FIELD_PATTERNS.items():
        match = pattern.search(text)
        if match:
            value = match.group(1).strip()
            if field == "price_paid":
                try:
                    result[field] = float(value)
                except ValueError:
                    pass
            else:
                result[field] = value
    return result


def format_quick_add_preview(data):
    lines = []
    labels = {
        "game_name": "Game",
        "store_url": "Store",
        "username": "Username",
        "password": "Password",
        "seller_contact": "Seller",
        "notes": "Notes",
    }
    for key, label in labels.items():
        if data.get(key) is not None:
            lines.append(f"{label}: {data[key]}")
    return "\n".join(lines) if lines else "No fields detected."
