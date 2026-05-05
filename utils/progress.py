import json
import os
from datetime import datetime
from typing import Optional, List, Any

from utils.logger import get_logger

logger = get_logger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRESS_DIR = os.path.join(_BASE_DIR, "data")
PROGRESS_FILE = os.path.join(PROGRESS_DIR, "progress.json")
FAILED_IDS_FILE = os.path.join(PROGRESS_DIR, "failed_ids.txt")


def _ensure_tmp() -> bool:
    """Ensure data directory exists. Return True if writable."""
    try:
        os.makedirs(PROGRESS_DIR, exist_ok=True)
        return True
    except Exception:
        return False


def save_progress(state: Any, extra: Optional[dict] = None) -> None:
    """
    Persist current cloning state to data/progress.json.

    Args:
        state: Global State object from config.py
        extra: Optional extra dict to merge into progress (e.g. {"source_idx": 2})
    """
    if not _ensure_tmp():
        logger.warning("Cannot write progress — data dir not accessible")
        return

    from config import SOURCE_ID, DEST_ID

    data = {
        "last_processed_id": getattr(state, "current_id", 0),
        "processed": getattr(state, "processed", 0),
        "failed": getattr(state, "failed", 0),
        "albums": getattr(state, "albums_count", 0),
        "batches_pinned": getattr(state, "batch_number", 0),
        "batch_number": getattr(state, "batch_number", 0),
        "index_msg_id": getattr(state, "index_msg_id", None),
        "batches_list": getattr(state, "batches_list", []),
        "source_idx": 0,
        "start_time": (
            state.start_time.strftime("%Y-%m-%d %H:%M:%S")
            if getattr(state, "start_time", None)
            else None
        ),
        "session_active": True,
        "source_id": SOURCE_ID,
        "dest_id": DEST_ID,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if extra:
        data.update(extra)

    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.debug(f"Progress saved — processed={data['processed']}")
    except Exception as e:
        logger.error(f"Failed to save progress: {e}")


def load_progress() -> Optional[dict]:
    """
    Load progress data from /tmp/progress.json.

    Returns:
        Dict with progress data, or None if file doesn't exist/is invalid
    """
    if not os.path.exists(PROGRESS_FILE):
        return None
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        logger.error(f"Failed to load progress: {e}")
        return None


def clear_progress() -> None:
    """Delete /tmp/progress.json to mark job as complete."""
    try:
        if os.path.exists(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)
            logger.info("Progress file cleared — job complete")
    except Exception as e:
        logger.error(f"Failed to clear progress: {e}")


def append_failed_id(msg_id: int) -> None:
    """
    Append a failed message ID to /tmp/failed_ids.txt.

    Args:
        msg_id: Telegram message ID that failed
    """
    _ensure_tmp()
    try:
        with open(FAILED_IDS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{msg_id}\n")
    except Exception as e:
        logger.error(f"Failed to append failed ID {msg_id}: {e}")


def load_failed_ids() -> List[int]:
    """
    Load all failed message IDs from /tmp/failed_ids.txt.

    Returns:
        List of integer message IDs
    """
    if not os.path.exists(FAILED_IDS_FILE):
        return []
    try:
        with open(FAILED_IDS_FILE, "r", encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        ids = []
        for line in lines:
            line = line.strip()
            if line.isdigit():
                ids.append(int(line))
        return ids
    except Exception as e:
        logger.error(f"Failed to load failed IDs: {e}")
        return []


def clear_failed_ids() -> None:
    """Delete /tmp/failed_ids.txt."""
    try:
        if os.path.exists(FAILED_IDS_FILE):
            os.remove(FAILED_IDS_FILE)
            logger.info("Failed IDs file cleared")
    except Exception as e:
        logger.error(f"Failed to clear failed IDs: {e}")


def mark_session_inactive() -> None:
    """Set session_active=false in progress.json without changing other data."""
    if not os.path.exists(PROGRESS_FILE):
        return
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["session_active"] = False
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to mark session inactive: {e}")


def mark_session_active() -> None:
    """Set session_active=true in progress.json."""
    if not os.path.exists(PROGRESS_FILE):
        return
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["session_active"] = True
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to mark session active: {e}")


def progress_exists() -> bool:
    """
    Check whether a progress file exists.

    Returns:
        True if /tmp/progress.json exists
    """
    return os.path.exists(PROGRESS_FILE)


# ─── Session persistence ─────────────────────
SESSION_FILE = os.path.join(PROGRESS_DIR, "session.txt")


def save_session(session_string: str) -> None:
    """Save Telethon StringSession to disk so login persists across restarts."""
    _ensure_tmp()
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            f.write(session_string.strip())
        logger.info("Session saved to disk")
    except Exception as e:
        logger.error(f"Failed to save session: {e}")


def load_session() -> Optional[str]:
    """Load Telethon StringSession from disk. Returns None if not found."""
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            s = f.read().strip()
        return s if s else None
    except Exception as e:
        logger.error(f"Failed to load session: {e}")
        return None


def clear_session() -> None:
    """Delete saved session file (on logout)."""
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
            logger.info("Session file cleared")
    except Exception as e:
        logger.error(f"Failed to clear session: {e}")