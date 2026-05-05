from datetime import datetime
from typing import Optional
from config import state as _state

_BOLD_DIGITS = str.maketrans("0123456789", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵")


def bold_num(n: int) -> str:
    return f"{n:,}".translate(_BOLD_DIGITS)


def make_progress_bar(percent: float, width: int = 20) -> str:
    """
    Generate a text progress bar.

    Args:
        percent: 0.0 to 100.0
        width: total bar character width

    Returns:
        String like '████████████░░░░░░░░'
    """
    percent = max(0.0, min(100.0, percent))
    filled = int(width * percent / 100)
    empty = width - filled
    return "█" * filled + "░" * empty


def format_duration(seconds: float) -> str:
    """
    Format seconds into human-readable duration.

    Args:
        seconds: total elapsed seconds

    Returns:
        String like '17h 52m 10s'
    """
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    parts = []
    if h:
        parts.append(f"{h}h")
    if m or h:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def calc_speed(processed: int, start_time: Optional[datetime]) -> float:
    """
    Calculate processing speed in messages per minute.

    Args:
        processed: number of messages processed
        start_time: datetime when cloning started

    Returns:
        Float messages per minute
    """
    if start_time is None or processed == 0:
        return 0.0
    elapsed = (datetime.now() - start_time).total_seconds()
    if elapsed <= 0:
        return 0.0
    return round((processed / elapsed) * 60, 1)


def calc_eta(remaining: int, speed: float) -> str:
    """
    Calculate estimated time of arrival.

    Args:
        remaining: messages left to process
        speed: messages per minute

    Returns:
        String like '~11h 48m left'
    """
    if speed <= 0 or remaining <= 0:
        return "Calculating..."
    minutes_left = remaining / speed
    seconds_left = minutes_left * 60
    return f"~{format_duration(seconds_left)} left"


_SEP = "⊶" * 22
_BAR_W = 15


def _clone_bar(pct: float) -> str:
    filled = int(_BAR_W * pct / 100)
    return "[" + "█" * filled + "░" * (_BAR_W - filled) + "]"


def _build_source_lines(state, current_source_name: str) -> str:
    """
    Build the source section of the status message.

    Single source  → one line with its total count.
    Multi-source   → each source on its own line with count, then a total row.
    Falls back to plain source_name if source_counts is not populated.
    """
    sc: dict = getattr(state, "source_counts", {})

    if not sc:
        return f"📤 `{current_source_name}`\n"

    if len(sc) == 1:
        info = next(iter(sc.values()))
        cnt = f"{info['count']:,} msgs" if info["count"] > 0 else "?"
        return f"📤 `{info['name']}` ({cnt})\n"

    # Multi-source: list each with count, mark active, show total
    lines = []
    total_count = sum(v["count"] for v in sc.values())
    for info in sc.values():
        cnt = f"{info['count']:,}" if info["count"] > 0 else "?"
        marker = "▶️" if info["name"] == current_source_name else "   "
        lines.append(f"{marker} `{info['name']}` ({cnt})")
    lines.append(f"📊 Total: {total_count:,} msgs")
    return "📤 " + "\n    ".join(lines) + "\n"


def get_status_text(state, source_name: str, dest_name: str) -> str:
    """Build the full clone status message."""
    processed = state.processed
    total = state.total_messages
    failed = state.failed
    albums = state.albums_count
    skipped = getattr(state, "skipped", 0)
    current_id = state.current_id

    speed = calc_speed(processed, state.start_time)
    elapsed = format_duration(
        (datetime.now() - state.start_time).total_seconds()
    ) if state.start_time else "0s"
    now_str = datetime.now().strftime("%H:%M:%S")

    if not state.cloning_active:
        status_label = "⏹️ 𝗜𝗱𝗹𝗲"
    elif state.pause_flag:
        status_label = "⏸️ 𝗣𝗮𝘂𝘀𝗲𝗱"
    else:
        status_label = "🟢 𝗥𝘂𝗻𝗻𝗶𝗻𝗴"

    if total > 0:
        pct = processed / total * 100
        bar = _clone_bar(pct)
        remaining = max(0, total - processed)
        eta = calc_eta(remaining, speed)
        progress_block = (
            f"📈 {bar} {pct:.1f}%\n"
            f"📝 {bold_num(processed)} 𝗼𝗳 {bold_num(total)} 𝗺𝘀𝗴𝘀\n"
        )
        eta_line = f"🏁 𝗘𝗧𝗔: {eta}\n"
    else:
        progress_block = f"📝 {bold_num(processed)} 𝗺𝘀𝗴𝘀 𝗳𝗼𝗿𝘄𝗮𝗿𝗱𝗲𝗱\n"
        eta_line = ""

    source_lines = _build_source_lines(state, source_name)

    return (
        f"📡 𝗖𝗹𝗼𝗻𝗲 𝗦𝘁𝗮𝘁𝘂𝘀 📡\n"
        f"{_SEP}\n\n"
        f"{source_lines}"
        f"📥 `{dest_name}`\n\n"
        f"{progress_block}\n"
        f"✓ 𝗙𝗼𝗿𝘄𝗮𝗿𝗱𝗲𝗱: {bold_num(processed)}  |  🖼️ 𝗔𝗹𝗯𝘂𝗺𝘀: {bold_num(albums)}\n"
        f"✗ 𝗙𝗮𝗶𝗹𝗲𝗱: {bold_num(failed)}  |  ⏭️ 𝗦𝗸𝗶𝗽𝗽𝗲𝗱: {bold_num(skipped)}\n"
        f"📍 𝗖𝘂𝗿𝗿 𝗜𝗗: {current_id:,}\n\n"
        f"⚡ {speed:,.0f} msg/min  •  ⏱️ {elapsed}\n"
        f"{eta_line}"
        f"{_SEP}\n"
        f"{status_label} · {now_str}"
    )


def get_completion_text(state, source_name: str, dest_name: str) -> str:
    """Build the cloning completion summary message."""
    elapsed = format_duration(
        (datetime.now() - state.start_time).total_seconds()
    ) if state.start_time else "N/A"

    skipped = getattr(state, "skipped", 0)
    speed = calc_speed(state.processed, state.start_time)
    now_str = datetime.now().strftime("%H:%M:%S")

    source_lines = _build_source_lines(state, source_name)

    return (
        f"🎊 𝗖𝗹𝗼𝗻𝗶𝗻𝗴 𝗖𝗼𝗺𝗽𝗹𝗲𝘁𝗲! 🎊\n"
        f"{_SEP}\n\n"
        f"{source_lines}"
        f"📥 `{dest_name}`\n\n"
        f"✓ 𝗙𝗼𝗿𝘄𝗮𝗿𝗱𝗲𝗱: {bold_num(state.processed)}  |  🖼️ 𝗔𝗹𝗯𝘂𝗺𝘀: {bold_num(state.albums_count)}\n"
        f"✗ 𝗙𝗮𝗶𝗹𝗲𝗱: {bold_num(state.failed)}  |  ⏭️ 𝗦𝗸𝗶𝗽𝗽𝗲𝗱: {bold_num(skipped)}\n"
        f"📌 𝗕𝗮𝘁𝗰𝗵𝗲𝘀: {state.batch_number} pinned\n\n"
        f"⚡ {speed:,.0f} msg/min  •  ⏱️ {elapsed}\n"
        f"{_SEP}\n"
        f"🕐 {now_str}  •  🎉 𝗦𝗮𝗯 𝗺𝘀𝗴𝘀 𝗰𝗹𝗼𝗻𝗲 𝗵𝗼 𝗴𝗮𝘆𝗲!"
    )


def get_welcome_text() -> str:
    """Return the welcome message for /start command."""
    return (
        "👋 **Welcome to TeleClone Bot!**\n\n"
        "🤖 Main ek Telegram Channel Cloner bot hoon.\n\n"
        "**Quick Start:**\n"
        "1️⃣ /login — Apna userbot login karo\n"
        "2️⃣ /clone — Cloning shuru karo\n"
        "3️⃣ /status — Live progress dekho\n\n"
        "**Controls:**\n"
        "⏸️ /pause — Pause karo\n"
        "▶️ /resume — Resume karo\n"
        "🛑 /cancel — Cancel karo\n"
        "🔄 /retry — Failed messages retry karo\n\n"
        "**Info:**\n"
        "📋 /logs — Error logs dekho\n"
        "🚪 /logout — Logout karo\n"
        "❓ /help — Detailed help\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔒 Sirf authorized admin use kar sakta hai."
    )


def get_help_text() -> str:
    """Return the detailed help message for /help command."""
    return (
        "📖 **TeleClone Bot — Help**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**🔐 Authentication**\n"
        "/login — Userbot login shuru karo\n"
        "  → Phone number bhejo with country code\n"
        "  → OTP enter karo (5 digits)\n"
        "/logout — Userbot disconnect karo\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**🚀 Cloning**\n"
        "/clone — Pre-flight checks aur cloning shuru\n"
        "/dryrun — Filter preview (kya forward, kya skip)\n"
        "/dryrun 5000 — Last 5000 msgs ka dry run\n"
        "/clonerange 1000 2500 — ID range clone karo\n"
        "/status — Live progress message\n"
        "/pause — Current message ke baad ruk jao\n"
        "/resume — Paused cloning resume karo\n"
        "/cancel — Cloning cancel karo (progress save)\n"
        "/retry — Failed messages dobara try karo\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**🎛️ Filters & Rules**\n"
        "/filter — Media type filter toggle (photo/video/etc)\n"
        "/setrule replace <find> | <replace> — Caption text replace\n"
        "/setrule skip <keyword> — Skip messages with keyword\n"
        "/listrules — List all active caption rules\n"
        "/clearrules — Delete all caption rules\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**⚡ Speed & Webhooks**\n"
        "/setspeed fast|normal|slow — Forwarding speed\n"
        "/setwebhook <url> — Webhook for events (clear to remove)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**📡 Multi-Source / Multi-Dest**\n"
        "/addsource -100XXXXXXXXXX — Extra source channel\n"
        "/adddest -100XXXXXXXXXX — Extra destination channel\n"
        "/sources — List all source channels\n"
        "/dests — List all destination channels\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**📊 Monitoring**\n"
        "/logs — Last 30 error log entries\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**💡 Notes**\n"
        "• Progress data/progress.json mein save hota hai\n"
        "• Restart ke baad auto-resume button aata hai\n"
        "• 2FA supported nahi hai — disable rakho\n"
        "• Multi-source: sources ek-ek karke clone honge\n"
        "• Multi-dest: har message sab dests mein jayega\n"
    )


def get_restart_notification(progress_data: dict) -> str:
    """
    Build restart detection notification message.

    Args:
        progress_data: dict loaded from progress.json

    Returns:
        Formatted notification string
    """
    processed = progress_data.get("processed", 0)
    last_id = progress_data.get("last_processed_id", 0)
    failed = progress_data.get("failed", 0)
    batch = progress_data.get("batch_number", 0)
    start_time = progress_data.get("start_time", "Unknown")

    return (
        f"⚠️ **Bot Restart Detected!**\n\n"
        f"Pichli session interrupted ho gayi thi.\n\n"
        f"📊 **Last Session Stats:**\n"
        f"✅ Processed: {processed:,}\n"
        f"📍 Last ID: {last_id:,}\n"
        f"❌ Failed: {failed:,}\n"
        f"📌 Batches: {batch}\n"
        f"🕐 Started: {start_time}\n\n"
        f"▶️ Resume karne ke liye:\n"
        f"1. /login karo\n"
        f"2. /clone karo (auto-resume hoga)\n\n"
        f"🗑️ Fresh start ke liye progress file delete karo."
    )