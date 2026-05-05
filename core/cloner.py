import asyncio
import traceback
from collections import defaultdict
from datetime import datetime
from typing import Optional, Any

from pyrogram import Client
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich.panel import Panel
from rich.columns import Columns
from rich import box

from config import (
    state,
    SOURCE_ID,
    DEST_ID,
    SOURCE_IDS_ENV,
    DEST_IDS_ENV,
    ADMIN_ID,
    MAX_RETRIES,
    PIN_ENABLED,
    caption_modifications_enabled,
)
from core.forwarder import forward_single, forward_batch_raw, get_media_info, get_smart_delay, get_caption_preview
from core.album import process_album

BATCH_SIZE = 100  # Messages per batch API call when no caption mod (Telegram max)
from core.caption import get_caption_summary
from utils.logger import get_logger
from utils.progress import (
    save_progress,
    load_progress,
    clear_progress,
    append_failed_id,
)
from utils.retry import forward_with_retry
from utils.pin import (
    create_index_message,
    handle_pin_checkpoint,
)
from bot.messages import get_status_text, get_completion_text, calc_speed, format_duration

logger = get_logger(__name__)
console = Console()


# ─────────────────────────────────────────────
# Rich dashboard builder
# ─────────────────────────────────────────────

def _build_dashboard() -> Panel:
    """Build rich live dashboard panel showing current cloning status."""
    processed = state.processed
    total = state.total_messages
    failed = state.failed
    albums = state.albums_count
    skipped = getattr(state, "skipped", 0)

    speed = calc_speed(processed, state.start_time)
    elapsed = format_duration(
        (datetime.now() - state.start_time).total_seconds()
    ) if state.start_time else "0s"

    cap_mod = caption_modifications_enabled()
    cap_status = "✅ Modified" if cap_mod else "⏭️ Preserved (forward mode)"
    pin_status = "✅ Enabled" if PIN_ENABLED else "❌ Disabled"
    status_label = "⏸️ Paused" if state.pause_flag else "🟢 Running"

    stats_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    stats_table.add_column("Key", style="bold cyan", width=18)
    stats_table.add_column("Value", style="white")

    bar_width = 30
    if total > 0:
        percent = (processed / total * 100)
        filled = int(bar_width * percent / 100)
        bar_str = "█" * filled + "░" * (bar_width - filled)
        remaining = max(0, total - processed)
        eta_mins = (remaining / speed) if speed > 0 else 0
        eta_str = format_duration(eta_mins * 60) if eta_mins > 0 else "Calculating..."
        stats_table.add_row("Progress", f"[{bar_str}] {percent:.1f}%")
        stats_table.add_row("Processed", f"{processed:,} / {total:,}")
    else:
        bar_str = "░" * bar_width
        eta_str = "Calculating..."
        stats_table.add_row("Progress", f"[{bar_str}] ?%")
        stats_table.add_row("Processed", f"{processed:,} / (counting...)")
    stats_table.add_row("✅ Forwarded", f"{processed:,}")
    stats_table.add_row("🖼️ Albums", f"{albums:,} groups")
    stats_table.add_row("❌ Failed", f"{failed:,}")
    stats_table.add_row("⏭️ Skipped", f"{skipped:,}")
    stats_table.add_row("⚡ Speed", f"{speed} msg/min")
    stats_table.add_row("⏱️ Elapsed", elapsed)
    stats_table.add_row("🏁 ETA", eta_str)
    stats_table.add_row("📍 Current ID", f"{state.current_id:,}")
    stats_table.add_row("📌 Batches", f"{state.batch_number}")
    stats_table.add_row("📝 Captions", cap_status)
    stats_table.add_row("📌 Pinning", pin_status)
    stats_table.add_row("Status", status_label)

    activity_lines = list(state.recent_activity)
    activity_text = "\n".join(activity_lines) if activity_lines else "No activity yet..."

    activity_table = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    activity_table.add_column("Recent Activity (last 5)", style="dim white")
    for line in activity_lines:
        activity_table.add_row(line)
    if not activity_lines:
        activity_table.add_row("No activity yet...")

    combined = Table.grid(padding=1)
    combined.add_row(stats_table, activity_table)

    return Panel(
        combined,
        title="[bold yellow]🚀 TeleClone — Live Dashboard[/bold yellow]",
        border_style="bright_blue",
        box=box.DOUBLE_EDGE,
    )


# ─────────────────────────────────────────────
# Recent activity tracker
# ─────────────────────────────────────────────

def add_to_recent_activity(
    message: Any,
    success: bool,
    media_info: Optional[dict] = None,
    is_album: bool = False,
    count: int = 1,
) -> None:
    """
    Append a one-line activity string to state.recent_activity deque.

    Args:
        message: Telethon message object
        success: Whether the forward succeeded
        media_info: dict from get_media_info()
        is_album: True if this is an album group
        count: Number of messages in album
    """
    try:
        msg_id = getattr(message, "id", "?")
        icon = "✅" if success else "❌"

        if is_album:
            cap = get_caption_preview(getattr(message, "text", None) or getattr(message, "message", None))
            line = f"{icon} ID:{msg_id} 🖼️ Album({count}) — {cap}"
        elif media_info:
            emoji = media_info.get("emoji", "📄")
            mtype = media_info.get("type", "other").capitalize()
            cap = get_caption_preview(getattr(message, "text", None) or getattr(message, "message", None))
            line = f"{icon} ID:{msg_id} {emoji} {mtype} — {cap}"
        else:
            cap = get_caption_preview(getattr(message, "text", None) or getattr(message, "message", None))
            line = f"{icon} ID:{msg_id} — {cap}"

        state.recent_activity.append(line)
    except Exception as e:
        logger.debug(f"add_to_recent_activity error: {e}")


# ─────────────────────────────────────────────
# Status message updater
# ─────────────────────────────────────────────

async def update_status_message(bot_client: Client) -> None:
    """Edit the pinned bot status message with current progress."""
    if state.status_message_id is None:
        return
    try:
        text = get_status_text(state, state.source_name, state.dest_name)
        await bot_client.edit_message_text(
            chat_id=ADMIN_ID,
            message_id=state.status_message_id,
            text=text,
        )
    except Exception as e:
        logger.debug(f"Status message update failed: {e}")


def _passes_filter(media_type: str) -> bool:
    """Return True if this media type should be forwarded (per filter settings)."""
    try:
        from utils.settings import get_filter
        return get_filter().get(media_type, True)
    except Exception:
        return True


def _check_caption_skip(message: Any) -> bool:
    """Return True if message should be skipped due to caption-skip rules."""
    try:
        from utils.settings import should_skip_by_caption
        caption = getattr(message, "text", None) or getattr(message, "message", None)
        return should_skip_by_caption(caption)
    except Exception:
        return False


def _get_all_source_ids() -> list:
    """Return all source IDs: env SOURCE_ID + env SOURCE_IDS + settings extras."""
    from utils.settings import get_extra_sources
    ids = []
    if SOURCE_ID:
        ids.append(SOURCE_ID)
    for sid in SOURCE_IDS_ENV:
        if sid not in ids:
            ids.append(sid)
    for sid in get_extra_sources():
        if sid not in ids:
            ids.append(sid)
    return ids or [SOURCE_ID]


def _get_all_dest_ids() -> list:
    """Return all destination IDs: env DEST_ID + env DEST_IDS + settings extras."""
    from utils.settings import get_extra_dests
    ids = []
    if DEST_ID:
        ids.append(DEST_ID)
    for did in DEST_IDS_ENV:
        if did not in ids:
            ids.append(did)
    for did in get_extra_dests():
        if did not in ids:
            ids.append(did)
    return ids or [DEST_ID]


# ─────────────────────────────────────────────
# Channel media scanner
# ─────────────────────────────────────────────

async def scan_source_media_types(userbot: Any, source_id: int, limit=2000) -> dict:
    """
    Scan the source channel and count each media type.
    Returns a dict with counts per type plus video sub-types (mp4/mkv).
    """
    counts = {
        "text": 0, "photo": 0,
        "video_mp4": 0, "video_mkv": 0, "video_other": 0,
        "audio": 0, "document": 0, "sticker": 0,
        "animation": 0, "voice": 0, "video_note": 0,
        "poll": 0, "other": 0,
    }
    scanned = 0
    try:
        async for msg in userbot.iter_messages(source_id, limit=limit):
            scanned += 1
            if msg.media is None:
                counts["text"] += 1
            elif msg.photo:
                counts["photo"] += 1
            elif msg.sticker:
                counts["sticker"] += 1
            elif msg.video:
                mime = ""
                fname = ""
                try:
                    mime = (getattr(msg.file, "mime_type", "") or "").lower()
                    fname = (msg.file.name or "").lower()
                except Exception:
                    pass
                if fname.endswith(".mkv") or "matroska" in mime:
                    counts["video_mkv"] += 1
                elif fname.endswith(".mp4") or "mp4" in mime:
                    counts["video_mp4"] += 1
                else:
                    counts["video_other"] += 1
            elif msg.gif:
                counts["animation"] += 1
            elif msg.audio:
                counts["audio"] += 1
            elif msg.voice:
                counts["voice"] += 1
            elif msg.video_note:
                counts["video_note"] += 1
            elif msg.document:
                counts["document"] += 1
            elif getattr(msg, "poll", None):
                counts["poll"] += 1
            else:
                counts["other"] += 1
    except Exception as e:
        logger.error(f"scan_source_media_types error: {e}")
    counts["_scanned"] = scanned
    return counts


def format_scan_summary(counts: dict) -> str:
    """Format scan result dict into a readable summary string."""
    scanned = counts.get("_scanned", 0)
    emoji_map = {
        "text": "📝", "photo": "🖼️", "audio": "🎵",
        "document": "📄", "video_mp4": "🎬 MP4", "video_mkv": "🎞️ MKV",
        "video_other": "🎥 Video(other)", "sticker": "🎭",
        "animation": "🌀 GIF", "voice": "🎙️", "video_note": "📹",
        "poll": "📊", "other": "📦",
    }
    lines = [f"📊 **Channel Scan** _(last {scanned:,} msgs)_\n"]
    for key, emoji in emoji_map.items():
        val = counts.get(key, 0)
        if val > 0:
            lines.append(f"  {emoji}: {val:,}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Dry Run
# ─────────────────────────────────────────────

_SEP = "⊶" * 22
_BOLD_DIGITS = str.maketrans("0123456789", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵")


def _bn(n: int) -> str:
    return f"{n:,}".translate(_BOLD_DIGITS)


def _dryrun_media_type(msg: Any) -> str:
    """Classify a Telethon message into a media type string."""
    if msg.media is None:
        return "text"
    if msg.photo:
        return "photo"
    if msg.sticker:
        return "sticker"
    if msg.gif:
        return "animation"
    if msg.voice:
        return "voice"
    if msg.video_note:
        return "video_note"
    if msg.audio:
        return "audio"
    if msg.video:
        mime = ""
        fname = ""
        try:
            mime = (getattr(msg.file, "mime_type", "") or "").lower()
            fname = (msg.file.name or "").lower()
        except Exception:
            pass
        if fname.endswith(".mkv") or "matroska" in mime:
            return "video_mkv"
        return "video_mp4"
    if getattr(msg, "poll", None):
        return "poll"
    if msg.document:
        return "document"
    return "other"


def _build_dryrun_ui(
    source_name: str,
    scanned: int,
    total: int,
    forwarded: int,
    skipped_filter: int,
    skipped_caption: int,
    fwd_breakdown: dict,
    skip_breakdown: dict,
    elapsed_s: float,
    done: bool = False,
) -> str:
    from bot.messages import format_duration
    emoji_map = {
        "text": "📝", "photo": "🖼️", "audio": "🎵",
        "document": "📄", "video_mp4": "🎬", "video_mkv": "🎞️",
        "voice": "🎙️", "video_note": "📹", "sticker": "🎭",
        "animation": "🌀", "poll": "📊", "other": "📦",
    }
    speed = (scanned / elapsed_s * 60) if elapsed_s > 0 else 0.0
    header = "✅ 𝗗𝗿𝘆 𝗥𝘂𝗻 𝗖𝗼𝗺𝗽𝗹𝗲𝘁𝗲!" if done else "🔍 𝗗𝗿𝘆 𝗥𝘂𝗻 𝗶𝗻 𝗣𝗿𝗼𝗴𝗿𝗲𝘀𝘀..."

    if total > 0:
        pct = min(100.0, scanned / total * 100)
        filled = int(15 * pct / 100)
        bar = "[" + "█" * filled + "░" * (15 - filled) + "]"
        progress = f"📈 {bar} {pct:.1f}%\n📝 {_bn(scanned)} 𝗼𝗳 {_bn(total)} 𝗺𝘀𝗴𝘀\n"
    else:
        progress = f"📝 {_bn(scanned)} 𝗺𝘀𝗴𝘀 𝘀𝗰𝗮𝗻𝗻𝗲𝗱\n"

    fwd_lines = ""
    for mtype, cnt in sorted(fwd_breakdown.items(), key=lambda x: -x[1]):
        if cnt > 0:
            fwd_lines += f"  {emoji_map.get(mtype,'•')} {mtype}: {_bn(cnt)}\n"

    skip_lines = ""
    for mtype, cnt in sorted(skip_breakdown.items(), key=lambda x: -x[1]):
        if cnt > 0:
            skip_lines += f"  {emoji_map.get(mtype,'•')} {mtype}: {_bn(cnt)}\n"
    if skipped_caption > 0:
        skip_lines += f"  📋 caption rule: {_bn(skipped_caption)}\n"

    return (
        f"{header}\n"
        f"{_SEP}\n\n"
        f"📤 `{source_name}`\n\n"
        f"{progress}\n"
        f"✓ 𝗪𝗼𝘂𝗹𝗱 𝗙𝗼𝗿𝘄𝗮𝗿𝗱: {_bn(forwarded)}\n"
        f"✗ 𝗪𝗼𝘂𝗹𝗱 𝗦𝗸𝗶𝗽: {_bn(skipped_filter + skipped_caption)}\n\n"
        + (f"📋 𝗙𝗼𝗿𝘄𝗮𝗿𝗱𝗲𝗱 𝗯𝗿𝗲𝗮𝗸𝗱𝗼𝘄𝗻:\n{fwd_lines}\n" if fwd_lines else "")
        + (f"⛔ 𝗦𝗸𝗶𝗽𝗽𝗲𝗱 𝗯𝗿𝗲𝗮𝗸𝗱𝗼𝘄𝗻:\n{skip_lines}\n" if skip_lines else "")
        + f"⚡ {speed:,.0f} msg/min  •  ⏱️ {format_duration(elapsed_s)}\n"
        f"{_SEP}"
    )


async def run_dryrun(
    userbot: Any,
    bot_client: Any,
    status_msg_id: int,
    limit: int = 0,
) -> None:
    """
    Scan source channel(s) with current filters and show what would be forwarded vs skipped.
    Updates status_msg_id in ADMIN chat with live progress every 2 seconds.
    """
    import time as _time
    from config import ADMIN_ID, state
    from utils.settings import get_filter, should_skip_by_caption

    async def _edit(text: str) -> None:
        try:
            await bot_client.edit_message_text(
                chat_id=ADMIN_ID, message_id=status_msg_id, text=text
            )
        except Exception:
            pass

    source_ids = _get_all_source_ids()
    flt = get_filter()

    for source_id in source_ids:
        # Resolve source name
        try:
            entity = await userbot.get_entity(source_id)
            source_name = getattr(entity, "title", None) or getattr(entity, "username", None) or str(source_id)
        except Exception:
            source_name = str(source_id)

        # Get total message count for progress bar
        total = 0
        try:
            result = await userbot.get_messages(source_id, limit=0)
            total = result.total if hasattr(result, "total") and result.total else 0
        except Exception:
            total = 0

        scanned = 0
        forwarded = 0
        skipped_filter = 0
        skipped_caption = 0
        fwd_breakdown: dict = {}
        skip_breakdown: dict = {}
        start_ts = _time.monotonic()
        last_edit = 0.0

        scan_limit = limit if limit > 0 else None

        async for msg in userbot.iter_messages(source_id, limit=scan_limit):
            scanned += 1
            elapsed_s = _time.monotonic() - start_ts
            mtype = _dryrun_media_type(msg)

            # Check caption skip rules first
            caption = getattr(msg, "text", None) or getattr(msg, "message", None)
            if should_skip_by_caption(caption):
                skipped_caption += 1
                skip_breakdown[mtype] = skip_breakdown.get(mtype, 0) + 1
            elif not flt.get(mtype, True):
                skipped_filter += 1
                skip_breakdown[mtype] = skip_breakdown.get(mtype, 0) + 1
            else:
                forwarded += 1
                fwd_breakdown[mtype] = fwd_breakdown.get(mtype, 0) + 1

            # Update UI every 2 seconds
            if elapsed_s - last_edit >= 2.0:
                last_edit = elapsed_s
                await _edit(_build_dryrun_ui(
                    source_name, scanned, total, forwarded,
                    skipped_filter, skipped_caption,
                    fwd_breakdown, skip_breakdown, elapsed_s,
                ))

        # Final result for this source
        elapsed_s = _time.monotonic() - start_ts
        await _edit(_build_dryrun_ui(
            source_name, scanned, total, forwarded,
            skipped_filter, skipped_caption,
            fwd_breakdown, skip_breakdown, elapsed_s,
            done=True,
        ))


# ─────────────────────────────────────────────
# Pre-flight checks
# ─────────────────────────────────────────────

async def run_preflight_checks(
    userbot: Any,
    bot_client: Client,
    checking_msg: Any,
) -> bool:
    """
    Run all pre-flight validation checks before cloning starts.

    Args:
        userbot: Connected Telethon TelegramClient
        bot_client: Pyrogram bot Client
        checking_msg: Pyrogram Message to edit with results

    Returns:
        True if all critical checks pass, False otherwise
    """
    results = []

    async def _edit(text: str) -> None:
        try:
            await checking_msg.edit(text)
        except Exception:
            pass

    # Check 1 — Source channel access
    await _edit("🔍 **Pre-flight Checks**\n\n⏳ Check 1/6: Source channel access...")
    try:
        source_entity = await userbot.get_entity(SOURCE_ID)
        source_name = getattr(source_entity, "title", str(SOURCE_ID))
        state.source_name = source_name
        async for _ in userbot.iter_messages(SOURCE_ID, limit=1):
            break
        results.append(f"✅ Check 1: Source access OK — `{source_name}`")
    except Exception as e:
        results.append(f"❌ Check 1: Source access FAILED — {e}")
        await _edit("🔍 **Pre-flight Checks**\n\n" + "\n".join(results) + "\n\n❌ Cannot proceed.")
        return False

    # Check 2 — Destination channel access
    await _edit("🔍 **Pre-flight Checks**\n\n" + "\n".join(results) + "\n⏳ Check 2/6: Dest channel access...")
    try:
        dest_entity = await userbot.get_entity(DEST_ID)
        dest_name = getattr(dest_entity, "title", str(DEST_ID))
        state.dest_name = dest_name
        results.append(f"✅ Check 2: Dest access OK — `{dest_name}`")
    except Exception as e:
        results.append(f"❌ Check 2: Dest access FAILED — {e}")
        await _edit("🔍 **Pre-flight Checks**\n\n" + "\n".join(results) + "\n\n❌ Cannot proceed.")
        return False

    # Check 3 — Write permission
    await _edit("🔍 **Pre-flight Checks**\n\n" + "\n".join(results) + "\n⏳ Check 3/6: Write permission...")
    try:
        test_msg = await userbot.send_message(DEST_ID, "🔧 TeleClone write test — deleting...")
        await asyncio.sleep(1)
        await userbot.delete_messages(DEST_ID, test_msg.id)
        results.append("✅ Check 3: Write permission OK")
    except Exception as e:
        results.append(f"❌ Check 3: Write permission FAILED — {e}\n  (Userbot needs admin rights in dest)")
        await _edit("🔍 **Pre-flight Checks**\n\n" + "\n".join(results) + "\n\n❌ Cannot proceed.")
        return False

    # Check 4 — Count total messages
    await _edit("🔍 **Pre-flight Checks**\n\n" + "\n".join(results) + "\n⏳ Check 4/6: Counting messages...")
    try:
        msgs = await userbot.get_messages(SOURCE_ID, limit=0)
        total = msgs.total if hasattr(msgs, "total") and msgs.total > 0 else 0
        state.total_messages = total
        if total > 0:
            results.append(f"✅ Check 4: Total messages ≈ {total:,}")
        else:
            results.append("⚠️ Check 4: Count unavailable — will update during cloning")
    except Exception as e:
        results.append(f"⚠️ Check 4: Count failed (non-critical) — {e}")
        state.total_messages = 0

    # Check 5 — Previous progress
    await _edit("🔍 **Pre-flight Checks**\n\n" + "\n".join(results) + "\n⏳ Check 5/6: Previous progress...")
    from utils.progress import load_progress, progress_exists
    if progress_exists():
        prev = load_progress()
        if prev:
            prev_processed = prev.get("processed", 0)
            prev_id = prev.get("last_processed_id", 0)
            results.append(f"✅ Check 5: Resume found — {prev_processed:,} done, last ID {prev_id:,}")
        else:
            results.append("✅ Check 5: Fresh start (no previous progress)")
    else:
        results.append("✅ Check 5: Fresh start (no previous progress)")

    # Check 6 — Caption modifier status
    await _edit("🔍 **Pre-flight Checks**\n\n" + "\n".join(results) + "\n⏳ Check 6/6: Caption config...")
    summary = get_caption_summary()
    cap_mod = caption_modifications_enabled()
    mode = "🔄 Reupload mode (captions modified)" if cap_mod else "⏩ Forward mode (captions preserved)"
    results.append(f"✅ Check 6: {mode}\n  {summary}")

    final_text = "🔍 **Pre-flight Checks — COMPLETE**\n\n" + "\n".join(results)
    await _edit(final_text)
    return True


# ─────────────────────────────────────────────
# Main cloning loop
# ─────────────────────────────────────────────

async def start_cloning(userbot: Any, bot_client: Client) -> None:
    """
    Main cloning orchestration function. Iterates all source channels
    and forwards/reuploads them to all destination channels.
    Supports multi-source, multi-dest, media filter, caption rules, speed profiles.
    """
    from utils.settings import get_speed_profile
    from utils.webhook import send_webhook

    state.cloning_active = True
    state.cancel_flag = False
    state.pause_flag = False
    state.start_time = datetime.now()

    cap_mod = caption_modifications_enabled()
    speed = get_speed_profile()

    # ─── Compute all source / dest IDs ────────────────────────
    if state.selected_source_ids:
        all_source_ids = list(state.selected_source_ids)
    else:
        all_source_ids = _get_all_source_ids()
    all_dest_ids   = _get_all_dest_ids()

    logger.info(f"Sources: {all_source_ids} | Dests: {all_dest_ids}")

    # ─── Load previous progress ────────────────────────────────
    min_id = 0
    resume_source_idx = 0
    from utils.progress import load_progress, progress_exists
    if progress_exists():
        prev = load_progress()
        if prev:
            min_id             = prev.get("last_processed_id", 0)
            resume_source_idx  = prev.get("source_idx", 0)
            state.processed    = prev.get("processed", 0)
            state.failed       = prev.get("failed", 0)
            state.albums_count = prev.get("albums", 0)
            state.batch_number = prev.get("batch_number", 0)
            state.index_msg_id = prev.get("index_msg_id")
            state.batches_list = prev.get("batches_list", [])
            logger.info(f"Resuming source_idx={resume_source_idx} from ID {min_id}, processed={state.processed}")

    # ─── Fetch per-source names + counts (always refresh) ──────
    # source_counts is always recomputed so the status UI shows
    # the correct breakdown for the currently selected sources.
    computed_total = 0
    state.source_counts = {}
    for sid in all_source_ids:
        name = str(sid)
        count = 0
        try:
            ent = await userbot.get_entity(sid)
            name = getattr(ent, "title", str(sid))
        except Exception:
            pass
        try:
            msgs = await userbot.get_messages(sid, limit=0)
            count = msgs.total if hasattr(msgs, "total") and msgs.total > 0 else 0
            computed_total += count
            logger.info(f"Source {sid} ({name}) message count: {count:,}")
        except Exception as e:
            logger.warning(f"Could not fetch total for source {sid}: {e}")
        state.source_counts[sid] = {"name": name, "count": count}
    state.total_messages = computed_total
    logger.info(f"Total messages across all selected sources: {state.total_messages:,}")

    # ─── Pre-fetch ALL dest entities once ─────────────────────
    dest_entities: list = []
    dest_names: list = []
    for did in all_dest_ids:
        try:
            ent = await userbot.get_entity(did)
            dest_entities.append(ent)
            dest_names.append(getattr(ent, 'title', str(did)))
        except Exception as e:
            logger.error(f"Failed to get dest entity {did}: {e}")
            dest_entities.append(None)
            dest_names.append(str(did))

    state.dest_name = ", ".join(dest_names)

    # ─── Create index message once for primary dest ─────────
    if PIN_ENABLED and state.index_msg_id is None:
        try:
            state.index_msg_id = await create_index_message(userbot, all_dest_ids[0])
        except Exception as e:
            logger.error(f"Failed to create index message: {e}")

    await send_webhook("clone_start", {
        "sources": all_source_ids,
        "dests": all_dest_ids,
        "resume_from": min_id,
    })

    async def _forward_to_all_dests(ids: list, src_id: int, src_entity: Any) -> bool:
        """Forward a batch of IDs to ALL destination channels."""
        success = True
        for i, did in enumerate(all_dest_ids):
            d_ent = dest_entities[i] if i < len(dest_entities) else None
            try:
                await forward_batch_raw(ids, userbot, did, src_id, src_entity, d_ent)
            except Exception as e:
                err_name = type(e).__name__
                if "FloodWait" in err_name:
                    wait_s = getattr(e, "seconds", 30) + 10
                    logger.warning(f"FloodWait {wait_s}s on dest {did} — waiting...")
                    state.floodwait_until = asyncio.get_event_loop().time() + wait_s
                    try:
                        from utils.webhook import send_webhook as _wh
                        await _wh("floodwait", {"seconds": wait_s, "dest": did})
                    except Exception:
                        pass
                    await asyncio.sleep(wait_s)
                    state.floodwait_until = None
                    try:
                        await forward_batch_raw(ids, userbot, did, src_id, src_entity, d_ent)
                    except Exception as e2:
                        logger.error(f"Batch retry failed for dest {did}: {e2}")
                        success = False
                else:
                    logger.error(f"Batch forward failed for dest {did}: {e}")
                    success = False
        return success

    async def _flush_buffer(buf: list, live_obj: Any, src_id: int, src_entity: Any) -> None:
        """Flush buffered messages as a single batch to all destinations."""
        if not buf:
            return

        if not cap_mod:
            ids = [m.id for m, _ in buf]
            ok = await _forward_to_all_dests(ids, src_id, src_entity)
            if ok:
                for msg, minfo in buf:
                    state.processed += 1
                    state.current_id = msg.id
                    add_to_recent_activity(msg, True, media_info=minfo)
            else:
                # Fallback: individual forward to all dests
                for msg, minfo in buf:
                    msg_ok = True
                    for i, did in enumerate(all_dest_ids):
                        try:
                            await forward_single(msg, userbot, did, src_id, False)
                        except Exception as e3:
                            err3 = type(e3).__name__
                            if "FloodWait" in err3:
                                ws = getattr(e3, "seconds", 30) + 5
                                await asyncio.sleep(ws)
                            msg_ok = False
                    if msg_ok:
                        state.processed += 1
                    else:
                        state.failed += 1
                        append_failed_id(msg.id)
                    state.current_id = msg.id
                    add_to_recent_activity(msg, msg_ok, media_info=minfo)
        else:
            # Caption mod: one by one to all dests
            for msg, minfo in buf:
                msg_ok = True
                for i, did in enumerate(all_dest_ids):
                    async def _do_fwd(m=msg, d=did, s=src_id):
                        return await forward_single(m, userbot, d, s, True)
                    ok = await forward_with_retry(_do_fwd, msg.id, bot_client, MAX_RETRIES)
                    if not ok:
                        msg_ok = False
                if msg_ok:
                    state.processed += 1
                else:
                    state.failed += 1
                    append_failed_id(msg.id)
                state.current_id = msg.id
                add_to_recent_activity(msg, msg_ok, media_info=minfo)
                await asyncio.sleep(get_smart_delay(minfo))

        last_msg_id = buf[-1][0].id if buf else 0
        try:
            await handle_pin_checkpoint(userbot, all_dest_ids[0], state, state.processed, last_msg_id)
        except Exception as e:
            logger.error(f"Pin checkpoint error: {e}")

        save_progress(state)
        if state.processed % 500 == 0:
            print(f"CHECKPOINT|{state.processed}|{state.current_id}|{state.failed}")

        live_obj.update(_build_dashboard())
        await asyncio.sleep(speed["batch_sleep"])

    try:
        with Live(
            _build_dashboard(),
            console=console,
            refresh_per_second=0.5,
            screen=False,
        ) as live:

            # ─── Background: auto-update status in Telegram every 5s ──
            async def _auto_status_updater():
                while state.cloning_active and not state.cancel_flag:
                    await asyncio.sleep(5)
                    if state.cloning_active and not state.cancel_flag:
                        await update_status_message(bot_client)

            status_updater_task = asyncio.ensure_future(_auto_status_updater())

            try:
                # ─── Multi-source loop ─────────────────────────────────
                for source_idx, source_id in enumerate(all_source_ids):
                    if state.cancel_flag:
                        break
                    if source_idx < resume_source_idx:
                        logger.info(f"Skipping source {source_id} (already done)")
                        continue

                    # Pre-fetch source entity for this source
                    src_entity = None
                    try:
                        src_entity = await userbot.get_entity(source_id)
                        state.source_name = getattr(src_entity, 'title', str(source_id))
                    except Exception as e:
                        logger.error(f"Failed to get entity for source {source_id}: {e}")
                        state.source_name = str(source_id)

                    state.current_source_id = source_id
                    state.current_source_name = state.source_name

                    # Resume mid-source if applicable, else start fresh per source
                    current_min_id = min_id if source_idx == resume_source_idx else 0
                    pages_fetched = 0
                    album_buffer: dict = defaultdict(list)
                    msg_buffer: list = []

                    logger.info(f"Cloning source[{source_idx}] {source_id} — start min_id={current_min_id}")

                    while not state.cancel_flag:
                        page = await userbot.get_messages(
                            source_id,
                            limit=BATCH_SIZE,
                            min_id=current_min_id,
                            reverse=True,
                        )

                        if not page:
                            logger.info(f"Source {source_id}: no more messages after id={current_min_id}")
                            break

                        pages_fetched += 1
                        logger.info(
                            f"Source {source_id} page {pages_fetched}: "
                            f"{len(page)} msgs (ids {page[0].id}→{page[-1].id})"
                        )

                        for message in page:
                            if state.cancel_flag:
                                break

                            while state.pause_flag:
                                await asyncio.sleep(1)
                                live.update(_build_dashboard())

                            try:
                                # ─── Caption skip rule check ─────────────
                                if _check_caption_skip(message):
                                    state.skipped += 1
                                    state.current_id = message.id
                                    live.update(_build_dashboard())
                                    continue

                                # ─── Album handling ───────────────────────
                                if message.grouped_id:
                                    if msg_buffer:
                                        await _flush_buffer(msg_buffer, live, source_id, src_entity)
                                        msg_buffer.clear()
                                    # Filter albums by first message media type
                                    m_info = get_media_info(message)
                                    mtype = m_info.get("type", "other") if m_info else "other"
                                    if not _passes_filter(mtype):
                                        state.skipped += 1
                                        state.current_id = message.id
                                        continue
                                    album_buffer[message.grouped_id].append(message)
                                    live.update(_build_dashboard())
                                    continue

                                # ─── Flush completed albums ───────────────
                                if album_buffer:
                                    stale_gids = [
                                        gid for gid in list(album_buffer.keys())
                                        if gid != message.grouped_id
                                    ]
                                    for gid in stale_gids:
                                        msgs = album_buffer.pop(gid)
                                        await _process_album_batch(
                                            msgs, userbot, bot_client, cap_mod, live,
                                            source_id, all_dest_ids, dest_entities
                                        )

                                # ─── Media type filter check ──────────────
                                media_info = get_media_info(message)
                                mtype = media_info.get("type", "other") if media_info else "other"
                                if not _passes_filter(mtype):
                                    state.skipped += 1
                                    state.current_id = message.id
                                    live.update(_build_dashboard())
                                    continue

                                # ─── Buffer this message ──────────────────
                                msg_buffer.append((message, media_info))

                                if len(msg_buffer) >= BATCH_SIZE:
                                    await _flush_buffer(msg_buffer, live, source_id, src_entity)
                                    msg_buffer.clear()

                            except Exception as e:
                                logger.error(
                                    f"Unhandled error for msg {getattr(message, 'id', '?')}: {e}\n"
                                    + traceback.format_exc()
                                )
                                state.failed += 1
                                msg_buffer.clear()
                                continue

                        # Advance the min_id window
                        current_min_id = page[-1].id
                        # Save source_idx so resume knows which source
                        state.current_id = current_min_id
                        save_progress(state, extra={"source_idx": source_idx})

                        # Flush after each page
                        if msg_buffer and not state.cancel_flag:
                            await _flush_buffer(msg_buffer, live, source_id, src_entity)
                            msg_buffer.clear()

                        await asyncio.sleep(speed["page_sleep"])

                    # ─── Flush remaining for this source ────────────
                    if msg_buffer:
                        await _flush_buffer(msg_buffer, live, source_id, src_entity)
                        msg_buffer.clear()
                    for gid, msgs in list(album_buffer.items()):
                        if msgs:
                            await _process_album_batch(
                                msgs, userbot, bot_client, cap_mod, live,
                                source_id, all_dest_ids, dest_entities
                            )

                    state.sources_done.append(source_id)
                    logger.info(f"Source {source_id} complete. Moving to next source.")
                    # After each source finishes, reset min_id for next source
                    min_id = 0

            finally:
                status_updater_task.cancel()
                try:
                    await status_updater_task
                except asyncio.CancelledError:
                    pass

    except Exception as e:
        logger.error(f"Fatal cloning error: {e}\n{traceback.format_exc()}")
        try:
            from utils.webhook import send_webhook as _wh
            await _wh("clone_error", {"error": str(e)})
        except Exception:
            pass
    finally:
        state.cloning_active = False
        save_progress(state)

        if not state.cancel_flag:
            clear_progress()
            try:
                completion = get_completion_text(state, state.source_name, state.dest_name)
                await bot_client.send_message(ADMIN_ID, completion)
            except Exception as e:
                logger.error(f"Failed to send completion message: {e}")
            try:
                from utils.webhook import send_webhook as _wh
                await _wh("clone_complete", {
                    "processed": state.processed,
                    "failed": state.failed,
                    "skipped": state.skipped,
                    "albums": state.albums_count,
                })
            except Exception:
                pass
        else:
            try:
                await bot_client.send_message(
                    ADMIN_ID,
                    f"🛑 **Cloning Cancelled**\n\n"
                    f"✅ Processed: {state.processed:,}\n"
                    f"❌ Failed: {state.failed:,}\n\n"
                    f"Progress saved. /clone se resume hoga.",
                )
            except Exception as e:
                logger.error(f"Failed to send cancel message: {e}")
            try:
                from utils.webhook import send_webhook as _wh
                await _wh("clone_cancelled", {
                    "processed": state.processed,
                    "failed": state.failed,
                })
            except Exception:
                pass

        logger.info(
            f"Cloning finished — processed={state.processed}, "
            f"failed={state.failed}, cancelled={state.cancel_flag}"
        )


async def start_cloning_range(
    userbot: Any,
    bot_client: Client,
    from_id: int,
    to_id: int,
    status_msg_id: int,
    source_id: int = 0,
) -> None:
    """
    Clone a specific message ID range [from_id, to_id] from source channel to all dests.
    Sends live progress updates to ADMIN chat via status_msg_id.
    Respects current media filters and caption rules. No progress file used.
    """
    import time as _time
    from collections import defaultdict as _defaultdict
    from utils.settings import get_speed_profile
    from bot.messages import format_duration

    src_id = source_id if source_id else _get_all_source_ids()[0]
    all_dest_ids = _get_all_dest_ids()
    cap_mod = caption_modifications_enabled()
    speed = get_speed_profile()

    async def _edit(text: str) -> None:
        try:
            await bot_client.edit_message_text(
                chat_id=ADMIN_ID, message_id=status_msg_id, text=text
            )
        except Exception:
            pass

    def _ui(forwarded: int, failed: int, skipped: int, current: int, elapsed_s: float, done: bool = False) -> str:
        total_range = max(1, to_id - from_id + 1)
        scanned = forwarded + failed + skipped
        pct = min(100.0, scanned / total_range * 100)
        filled = int(15 * pct / 100)
        bar = "[" + "█" * filled + "░" * (15 - filled) + "]"
        speed_val = (scanned / elapsed_s * 60) if elapsed_s > 0 else 0.0
        header = "✅ 𝗖𝗹𝗼𝗻𝗲 𝗥𝗮𝗻𝗴𝗲 𝗗𝗼𝗻𝗲!" if done else "🎯 𝗖𝗹𝗼𝗻𝗲 𝗥𝗮𝗻𝗴𝗲 𝗶𝗻 𝗣𝗿𝗼𝗴𝗿𝗲𝘀𝘀..."
        return (
            f"{header}\n"
            f"{_SEP}\n\n"
            f"📍 𝗥𝗮𝗻𝗴𝗲: `{from_id:,}` → `{to_id:,}`\n\n"
            f"📈 {bar} {pct:.1f}%\n"
            f"📝 {_bn(scanned)} 𝗼𝗳 ~{_bn(total_range)} 𝗺𝘀𝗴𝘀\n\n"
            f"✓ 𝗙𝗼𝗿𝘄𝗮𝗿𝗱𝗲𝗱: {_bn(forwarded)}  |  ✗ 𝗙𝗮𝗶𝗹𝗲𝗱: {_bn(failed)}\n"
            f"⏭️ 𝗦𝗸𝗶𝗽𝗽𝗲𝗱: {_bn(skipped)}  |  📍 𝗜𝗗: {current:,}\n\n"
            f"⚡ {speed_val:,.0f} msg/min  •  ⏱️ {format_duration(elapsed_s)}\n"
            f"{_SEP}"
        )

    # ─── Resolve source entity and dest entities ───────────────
    src_entity = None
    src_name = str(src_id)
    try:
        src_entity = await userbot.get_entity(src_id)
        src_name = getattr(src_entity, "title", src_name)
    except Exception as e:
        logger.error(f"clonerange: failed to get source entity: {e}")

    dest_entities_r: list = []
    for did in all_dest_ids:
        try:
            dest_entities_r.append(await userbot.get_entity(did))
        except Exception:
            dest_entities_r.append(None)

    # ─── Notify start ──────────────────────────────────────────
    await _edit(
        f"🎯 𝗖𝗹𝗼𝗻𝗲 𝗥𝗮𝗻𝗴𝗲 𝗦𝘁𝗮𝗿𝘁𝗲𝗱\n"
        f"{_SEP}\n\n"
        f"📤 `{src_name}`\n"
        f"📍 𝗜𝗗𝘀: `{from_id:,}` → `{to_id:,}`\n\n"
        f"⏳ Scanning range..."
    )

    state.cloning_active = True
    state.cancel_flag = False
    forwarded = failed = skipped = 0
    current_id = from_id
    start_ts = _time.monotonic()
    last_edit = 0.0
    msg_buffer: list = []
    album_buffer: dict = _defaultdict(list)

    async def _forward_range_dests(ids: list) -> bool:
        ok = True
        for i, did in enumerate(all_dest_ids):
            d_ent = dest_entities_r[i] if i < len(dest_entities_r) else None
            try:
                await forward_batch_raw(ids, userbot, did, src_id, src_entity, d_ent)
            except Exception as e:
                err_name = type(e).__name__
                if "FloodWait" in err_name:
                    wait_s = getattr(e, "seconds", 30) + 10
                    logger.warning(f"clonerange FloodWait {wait_s}s — waiting")
                    await asyncio.sleep(wait_s)
                    try:
                        await forward_batch_raw(ids, userbot, did, src_id, src_entity, d_ent)
                    except Exception as e2:
                        logger.error(f"clonerange retry failed for dest {did}: {e2}")
                        ok = False
                else:
                    logger.error(f"clonerange batch forward failed for dest {did}: {e}")
                    ok = False
        return ok

    async def _flush_range_buf(buf: list) -> None:
        nonlocal forwarded, failed
        if not buf:
            return
        if not cap_mod:
            ids = [m.id for m, _ in buf]
            ok = await _forward_range_dests(ids)
            if ok:
                forwarded += len(buf)
            else:
                for msg, _ in buf:
                    msg_ok = True
                    for i, did in enumerate(all_dest_ids):
                        try:
                            await forward_single(msg, userbot, did, src_id, False)
                        except Exception as e:
                            if "FloodWait" in type(e).__name__:
                                await asyncio.sleep(getattr(e, "seconds", 10) + 2)
                            msg_ok = False
                    if msg_ok:
                        forwarded += 1
                    else:
                        failed += 1
        else:
            for msg, minfo in buf:
                msg_ok = True
                for i, did in enumerate(all_dest_ids):
                    async def _do(m=msg, d=did):
                        return await forward_single(m, userbot, d, src_id, True)
                    ok = await forward_with_retry(_do, msg.id, bot_client, MAX_RETRIES)
                    if not ok:
                        msg_ok = False
                if msg_ok:
                    forwarded += 1
                else:
                    failed += 1
                await asyncio.sleep(get_smart_delay(minfo))
        await asyncio.sleep(speed["batch_sleep"])

    try:
        current_min = from_id - 1
        max_id_bound = to_id + 1

        while not state.cancel_flag:
            page = await userbot.get_messages(
                src_id,
                limit=BATCH_SIZE,
                min_id=current_min,
                max_id=max_id_bound,
                reverse=True,
            )

            if not page:
                break

            for message in page:
                if state.cancel_flag:
                    break

                current_id = message.id
                elapsed_s = _time.monotonic() - start_ts

                # Caption skip check
                if _check_caption_skip(message):
                    skipped += 1
                    continue

                # Album grouping
                if message.grouped_id:
                    if msg_buffer:
                        await _flush_range_buf(msg_buffer)
                        msg_buffer.clear()
                    m_info = get_media_info(message)
                    mtype = m_info.get("type", "other") if m_info else "other"
                    if not _passes_filter(mtype):
                        skipped += 1
                        continue
                    album_buffer[message.grouped_id].append(message)
                    continue

                # Flush stale albums
                if album_buffer:
                    stale = [gid for gid in list(album_buffer.keys()) if gid != message.grouped_id]
                    for gid in stale:
                        msgs = album_buffer.pop(gid)
                        for i, did in enumerate(all_dest_ids):
                            ok = await process_album(msgs, userbot, did, cap_mod, src_id=src_id)
                        if ok:
                            forwarded += len(msgs)
                            state.albums_count += 1
                        else:
                            failed += 1

                # Media filter
                media_info = get_media_info(message)
                mtype = media_info.get("type", "other") if media_info else "other"
                if not _passes_filter(mtype):
                    skipped += 1
                    continue

                msg_buffer.append((message, media_info))

                if len(msg_buffer) >= BATCH_SIZE:
                    await _flush_range_buf(msg_buffer)
                    msg_buffer.clear()

                # Live update every 3 seconds
                if elapsed_s - last_edit >= 3.0:
                    last_edit = elapsed_s
                    await _edit(_ui(forwarded, failed, skipped, current_id, elapsed_s))

            current_min = page[-1].id
            if msg_buffer and not state.cancel_flag:
                await _flush_range_buf(msg_buffer)
                msg_buffer.clear()
            await asyncio.sleep(speed["page_sleep"])

        # Flush remaining albums
        for gid, msgs in list(album_buffer.items()):
            if msgs:
                for i, did in enumerate(all_dest_ids):
                    ok = await process_album(msgs, userbot, did, cap_mod, src_id=src_id)
                if ok:
                    forwarded += len(msgs)
                    state.albums_count += 1
                else:
                    failed += 1

        if msg_buffer:
            await _flush_range_buf(msg_buffer)

    except Exception as e:
        logger.error(f"clonerange error: {e}\n{traceback.format_exc()}")
    finally:
        state.cloning_active = False
        elapsed_s = _time.monotonic() - start_ts
        await _edit(_ui(forwarded, failed, skipped, current_id, elapsed_s, done=True))
        logger.info(f"clonerange done — forwarded={forwarded}, failed={failed}, skipped={skipped}")


async def _process_album_batch(
    msgs: list,
    userbot: Any,
    bot_client: Client,
    cap_mod: bool,
    live: Live,
    src_id: int = 0,
    dest_ids: Optional[list] = None,
    dest_entities_list: Optional[list] = None,
) -> None:
    """
    Process a collected album group, forwarding to all dest channels.
    """
    from utils.settings import get_speed_profile
    speed = get_speed_profile()

    if dest_ids is None:
        dest_ids = [DEST_ID]

    try:
        overall_success = True
        for i, did in enumerate(dest_ids):
            success = await process_album(msgs, userbot, did, cap_mod, src_id=src_id)
            if not success:
                overall_success = False

        if overall_success:
            state.albums_count += 1
            state.processed += len(msgs)
        else:
            state.failed += 1
            for m in msgs:
                append_failed_id(m.id)

        if msgs:
            add_to_recent_activity(
                msgs[0], overall_success, is_album=True, count=len(msgs)
            )

        await asyncio.sleep(speed["album_sleep"])

        try:
            await handle_pin_checkpoint(
                userbot, dest_ids[0], state, state.processed,
                msgs[0].id if msgs else 0
            )
        except Exception as e:
            logger.error(f"Pin checkpoint error after album: {e}")

        live.update(_build_dashboard())

    except Exception as e:
        logger.error(f"Album batch error: {e}\n{traceback.format_exc()}")
        state.failed += 1