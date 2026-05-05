"""Bot package — Pyrogram bot client handlers, callbacks, and message templates."""

from bot.handlers import register_handlers
from bot.callbacks import register_callbacks

__all__ = [
    "register_handlers",
    "register_callbacks",
]