import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from typing import List

try:
    from rich.logging import RichHandler
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

LOG_FILE = "/tmp/clone.log"
ERROR_FILE = "/tmp/errors.log"

_logging_setup_done = False


def _ensure_log_dirs() -> bool:
    """Ensure /tmp directory is writable. Return True if accessible."""
    try:
        os.makedirs("/tmp", exist_ok=True)
        return True
    except Exception:
        return False


def setup_logging() -> None:
    """
    Configure root logger with rotating file handlers and rich console output.
    Safe to call multiple times — only initializes once.
    """
    global _logging_setup_done
    if _logging_setup_done:
        return
    _logging_setup_done = True

    tmp_ok = _ensure_log_dirs()

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ─── Main file handler ────────────────────────────────────
    if tmp_ok:
        try:
            main_handler = RotatingFileHandler(
                LOG_FILE,
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            main_handler.setLevel(logging.INFO)
            main_handler.setFormatter(fmt)
            root.addHandler(main_handler)
        except Exception as e:
            print(f"Warning: Cannot create main log file: {e}", file=sys.stderr)

    # ─── Error file handler ───────────────────────────────────
    if tmp_ok:
        try:
            error_handler = RotatingFileHandler(
                ERROR_FILE,
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(fmt)
            root.addHandler(error_handler)
        except Exception as e:
            print(f"Warning: Cannot create error log file: {e}", file=sys.stderr)

    # ─── Console handler ──────────────────────────────────────
    if RICH_AVAILABLE:
        console_handler = RichHandler(
            level=logging.INFO,
            show_time=True,
            show_path=True,
            markup=True,
        )
        root.addHandler(console_handler)
    else:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(fmt)
        root.addHandler(console_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger. Sets up logging if not already done.

    Args:
        name: Logger name (usually __name__)

    Returns:
        logging.Logger instance
    """
    if not _logging_setup_done:
        setup_logging()
    return logging.getLogger(name)


def log_error(msg_id: int, error: Exception, traceback_str: str) -> None:
    """
    Write a structured error entry to the error log.

    Args:
        msg_id: Telegram message ID that caused the error
        error: The exception object
        traceback_str: Full traceback string from traceback.format_exc()
    """
    logger = get_logger("error_logger")
    logger.error(
        f"MSG_ID={msg_id} | ERROR={type(error).__name__}: {error}\n{traceback_str}"
    )


def get_last_n_errors(n: int = 30) -> List[str]:
    """
    Read the last n lines from the error log file.

    Args:
        n: Number of lines to return

    Returns:
        List of log line strings
    """
    if not os.path.exists(ERROR_FILE):
        return []
    try:
        with open(ERROR_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [line.rstrip() for line in lines[-n:]]
    except Exception as e:
        return [f"Failed to read error log: {e}"]