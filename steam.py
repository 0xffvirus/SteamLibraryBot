# steam.py
import re
import httpx

STEAM_URL_RE = re.compile(
    r"(?:https?://)?store\.steampowered\.com/app/(\d+)",
    re.IGNORECASE,
)


def extract_steam_app_id(text):
    match = STEAM_URL_RE.search(text.strip())
    return match.group(1) if match else None


async def fetch_steam_app_details(app_id):
    url = f"https://store.steampowered.com/api/appdetails?appids={app_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()

    entry = payload.get(str(app_id), {})
    if not entry.get("success"):
        return None

    data = entry.get("data", {})
    return {
        "name": data.get("name"),
        "header_image": data.get("header_image"),
        "store_url": f"https://store.steampowered.com/app/{app_id}/",
    }
