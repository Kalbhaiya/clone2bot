import asyncio
import signal
import sys
import os

from pyrogram import Client, idle
from rich.console import Console
from rich.panel import Panel
from rich.box import DOUBLE_EDGE

from config import validate_config, API_ID, API_HASH, BOT_TOKEN, ADMIN_ID, state, TELETHON_CONNECTION_PARAMS
from utils.logger import get_logger, setup_logging
from utils.progress import progress_exists, load_progress, mark_session_inactive, save_progress, load_session
from bot.handlers import register_handlers
from bot.callbacks import register_callbacks

logger = get_logger(__name__)
console = Console()


def print_banner() -> None:
    """Print the startup banner using rich."""
    banner_text = (
        "[bold cyan]📚 Userbot    :[/bold cyan] Telethon (StringSession)\n"
        "[bold cyan]🤖 Bot        :[/bold cyan] Pyrogram\n"
        "[bold cyan]🔒 Sender     :[/bold cyan] Hidden (drop_author=True)\n"
        "[bold cyan]🖥️  Host       :[/bold cyan] Render Worker Service\n"
        "[bold cyan]📝 Captions   :[/bold cyan] Smart (modify or preserve)\n"
        "[bold cyan]🖼️  Albums     :[/bold cyan] Grouped Forward\n"
        "[bold cyan]📌 Indexing   :[/bold cyan] Every 2000 messages"
    )
    panel = Panel(
        banner_text,
        title="[bold yellow]🚀 TELECLONE BOT v2.0[/bold yellow]",
        subtitle="[dim]Telegram Channel Cloner — Render Ready[/dim]",
        box=DOUBLE_EDGE,
        border_style="bright_blue",
        padding=(1, 2),
    )
    console.print(panel)


async def keep_alive_loop() -> None:
    """Keep-alive loop that prints heartbeat every hour."""
    while True:
        await asyncio.sleep(3600)
        print("✅ TeleClone alive — bot running")
        logger.info("Heartbeat — bot running normally")


async def send_restart_notification(bot: Client, progress_data: dict) -> None:
    """Send restart/resume notification to admin if previous session was interrupted."""
    from bot.messages import get_restart_notification
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await asyncio.sleep(3)
    try:
        text = get_restart_notification(progress_data)
        processed = progress_data.get("processed", 0)
        last_id   = progress_data.get("last_processed_id", 0)
        resume_text = (
            f"\n\n📌 **Progress Found:**\n"
            f"✅ Processed: {processed:,}\n"
            f"📍 Last ID: {last_id:,}\n\n"
            f"Kya resume karna chahte ho?"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Resume Cloning", callback_data="auto_resume")],
        ])
        await bot.send_message(ADMIN_ID, text + resume_text, reply_markup=buttons)
    except Exception as e:
        logger.error(f"Failed to send restart notification: {e}")


async def _try_auto_connect_userbot() -> None:
    """Auto-connect userbot from saved session on startup."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    session_string = load_session()
    if not session_string:
        return

    logger.info("Saved session found — auto-connecting userbot...")
    try:
        userbot = TelegramClient(
            StringSession(session_string),
            API_ID,
            API_HASH,
            **TELETHON_CONNECTION_PARAMS,
        )
        await userbot.connect()
        me = await userbot.get_me()
        if me:
            state.userbot = userbot
            state.session_string = session_string
            state.status = "LOGGED_IN"
            name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            logger.info(f"Userbot auto-reconnected as {name} ({me.id})")
        else:
            await userbot.disconnect()
            logger.warning("Auto-connect: get_me() returned None — session may be expired")
    except Exception as e:
        logger.error(f"Auto-connect userbot failed: {e} — manual /login required")


async def main() -> None:
    """Main entry point — starts bot and keep-alive loop."""
    validate_config()
    setup_logging()
    print_banner()

    logger.info("TeleClone Bot starting up...")

    check_restart = False
    restart_data: dict = {}

    if progress_exists():
        data = load_progress()
        if data and data.get("processed", 0) > 0:
            check_restart = True
            restart_data = data
            logger.info(f"Detected resumable progress — processed={data.get('processed', 0)}")

    bot = Client(
        "teleclone_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True,
    )

    state.bot_client = bot

    register_handlers(bot)
    register_callbacks(bot)

    logger.info("Handlers and callbacks registered")

    def handle_sigterm(*args):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        async with bot:
            logger.info("Bot started successfully")
            console.print("[bold green]✅ Bot is online and ready![/bold green]")

            await _try_auto_connect_userbot()

            if state.status == "LOGGED_IN":
                startup_msg = "✅ TeleClone Bot online!\n\n🔗 Userbot auto-connected (saved session). /clone se seedha shuru karo!"
            else:
                startup_msg = "Hi! 👋 TeleClone Bot is online and ready.\n\nUse /login to connect your userbot, then /clone to start cloning."

            try:
                await bot.send_message(ADMIN_ID, startup_msg)
            except Exception as e:
                logger.error(f"Failed to send startup message: {e}")

            if check_restart and restart_data:
                asyncio.create_task(send_restart_notification(bot, restart_data))

            await asyncio.gather(
                idle(),
                keep_alive_loop(),
            )
    except KeyboardInterrupt:
        logger.info("Shutdown signal received")
        console.print("[bold yellow]⚠️ Shutdown initiated...[/bold yellow]")
    except Exception as e:
        logger.exception(f"Fatal error in main: {e}")
    finally:
        state.cancel_flag = True
        mark_session_inactive()
        save_progress(state)
        processed = state.processed
        failed = state.failed
        current_id = state.current_id
        print(f"SHUTDOWN|{processed}|{current_id}|{failed}")
        logger.info(f"Graceful shutdown complete — processed={processed}, failed={failed}")
        console.print("[bold red]🛑 TeleClone Bot stopped[/bold red]")


if __name__ == "__main__":
    asyncio.run(main())