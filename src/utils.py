"""
Utility layer: JSON persistence, affinity tracking, long-term memory,
server-language settings, PTV commute subscriptions, and broadcast config.

All JSON writes use atomic replace (write-tmp → os.replace) so that
a crash never leaves a half-written file.  This is required for Docker
deployments where the data directory is a bind mount.
"""

import os
import json
import datetime
from config import (
    logger, AFFINITY_FILE, MEMORY_FILE, SETTINGS_FILE,
    PTV_SUBSCRIPTIONS_FILE,
    AFFINITY_DEFAULT, AFFINITY_TIERS,
)


# ═══════════════════════════════════════════════════════════════════
# Generic JSON I/O
# ═══════════════════════════════════════════════════════════════════

def _load_json(path: str) -> dict:
    """Load a JSON dict from disk; return {} if missing or corrupted."""
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read JSON %s: %s", path, exc)
    return {}


def _save_json_atomic(path: str, data: dict) -> None:
    """Write JSON via temp-file + atomic rename (crash-safe)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ═══════════════════════════════════════════════════════════════════
# Server Language Settings
# ═══════════════════════════════════════════════════════════════════

def get_server_language(guild_id: str) -> str:
    """Return the language code ('zh' or 'en') for a Discord guild."""
    return _load_json(SETTINGS_FILE).get(str(guild_id), "en")


def save_server_language(guild_id: str, lang: str) -> None:
    settings = _load_json(SETTINGS_FILE)
    settings[str(guild_id)] = lang
    _save_json_atomic(SETTINGS_FILE, settings)


# ═══════════════════════════════════════════════════════════════════
# Broadcast Channel
# ═══════════════════════════════════════════════════════════════════

def get_broadcast_channel(guild_id: str) -> str | None:
    """Return the channel ID configured for worldline broadcasts, or None."""
    settings = _load_json(SETTINGS_FILE)
    return settings.get(f"{guild_id}_broadcast")


def save_broadcast_channel(guild_id: str, channel_id: str) -> None:
    settings = _load_json(SETTINGS_FILE)
    settings[f"{guild_id}_broadcast"] = channel_id
    _save_json_atomic(SETTINGS_FILE, settings)


# ═══════════════════════════════════════════════════════════════════
# Art Channel (立绘频道)
# ═══════════════════════════════════════════════════════════════════

def get_art_channel(guild_id: str):
    """Return the channel ID where images are enabled, or None."""
    settings = _load_json(SETTINGS_FILE)
    return settings.get(f"{guild_id}_art_channel")


def save_art_channel(guild_id: str, channel_id: str) -> None:
    settings = _load_json(SETTINGS_FILE)
    settings[f"{guild_id}_art_channel"] = channel_id
    _save_json_atomic(SETTINGS_FILE, settings)


def clear_art_channel(guild_id: str) -> None:
    settings = _load_json(SETTINGS_FILE)
    settings.pop(f"{guild_id}_art_channel", None)
    _save_json_atomic(SETTINGS_FILE, settings)


# ═══════════════════════════════════════════════════════════════════
# Long-Term Memory (per channel)
# ═══════════════════════════════════════════════════════════════════

def load_memory(channel_id: str) -> str:
    """Return the long-term memory summary for a channel, or empty."""
    return _load_json(MEMORY_FILE).get(str(channel_id), "")


def save_memory(channel_id: str, summary: str) -> None:
    mem = _load_json(MEMORY_FILE)
    mem[str(channel_id)] = summary
    _save_json_atomic(MEMORY_FILE, mem)


# ═══════════════════════════════════════════════════════════════════
# Affinity System
# ═══════════════════════════════════════════════════════════════════

def _default_affinity() -> dict:
    return {"count": AFFINITY_DEFAULT, "last_seen": ""}


def get_affinity(user_id: str) -> dict:
    """Return the affinity record for a user (lazy — no disk write for first-timers)."""
    entry = _load_json(AFFINITY_FILE).get(str(user_id))
    if not isinstance(entry, dict):
        return _default_affinity()
    base = _default_affinity()
    base.update(entry)
    return base


def increment_affinity(user_id: str) -> dict:
    """Increment interaction counter and persist."""
    uid = str(user_id)
    data = _load_json(AFFINITY_FILE)
    entry = _default_affinity()
    entry.update(data.get(uid) or {})

    entry["count"] += 1
    entry["last_seen"] = datetime.datetime.now().isoformat(timespec="seconds")

    data[uid] = entry
    _save_json_atomic(AFFINITY_FILE, data)
    return entry


def get_affinity_tier(entry: dict) -> dict:
    """Map affinity record → tier descriptor {key, emoji, label_zh, label_en}."""
    count = entry.get("count", 0)
    for lo, hi, key, emoji in AFFINITY_TIERS:
        if lo <= count <= hi:
            return {
                "key": key,
                "emoji": emoji,
                "label_zh": _AFFINITY_LABELS_ZH[key],
                "label_en": _AFFINITY_LABELS_EN[key],
            }
    return {
        "key": "stranger", "emoji": "🙂",
        "label_zh": _AFFINITY_LABELS_ZH["stranger"],
        "label_en": _AFFINITY_LABELS_EN["stranger"],
    }


_AFFINITY_LABELS_ZH = {
    "stranger":   "刚认识的群友，保持礼貌与专业科学家的外壳。",
    "acquainted": "已经混熟的群友，可以放心开启毒舌傲娇模式。",
    "familiar":   "日常调戏自己的老熟人，嘴上不饶人但心里很在意。",
}

_AFFINITY_LABELS_EN = {
    "stranger":   "A new acquaintance; maintain the 'serious scientist' facade with polite distance.",
    "acquainted": "Someone you've gotten used to; safe to unleash your tsundere sarcasm.",
    "familiar":   "A regular who teases you daily; you insult them freely but secretly care a lot.",
}


# ═══════════════════════════════════════════════════════════════════
# PTV Commute Subscriptions
# ═══════════════════════════════════════════════════════════════════

def load_ptv_subscriptions() -> dict:
    """
    Return all PTV commute subscriptions.

    Format: {subscription_id: {user_id, channel_id, stop_id, stop_name,
             route_type, check_time (HH:MM), created_at}}
    """
    return _load_json(PTV_SUBSCRIPTIONS_FILE)


def save_ptv_subscriptions(data: dict) -> None:
    _save_json_atomic(PTV_SUBSCRIPTIONS_FILE, data)


def add_ptv_subscription(
    user_id: str,
    channel_id: str,
    stop_id: int,
    stop_name: str,
    route_type: str,
    check_time: str,  # "HH:MM"
) -> str:
    """
    Create a new PTV commute subscription.  Returns the subscription ID.

    One user can have multiple subscriptions (different stops/times).
    """
    data = load_ptv_subscriptions()
    sub_id = f"{user_id}_{stop_id}_{check_time}"
    data[sub_id] = {
        "user_id": str(user_id),
        "channel_id": str(channel_id),
        "stop_id": stop_id,
        "stop_name": stop_name,
        "route_type": route_type,
        "check_time": check_time,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    save_ptv_subscriptions(data)
    logger.info("PTV subscription added: %s for user %s at %s", stop_name, user_id, check_time)
    return sub_id


def remove_ptv_subscription(sub_id: str) -> bool:
    """Remove a subscription by ID. Returns True if it existed."""
    data = load_ptv_subscriptions()
    if sub_id in data:
        del data[sub_id]
        save_ptv_subscriptions(data)
        return True
    return False


def get_user_subscriptions(user_id: str) -> list[dict]:
    """Return all subscriptions for a given user."""
    data = load_ptv_subscriptions()
    return [
        {"id": sid, **info}
        for sid, info in data.items()
        if info.get("user_id") == str(user_id)
    ]


def get_subscriptions_due(now_time: str, lookahead_min: int = 30) -> list[dict]:
    """
    Return subscriptions whose check_time falls within [now, now + lookahead].

    now_time: "HH:MM" string
    """
    data = load_ptv_subscriptions()
    now_mins = _hm_to_minutes(now_time)
    due = []
    for sid, info in data.items():
        target = _hm_to_minutes(info.get("check_time", "09:00"))
        # Handle morning vs evening: if target is within the window
        delta = target - now_mins
        if 0 <= delta <= lookahead_min:
            due.append({"id": sid, **info})
    return due


def _hm_to_minutes(hm: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    try:
        h, m = hm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, IndexError):
        return 9 * 60  # Default to 9:00 AM


# ═══════════════════════════════════════════════════════════════════
# Startup Integrity Check
# ═══════════════════════════════════════════════════════════════════

def run_startup_check() -> None:
    """Validate prerequisites before connecting to Discord."""
    from config import DISCORD_TOKEN, PTV_DEVELOPER_ID, PTV_API_KEY

    logger.info("=== Amadeus Startup Self-Check ===")

    if not DISCORD_TOKEN or DISCORD_TOKEN == "your_token_here":
        logger.critical("DISCORD_BOT_TOKEN is not set. Copy .env.example → .env and fill in your token.")
        import sys
        sys.exit(1)

    if not PTV_DEVELOPER_ID or not PTV_API_KEY:
        logger.warning(
            "PTV API credentials not set — /ptv commands and subscriptions will be unavailable. "
            "Apply at: https://www.ptv.vic.gov.au/footer/data-and-reporting/datasets/ptv-timetable-api/"
        )
    else:
        logger.info("PTV API credentials found — transport features enabled.")

    os.makedirs(os.path.dirname(AFFINITY_FILE) if os.path.dirname(AFFINITY_FILE) else ".", exist_ok=True)

    logger.info("Startup check passed — connecting to Discord...")