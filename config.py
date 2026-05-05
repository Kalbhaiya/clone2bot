import os
import sys
import json
from collections import deque
from typing import Optional, Any

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _get_env(key: str, default: Any = None) -> Any:
    return os.environ.get(key, default)


def _get_int(key: str, default: Optional[int] = None) -> Optional[int]:
    val = os.environ.get(key)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _get_float(key: str, default: float = 2.0) -> float:
    val = os.environ.get(key)
    if val is None or val.strip() == "":
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
# DEBUG (remove later)
# ─────────────────────────────────────────────

print("RAW ENV:")
print("API_ID =", repr(os.getenv("API_ID")))
print("API_HASH =", repr(os.getenv("API_HASH")))
print("BOT_TOKEN =", repr(os.getenv("BOT_TOKEN")))
print("ADMIN_ID =", repr(os.getenv("ADMIN_ID")))
print("SOURCE_ID =", repr(os.getenv("SOURCE_ID")))
print("DEST_ID =", repr(os.getenv("DEST_ID")))

# ─────────────────────────────────────────────
# REQUIRED VARIABLES (FIXED)
# ─────────────────────────────────────────────

API_ID = _get_int("API_ID", 0)
API_HASH = _get_env("API_HASH", "")
BOT_TOKEN = _get_env("BOT_TOKEN", "")
ADMIN_ID = _get_int("ADMIN_ID", 0)

SOURCE_ID = _get_int("SOURCE_ID", 0)
DEST_ID = _get_int("DEST_ID", 0)

# ─────────────────────────────────────────────
# OPTIONAL
# ─────────────────────────────────────────────

DELAY = _get_float("DELAY", 2.0)
MAX_RETRIES = _get_int("MAX_RETRIES", 3)
PIN_ENABLED = _get_bool("PIN_ENABLED", True)

# ─────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────

def validate_config():
    errors = []

    if not API_ID:
        errors.append("API_ID missing or invalid")

    if not API_HASH or len(API_HASH) < 30:
        errors.append("API_HASH invalid")

    if not BOT_TOKEN:
        errors.append("BOT_TOKEN missing")

    if not ADMIN_ID:
        errors.append("ADMIN_ID missing")

    if not SOURCE_ID or SOURCE_ID > 0:
        errors.append("SOURCE_ID must be negative")

    if not DEST_ID or DEST_ID > 0:
        errors.append("DEST_ID must be negative")

    if SOURCE_ID == DEST_ID:
        errors.append("SOURCE_ID and DEST_ID cannot be same")

    if errors:
        print("\n❌ CONFIG ERRORS:")
        for e in errors:
            print(" -", e)
        print()
        raise Exception("Config validation failed")

    print("✅ Config OK")


# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────

class State:
    def __init__(self):
        self.status = "IDLE"
        self.processed = 0
        self.failed = 0
        self.bot_client = None


state = State()
