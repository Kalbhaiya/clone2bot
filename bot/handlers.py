import asyncio
import re
import time
from functools import wraps
from typing import Callable, Any

from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
)

from config import (
    ADMIN_ID,
    API_ID,
    API_HASH,
    TELETHON_CONNECTION_PARAMS,
    state,
)
from utils.logger import get_logger, get_last_n_errors
from utils.progress import mark_session_active, mark_session_inactive, save_progress, save_session, clear_session
from bot.messages import get_welcome_text, get_help_text, get_status_text

logger = get_logger(__name__)

_otp_timeout_task: Any = None


# ─────────────────────────────────────────────
# Admin check decorator
# ─────────────────────────────────────────────

def admin_only(func: Callable) -> Callable:
    """Decorator — only allow ADMIN_ID to execute the handler (silent ignore for others)."""
    @wraps(func)
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        if message.from_user is None or message.from_user.id != ADMIN_ID:
            return
        return await func(client, message, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────
# OTP timeout task
# ─────────────────────────────────────────────

async def _otp_timeout_task_fn(bot: Client) -> None:
    """Cancel OTP waiting after 120 seconds."""
    await asyncio.sleep(120)
    if state.status == "WAITING_OTP":
        state.status = "IDLE"
        state.phone = None
        state.phone_code_hash = None
        state.otp_sent_at = None
        try:
            await bot.send_message(
                ADMIN_ID,
                "⏰ OTP timeout! 120 seconds beet gaye.\nDobara /login karo.",
            )
        except Exception as e:
            logger.error(f"Failed to send OTP timeout message: {e}")


# ─────────────────────────────────────────────
# Register all handlers
# ─────────────────────────────────────────────

def register_handlers(bot: Client) -> None:
    """Register all command and message handlers on the bot client."""

    @bot.on_message(filters.command("start"))
    @admin_only
    async def cmd_start(client: Client, message: Message) -> None:
        """Handle /start command."""
        await message.reply(get_welcome_text(), disable_web_page_preview=True)

    @bot.on_message(filters.command("help"))
    @admin_only
    async def cmd_help(client: Client, message: Message) -> None:
        """Handle /help command."""
        await message.reply(get_help_text(), disable_web_page_preview=True)

    @bot.on_message(filters.command("login"))
    @admin_only
    async def cmd_login(client: Client, message: Message) -> None:
        """Handle /login command — initiate userbot authentication."""
        if state.status == "LOGGED_IN":
            await message.reply(
                "✅ Already logged in!\n\nUserbot active hai. /clone se cloning shuru karo."
            )
            return

        if state.status in ("WAITING_PHONE", "WAITING_OTP"):
            status_map = {
                "WAITING_PHONE": "📞 Phone number bhejo (e.g. +919876543210)",
                "WAITING_OTP": "🔑 OTP bhejo jo Telegram ne bheja",
            }
            await message.reply(
                f"⚠️ Login already in progress!\n\n{status_map.get(state.status, '')}"
            )
            return

        state.status = "WAITING_PHONE"
        await message.reply(
            "📱 **Login — Step 1/2**\n\n"
            "Apna phone number bhejo with country code:\n"
            "Example: `+919876543210`\n\n"
            "⚠️ Make sure Telegram 2FA (two-step verification) is disabled.",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    @bot.on_message(filters.command("clone"))
    @admin_only
    async def cmd_clone(client: Client, message: Message) -> None:
        """Handle /clone command — show source picker if multiple, then resume/fresh."""
        if state.status != "LOGGED_IN":
            await message.reply("❌ Pehle /login karo!\n\nUserbot connected nahi hai.")
            return

        if state.cloning_active:
            await message.reply("⚙️ Cloning already chal rahi hai!\n/status se progress dekho.")
            return

        from core.cloner import _get_all_source_ids
        all_sources = _get_all_source_ids()

        if len(all_sources) > 1:
            # Resolve channel names for display
            source_labels = []
            for sid in all_sources:
                try:
                    ent = await state.userbot.get_entity(sid)
                    name = getattr(ent, "title", str(sid))
                except Exception:
                    name = str(sid)
                source_labels.append((sid, name))

            rows = []
            for sid, name in source_labels:
                short = name[:28] + "…" if len(name) > 28 else name
                rows.append([InlineKeyboardButton(
                    f"📥 {short}",
                    callback_data=f"source_pick:{sid}"
                )])
            rows.append([InlineKeyboardButton("🔀 All Sources (Sab)", callback_data="source_pick:all")])
            rows.append([InlineKeyboardButton("❌ Cancel", callback_data="clone_cancel")])

            lines = ["📋 **Source Channels:**\n"]
            for sid, name in source_labels:
                lines.append(f"• `{sid}` — {name}")

            await message.reply(
                "\n".join(lines) + "\n\nKis source se clone karna hai?",
                reply_markup=InlineKeyboardMarkup(rows),
                parse_mode=enums.ParseMode.MARKDOWN,
            )
            return

        # Single source — proceed directly
        state.selected_source_ids = []
        await _proceed_to_clone_flow(client, message)

    async def _proceed_to_clone_flow(client: Client, message: Message) -> None:
        """After source selection, show resume/fresh/preflight options."""
        from utils.progress import progress_exists, load_progress

        if progress_exists():
            prev = load_progress() or {}
            prev_processed = prev.get("processed", 0)
            prev_id = prev.get("last_processed_id", 0)
            prev_saved = prev.get("saved_at", "?")

            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"▶️ Resume (ID: {prev_id:,} · Done: {prev_processed:,})",
                    callback_data="clone_resume"
                )],
                [InlineKeyboardButton("🔄 Fresh Start (Shuruaat se)", callback_data="clone_fresh")],
                [InlineKeyboardButton("❌ Cancel", callback_data="clone_cancel")],
            ])

            await message.reply(
                f"💾 **Previous Progress Found!**\n\n"
                f"✅ Processed  : {prev_processed:,}\n"
                f"📍 Last ID   : {prev_id:,}\n"
                f"🕐 Saved at  : {prev_saved}\n\n"
                f"Kahan se start karna hai?",
                reply_markup=buttons,
            )
        else:
            checking_msg = await message.reply("🔍 Running pre-flight checks...")
            state.status_message_id = checking_msg.id

            from core.cloner import run_preflight_checks
            passed = await run_preflight_checks(state.userbot, client, checking_msg)
            if not passed:
                return

            buttons = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Start Cloning", callback_data="clone_start"),
                    InlineKeyboardButton("❌ Cancel", callback_data="clone_cancel"),
                ]
            ])
            await checking_msg.edit(
                "✅ **Pre-flight checks passed!**\n\nSab kuch ready hai. Cloning start karein?",
                reply_markup=buttons,
            )

    @bot.on_message(filters.command("status"))
    @admin_only
    async def cmd_status(client: Client, message: Message) -> None:
        """Handle /status command — show live cloning status."""
        if not state.cloning_active:
            await message.reply(
                "ℹ️ Abhi koi cloning nahi chal rahi.\n\n"
                "/clone se shuru karo."
            )
            return

        try:
            source_entity = await state.userbot.get_entity(
                int(state.bot_client._bot_id) if hasattr(state.bot_client, '_bot_id') else 0
            )
            source_name = "Source Channel"
        except Exception:
            source_name = "Source Channel"

        dest_name = "Destination Channel"

        text = get_status_text(state, source_name, dest_name)
        sent = await message.reply(text)
        state.status_message_id = sent.id

    @bot.on_message(filters.command("pause"))
    @admin_only
    async def cmd_pause(client: Client, message: Message) -> None:
        """Handle /pause command — pause cloning after current message."""
        if not state.cloning_active:
            await message.reply("ℹ️ Koi cloning nahi chal rahi pause karne ke liye.")
            return

        if state.pause_flag:
            await message.reply("⏸️ Already paused hai.")
            return

        state.pause_flag = True
        save_progress(state)
        await message.reply(
            "⏸️ **Pausing...**\n\nCurrent message ke baad ruk jayega.\n"
            "/resume se resume karo."
        )

    @bot.on_message(filters.command("resume"))
    @admin_only
    async def cmd_resume(client: Client, message: Message) -> None:
        """Handle /resume command — resume paused cloning."""
        if not state.pause_flag:
            await message.reply("ℹ️ Cloning paused nahi hai.")
            return

        state.pause_flag = False
        await message.reply("▶️ **Resumed!**\n\nCloning continue ho gayi.")

    @bot.on_message(filters.command("retry"))
    @admin_only
    async def cmd_retry(client: Client, message: Message) -> None:
        """Handle /retry command — retry all failed message IDs."""
        from utils.progress import load_failed_ids, clear_failed_ids
        from core.forwarder import forward_single
        from utils.retry import forward_with_retry
        from config import SOURCE_ID, DEST_ID, MAX_RETRIES
        from config import caption_modifications_enabled

        failed_ids = load_failed_ids()
        if not failed_ids:
            await message.reply("✅ No failed messages! Sab kuch successfully forward hua.")
            return

        if state.userbot is None:
            await message.reply("❌ Userbot connected nahi hai. /login karo pehle.")
            return

        status_msg = await message.reply(
            f"🔄 Retrying {len(failed_ids)} failed messages..."
        )

        cap_mod = caption_modifications_enabled()
        success_count = 0
        fail_count = 0

        for msg_id in failed_ids:
            try:
                messages = await state.userbot.get_messages(SOURCE_ID, ids=msg_id)
                if not messages:
                    fail_count += 1
                    continue

                msg = messages[0] if isinstance(messages, list) else messages

                ok = await forward_with_retry(
                    lambda m=msg: forward_single(m, state.userbot, DEST_ID, SOURCE_ID, cap_mod),
                    msg_id,
                    client,
                    MAX_RETRIES,
                )
                if ok:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"Retry failed for msg {msg_id}: {e}")
                fail_count += 1

            await asyncio.sleep(2)

        clear_failed_ids()
        await status_msg.edit(
            f"✅ **Retry Complete!**\n\n"
            f"✅ Successful: {success_count}\n"
            f"❌ Still failed: {fail_count}"
        )

    @bot.on_message(filters.command(["cancel", "cancle", "canceel", "canc"]))
    @admin_only
    async def cmd_cancel(client: Client, message: Message) -> None:
        """Handle /cancel command — show confirmation buttons."""
        if not state.cloning_active and not state.delall_active:
            await message.reply("ℹ️ Koi active cloning/deletion nahi hai cancel karne ke liye.")
            return

        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚠️ Haan, Cancel Karo", callback_data="cancel_confirm"),
                InlineKeyboardButton("↩️ Nahi, Chalne Do", callback_data="cancel_abort"),
            ]
        ])
        await message.reply(
            "⚠️ **Cloning cancel karna chahte ho?**\n\n"
            "Progress save ho jayega. Baad mein resume kar sakte ho.",
            reply_markup=buttons,
        )

    @bot.on_message(filters.command("logs"))
    @admin_only
    async def cmd_logs(client: Client, message: Message) -> None:
        """Handle /logs command — send recent error logs."""
        errors = get_last_n_errors(30)
        if not errors:
            await message.reply("✅ No errors! Logs clean hai.")
            return

        content = "\n".join(errors)
        if len(content) <= 3800:
            await message.reply(
                f"📋 **Recent Errors (last 30):**\n\n```\n{content}\n```",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
        else:
            log_path = "/tmp/error_export.txt"
            try:
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(content)
                await client.send_document(
                    ADMIN_ID,
                    log_path,
                    caption="📋 Recent error logs (last 30 entries)",
                )
            except Exception as e:
                await message.reply(f"❌ Log file send nahi ho paya: {e}")

    @bot.on_message(filters.command("logout"))
    @admin_only
    async def cmd_logout(client: Client, message: Message) -> None:
        """Handle /logout command — disconnect userbot and reset state."""
        if state.cloning_active:
            state.cancel_flag = True
            await asyncio.sleep(2)

        if state.userbot is not None:
            try:
                await state.userbot.disconnect()
            except Exception:
                pass

        state.userbot = None
        state.session_string = None
        state.status = "IDLE"
        state.cloning_active = False
        state.pause_flag = False
        state.cancel_flag = False
        clear_session()
        mark_session_inactive()

        await message.reply(
            "✅ **Logged out!**\n\nUserbot disconnected. /login se dobara login karo."
        )

    @bot.on_message(filters.command("delall"))
    @admin_only
    async def cmd_delall(client: Client, message: Message) -> None:
        """/delall <chat_id> — Delete all messages in a group/channel where bot is admin."""
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply(
                "❌ **Chat ID nahi diya!**\n\n"
                "Usage: `/delall -1001234567890`\n\n"
                "Bot us group/channel mein admin hona chahiye.",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
            return

        raw_id = args[1].strip()
        try:
            chat_id = int(raw_id)
        except ValueError:
            await message.reply(
                "❌ **Invalid chat ID** — sirf number dalo.\n"
                "Example: `/delall -1001234567890`",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
            return

        checking = await message.reply("🔍 Chat check kar raha hoon...")

        try:
            chat = await client.get_chat(chat_id)
            chat_title = getattr(chat, "title", str(chat_id))
            member = await client.get_chat_member(chat_id, "me")

            from pyrogram.enums import ChatMemberStatus
            if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                await checking.edit(
                    f"❌ **Bot admin nahi hai**\n\n"
                    f"Chat: `{chat_title}`\n"
                    f"Bot ko admin banao aur 'Delete Messages' permission do.",
                    parse_mode=enums.ParseMode.MARKDOWN,
                )
                return
        except Exception as e:
            await checking.edit(
                f"❌ **Chat access nahi mila:** `{e}`\n\n"
                f"• Bot us chat mein member/admin hona chahiye\n"
                f"• Chat ID sahi hona chahiye",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
            return

        state.delall_chat_id = chat_id

        await checking.edit(
            f"🗑️ **Delete Messages**\n\n"
            f"📌 Chat: **{chat_title}**\n"
            f"🆔 ID: `{chat_id}`\n\n"
            f"🚨 Yeh action **REVERSIBLE NAHI** hai!\n\n"
            f"Kitne messages delete karne hain?",
            parse_mode=enums.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Last 1,000", callback_data="delall_count:1000"),
                    InlineKeyboardButton("Last 2,000", callback_data="delall_count:2000"),
                    InlineKeyboardButton("Last 5,000", callback_data="delall_count:5000"),
                ],
                [
                    InlineKeyboardButton("Last 10,000", callback_data="delall_count:10000"),
                    InlineKeyboardButton("Last 50,000", callback_data="delall_count:50000"),
                    InlineKeyboardButton("🗑️ Delete ALL", callback_data="delall_count:0"),
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data="delall_cancel")],
            ]),
        )

    # ─────────────────────────────────────────────
    # /filter — media type filter inline keyboard
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("filter"))
    @admin_only
    async def cmd_filter(client: Client, message: Message) -> None:
        """Show current media filter settings with toggle buttons and scan option."""
        from utils.settings import build_filter_keyboard, get_filter_summary
        summary = get_filter_summary()
        kb = build_filter_keyboard(show_scan=True)
        await message.reply(
            f"🎛️ **Media Filter Settings**\n\n"
            f"{summary}\n\n"
            f"Toggle karo ya 📊 Scan Channel dabao source ki detail dekhne ke liye:",
            reply_markup=kb,
        )

    # ─────────────────────────────────────────────
    # /dryrun — preview what would be forwarded
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("dryrun"))
    @admin_only
    async def cmd_dryrun(client: Client, message: Message) -> None:
        """/dryrun [limit] — Scan source with current filters, show forward/skip preview."""
        if state.userbot is None:
            await message.reply("❌ Userbot connected nahi hai. Pehle /login karo.")
            return
        if state.cloning_active:
            await message.reply("⚠️ Cloning already chal rahi hai. Pehle /cancel karo.")
            return

        # Parse optional limit arg: /dryrun 5000
        limit = 0
        args = message.text.split()
        if len(args) > 1:
            try:
                limit = int(args[1])
            except ValueError:
                await message.reply("❌ Limit number hona chahiye. Example: `/dryrun 5000`")
                return

        limit_str = f"last {limit:,}" if limit > 0 else "full channel"
        status_msg = await message.reply(
            f"🔍 **Dry Run shuru ho rahi hai** ({limit_str})...\n\n"
            "⏳ Source scan ho raha hai, please wait...\n\n"
            "Current filters se check karunga — kya forward hoga, kya skip."
        )

        from core.cloner import run_dryrun
        asyncio.create_task(run_dryrun(state.userbot, client, status_msg.id, limit=limit))

    # ─────────────────────────────────────────────
    # /clonerange — clone specific message ID range
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("clonerange"))
    @admin_only
    async def cmd_clonerange(client: Client, message: Message) -> None:
        """/clonerange <from_id> <to_id> — Clone a specific message ID range from source."""
        if state.userbot is None:
            await message.reply("❌ Userbot connected nahi hai. Pehle /login karo.")
            return
        if state.cloning_active:
            await message.reply(
                "⚠️ Cloning already chal rahi hai.\n"
                "Pehle /cancel karo, phir /clonerange chalao."
            )
            return

        args = message.text.split()
        if len(args) < 3:
            await message.reply(
                "❌ **Usage:**\n\n"
                "`/clonerange <from_id> <to_id>`\n\n"
                "**Example:**\n"
                "`/clonerange 1000 2500`\n\n"
                "Source channel ke message IDs `from_id` se `to_id` tak clone honge.\n"
                "Current filters aur caption rules apply honge.",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
            return

        try:
            from_id = int(args[1])
            to_id = int(args[2])
        except ValueError:
            await message.reply("❌ IDs sirf numbers hone chahiye.\nExample: `/clonerange 1000 2500`")
            return

        if from_id <= 0 or to_id <= 0:
            await message.reply("❌ IDs positive numbers hone chahiye.")
            return
        if from_id > to_id:
            await message.reply(
                f"❌ `from_id` ({from_id:,}) `to_id` ({to_id:,}) se bada nahi ho sakta."
            )
            return

        total_range = to_id - from_id + 1
        status_msg = await message.reply(
            f"🎯 **Clone Range shuru ho rahi hai...**\n\n"
            f"📍 Range: `{from_id:,}` → `{to_id:,}`\n"
            f"📊 ~{total_range:,} message IDs\n\n"
            f"⏳ Connecting to source channel...",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

        from core.cloner import start_cloning_range
        asyncio.create_task(
            start_cloning_range(state.userbot, client, from_id, to_id, status_msg.id)
        )

    # ─────────────────────────────────────────────
    # /setrule — caption replace or skip rule
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("setrule"))
    @admin_only
    async def cmd_setrule(client: Client, message: Message) -> None:
        """/setrule replace <find> | <replace>  or  /setrule skip <keyword>"""
        from utils.settings import add_caption_rule
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply(
                "❌ **Usage:**\n\n"
                "`/setrule replace <find> | <replace>`\n"
                "`/setrule skip <keyword>`\n\n"
                "Examples:\n"
                "`/setrule replace t.me/old | t.me/new`\n"
                "`/setrule skip ADVERTISEMENT`",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
            return

        rest = args[1].strip()
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("❌ Rule type aur value dono chahiye.")
            return

        rule_type = parts[0].lower()
        value = parts[1].strip()

        if rule_type == "replace":
            if "|" not in value:
                await message.reply("❌ Replace rule ke liye format: `<find> | <replace>`")
                return
            find, repl = [x.strip() for x in value.split("|", 1)]
            add_caption_rule("replace", find, repl)
            await message.reply(
                f"✅ **Replace Rule Added!**\n\n"
                f"Find: `{find}`\nReplace: `{repl}`",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
        elif rule_type == "skip":
            add_caption_rule("skip", value)
            await message.reply(
                f"✅ **Skip Rule Added!**\n\nKeyword: `{value}`",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
        else:
            await message.reply("❌ Rule type: `replace` ya `skip` hona chahiye.")

    # ─────────────────────────────────────────────
    # /listrules — list all caption rules
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("listrules"))
    @admin_only
    async def cmd_listrules(client: Client, message: Message) -> None:
        """List all active caption rules."""
        from utils.settings import list_caption_rules
        rules = list_caption_rules()
        if not rules:
            await message.reply("📋 Koi caption rules nahi hain. /setrule se add karo.")
            return
        lines = ["📋 **Active Caption Rules:**\n"]
        for i, r in enumerate(rules, 1):
            if r.get("type") == "replace":
                lines.append(f"{i}. **replace** `{r['find']}` → `{r['replace']}`")
            elif r.get("type") == "skip":
                lines.append(f"{i}. **skip** `{r['keyword']}`")
        await message.reply("\n".join(lines), parse_mode=enums.ParseMode.MARKDOWN)

    # ─────────────────────────────────────────────
    # /clearrules — delete all caption rules
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("clearrules"))
    @admin_only
    async def cmd_clearrules(client: Client, message: Message) -> None:
        """Clear all caption rules."""
        from utils.settings import clear_caption_rules
        clear_caption_rules()
        await message.reply("🗑️ **All caption rules cleared!**")

    # ─────────────────────────────────────────────
    # /setspeed — change speed profile
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("setspeed"))
    @admin_only
    async def cmd_setspeed(client: Client, message: Message) -> None:
        """/setspeed fast|normal|slow"""
        from utils.settings import set_speed_profile, get_speed_profile_name
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            current = get_speed_profile_name()
            await message.reply(
                f"⚡ **Speed Profiles:**\n\n"
                f"• `fast` — 0.05s batch, 0.3s page (risky)\n"
                f"• `normal` — 0.3s batch, 1.0s page (recommended)\n"
                f"• `slow` — 1.0s batch, 2.0s page (safest)\n\n"
                f"Current: **{current}**\n\n"
                f"Usage: `/setspeed fast`",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
            return
        profile = args[1].strip().lower()
        if profile not in ("fast", "normal", "slow"):
            await message.reply("❌ Valid profiles: `fast`, `normal`, `slow`")
            return
        set_speed_profile(profile)
        await message.reply(f"✅ Speed set to **{profile}**!")

    # ─────────────────────────────────────────────
    # /setwebhook — save webhook URL
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("setwebhook"))
    @admin_only
    async def cmd_setwebhook(client: Client, message: Message) -> None:
        """/setwebhook <url>  or  /setwebhook clear"""
        from utils.settings import set_webhook_url, get_webhook_url
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            current = get_webhook_url() or "None"
            await message.reply(
                f"🔔 **Webhook URL:**\n\n`{current}`\n\n"
                f"Usage: `/setwebhook https://your-endpoint.com/hook`\n"
                f"Clear: `/setwebhook clear`",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
            return
        url = args[1].strip()
        if url.lower() == "clear":
            set_webhook_url("")
            await message.reply("✅ Webhook cleared.")
        else:
            set_webhook_url(url)
            await message.reply(f"✅ Webhook set!\n\n`{url}`", parse_mode=enums.ParseMode.MARKDOWN)

    # ─────────────────────────────────────────────
    # /addsource — add an extra source channel
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("addsource"))
    @admin_only
    async def cmd_addsource(client: Client, message: Message) -> None:
        """/addsource -100XXXXXXXXXX"""
        from utils.settings import add_extra_source
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply("Usage: `/addsource -100XXXXXXXXXX`", parse_mode=enums.ParseMode.MARKDOWN)
            return
        try:
            cid = int(args[1].strip())
        except ValueError:
            await message.reply("❌ Invalid channel ID. Must be a number like `-1001234567890`.")
            return
        add_extra_source(cid)
        await message.reply(f"✅ Source added: `{cid}`\n\n/sources se list dekho.", parse_mode=enums.ParseMode.MARKDOWN)

    # ─────────────────────────────────────────────
    # /adddest — add an extra destination channel
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("adddest"))
    @admin_only
    async def cmd_adddest(client: Client, message: Message) -> None:
        """/adddest -100XXXXXXXXXX"""
        from utils.settings import add_extra_dest
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply("Usage: `/adddest -100XXXXXXXXXX`", parse_mode=enums.ParseMode.MARKDOWN)
            return
        try:
            cid = int(args[1].strip())
        except ValueError:
            await message.reply("❌ Invalid channel ID.")
            return
        add_extra_dest(cid)
        await message.reply(f"✅ Destination added: `{cid}`\n\n/dests se list dekho.", parse_mode=enums.ParseMode.MARKDOWN)

    # ─────────────────────────────────────────────
    # /sources — list all configured sources
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("sources"))
    @admin_only
    async def cmd_sources(client: Client, message: Message) -> None:
        """List all source channel IDs."""
        from core.cloner import _get_all_source_ids
        ids = _get_all_source_ids()
        lines = [f"📥 **Source Channels ({len(ids)}):**\n"]
        for i, sid in enumerate(ids, 1):
            lines.append(f"{i}. `{sid}`")
        await message.reply("\n".join(lines), parse_mode=enums.ParseMode.MARKDOWN)

    # ─────────────────────────────────────────────
    # /dests — list all configured destinations
    # ─────────────────────────────────────────────

    @bot.on_message(filters.command("dests"))
    @admin_only
    async def cmd_dests(client: Client, message: Message) -> None:
        """List all destination channel IDs."""
        from core.cloner import _get_all_dest_ids
        ids = _get_all_dest_ids()
        lines = [f"📤 **Destination Channels ({len(ids)}):**\n"]
        for i, did in enumerate(ids, 1):
            lines.append(f"{i}. `{did}`")
        await message.reply("\n".join(lines), parse_mode=enums.ParseMode.MARKDOWN)

    # ─────────────────────────────────────────────
    # Text input handler
    # ─────────────────────────────────────────────

    @bot.on_message(filters.private & ~filters.command([
        "start", "help", "login", "clone", "status",
        "pause", "resume", "retry", "cancel", "cancle", "canceel", "canc",
        "logs", "logout", "delall", "dryrun", "clonerange",
        "filter", "setrule", "listrules", "clearrules",
        "setspeed", "setwebhook", "addsource", "adddest", "sources", "dests",
    ]))
    @admin_only
    async def handle_text_input(client: Client, message: Message) -> None:
        """Handle plain text input for phone number and OTP during login flow."""
        global _otp_timeout_task

        text = (message.text or "").strip()

        if state.status == "WAITING_PHONE":
            if not re.match(r"^\+\d{10,15}$", text):
                await message.reply(
                    "❌ Invalid phone number format.\n\n"
                    "Country code ke saath bhejo, e.g. `+919876543210`",
                    parse_mode=enums.ParseMode.MARKDOWN,
                )
                return

            state.phone = text
            connecting_msg = await message.reply("🔗 Connecting to Telegram...")

            try:
                userbot = TelegramClient(
                    StringSession(""),
                    API_ID,
                    API_HASH,
                    **TELETHON_CONNECTION_PARAMS,
                )
                await userbot.connect()

                result = await userbot.send_code_request(text)
                state.phone_code_hash = result.phone_code_hash
                state.otp_sent_at = time.time()
                state.userbot = userbot
                state.status = "WAITING_OTP"

                if _otp_timeout_task and not _otp_timeout_task.done():
                    _otp_timeout_task.cancel()
                _otp_timeout_task = asyncio.create_task(_otp_timeout_task_fn(client))

                await connecting_msg.edit(
                    "✅ **OTP Bheja Gaya!**\n\n"
                    "📱 Telegram ne aapke number pe OTP bheja hai.\n\n"
                    "**Step 2/2:** OTP bhejo (5 digits):\n"
                    "Example: `12345`\n\n"
                    "⏰ 120 seconds mein expire ho jayega.",
                    parse_mode=enums.ParseMode.MARKDOWN,
                )

            except Exception as e:
                logger.error(f"Phone code request failed: {e}")
                state.status = "IDLE"
                state.phone = None
                if state.userbot:
                    try:
                        await state.userbot.disconnect()
                    except Exception:
                        pass
                    state.userbot = None
                await connecting_msg.edit(
                    f"❌ **Error!**\n\n{str(e)}\n\nDobara /login karo."
                )

        elif state.status == "WAITING_OTP":
            clean_otp = re.sub(r"[\s.\-]", "", text)

            if not re.match(r"^\d{5}$", clean_otp):
                await message.reply(
                    "❌ Invalid OTP format.\n\n5 digit ka OTP bhejo, e.g. `12345`",
                    parse_mode=enums.ParseMode.MARKDOWN,
                )
                return

            if state.otp_sent_at and (time.time() - state.otp_sent_at) > 120:
                state.status = "IDLE"
                state.phone = None
                state.phone_code_hash = None
                state.otp_sent_at = None
                await message.reply(
                    "⏰ OTP expire ho gaya!\n\nDobara /login karo."
                )
                return

            verifying_msg = await message.reply("🔐 Verifying OTP...")

            try:
                await state.userbot.sign_in(
                    phone=state.phone,
                    code=clean_otp,
                    phone_code_hash=state.phone_code_hash,
                )

                session_string = state.userbot.session.save()
                state.session_string = session_string
                state.status = "LOGGED_IN"
                save_session(session_string)
                mark_session_active()

                if _otp_timeout_task and not _otp_timeout_task.done():
                    _otp_timeout_task.cancel()

                me = await state.userbot.get_me()
                name = f"{me.first_name or ''} {me.last_name or ''}".strip()
                username = f"@{me.username}" if me.username else "No username"
                phone = state.phone or ""
                masked_phone = phone[:3] + "****" + phone[-3:] if len(phone) > 6 else "****"

                await verifying_msg.edit(
                    f"✅ **Login Successful!**\n\n"
                    f"👤 Name: {name}\n"
                    f"🔗 Username: {username}\n"
                    f"📱 Phone: {masked_phone}\n\n"
                    f"🚀 Ab /clone karo!",
                )

                logger.info(f"Userbot logged in as {name} ({me.id})")

            except PhoneCodeInvalidError:
                await verifying_msg.edit(
                    "❌ **OTP Galat Hai!**\n\nSahi OTP bhejo ya /login se restart karo."
                )
            except PhoneCodeExpiredError:
                state.status = "IDLE"
                state.phone = None
                state.phone_code_hash = None
                state.otp_sent_at = None
                if state.userbot:
                    try:
                        await state.userbot.disconnect()
                    except Exception:
                        pass
                    state.userbot = None
                await verifying_msg.edit(
                    "⏰ **OTP Expire Ho Gaya!**\n\nDobara /login karo."
                )
            except SessionPasswordNeededError:
                state.status = "IDLE"
                await verifying_msg.edit(
                    "❌ **2FA Enabled Hai!**\n\n"
                    "Is bot mein 2FA support nahi hai.\n"
                    "Telegram Settings → Privacy & Security → Two-Step Verification → Disable karo.\n"
                    "Phir /login karo."
                )
            except Exception as e:
                logger.error(f"OTP verification failed: {e}")
                await verifying_msg.edit(
                    f"❌ **Verification Failed!**\n\n{str(e)}\n\nDobara /login karo."
                )
        else:
            if text.startswith("/"):
                await message.reply(
                    f"❓ `{text.split()[0]}` command nahi pehchana.\n\n"
                    "/help se sahi commands dekho.",
                    parse_mode=enums.ParseMode.MARKDOWN,
                )
            else:
                await message.reply(
                    "ℹ️ Koi active input nahi expected.\n\n"
                    "/help se commands dekho."
                )