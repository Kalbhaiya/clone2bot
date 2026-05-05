import os
import asyncio
import traceback
from typing import Any, List, Optional

from core.caption import modify_caption
from utils.logger import get_logger

logger = get_logger(__name__)


async def process_album(
    album_msgs: List[Any],
    userbot: Any,
    dest_id: int,
    caption_mod_enabled: bool,
    src_id: int = 0,
) -> bool:
    """
    Process a Telegram media group (album) — forward grouped or reupload with caption.

    Args:
        album_msgs: List of Telethon message objects sharing the same grouped_id
        userbot: Connected Telethon TelegramClient
        dest_id: Destination channel ID
        caption_mod_enabled: True = reupload with modified caption, False = grouped forward

    Returns:
        True on success, False on failure
    """
    if not album_msgs:
        return True

    album_msgs_sorted = sorted(album_msgs, key=lambda m: m.id)

    # ─── Forward mode — no caption modification ───────────────
    if not caption_mod_enabled:
        try:
            msg_ids = [m.id for m in album_msgs_sorted]
            source_peer = src_id if src_id else album_msgs_sorted[0].peer_id
            await userbot.forward_messages(
                entity=dest_id,
                messages=msg_ids,
                from_peer=source_peer,
                drop_author=True,
            )
            logger.debug(f"Album forwarded: {msg_ids}")
            return True
        except Exception as e:
            logger.error(
                f"Album grouped forward failed (IDs: {[m.id for m in album_msgs_sorted]}): "
                f"{e}\n{traceback.format_exc()}"
            )
            return False

    # ─── Reupload mode — download each, send with modified caption ─
    temp_files: List[str] = []

    try:
        # Find the first message with a caption
        raw_caption = None
        for msg in album_msgs_sorted:
            cap = getattr(msg, "text", None) or getattr(msg, "message", None)
            if cap:
                raw_caption = cap
                break

        modified_caption = modify_caption(raw_caption)

        # Download all media files
        for msg in album_msgs_sorted:
            if getattr(msg, "media", None) is None:
                continue
            temp_path = f"/tmp/album_{msg.id}"
            try:
                downloaded = await userbot.download_media(msg, file=temp_path)
                if downloaded:
                    temp_files.append(str(downloaded))
                else:
                    logger.warning(f"Album msg {msg.id} has no downloadable media, skipping file")
            except Exception as e:
                logger.error(f"Failed to download album msg {msg.id}: {e}")
                return False

        if not temp_files:
            logger.warning(f"Album has no downloadable files, skipping")
            return False

        # Send all as album
        await userbot.send_file(
            dest_id,
            file=temp_files,
            caption=modified_caption,
        )

        logger.debug(f"Album reuploaded: {len(temp_files)} files")
        return True

    except Exception as e:
        logger.error(
            f"Album reupload failed: {e}\n{traceback.format_exc()}"
        )
        return False

    finally:
        for path in temp_files:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as cleanup_err:
                    logger.debug(f"Failed to delete temp file {path}: {cleanup_err}")


def is_album_complete(buffer: dict, grouped_id: int, last_seen_time: float) -> bool:
    """
    Heuristic check whether an album is complete (no more parts arriving).

    Args:
        buffer: defaultdict of grouped_id -> list of messages
        grouped_id: The album group ID to check
        last_seen_time: asyncio event loop time when last part was seen

    Returns:
        True if 0.5 seconds have passed since last part was added
    """
    import asyncio
    current_time = asyncio.get_event_loop().time()
    return (current_time - last_seen_time) >= 0.5