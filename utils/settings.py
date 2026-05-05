import json
import os
from typing import Any, Dict, List, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(_BASE_DIR, "data", "settings.json")

_DEFAULT: Dict[str, Any] = {
    "filter": {
        "text":       True,
        "photo":      True,
        "audio":      True,
        "document":   True,
        "video_mp4":  True,
        "video_mkv":  True,
        "voice":      True,
        "video_note": True,
        "other":      True,
        # Disabled by default — not forwarded
        "sticker":    False,
        "animation":  False,
        "poll":       False,
    },
    "caption_rules": [],
    "speed": "fast",
    "webhook_url": None,
    "extra_sources": [],
    "extra_dests": [],
}

_cache: Dict[str, Any] = {}


def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)


def _load() -> Dict[str, Any]:
    global _cache
    if not os.path.exists(SETTINGS_FILE):
        _cache = {k: v for k, v in _DEFAULT.items()}
        _cache["filter"] = dict(_DEFAULT["filter"])
        return _cache
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        merged: Dict[str, Any] = {k: v for k, v in _DEFAULT.items()}
        merged["filter"] = dict(_DEFAULT["filter"])
        merged["caption_rules"] = []
        merged["extra_sources"] = []
        merged["extra_dests"] = []
        merged.update({k: v for k, v in loaded.items() if k != "filter"})
        if "filter" in loaded:
            loaded_filter = dict(loaded["filter"])
            # Migrate old "video" key to video_mp4 + video_mkv
            if "video" in loaded_filter and "video_mp4" not in loaded_filter:
                val = loaded_filter.pop("video")
                loaded_filter["video_mp4"] = val
                loaded_filter["video_mkv"] = val
            merged["filter"].update(loaded_filter)
        _cache = merged
        return _cache
    except Exception:
        _cache = {k: v for k, v in _DEFAULT.items()}
        _cache["filter"] = dict(_DEFAULT["filter"])
        return _cache


def _save() -> None:
    _ensure_dir()
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, indent=2)
    except Exception as e:
        from utils.logger import get_logger
        get_logger(__name__).error(f"Failed to save settings: {e}")


def _get() -> Dict[str, Any]:
    if not _cache:
        _load()
    return _cache


# ─── Filter ───────────────────────────────────

def get_filter() -> Dict[str, bool]:
    return _get().get("filter", dict(_DEFAULT["filter"]))


def set_filter_type(type_name: str, enabled: bool) -> None:
    s = _get()
    s["filter"][type_name] = enabled
    _save()


def set_filter_all(enabled: bool) -> None:
    s = _get()
    for k in s["filter"]:
        s["filter"][k] = enabled
    _save()


def get_filter_summary() -> str:
    f = get_filter()
    emoji_map = {
        "text": "📝", "photo": "🖼️", "audio": "🎵",
        "document": "📄", "video_mp4": "🎬", "video_mkv": "🎞️",
        "sticker": "🎭", "animation": "🌀", "voice": "🎙️",
        "video_note": "📹", "poll": "📊", "other": "📦",
    }
    label_map = {
        "text": "text", "photo": "photo", "audio": "audio",
        "document": "document", "video_mp4": "mp4", "video_mkv": "mkv",
        "sticker": "sticker", "animation": "animation", "voice": "voice",
        "video_note": "video_note", "poll": "poll", "other": "other",
    }
    on  = [f"{emoji_map.get(k,'•')} {label_map.get(k,k)}" for k, v in f.items() if v]
    off = [f"{emoji_map.get(k,'•')} {label_map.get(k,k)}" for k, v in f.items() if not v]
    lines = []
    if on:
        lines.append("✅ Forwarding: " + ", ".join(on))
    if off:
        lines.append("⛔ Skipping: " + ", ".join(off))
    return "\n".join(lines) if lines else "All types enabled"


# ─── Caption rules ────────────────────────────

def get_caption_rules() -> List[Dict]:
    return _get().get("caption_rules", [])


def add_caption_rule(rule_type: str, find_or_keyword: str, replace: str = "") -> int:
    """Add a caption rule. rule_type: 'replace' or 'skip'."""
    s = _get()
    if rule_type == "replace":
        rule: Dict = {"type": "replace", "find": find_or_keyword, "replace": replace}
    elif rule_type == "skip":
        rule = {"type": "skip", "pattern": find_or_keyword, "keyword": find_or_keyword}
    else:
        rule = {"type": rule_type, "find": find_or_keyword, "replace": replace}
    s["caption_rules"].append(rule)
    _save()
    return len(s["caption_rules"])


def delete_caption_rule(idx: int) -> bool:
    s = _get()
    rules = s.get("caption_rules", [])
    if 0 <= idx < len(rules):
        rules.pop(idx)
        _save()
        return True
    return False


def clear_caption_rules() -> None:
    s = _get()
    s["caption_rules"] = []
    _save()


def apply_caption_rules(text: Optional[str]) -> Optional[str]:
    """Apply dynamic replace rules. Returns None signals skip-message."""
    rules = get_caption_rules()
    if not rules:
        return text
    t = text or ""
    for rule in rules:
        rtype = rule.get("type")
        if rtype == "skip":
            pattern = rule.get("pattern", "")
            if pattern and pattern.lower() in t.lower():
                return None
        elif rtype == "replace":
            find = rule.get("find", "")
            rep  = rule.get("replace", "")
            if find:
                t = t.replace(find, rep)
    return t if t else text


def should_skip_by_caption(caption: Optional[str]) -> bool:
    """Return True if any skip-rule matches caption."""
    rules = get_caption_rules()
    text = caption or ""
    for rule in rules:
        if rule.get("type") == "skip":
            pattern = rule.get("pattern", "")
            if pattern and pattern.lower() in text.lower():
                return True
    return False


# ─── Speed ────────────────────────────────────

SPEED_PROFILES = {
    "fast":   {"batch_sleep": 0.05, "page_sleep": 0.3,  "album_sleep": 0.2},
    "normal": {"batch_sleep": 0.3,  "page_sleep": 1.0,  "album_sleep": 0.5},
    "slow":   {"batch_sleep": 1.0,  "page_sleep": 2.0,  "album_sleep": 1.0},
}


def get_speed() -> str:
    return _get().get("speed", "fast")


def get_speed_profile() -> Dict[str, float]:
    return SPEED_PROFILES.get(get_speed(), SPEED_PROFILES["fast"])


def set_speed(speed: str) -> bool:
    if speed not in SPEED_PROFILES:
        return False
    _get()["speed"] = speed
    _save()
    return True


# ─── Webhook ──────────────────────────────────

def get_webhook_url() -> Optional[str]:
    return _get().get("webhook_url")


def set_webhook_url(url: Optional[str]) -> None:
    _get()["webhook_url"] = url
    _save()


# ─── Multi-source / Multi-dest ────────────────

def get_extra_sources() -> List[int]:
    return _get().get("extra_sources", [])


def get_extra_dests() -> List[int]:
    return _get().get("extra_dests", [])


def add_source(channel_id: int) -> None:
    s = _get()
    if channel_id not in s["extra_sources"]:
        s["extra_sources"].append(channel_id)
        _save()


def remove_source(channel_id: int) -> bool:
    s = _get()
    if channel_id in s["extra_sources"]:
        s["extra_sources"].remove(channel_id)
        _save()
        return True
    return False


def add_dest(channel_id: int) -> None:
    s = _get()
    if channel_id not in s["extra_dests"]:
        s["extra_dests"].append(channel_id)
        _save()


def remove_dest(channel_id: int) -> bool:
    s = _get()
    if channel_id in s["extra_dests"]:
        s["extra_dests"].remove(channel_id)
        _save()
        return True
    return False


def clear_extra_sources() -> None:
    _get()["extra_sources"] = []
    _save()


def clear_extra_dests() -> None:
    _get()["extra_dests"] = []
    _save()


# ─── Convenience aliases ───────────────────────

def list_caption_rules() -> List[Dict]:
    """Alias for get_caption_rules — for handler compatibility."""
    return get_caption_rules()


def set_speed_profile(name: str) -> bool:
    """Alias for set_speed."""
    return set_speed(name)


def get_speed_profile_name() -> str:
    """Return current speed profile name."""
    return get_speed()


def add_extra_source(channel_id: int) -> None:
    """Alias for add_source."""
    add_source(channel_id)


def add_extra_dest(channel_id: int) -> None:
    """Alias for add_dest."""
    add_dest(channel_id)


# ─── Filter keyboard builder ──────────────────

def build_filter_keyboard(show_scan: bool = False):
    """Build Pyrogram InlineKeyboardMarkup for filter toggles. Optionally include scan button."""
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    f = get_filter()
    emoji_map = {
        "text": "📝", "photo": "🖼️", "audio": "🎵",
        "document": "📄", "video_mp4": "🎬", "video_mkv": "🎞️",
        "voice": "🎙️", "video_note": "📹", "other": "📦",
    }
    label_map = {
        "text": "text", "photo": "photo", "audio": "audio",
        "document": "document", "video_mp4": "mp4", "video_mkv": "mkv",
        "voice": "voice", "video_note": "video_note", "other": "other",
    }
    # Sticker, animation, poll are hidden from main UI (always controlled via separate toggle)
    hidden_types = {"sticker", "animation", "poll"}

    rows = []
    visible_items = [(k, v) for k, v in f.items() if k not in hidden_types]
    for i in range(0, len(visible_items), 2):
        row = []
        for key, val in visible_items[i:i+2]:
            icon = emoji_map.get(key, "•")
            status = "✅" if val else "⛔"
            display = label_map.get(key, key)
            label = f"{status} {icon} {display}"
            row.append(InlineKeyboardButton(label, callback_data=f"filter_toggle:{key}"))
        rows.append(row)

    rows.append([
        InlineKeyboardButton("✅ Enable All", callback_data="filter_all:on"),
        InlineKeyboardButton("⛔ Disable All", callback_data="filter_all:off"),
    ])

    if show_scan:
        rows.append([
            InlineKeyboardButton("📊 Scan: Last 2K", callback_data="filter_scan:2000"),
            InlineKeyboardButton("📊 Scan: Last 5K", callback_data="filter_scan:5000"),
        ])
        rows.append([
            InlineKeyboardButton("📊 Scan: Last 10K", callback_data="filter_scan:10000"),
            InlineKeyboardButton("📊 Scan: Full Channel", callback_data="filter_scan:0"),
        ])

    return InlineKeyboardMarkup(rows)


# Load on import
_load()
