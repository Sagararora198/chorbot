"""
Utility helpers used across the codebase.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from functools import wraps
from typing import Callable

import pytz
from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings

logger = logging.getLogger(__name__)
TZ = pytz.timezone(settings.TIMEZONE)


def now_tz() -> datetime:
    """Current datetime in configured timezone."""
    return datetime.now(TZ)


def today_tz() -> date:
    """Current date in configured timezone."""
    return now_tz().date()


def format_date(d: date | datetime) -> str:
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%a, %d %b %Y")


def admin_only(func: Callable) -> Callable:
    """Decorator: restrict handler to admin users only."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user and user.id == settings.ADMIN_TELEGRAM_ID:
            return await func(update, context, *args, **kwargs)
        # Also allow if user is admin in DB (handled inside handlers for DB check)
        msg = update.message or (update.callback_query and update.callback_query.message)
        if msg:
            await msg.reply_text("⛔ This command is for admins only.")
        return None
    return wrapper


def parse_time(t: str) -> bool:
    """Return True if t is a valid HH:MM time string."""
    try:
        datetime.strptime(t.strip(), "%H:%M")
        return True
    except ValueError:
        return False
