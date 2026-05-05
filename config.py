import os
import sys
import json
from collections import deque
from typing import Optional, Any


# ─────────────────────────────────────────────
# Raw env reads
# ─────────────────────────────────────────────

def _get_env(key: str, default: Any = None) -> Any:
    return os.environ.get(key, default)


def _get_int(key: str, default: Optional[int] = None) -> Optional[int]:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _get_float(key: str, default: float = 2.0) -> float:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _get_bool(key: str, default: bool = True) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


# ─────────────────────────────────────────────
# Required variables (validated later)
# ─────────────────────────────────────────────

API_ID: int = _get_int("API_ID", 0)  # type: ignore
API_HASH: str = _get_env("API_HASH", "")
BOT_TOKEN: str = _get_env("BOT_TOKEN", "")
ADMIN_ID: int = _get_int("ADMIN_ID", 0)  # type: ignore
SOURCE_ID: int = _get_int("SOURCE_ID", 0)  # type: ignore
DEST_ID: int = _get_int("DEST_ID", 0)  # type: ignore

# Multi-source / multi-dest (comma-separated negative IDs, e.g. "-100111,-100222")
def _parse_id_list(key: str) -> list:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return []
    result = []
    for part in raw.split(","):
        part = part.strip()
        try:
            result.append(int(part))
        except ValueError:
            pass
    return result

SOURCE_IDS_ENV: list = _parse_id_list("SOURCE_IDS")
DEST_IDS_ENV: list   = _parse_id_list("DEST_IDS")

# ─────────────────────────────────────────────
# Optional variables with defaults
# ─────────────────────────────────────────────

DELAY: float = _get_float("DELAY", 2.0)
MAX_RETRIES: int = _get_int("MAX_RETRIES", 3)  # type: ignore
PIN_ENABLED: bool = _get_bool("PIN_ENABLED", True)
PIN_INTERVAL: int = _get_int("PIN_INTERVAL", 2000)  # type: ignore
PIN_TEXT: str = _get_env("PIN_TEXT", "📌 Batch {n} — Messages {start} to {end}")

CAPTION_ADD_START: str = _get_env("CAPTION_ADD_START", "")
CAPTION_ADD_END: str = _get_env("CAPTION_ADD_END", "")
CAPTION_REPLACE: str = _get_env("CAPTION_REPLACE", "")
CAPTION_REMOVE: str = _get_env("CAPTION_REMOVE", "")
CAPTION_REMOVE_LINES: str = _get_env("CAPTION_REMOVE_LINES", "")

# ─────────────────────────────────────────────
# Telethon connection params
# ─────────────────────────────────────────────

TELETHON_CONNECTION_PARAMS: dict = {
    "connection_retries": 10,
    "retry_delay": 5,
    "timeout": 30,
    "request_retries": 10,
}

# ─────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────

def validate_config() -> None:
    """Validate all required environment variables. Exit on failure."""
    errors = []

    if not API_ID or API_ID == 0:
        errors.append("❌ API_ID is missing or invalid (must be a positive integer)")

    if not API_HASH:
        errors.append("❌ API_HASH is missing")
    elif len(API_HASH) < 30:
        errors.append(f"❌ API_HASH looks invalid (too short: {len(API_HASH)} chars, expected >= 30)")

    if not BOT_TOKEN:
        errors.append("❌ BOT_TOKEN is missing")

    if not ADMIN_ID or ADMIN_ID == 0:
        errors.append("❌ ADMIN_ID is missing or invalid (must be a positive integer)")

    if not SOURCE_ID or SOURCE_ID == 0:
        errors.append("❌ SOURCE_ID is missing")
    elif SOURCE_ID > 0:
        errors.append(f"❌ SOURCE_ID must be negative (got {SOURCE_ID})")

    if not DEST_ID or DEST_ID == 0:
        errors.append("❌ DEST_ID is missing")
    elif DEST_ID > 0:
        errors.append(f"❌ DEST_ID must be negative (got {DEST_ID})")

    if SOURCE_ID and DEST_ID and SOURCE_ID == DEST_ID:
        errors.append("❌ SOURCE_ID and DEST_ID must not be the same channel")

    if CAPTION_REPLACE:
        try:
            parsed = json.loads(CAPTION_REPLACE)
            if not isinstance(parsed, dict):
                errors.append("❌ CAPTION_REPLACE must be a JSON object (dict), e.g. {\"old\": \"new\"}")
        except json.JSONDecodeError as e:
            errors.append(f"❌ CAPTION_REPLACE is not valid JSON: {e}")

    if errors:
        print("\n🚨 CONFIGURATION ERRORS:\n")
        for err in errors:
            print(f"  {err}")
        print("\nFix these environment variables and restart.\n")
        sys.exit(1)

    print("✅ Configuration validated successfully")


def caption_modifications_enabled() -> bool:
    """Return True if ANY caption modification env var is set and non-empty."""
    return any([
        CAPTION_ADD_START.strip(),
        CAPTION_ADD_END.strip(),
        CAPTION_REPLACE.strip(),
        CAPTION_REMOVE.strip(),
        CAPTION_REMOVE_LINES.strip(),
    ])


# ─────────────────────────────────────────────
# Global State
# ─────────────────────────────────────────────

class State:
    """Global singleton state shared across all modules."""

    def __init__(self):
        self.status: str = "IDLE"
        self.phone: Optional[str] = None
        self.phone_code_hash: Optional[str] = None
        self.otp_sent_at: Optional[float] = None
        self.userbot: Optional[Any] = None
        self.session_string: Optional[str] = None
        self.pause_flag: bool = False
        self.cancel_flag: bool = False
        self.cloning_active: bool = False
        self.processed: int = 0
        self.failed: int = 0
        self.albums_count: int = 0
        self.total_messages: int = 0
        self.current_id: int = 0
        self.start_time: Optional[Any] = None
        self.status_message_id: Optional[int] = None
        self.index_msg_id: Optional[int] = None
        self.batch_number: int = 0
        self.recent_activity: deque = deque(maxlen=5)
        self.bot_client: Optional[Any] = None
        self.batches_list: list = []
        self.skipped: int = 0
        self.source_name: str = "Source Channel"
        self.dest_name: str = "Destination Channel"
        self.delall_chat_id: Optional[int] = None
        self.delall_active: bool = False
        # Multi-source tracking
        self.current_source_id: int = 0
        self.current_source_name: str = ""
        self.sources_done: list = []
        # Retry tracking
        self.retry_active: bool = False
        # Source selection (empty = use all)
        self.selected_source_ids: list = []
        # Per-source info: {source_id: {"name": str, "count": int}}
        self.source_counts: dict = {}
        # Flood-wait tracking
        self.floodwait_until: Optional[float] = None
        # Delall count (0 = all)
        self.delall_count: int = 0


state = State()