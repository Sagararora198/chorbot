"""
Stats & admin callback handler for inline button actions from admin menu.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete as sql_delete, select
from telegram import Update
from telegram.ext import ContextTypes

from bot.database import get_session
from bot.keyboards.inline import chore_list_keyboard
from bot.models import Assignment, Chore, Handover, Statistics, Swap, User
from bot.services.assignment_engine import generate_schedule
from bot.services.stats_service import get_weekly_report_data
from bot.utils.helpers import reply_html, reply_text

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
        await query.edit_message_text(
            "🧹 Use /addchore to start the guided chore setup."
        )

    elif action == "addmember":
        await query.edit_message_text(
            "➕ Add a member with:\n"
            "<code>/addmember &lt;telegram_id&gt; &lt;Full Name&gt;</code>\n\n"
            "Example: <code>/addmember 123456789 Rahul Sharma</code>",
            parse_mode="HTML",
        )

    elif action == "removemember":
        await query.edit_message_text(
            "➖ Use /removemember to pick a member to deactivate."
        )

    elif action == "deletechore":
        async with get_session() as session:
            result = await session.execute(select(Chore))
            chores = list(result.scalars().all())
        if not chores:
            await query.edit_message_text("No chores found.")
            return
        await query.edit_message_text(
            "🗑 <b>Delete Chore</b>\nSelect the chore to delete:",
            parse_mode="HTML",
            reply_markup=chore_list_keyboard(chores),
        )

    elif action == "editchore":
        await query.edit_message_text(
            "✏️ Edit chore is not available yet.\n"
            "Delete with /deletechore and recreate with /addchore."
        )

    elif action == "markdone":
        from bot.handlers.member import markdone_start
        await markdone_start(update, context)

    elif action == "settings":
        await query.edit_message_text(
            "⚙️ Settings: use TIMEZONE / REMINDER_TIMEOUT_MINUTES / "
            "SCHEDULE_DAYS_AHEAD in your .env file."
        )


async def chore_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle chore deletion when admin taps a chore from the list."""
    query = update.callback_query
    await query.answer()
    chore_id = int(query.data.split(":")[1])

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
    try:
        chore_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Invalid delete request.")
        return

    try:
        async with get_session() as session:
            res = await session.execute(select(Chore).where(Chore.id == chore_id))
            chore = res.scalars().first()
            if not chore:
                await query.edit_message_text("❌ Chore not found.")
                return

            name = chore.name

            # SAFETY: delete dependent rows first — FK from handovers/swaps → assignments
            # blocks a plain chore delete and previously failed silently for the user.
            asgn_ids = list(
                (
                    await session.execute(
                        select(Assignment.id).where(Assignment.chore_id == chore_id)
                    )
                ).scalars().all()
            )
            if asgn_ids:
                await session.execute(
                    sql_delete(Handover).where(Handover.assignment_id.in_(asgn_ids))
                )
                await session.execute(
                    sql_delete(Swap).where(
                        (Swap.assignment_id.in_(asgn_ids))
                        | (Swap.target_assignment_id.in_(asgn_ids))
                    )
                )
                await session.execute(
                    sql_delete(Assignment).where(Assignment.chore_id == chore_id)
                )

            await session.delete(chore)
            await session.flush()
            await generate_schedule(session)

        await query.edit_message_text(
            f"✅ Chore <b>{name}</b> deleted and schedule updated.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Failed to delete chore %s: %s", chore_id, e, exc_info=True)
        try:
            await query.edit_message_text(
                "❌ Could not delete this chore. Try /reset then delete again."
            )
        except Exception:
            pass


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generic cancel for inline confirm dialogs outside conversations."""
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_text("❌ Cancelled.")
    except Exception:
        pass


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
            await reply_text(update, "No stats yet.")
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

    await reply_html(update, "\n".join(lines))
