"""
Stats & admin callback handler for inline button actions from admin menu.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from bot.database import get_session
from bot.keyboards.inline import admin_menu_keyboard, schedule_nav_keyboard
from bot.models import Assignment, AssignmentStatus, Chore, Statistics, User
from bot.services.assignment_engine import generate_schedule
from bot.services.stats_service import get_weekly_report_data
from bot.utils.helpers import admin_only

logger = logging.getLogger(__name__)


async def admin_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route admin menu inline button presses."""
    query = update.callback_query
    await query.answer()
    action = query.data.split(":")[1]

    if action == "regen":
        async with get_session() as session:
            await generate_schedule(session)
        await query.edit_message_text("✅ Schedule regenerated for next 30 days!")

    elif action == "stats":
        async with get_session() as session:
            data = await get_weekly_report_data(session)

        if not data:
            await query.edit_message_text("No statistics yet.")
            return

        lines = ["📊 <b>All Member Stats</b>\n"]
        for entry in data:
            lines.append(
                f"👤 <b>{entry['name']}</b>\n"
                f"  ✅ {entry['completed']}  ❌ {entry['missed']}  📈 {entry['pct']}%"
            )
        await query.edit_message_text("\n".join(lines), parse_mode="HTML")

    elif action == "addchore":
        await query.edit_message_text("Use the /addchore command to start the guided chore setup.")

    elif action == "settings":
        await query.edit_message_text("⚙️ Settings: use /settings command (coming in next version).")


async def chore_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle chore deletion when admin taps a chore from the list."""
    query = update.callback_query
    await query.answer()
    chore_id = int(query.data.split(":")[1])

    # Store intent in context for confirmation
    context.user_data["delete_chore_id"] = chore_id

    async with get_session() as session:
        res = await session.execute(select(Chore).where(Chore.id == chore_id))
        chore = res.scalars().first()
        if not chore:
            await query.edit_message_text("❌ Chore not found.")
            return
        chore_name = chore.name

    from bot.keyboards.inline import confirm_keyboard
    await query.edit_message_text(
        f"🗑 Delete chore <b>{chore_name}</b>?\n\nThis will remove all future assignments too.",
        parse_mode="HTML",
        reply_markup=confirm_keyboard(f"delete_chore_confirm:{chore_id}", "cancel"),
    )


async def delete_chore_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chore_id = int(query.data.split(":")[1])

    async with get_session() as session:
        res = await session.execute(select(Chore).where(Chore.id == chore_id))
        chore = res.scalars().first()
        if chore:
            name = chore.name
            await session.delete(chore)
            await generate_schedule(session)
            await query.edit_message_text(
                f"✅ Chore <b>{name}</b> deleted and schedule updated.",
                parse_mode="HTML"
            )
        else:
            await query.edit_message_text("❌ Chore not found.")


async def all_stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats command — show all member statistics."""
    async with get_session() as session:
        result = await session.execute(
            select(User, Statistics)
            .join(Statistics, Statistics.user_id == User.id, isouter=True)
            .where(User.active == True)
        )
        rows = result.all()

    if not rows:
        await update.message.reply_text("No stats yet.")
        return

    lines = ["📊 <b>All Member Statistics</b>\n"]
    for user, stats in rows:
        if stats:
            pct = stats.completion_percentage
            lines.append(
                f"👤 <b>{user.name}</b>\n"
                f"  ✅ Completed: {stats.total_completed}\n"
                f"  ❌ Missed: {stats.total_missed}\n"
                f"  📈 {pct}%  🔥 Streak: {stats.current_streak}\n"
                f"  🔄 HO Given: {stats.handovers_given}  🙋 Taken: {stats.handovers_taken}\n"
            )
        else:
            lines.append(f"👤 <b>{user.name}</b> — No activity yet.\n")

    await update.message.reply_html("\n".join(lines))
