"""
/start, /help, /join — general member-facing handlers.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from bot.database import get_session
from bot.keyboards.inline import main_menu_keyboard
from bot.models import Statistics, User
from bot.config import settings
from bot.services.assignment_engine import generate_schedule
from bot.utils.helpers import reply_html

logger = logging.getLogger(__name__)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message with main menu."""
    user = update.effective_user
    text = (
        f"👋 <b>Welcome to Roommate Chore Manager!</b>\n\n"
        f"Hi <b>{user.first_name}</b>! I help manage household chores fairly.\n\n"
        f"Use /join to register yourself as a flat member.\n"
        f"Then use the menu below to manage your chores!"
    )
    await reply_html(update, text, reply_markup=main_menu_keyboard())


async def join_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Register the calling user as a flat member."""
    tg_user = update.effective_user

    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == tg_user.id)
        )
        existing = result.scalars().first()

        if existing:
            if not existing.active:
                existing.active = True
                await generate_schedule(session)
                status = "♻️ Welcome back! Your account has been reactivated and schedule updated."
            else:
                status = "ℹ️ You are already registered as a member!"
        else:
            name = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip()
            is_admin = tg_user.id == settings.ADMIN_TELEGRAM_ID
            new_user = User(
                telegram_id=tg_user.id,
                username=tg_user.username,
                name=name or tg_user.username or "Member",
                active=True,
                is_admin=is_admin,
            )
            session.add(new_user)
            await session.flush()
            session.add(Statistics(user_id=new_user.id))
            await generate_schedule(session)
            status = (
                f"✅ <b>{new_user.name}</b> joined the flat! "
                f"You're now included in the chore schedule."
            )

    await reply_html(update, status)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the help message."""
    text = (
        "📖 <b>Roommate Chore Manager — Help</b>\n\n"
        "<b>👤 Member Commands</b>\n"
        "/start — Main menu\n"
        "/join — Register as a flat member\n"
        "/today — View today's assigned chores\n"
        "/upcoming — View upcoming schedule\n"
        "/vacation — Set vacation dates\n"
        "/mystats — View your personal stats\n"
        "/whoisnext — Who's next for each chore?\n"
        "/leaderboard — Completion leaderboard\n\n"
        "<i>Mark done / handover via buttons on reminders or /today.</i>\n\n"
        "<b>🔧 Admin Commands</b>\n"
        "/admin — Admin panel\n"
        "/addmember — Add a member by Telegram ID\n"
        "/removemember — Deactivate a member\n"
        "/addchore — Create a new chore (guided flow)\n"
        "/deletechore — Delete a chore\n"
        "/markdone — Credit someone who already did today's chore\n"
        "/schedule — Full upcoming schedule\n"
        "/stats — All member statistics\n"
        "/reset — Regenerate assignments\n"
        "/backup — Download database backup\n"
    )
    await reply_html(update, text)
