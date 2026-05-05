import os
import json
from collections import deque
from typing import Optional, Any


# ─────────────────────────────────────────────
# Raw env helpers
# ─────────────────────────────────────────────

def _get_env(key: str, default: Any = None) -> Any:
    value = os.environ.get(key, default)
    if isinstance(value, str):
        return value.strip()
    return value


def _get_int(key: str, default: Optional[int] = None) -> Optional[int]:
    val = os.environ.get(key)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


def _get_float(key: str, default: float = 2.0) -> float:
    val = os.environ.get(key)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val.strip())
    except ValueError:
        return default


def _get_bool(key: str, default: bool = True) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _parse_id_list(key: str) -> list[int]:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return []
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            pass
    return result


# ─────────────────────────────────────────────
# DEBUG
# ─────────────────────────────────────────────

print("RAW ENV:")
print("API_ID =", repr(os.getenv("API_ID")))
print("API_HASH =", repr(os.getenv("API_HASH")))
print("BOT_TOKEN =", repr(os.getenv("BOT_TOKEN")))
print("ADMIN_ID =", repr(os.getenv("ADMIN_ID")))
print("SOURCE_ID =", repr(os.getenv("SOURCE_ID")))
print("DEST_ID =", repr(os.getenv("DEST_ID")))
print("SOURCE_IDS =", repr(os.getenv("SOURCE_IDS")))
print("DEST_IDS =", repr(os.getenv("DEST_IDS")))


# ─────────────────────────────────────────────
# Required variables
# ─────────────────────────────────────────────

API_ID: Optional[int] = _get_int("API_ID", 0)
API_HASH: str = _get_env("API_HASH", "")
BOT_TOKEN: str = _get_env("BOT_TOKEN", "")
ADMIN_ID: Optional[int] = _get_int("ADMIN_ID", 0)

SOURCE_ID: Optional[int] = _get_int("SOURCE_ID", 0)
DEST_ID: Optional[int] = _get_int("DEST_ID", 0)

SOURCE_IDS_ENV: list[int] = _parse_id_list("SOURCE_IDS")
DEST_IDS_ENV: list[int] = _parse_id_list("DEST_IDS")

# Backward-compatible fallback:
# If single IDs are missing, use the first ID from the list vars.
if not SOURCE_ID and SOURCE_IDS_ENV:
    SOURCE_ID = SOURCE_IDS_ENV[0]

if not DEST_ID and DEST_IDS_ENV:
    DEST_ID = DEST_IDS_ENV[0]


# ─────────────────────────────────────────────
# Optional variables with defaults
# ─────────────────────────────────────────────

DELAY: float = _get_float("DELAY", 2.0)
MAX_RETRIES: int = _get_int("MAX_RETRIES", 3) or 3
PIN_ENABLED: bool = _get_bool("PIN_ENABLED", True)
PIN_INTERVAL: int = _get_int("PIN_INTERVAL", 2000) or 2000
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
    """Validate all required environment variables."""
    errors = []

    if not API_ID or API_ID <= 0:
        errors.append("❌ API_ID is missing or invalid (must be a positive integer)")

    if not API_HASH:
        errors.append("❌ API_HASH is missing")
    elif len(API_HASH) < 30:
        errors.append(f"❌ API_HASH looks invalid (too short: {len(API_HASH)} chars, expected >= 30)")

    if not BOT_TOKEN:
        errors.append("❌ BOT_TOKEN is missing")

    if not ADMIN_ID or ADMIN_ID <= 0:
        errors.append("❌ ADMIN_ID is missing or invalid (must be a positive integer)")

    # Support both single and multi-source / multi-dest setups
    source_ok = bool(SOURCE_ID and SOURCE_ID != 0) or len(SOURCE_IDS_ENV) > 0
    dest_ok = bool(DEST_ID and DEST_ID != 0) or len(DEST_IDS_ENV) > 0

    if not source_ok:
        errors.append("❌ SOURCE_ID or SOURCE_IDS is missing")

    if not dest_ok:
        errors.append("❌ DEST_ID or DEST_IDS is missing")

    if SOURCE_ID and SOURCE_ID > 0:
        errors.append(f"❌ SOURCE_ID must be negative (got {SOURCE_ID})")

    if DEST_ID and DEST_ID > 0:
        errors.append(f"❌ DEST_ID must be negative (got {DEST_ID})")

    if SOURCE_ID and DEST_ID and SOURCE_ID == DEST_ID:
        errors.append("❌ SOURCE_ID and DEST_ID must not be the same channel")

    # If multi-source/multi-dest are set, basic sanity check their signs too
    for sid in SOURCE_IDS_ENV:
        if sid > 0:
            errors.append(f"❌ SOURCE_IDS contains a positive ID: {sid}")

    for did in DEST_IDS_ENV:
        if did > 0:
            errors.append(f"❌ DEST_IDS contains a positive ID: {did}")

    if CAPTION_REPLACE:
        try:
            parsed = json.loads(CAPTION_REPLACE)
            if not isinstance(parsed, dict):
                errors.append('❌ CAPTION_REPLACE must be a JSON object (dict), e.g. {"old": "new"}')
        except json.JSONDecodeError as e:
            errors.append(f"❌ CAPTION_REPLACE is not valid JSON: {e}")

    if errors:
        print("\n🚨 CONFIGURATION ERRORS:\n")
        for err in errors:
            print(f"  {err}")
        print("\nFix these environment variables and redeploy.\n")
        raise Exception("Config validation failed")

    print("✅ Configuration validated successfully")


def caption_modifications_enabled() -> bool:
    """Return True if any caption modification env var is set."""
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
        self.retry_active: bool = False
        self.selected_source_ids: list = []
        self.source_counts: dict = {}

        # Flood-wait tracking
        self.floodwait_until: Optional[float] = None

        # Delall count (0 = all)
        self.delall_count: int = 0


state = State()
