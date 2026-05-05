import asyncio
from datetime import datetime
from typing import Any, List, Optional

from config import PIN_ENABLED, PIN_INTERVAL, PIN_TEXT, state
from utils.logger import get_logger

logger = get_logger(__name__)


async def send_separator(
    userbot: Any,
    dest_id: int,
    batch_num: int,
    start_msg: int,
    end_msg: int,
) -> Optional[int]:
    """
    Send a visual separator message to the destination channel.

    Args:
        userbot: Connected Telethon TelegramClient
        dest_id: Destination channel ID
        batch_num: Batch number
        start_msg: First message ID in this batch
        end_msg: Last message ID in this batch

    Returns:
        Sent message ID or None on failure
    """
    if not PIN_ENABLED:
        return None

    label = PIN_TEXT.format(n=batch_num, start=start_msg, end=end_msg)
    text = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{label}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    try:
        msg = await userbot.send_message(dest_id, text)
        logger.debug(f"Separator sent for batch {batch_num}: msg_id={msg.id}")
        return msg.id
    except Exception as e:
        logger.error(f"Failed to send separator for batch {batch_num}: {e}")
        return None


async def create_index_message(
    userbot: Any,
    dest_id: int,
) -> Optional[int]:
    """
    Send and pin the initial index message in the destination channel.

    Args:
        userbot: Connected Telethon TelegramClient
        dest_id: Destination channel ID

    Returns:
        Message ID of the pinned index message, or None on failure
    """
    if not PIN_ENABLED:
        return None

    try:
        msg = await userbot.send_message(
            dest_id,
            "📌 **CHANNEL INDEX**\n_(Building... please wait)_",
        )
        await asyncio.sleep(1)
        await userbot.pin_message(dest_id, msg.id, notify=False)
        state.index_msg_id = msg.id
        logger.info(f"Index message created and pinned: msg_id={msg.id}")
        return msg.id
    except Exception as e:
        logger.error(f"Failed to create index message: {e}")
        return None


async def update_index_message(
    userbot: Any,
    dest_id: int,
    index_msg_id: int,
    batches_list: List[dict],
) -> None:
    """
    Edit the pinned index message with updated batch information.

    Args:
        userbot: Connected Telethon TelegramClient
        dest_id: Destination channel ID
        index_msg_id: Message ID of the index message to edit
        batches_list: List of dicts with keys: num, start, end
    """
    if not PIN_ENABLED or not index_msg_id:
        return

    now_str = datetime.now().strftime("%H:%M:%S")
    total = sum(
        (b.get("end", 0) - b.get("start", 0) + 1) for b in batches_list
    )

    lines = [
        "📌 **CHANNEL INDEX**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for batch in batches_list:
        num = batch.get("num", "?")
        start = batch.get("start", 0)
        end = batch.get("end", 0)
        lines.append(f"🔢 Batch {num}:  Msg {start:,} – {end:,}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 Total processed: {state.processed:,}")
    lines.append(f"🕐 Updated: {now_str}")

    text = "\n".join(lines)

    try:
        await userbot.edit_message(dest_id, index_msg_id, text)
    except Exception as e:
        err_name = type(e).__name__
        if "MessageNotModified" in err_name or "not modified" in str(e).lower():
            pass
        else:
            logger.error(f"Failed to update index message: {e}")


async def handle_pin_checkpoint(
    userbot: Any,
    dest_id: int,
    state_obj: Any,
    processed_count: int,
    msg_id: int,
) -> None:
    """
    Called after every message. Triggers separator and index update at PIN_INTERVAL.

    Args:
        userbot: Connected Telethon TelegramClient
        dest_id: Destination channel ID
        state_obj: Global State object
        processed_count: Total messages processed so far
        msg_id: Current message ID (used as end marker for batch)
    """
    if not PIN_ENABLED:
        return

    if processed_count > 0 and processed_count % PIN_INTERVAL == 0:
        batch_num = state_obj.batch_number + 1
        state_obj.batch_number = batch_num

        # Determine start of this batch
        prev_batches = getattr(state_obj, "batches_list", [])
        if prev_batches:
            prev_end = prev_batches[-1].get("end", 0)
            batch_start = prev_end + 1
        else:
            batch_start = 1

        batch_end = msg_id

        # Send separator
        await send_separator(userbot, dest_id, batch_num, batch_start, batch_end)

        # Record batch
        batch_info = {"num": batch_num, "start": batch_start, "end": batch_end}
        if not hasattr(state_obj, "batches_list"):
            state_obj.batches_list = []
        state_obj.batches_list.append(batch_info)

        # Update index message
        if state_obj.index_msg_id:
            await update_index_message(
                userbot, dest_id, state_obj.index_msg_id, state_obj.batches_list
            )

        logger.info(
            f"Pin checkpoint — batch {batch_num}: msgs {batch_start}-{batch_end}"
        )