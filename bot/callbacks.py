import asyncio
import time
from pyrogram import Client
from pyrogram.types import CallbackQuery

from config import ADMIN_ID, state
from utils.logger import get_logger
from utils.progress import save_progress
from bot.messages import format_duration, bold_num

logger = get_logger(__name__)

_SEP = "⊶" * 22
_BAR_W = 15


def _del_bar(pct: float) -> str:
    filled = int(_BAR_W * pct / 100)
    return "[" + "█" * filled + "░" * (_BAR_W - filled) + "]"


def _build_del_ui(
    chat_id: int,
    deleted: int,
    failed: int,
    target: int,
    start_ts: float,
    cancelled: bool = False,
    done: bool = False,
) -> str:
    elapsed_s = time.monotonic() - start_ts
    elapsed_str = format_duration(elapsed_s)
    speed = (deleted / elapsed_s * 60) if elapsed_s > 0 else 0.0

    if done:
        header = "✅ 𝗗𝗲𝗹𝗲𝘁𝗶𝗼𝗻 𝗖𝗼𝗺𝗽𝗹𝗲𝘁𝗲𝗱 ✅"
    elif cancelled:
        header = "🛑 𝗗𝗲𝗹𝗲𝘁𝗶𝗼𝗻 𝗦𝘁𝗼𝗽𝗽𝗲𝗱 🛑"
    else:
        header = "♻️ 𝗗𝗲𝗹𝗲𝘁𝗶𝗼𝗻 𝗶𝗻 𝗣𝗿𝗼𝗴𝗿𝗲𝘀𝘀 ♻️"

    if target > 0:
        pct = min(100.0, deleted / target * 100)
        bar = _del_bar(pct)
        remaining = max(0, target - deleted)
        eta_s = (remaining / (speed / 60)) if speed > 0 else 0
        progress_block = (
            f"📈 {bar} {pct:.1f}%\n"
            f"📝 {bold_num(deleted)} 𝗼𝗳 {bold_num(target)} 𝗺𝘀𝗴𝘀\n"
        )
        remaining_line = f"⏳ 𝗥𝗲𝗺𝗮𝗶𝗻𝗶𝗻𝗴: {bold_num(remaining)}\n" if not done else ""
        eta_line = f"🏁 𝗘𝗧𝗔: ~{format_duration(eta_s)}\n" if not done and speed > 0 else ""
    else:
        progress_block = f"📝 {bold_num(deleted)} 𝗺𝘀𝗴𝘀 𝗱𝗲𝗹𝗲𝘁𝗲𝗱\n"
        remaining_line = ""
        eta_line = ""

    return (
        f"{header}\n"
        f"{_SEP}\n\n"
        f"📍 𝗜𝗗: `{chat_id}`\n"
        f"{progress_block}\n"
        f"✓ 𝗦𝘂𝗰𝗰𝗲𝘀𝘀: {bold_num(deleted)}  |  ✗ 𝗙𝗮𝗶𝗹𝗲𝗱: {bold_num(failed)}\n"
        f"{remaining_line}"
        f"\n"
        f"⚡ {speed:,.0f} msg/min  •  ⏱️ {elapsed_str}\n"
        f"{eta_line}"
        f"{_SEP}"
    )


async def _delete_all_messages(client: Client, query: CallbackQuery, chat_id: int, limit: int = 0) -> None:
    """
    Delete messages in chat_id at maximum speed.
    Progress is always shown in ADMIN's PRIVATE chat — safe even when deleting the same group.
    """
    if state.userbot is None:
        try:
            await query.message.edit("❌ Userbot connected nahi hai. /login karo pehle.")
        except Exception:
            pass
        return

    # ─── Dismiss original button message (may be in the group) ───
    try:
        await query.answer("🗑️ Starting! Progress private chat mein dekho.")
    except Exception:
        pass
    try:
        await query.message.edit(
            "🗑️ **Deletion shuru ho gayi!**\n\n"
            "📩 Progress updates **apni private chat** mein dekho (bot ke saath).\n"
            "Wahan se /cancel bhi kar sakte ho."
        )
    except Exception:
        pass

    # ─── Send LIVE progress to admin's PRIVATE chat ───────────
    try:
        progress_msg = await client.send_message(ADMIN_ID, "⏳ Deletion shuru ho rahi hai...")
        prog_id = progress_msg.id
    except Exception as e:
        logger.error(f"Could not send progress message to admin: {e}")
        return

    async def _edit(text: str) -> None:
        try:
            await client.edit_message_text(chat_id=ADMIN_ID, message_id=prog_id, text=text)
        except Exception:
            pass

    state.delall_active = True
    deleted = 0
    failed = 0
    completed = False
    start_ts = time.monotonic()

    # ─── Get total count for progress bar ─────────────────────
    target = limit
    if limit == 0:
        try:
            msgs_meta = await state.userbot.get_messages(chat_id, limit=0)
            target = msgs_meta.total if hasattr(msgs_meta, "total") and msgs_meta.total > 0 else 0
        except Exception:
            target = 0

    # ─── 1-second live UI updater (reads deleted/failed from closure) ─
    async def _ui_loop() -> None:
        while state.delall_active:
            await _edit(_build_del_ui(chat_id, deleted, failed, target, start_ts))
            await asyncio.sleep(1)

    # Show initial UI and start updater
    await _edit(_build_del_ui(chat_id, deleted, failed, target, start_ts))
    ui_task = asyncio.create_task(_ui_loop())

    try:
        iter_limit = limit if limit > 0 else None
        batch: list = []

        async for msg in state.userbot.iter_messages(chat_id, limit=iter_limit):
            if not state.delall_active:
                break

            msg_id = getattr(msg, "id", None)
            if msg_id is None:
                continue

            batch.append(msg_id)

            if len(batch) >= 100:
                try:
                    await state.userbot.delete_messages(chat_id, batch)
                    deleted += len(batch)
                except Exception as e:
                    err_name = type(e).__name__
                    if "FloodWait" in err_name:
                        wait_s = getattr(e, "seconds", 10) + 2
                        logger.warning(f"FloodWait {wait_s}s during delall — waiting")
                        await asyncio.sleep(wait_s)
                        try:
                            await state.userbot.delete_messages(chat_id, batch)
                            deleted += len(batch)
                        except Exception as e2:
                            logger.error(f"Retry delete failed: {e2}")
                            failed += len(batch)
                    else:
                        logger.error(f"Batch delete error: {e}")
                        failed += len(batch)
                batch = []

        # Flush remaining
        if batch and state.delall_active:
            try:
                await state.userbot.delete_messages(chat_id, batch)
                deleted += len(batch)
            except Exception as e:
                logger.error(f"Final batch delete error: {e}")
                failed += len(batch)

        completed = True

    except Exception as e:
        logger.error(f"delall task error: {e}")
    finally:
        state.delall_active = False
        ui_task.cancel()
        try:
            await ui_task
        except asyncio.CancelledError:
            pass

        # Final summary in private chat
        await _edit(
            _build_del_ui(
                chat_id, deleted, failed, target, start_ts,
                done=completed,
                cancelled=not completed,
            )
        )

        state.delall_active = False
        state.delall_chat_id = None
        state.delall_count = 0


def register_callbacks(bot: Client) -> None:
    """Register all inline button callback query handlers on the bot client."""

    @bot.on_callback_query()
    async def handle_callbacks(client: Client, query: CallbackQuery) -> None:
        """Dispatch all inline button callbacks."""
        if query.from_user.id != ADMIN_ID:
            await query.answer("❌ Unauthorized", show_alert=True)
            return

        data = query.data

        if data == "clone_start":
            await _handle_clone_start(client, query)
        elif data == "clone_resume":
            await _handle_clone_resume(client, query)
        elif data == "clone_fresh":
            await _handle_clone_fresh(client, query)
        elif data == "clone_cancel":
            await _handle_clone_cancel(client, query)
        elif data.startswith("source_pick:"):
            picked = data.split(":", 1)[1]
            await _handle_source_pick(client, query, picked)
        elif data == "cancel_confirm":
            await _handle_cancel_confirm(client, query)
        elif data == "cancel_abort":
            await _handle_cancel_abort(client, query)
        elif data.startswith("delall_count:"):
            count = int(data.split(":", 1)[1])
            await _handle_delall_count(client, query, count)
        elif data == "delall_confirm":
            await _handle_delall_confirm(client, query)
        elif data == "delall_cancel":
            await _handle_delall_cancel(client, query)
        elif data.startswith("filter_toggle:"):
            mtype = data.split(":", 1)[1]
            await _handle_filter_toggle(client, query, mtype)
        elif data.startswith("filter_all:"):
            enabled = data.split(":", 1)[1] == "on"
            await _handle_filter_all(client, query, enabled)
        elif data.startswith("filter_scan:"):
            scan_limit = int(data.split(":", 1)[1])
            await _handle_filter_scan(client, query, scan_limit)
        elif data == "auto_resume":
            await _handle_auto_resume(client, query)
        else:
            await query.answer("❓ Unknown action", show_alert=True)


async def _handle_clone_start(client: Client, query: CallbackQuery) -> None:
    """Start cloning in background task."""
    await query.answer("🚀 Starting!")

    try:
        await query.message.edit(
            "🚀 **Cloning shuru ho rahi hai...**\n\n"
            "Progress dekhne ke liye /status use karo.\n"
            "Rokne ke liye /pause ya /cancel use karo."
        )
    except Exception:
        pass

    from core.cloner import start_cloning

    if state.userbot is None:
        try:
            await query.message.edit("❌ Userbot connected nahi hai. /login karo.")
        except Exception:
            pass
        return

    asyncio.create_task(start_cloning(state.userbot, client))
    logger.info("Cloning task created via callback")


async def _handle_clone_cancel(client: Client, query: CallbackQuery) -> None:
    """Cancel clone start from preflight confirmation."""
    await query.answer("❌ Cancelled")
    try:
        await query.message.edit(
            "❌ **Clone Cancelled**\n\nKoi cloning shuru nahi hui.\n/clone se dobara try karo."
        )
    except Exception:
        pass


async def _handle_cancel_confirm(client: Client, query: CallbackQuery) -> None:
    """Confirm and execute cloning cancellation."""
    await query.answer("🛑 Cancelling...")

    state.cancel_flag = True
    save_progress(state)

    processed = state.processed
    failed = state.failed

    try:
        await query.message.edit(
            f"🛑 **Cloning Cancel Ki Ja Rahi Hai...**\n\n"
            f"✅ Processed: {processed:,}\n"
            f"❌ Failed: {failed:,}\n\n"
            f"Progress save ho gaya. /clone se resume ho sakta hai."
        )
    except Exception:
        pass

    logger.info(f"Cloning cancelled by admin — processed={processed}, failed={failed}")


async def _handle_cancel_abort(client: Client, query: CallbackQuery) -> None:
    """Abort the cancel request — continue cloning."""
    await query.answer("✅ Continuing!")
    try:
        await query.message.edit(
            "▶️ **Cloning Continue Hai**\n\nKoi change nahi hua."
        )
    except Exception:
        pass


async def _handle_delall_confirm(client: Client, query: CallbackQuery) -> None:
    """Confirm deletion — uses count stored in state.delall_count (0 = all)."""
    chat_id = state.delall_chat_id
    if not chat_id:
        await query.answer("❌ No chat ID found.", show_alert=True)
        return
    count = getattr(state, "delall_count", 0)
    await query.answer("🗑️ Deleting...")
    asyncio.create_task(_delete_all_messages(client, query, chat_id, limit=count))


async def _handle_delall_cancel(client: Client, query: CallbackQuery) -> None:
    """Cancel deletion."""
    state.delall_active = False
    state.delall_chat_id = None
    await query.answer("❌ Cancelled")
    try:
        await query.message.edit("❌ **Deletion Cancelled**\n\nKoi messages delete nahi hue.")
    except Exception:
        pass


async def _handle_filter_toggle(client: Client, query: CallbackQuery, mtype: str) -> None:
    """Toggle a single media type filter."""
    from utils.settings import get_filter, set_filter_type, build_filter_keyboard
    current = get_filter().get(mtype, True)
    set_filter_type(mtype, not current)
    status = "✅ Enabled" if not current else "⛔ Disabled"
    await query.answer(f"{status}: {mtype}")
    try:
        await query.message.edit_reply_markup(reply_markup=build_filter_keyboard())
    except Exception:
        pass


async def _handle_filter_all(client: Client, query: CallbackQuery, enabled: bool) -> None:
    """Enable or disable all filter types."""
    from utils.settings import set_filter_all, build_filter_keyboard
    set_filter_all(enabled)
    status = "✅ All Enabled" if enabled else "⛔ All Disabled"
    await query.answer(status)
    try:
        await query.message.edit_reply_markup(reply_markup=build_filter_keyboard())
    except Exception:
        pass


async def _handle_auto_resume(client: Client, query: CallbackQuery) -> None:
    """Auto-resume cloning from saved progress."""
    from core.cloner import start_cloning
    await query.answer("▶️ Resuming!")
    try:
        await query.message.edit(
            "▶️ **Auto-Resuming...**\n\nProgress se resume ho rahi hai.\n"
            "/status se dekhte raho."
        )
    except Exception:
        pass

    if state.userbot is None:
        try:
            await query.message.edit("❌ Userbot connected nahi hai. /login karo pehle.")
        except Exception:
            pass
        return

    asyncio.create_task(start_cloning(state.userbot, client))


async def _handle_clone_resume(client: Client, query: CallbackQuery) -> None:
    """Run preflight checks and then show Start button (resume from saved progress)."""
    await query.answer("🔍 Checking...")
    if state.userbot is None:
        try:
            await query.message.edit("❌ Userbot connected nahi hai. /login karo pehle.")
        except Exception:
            pass
        return

    try:
        await query.message.edit("🔍 **Running pre-flight checks...**\n\n⏳ Check 1/6...")
    except Exception:
        pass

    from core.cloner import run_preflight_checks
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    passed = await run_preflight_checks(state.userbot, client, query.message)
    if not passed:
        return

    try:
        await query.message.edit(
            "✅ **Pre-flight checks passed!**\n\n▶️ Resume karein picha chhode se?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Resume Cloning", callback_data="clone_start"),
                    InlineKeyboardButton("❌ Cancel", callback_data="clone_cancel"),
                ]
            ]),
        )
    except Exception:
        pass


async def _handle_clone_fresh(client: Client, query: CallbackQuery) -> None:
    """Clear saved progress then run preflight and show Start button."""
    await query.answer("🔄 Fresh start...")
    if state.userbot is None:
        try:
            await query.message.edit("❌ Userbot connected nahi hai. /login karo pehle.")
        except Exception:
            pass
        return

    from utils.progress import clear_progress
    clear_progress()

    try:
        await query.message.edit("🔍 **Fresh Start — Running pre-flight checks...**\n\n⏳ Check 1/6...")
    except Exception:
        pass

    from core.cloner import run_preflight_checks
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    passed = await run_preflight_checks(state.userbot, client, query.message)
    if not passed:
        return

    try:
        await query.message.edit(
            "✅ **Pre-flight checks passed!**\n\n🔄 Shuruaat se cloning start karein?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Start Fresh", callback_data="clone_start"),
                    InlineKeyboardButton("❌ Cancel", callback_data="clone_cancel"),
                ]
            ]),
        )
    except Exception:
        pass


async def _handle_delall_count(client: Client, query: CallbackQuery, count: int) -> None:
    """Show confirmation after user picks how many messages to delete."""
    chat_id = state.delall_chat_id
    if not chat_id:
        await query.answer("❌ No chat selected.", show_alert=True)
        return

    state.delall_count = count
    count_label = f"Last {count:,}" if count > 0 else "ALL"

    await query.answer(f"Selected: {count_label}")
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    try:
        await query.message.edit(
            f"⚠️ **Confirm Deletion**\n\n"
            f"🆔 Chat ID: `{chat_id}`\n"
            f"🗑️ Delete: **{count_label} messages**\n\n"
            f"🚨 Yeh action **REVERSIBLE NAHI** hai!\n\n"
            f"Pakka karna hai?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(f"✅ Haan, {count_label} Delete Karo", callback_data="delall_confirm"),
                    InlineKeyboardButton("❌ Cancel", callback_data="delall_cancel"),
                ]
            ]),
        )
    except Exception:
        pass


async def _handle_source_pick(client: Client, query: CallbackQuery, picked: str) -> None:
    """Handle source channel selection before cloning starts."""
    from utils.progress import progress_exists, load_progress
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    if picked == "all":
        state.selected_source_ids = []
        label = "🔀 All Sources"
    else:
        try:
            sid = int(picked)
        except ValueError:
            await query.answer("❌ Invalid source ID", show_alert=True)
            return
        state.selected_source_ids = [sid]
        try:
            ent = await state.userbot.get_entity(sid)
            label = f"📥 {getattr(ent, 'title', str(sid))}"
        except Exception:
            label = f"📥 {sid}"

    await query.answer(f"✅ Selected: {label}")

    if progress_exists():
        prev = load_progress() or {}
        prev_processed = prev.get("processed", 0)
        prev_id = prev.get("last_processed_id", 0)
        prev_saved = prev.get("saved_at", "?")

        try:
            await query.message.edit(
                f"✅ **Source:** {label}\n\n"
                f"💾 **Previous Progress Found!**\n\n"
                f"✅ Processed  : {prev_processed:,}\n"
                f"📍 Last ID   : {prev_id:,}\n"
                f"🕐 Saved at  : {prev_saved}\n\n"
                f"Kahan se start karna hai?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        f"▶️ Resume (ID: {prev_id:,} · Done: {prev_processed:,})",
                        callback_data="clone_resume"
                    )],
                    [InlineKeyboardButton("🔄 Fresh Start (Shuruaat se)", callback_data="clone_fresh")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="clone_cancel")],
                ]),
            )
        except Exception:
            pass
    else:
        try:
            await query.message.edit(
                f"✅ **Source:** {label}\n\n🔍 Running pre-flight checks..."
            )
        except Exception:
            pass

        from core.cloner import run_preflight_checks
        passed = await run_preflight_checks(state.userbot, client, query.message)
        if not passed:
            return

        try:
            await query.message.edit(
                f"✅ **Source:** {label}\n\n"
                f"✅ **Pre-flight checks passed!**\n\nSab kuch ready hai. Cloning start karein?",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Start Cloning", callback_data="clone_start"),
                        InlineKeyboardButton("❌ Cancel", callback_data="clone_cancel"),
                    ]
                ]),
            )
        except Exception:
            pass


async def _handle_filter_scan(client: Client, query: CallbackQuery, scan_limit: int = 2000) -> None:
    """Scan source channel and show media type breakdown with filter toggles."""
    if state.userbot is None:
        await query.answer("❌ Userbot connected nahi hai. /login karo pehle.", show_alert=True)
        return

    label = f"Last {scan_limit:,}" if scan_limit > 0 else "Full Channel"
    await query.answer(f"📊 Scanning {label}...")
    try:
        await query.message.edit(
            f"📊 **Scanning source channel...**\n\n"
            f"Range: **{label}** messages\n"
            f"⏳ Please wait (this may take a moment)..."
        )
    except Exception:
        pass

    from config import SOURCE_ID
    from core.cloner import scan_source_media_types, format_scan_summary
    from utils.settings import build_filter_keyboard

    try:
        limit = scan_limit if scan_limit > 0 else None
        counts = await scan_source_media_types(state.userbot, SOURCE_ID, limit=limit)
        summary = format_scan_summary(counts)

        try:
            await query.message.edit(
                f"{summary}\n\n"
                f"🎛️ **Filter Toggle** — kya forward karna hai choose karo:",
                reply_markup=build_filter_keyboard(show_scan=True),
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"filter_scan error: {e}")
        try:
            await query.message.edit(
                f"❌ Scan failed: `{e}`\n\n"
                "Filter buttons neeche hain:",
                reply_markup=build_filter_keyboard(show_scan=True),
            )
        except Exception:
            pass