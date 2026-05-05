import asyncio
import traceback
from typing import Callable, Any, Coroutine, Optional

from config import ADMIN_ID
from utils.logger import get_logger, log_error
from utils.progress import append_failed_id

logger = get_logger(__name__)


async def flood_countdown(
    seconds: int,
    bot_client: Any,
    admin_id: int,
) -> None:
    """
    Send and continuously edit a FloodWait countdown message to admin.

    Args:
        seconds: Total flood wait seconds
        bot_client: Pyrogram bot Client
        admin_id: Admin's Telegram user ID
    """
    total = seconds
    try:
        msg = await bot_client.send_message(
            admin_id,
            f"⏳ **FloodWait Detected!**\n\n"
            f"Telegram ne {seconds}s wait maanga.\n"
            f"Resume in: {seconds}s",
        )
    except Exception as e:
        logger.error(f"Failed to send flood countdown message: {e}")
        await asyncio.sleep(seconds)
        return

    width = 20
    for remaining in range(seconds, 0, -1):
        elapsed = total - remaining
        percent = (elapsed / total) * 100 if total > 0 else 100
        filled = int(width * percent / 100)
        bar = "█" * filled + "░" * (width - filled)
        percent_str = f"{percent:.0f}%"

        try:
            await msg.edit(
                f"⏳ **FloodWait**\n"
                f"[{bar}] {percent_str}\n"
                f"Resuming in: {remaining}s"
            )
        except Exception:
            pass

        await asyncio.sleep(1)

    try:
        await msg.edit("▶️ **Resuming...**")
    except Exception:
        pass


async def forward_with_retry(
    forward_func: Callable[[], Coroutine],
    message_id: int,
    bot_client: Any,
    max_retries: int,
    attempt: int = 1,
    extra_delay: float = 0.0,
) -> bool:
    """
    Execute a forward coroutine with retry logic and error handling.

    Handles:
    - FloodWaitError: waits + retries (not counted as retry attempt)
    - ChatForwardsRestrictedError: logs + skips
    - MessageIdInvalidError: silently skips
    - ChannelPrivateError: raises StopAsyncIteration to halt loop
    - Other exceptions: exponential backoff retry up to max_retries

    Args:
        forward_func: Async callable that performs the forward
        message_id: Source message ID (for logging)
        bot_client: Pyrogram bot Client for flood notifications
        max_retries: Maximum retry attempts for generic errors
        attempt: Current attempt number (used for exponential backoff)
        extra_delay: Additional delay to add (accumulated from flood waits)

    Returns:
        True on success, False on skip/failure
    """
    if extra_delay > 0:
        await asyncio.sleep(extra_delay)

    try:
        result = await forward_func()
        return result if result is not None else True

    except Exception as e:
        err_name = type(e).__name__

        # ─── FloodWaitError ───────────────────────────────────
        if "FloodWait" in err_name:
            wait_seconds = getattr(e, "seconds", 30)
            logger.warning(f"FloodWait {wait_seconds}s for msg {message_id}")

            await flood_countdown(wait_seconds + 10, bot_client, ADMIN_ID)
            await asyncio.sleep(wait_seconds + 10)

            return await forward_with_retry(
                forward_func,
                message_id,
                bot_client,
                max_retries,
                attempt=attempt,
                extra_delay=5.0,
            )

        # ─── Forwards restricted ──────────────────────────────
        if "ChatForwardsRestricted" in err_name or "ForwardsRestricted" in err_name:
            logger.warning(
                f"Msg {message_id} skipped — chat forwards restricted"
            )
            append_failed_id(message_id)
            return False

        # ─── Message deleted / invalid ────────────────────────
        if "MessageIdInvalid" in err_name or "MessageNotFound" in err_name:
            logger.debug(f"Msg {message_id} skipped — deleted or not found")
            return False

        # ─── Channel became private ───────────────────────────
        if "ChannelPrivate" in err_name or "UserBannedInChannel" in err_name:
            logger.critical(
                f"Critical: Channel private or banned — stopping clone loop"
            )
            try:
                await bot_client.send_message(
                    ADMIN_ID,
                    "🚨 **Critical Error!**\n\n"
                    "Source/destination channel inaccessible.\n"
                    "Cloning rukk gayi. Channel permissions check karo."
                )
            except Exception:
                pass
            raise StopAsyncIteration("Channel private or access revoked")

        # ─── Generic retry ────────────────────────────────────
        tb_str = traceback.format_exc()
        log_error(message_id, e, tb_str)

        if attempt <= max_retries:
            sleep_time = 5 * attempt
            logger.warning(
                f"Msg {message_id} attempt {attempt}/{max_retries} failed: "
                f"{err_name} — retrying in {sleep_time}s"
            )
            await asyncio.sleep(sleep_time)
            return await forward_with_retry(
                forward_func,
                message_id,
                bot_client,
                max_retries,
                attempt=attempt + 1,
                extra_delay=0.0,
            )
        else:
            logger.error(
                f"Msg {message_id} failed after {max_retries} retries: "
                f"{err_name}: {e}"
            )
            append_failed_id(message_id)
            return False


async def reconnect_userbot(userbot_client: Any) -> bool:
    """
    Attempt to reconnect a disconnected Telethon client.

    Args:
        userbot_client: Telethon TelegramClient instance

    Returns:
        True if reconnected successfully, False if all attempts failed
    """
    max_attempts = 5
    wait_between = 30

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"Reconnect attempt {attempt}/{max_attempts}...")

            if userbot_client.is_connected():
                await userbot_client.disconnect()
                await asyncio.sleep(2)

            await userbot_client.connect()

            if await userbot_client.is_user_authorized():
                logger.info(f"Reconnected successfully on attempt {attempt}")
                return True
            else:
                logger.warning(f"Reconnect attempt {attempt}: connected but not authorized")

        except Exception as e:
            logger.error(f"Reconnect attempt {attempt} failed: {e}")

        if attempt < max_attempts:
            logger.info(f"Waiting {wait_between}s before next reconnect attempt...")
            await asyncio.sleep(wait_between)

    logger.critical("All reconnect attempts failed")
    return False