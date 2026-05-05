import os
import asyncio
import traceback
from typing import Any, Optional

from config import caption_modifications_enabled
from core.caption import modify_caption
from utils.logger import get_logger

logger = get_logger(__name__)


def get_media_info(message: Any) -> dict:
    """
    Inspect a Telethon message and return its media type info.

    Args:
        message: Telethon message object

    Returns:
        Dict with keys: type, emoji, size_mb, has_caption
    """
    from telethon.tl.types import (
        MessageMediaPhoto,
        MessageMediaDocument,
        DocumentAttributeVideo,
        DocumentAttributeAudio,
        DocumentAttributeSticker,
        DocumentAttributeAnimated,
    )

    result = {
        "type": "text",
        "emoji": "📝",
        "size_mb": 0.0,
        "has_caption": False,
    }

    caption = getattr(message, "text", None) or getattr(message, "message", None)
    if caption:
        result["has_caption"] = True

    media = getattr(message, "media", None)
    if media is None:
        return result

    if isinstance(media, MessageMediaPhoto):
        result["type"] = "photo"
        result["emoji"] = "🖼️"
        return result

    if isinstance(media, MessageMediaDocument):
        doc = getattr(media, "document", None)
        if doc is None:
            result["type"] = "document"
            result["emoji"] = "📄"
            return result

        size_bytes = getattr(doc, "size", 0)
        result["size_mb"] = round(size_bytes / (1024 * 1024), 2)

        attrs = getattr(doc, "attributes", [])

        is_sticker = any(
            isinstance(a, (DocumentAttributeSticker, DocumentAttributeAnimated))
            for a in attrs
        )
        if is_sticker:
            result["type"] = "sticker"
            result["emoji"] = "🎭"
            return result

        is_video = any(isinstance(a, DocumentAttributeVideo) for a in attrs)
        if is_video:
            mime = getattr(doc, "mime_type", "") or ""
            if "matroska" in mime or mime.endswith("mkv") or mime == "video/x-matroska":
                result["type"] = "video_mkv"
                result["emoji"] = "🎞️"
            else:
                result["type"] = "video_mp4"
                result["emoji"] = "🎬"
            return result

        is_audio = any(isinstance(a, DocumentAttributeAudio) for a in attrs)
        if is_audio:
            audio_attr = next(a for a in attrs if isinstance(a, DocumentAttributeAudio))
            if getattr(audio_attr, "voice", False):
                result["type"] = "voice"
                result["emoji"] = "🎙️"
            else:
                result["type"] = "audio"
                result["emoji"] = "🎵"
            return result

        result["type"] = "document"
        result["emoji"] = "📄"
        return result

    # Poll
    from telethon.tl.types import MessageMediaPoll
    if isinstance(media, MessageMediaPoll):
        result["type"] = "poll"
        result["emoji"] = "📊"
        return result

    result["type"] = "other"
    result["emoji"] = "📦"
    return result


def get_smart_delay(media_info: dict) -> float:
    """
    Return appropriate sleep delay based on media type and size.

    Args:
        media_info: dict from get_media_info()

    Returns:
        Float seconds to sleep
    """
    mtype = media_info.get("type", "other")
    size_mb = media_info.get("size_mb", 0.0)

    if mtype in ("text", "sticker", "poll"):
        return 0.3
    if mtype == "photo":
        return 0.5
    if mtype in ("audio", "voice"):
        return 0.8
    if mtype == "video":
        return 1.0 if size_mb < 50 else 1.5
    if mtype == "document":
        return 1.0 if size_mb < 50 else 1.5
    return 1.0


def get_caption_preview(caption: Optional[str]) -> str:
    """
    Return first 30 characters of a caption for activity log display.

    Args:
        caption: Raw caption string or None

    Returns:
        Preview string
    """
    if not caption:
        return "no caption"
    preview = caption.replace("\n", " ").strip()
    return preview[:30] + ("..." if len(preview) > 30 else "")


async def forward_batch_raw(
    message_ids: list,
    userbot: Any,
    dest_id: int,
    source_id: int,
    source_entity: Any = None,
    dest_entity: Any = None,
) -> bool:
    """
    Forward a batch of messages in a single Telegram API call with drop_author.

    Args:
        message_ids: List of source message IDs to forward
        userbot: Connected Telethon TelegramClient
        dest_id: Destination channel ID
        source_id: Source channel ID
        source_entity: Pre-fetched source entity (optional, avoids repeated API calls)
        dest_entity: Pre-fetched dest entity (optional, avoids repeated API calls)

    Returns:
        True on success
    """
    from telethon.tl.functions.messages import ForwardMessagesRequest
    if source_entity is None:
        source_entity = await userbot.get_entity(source_id)
    if dest_entity is None:
        dest_entity = await userbot.get_entity(dest_id)
    await userbot(ForwardMessagesRequest(
        from_peer=source_entity,
        id=message_ids,
        to_peer=dest_entity,
        drop_author=True,
        silent=True,
    ))
    return True


async def forward_single(
    message: Any,
    userbot: Any,
    dest_id: int,
    source_id: int,
    caption_mod_enabled: bool,
) -> bool:
    """
    Forward or reupload a single Telethon message to destination.

    Args:
        message: Telethon message object
        userbot: Connected Telethon TelegramClient
        dest_id: Destination channel ID
        source_id: Source channel ID
        caption_mod_enabled: True = reupload with modified caption

    Returns:
        True on success, False on failure
    """
    media = getattr(message, "media", None)
    text = getattr(message, "text", None) or getattr(message, "message", None)

    async def _forward_raw(msg_id: int) -> None:
        """Forward using raw Telegram API with drop_author support."""
        from telethon.tl.functions.messages import ForwardMessagesRequest
        source_entity = await userbot.get_entity(source_id)
        dest_entity = await userbot.get_entity(dest_id)
        await userbot(ForwardMessagesRequest(
            from_peer=source_entity,
            id=[msg_id],
            to_peer=dest_entity,
            drop_author=True,
            silent=True,
        ))

    # ─── No media: text-only message ─────────────────────────
    if media is None:
        if not text:
            # Empty message (service msg etc) — skip
            return True
        if caption_mod_enabled:
            modified = modify_caption(text)
            if modified:
                await userbot.send_message(dest_id, modified)
            return True
        else:
            await _forward_raw(message.id)
            return True

    # ─── Has media, no caption mod ────────────────────────────
    if not caption_mod_enabled:
        await _forward_raw(message.id)
        return True

    # ─── Has media, caption mod enabled ──────────────────────
    temp_path = f"/tmp/media_{message.id}"
    try:
        downloaded = await userbot.download_media(message, file=temp_path)
        if downloaded is None:
            # No downloadable media (sticker pack reference etc) — forward normally
            await _forward_raw(message.id)
            return True

        modified_cap = modify_caption(text)
        await userbot.send_file(
            dest_id,
            file=downloaded,
            caption=modified_cap,
        )
        return True

    except Exception as e:
        logger.error(f"forward_single reupload failed for {message.id}: {e}")
        raise
    finally:
        for p in [temp_path, temp_path + ".jpg", temp_path + ".mp4",
                  temp_path + ".mp3", temp_path + ".pdf"]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        # Also try exact downloaded path
        if "downloaded" in dir() and downloaded and os.path.exists(str(downloaded)):
            try:
                os.remove(str(downloaded))
            except Exception:
                pass