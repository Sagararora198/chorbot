"""
Member chore handlers: /today, /upcoming, /done, /handover, /vacation, /mystats.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytz
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)

from bot.config import settings
from bot.database import get_session
from bot.keyboards.inline import (
    cancel_keyboard, reminder_keyboard, schedule_nav_keyboard,
)
from bot.models import (
    Assignment, AssignmentStatus, Handover, HandoverStatus,
    Statistics, User
)
from bot.services.assignment_engine import generate_schedule
from bot.services.notification_service import (
    notify_group_handover_request, notify_group_task_done,
    notify_handover_accepted,
)
from bot.services.stats_service import (
    get_or_create_stats, record_completion, record_handover_given,
    record_handover_taken
)
from bot.utils.helpers import format_date, reply_html, reply_text, today_tz

logger = logging.getLogger(__name__)
TZ = pytz.timezone(settings.TIMEZONE)

# ── Conversation states ──────────────────────────────────────────────────────
VAC_START, VAC_END = range(2)
HANDOVER_REASON = 4


async def _get_member(session, tg_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == tg_id))
    return result.scalars().first()


# ─────────────────────────────────────────────────────────────────────────────
# /today
# ─────────────────────────────────────────────────────────────────────────────

async def today_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    today = today_tz()
    today_start = datetime(today.year, today.month, today.day, 0, 0, 0)
    today_end = today_start + timedelta(days=1)

    async with get_session() as session:
        member = await _get_member(session, tg_user.id)
        if not member:
            await reply_text(update, "⚠️ You are not registered. Use /join first.")
            return

        member_name = member.name
        result = await session.execute(
            select(Assignment)
            .options(selectinload(Assignment.chore))
            .where(
                Assignment.user_id == member.id,
                Assignment.scheduled_date >= today_start,
                Assignment.scheduled_date < today_end,
            )
        )
        assignments = list(result.scalars().all())

        # Snapshot fields while session is open (async-safe)
        pending_msgs: list[tuple[str, int]] = []
        other_lines: list[str] = []
        for assignment in assignments:
            chore = assignment.chore
            times = ", ".join(chore.reminder_times or [])
            status_icon = {
                AssignmentStatus.PENDING: "⏳",
                AssignmentStatus.COMPLETED: "✅",
                AssignmentStatus.MISSED: "❌",
                AssignmentStatus.HANDED_OVER: "🔄",
            }.get(assignment.status, "❓")
            line = (
                f"{status_icon} <b>{chore.name}</b>\n"
                f"   ⏰ {times or 'No reminder set'}"
            )
            if assignment.status == AssignmentStatus.PENDING:
                msg = (
                    f"📋 <b>Today's Chores — {format_date(today)}</b>\n\n"
                    f"{line}\n\n"
                    f"💬 {chore.reminder_message or ''}".strip()
                )
                pending_msgs.append((msg, assignment.id))
            else:
                other_lines.append(line)

    if not assignments:
        await reply_html(
            update,
            f"🎉 <b>No chores today!</b> Enjoy your free day, {member_name}!",
        )
        return

    # Answer callback once for menu taps (reply_html also answers — skip double for first)
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except Exception:
            pass

    target = (query.message if query else update.message)
    if not target:
        return

    for msg, asgn_id in pending_msgs:
        await target.reply_html(msg, reply_markup=reminder_keyboard(asgn_id))

    if other_lines:
        header = f"📋 <b>Today's Chores — {format_date(today)}</b>\n\n"
        await target.reply_html(header + "\n".join(other_lines))
    elif not pending_msgs:
        await target.reply_html(
            f"📋 <b>Today's Chores — {format_date(today)}</b>\n\n"
            "No pending chores."
        )


# ─────────────────────────────────────────────────────────────────────────────
# /upcoming — schedule view
# ─────────────────────────────────────────────────────────────────────────────

async def upcoming_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_html(
        update,
        "📅 <b>View Schedule</b>\nChoose a period:",
        reply_markup=schedule_nav_keyboard(),
    )


async def schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    period = query.data.split(":")[1]

    today = today_tz()

    if period == "today":
        start = datetime(today.year, today.month, today.day)
        end = start + timedelta(days=1)
        label = "Today"
    elif period == "tomorrow":
        tomorrow = today + timedelta(days=1)
        start = datetime(tomorrow.year, tomorrow.month, tomorrow.day)
        end = start + timedelta(days=1)
        label = "Tomorrow"
    elif period == "week":
        start = datetime(today.year, today.month, today.day)
        end = start + timedelta(days=7)
        label = "This Week"
    else:  # next_week
        start = datetime(today.year, today.month, today.day) + timedelta(days=7)
        end = start + timedelta(days=7)
        label = "Next Week"

    async with get_session() as session:
        result = await session.execute(
            select(Assignment)
            .options(
                selectinload(Assignment.chore),
                selectinload(Assignment.user),
            )
            .where(
                Assignment.scheduled_date >= start,
                Assignment.scheduled_date < end,
            )
            .order_by(Assignment.scheduled_date)
        )
        assignments = list(result.scalars().all())

        if not assignments:
            await query.edit_message_text(f"📅 No assignments for {label}.")
            return

        lines = [f"📅 <b>Schedule — {label}</b>\n"]
        current_date = None
        for asgn in assignments:
            d = asgn.scheduled_date.date()
            if d != current_date:
                current_date = d
                lines.append(f"\n📆 <b>{format_date(d)}</b>")
            chore = asgn.chore
            user = asgn.user
            status_icon = "✅" if asgn.status == AssignmentStatus.COMPLETED else "⏳"
            lines.append(f"  {status_icon} {chore.name} → {user.name}")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=schedule_nav_keyboard(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# /done callback — Mark assignment complete
# ─────────────────────────────────────────────────────────────────────────────

async def done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    assignment_id = int(query.data.split(":")[1])
    tg_user = update.effective_user

    async with get_session() as session:
        asgn_res = await session.execute(
            select(Assignment)
            .options(selectinload(Assignment.chore))
            .where(Assignment.id == assignment_id)
        )
        assignment = asgn_res.scalars().first()

        if not assignment:
            await query.edit_message_text("❌ Assignment not found.")
            return

        member = await _get_member(session, tg_user.id)
        if not member or assignment.user_id != member.id:
            await query.answer("❌ This task is not assigned to you.", show_alert=True)
            return

        if assignment.status == AssignmentStatus.COMPLETED:
            await query.answer("✅ Already marked as done!", show_alert=True)
            return

        assignment.status = AssignmentStatus.COMPLETED
        assignment.completed_at = datetime.utcnow()
        chore_name = assignment.chore.name
        member_name = member.name

        await record_completion(session, member)
        await notify_group_task_done(context.bot, member, assignment)

    await query.edit_message_text(
        f"✅ <b>{chore_name}</b> marked as complete! Great job, {member_name}! 🎉",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /handover — request handover via reminder button
# ─────────────────────────────────────────────────────────────────────────────

async def handover_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    assignment_id = int(query.data.split(":")[1])
    context.user_data["handover_assignment_id"] = assignment_id

    await query.edit_message_text(
        "🔄 <b>Handover Request</b>\n\n"
        "Please send your reason for handing over this task:",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    return HANDOVER_REASON


async def handover_reason_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reason = update.message.text.strip()
    assignment_id = context.user_data.get("handover_assignment_id")
    tg_user = update.effective_user

    async with get_session() as session:
        asgn_res = await session.execute(
            select(Assignment)
            .options(selectinload(Assignment.chore))
            .where(Assignment.id == assignment_id)
        )
        assignment = asgn_res.scalars().first()
        member = await _get_member(session, tg_user.id)

        if not assignment or not member:
            await update.message.reply_text("❌ Error processing handover.")
            return ConversationHandler.END

        handover = Handover(
            assignment_id=assignment_id,
            from_user_id=member.id,
            reason=reason,
            status=HandoverStatus.REQUESTED,
        )
        session.add(handover)
        await session.flush()
        assignment.status = AssignmentStatus.HANDED_OVER

        await record_handover_given(session, member)
        await notify_group_handover_request(context.bot, member, assignment, handover)

    await update.message.reply_html(
        f"✅ Handover posted to group! First member to claim it gets the task."
    )
    context.user_data.clear()
    return ConversationHandler.END


async def handover_accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    handover_id = int(query.data.split(":")[1])
    tg_user = update.effective_user

    async with get_session() as session:
        hv_res = await session.execute(
            select(Handover)
            .options(
                selectinload(Handover.assignment).selectinload(Assignment.chore),
                selectinload(Handover.from_user),
            )
            .where(Handover.id == handover_id)
        )
        handover = hv_res.scalars().first()

        if not handover or handover.status != HandoverStatus.REQUESTED:
            await query.answer("❌ This handover is no longer available.", show_alert=True)
            return

        taker = await _get_member(session, tg_user.id)
        if not taker:
            await query.answer("You are not registered. Use /join first.", show_alert=True)
            return

        if taker.id == handover.from_user_id:
            await query.answer("You can't accept your own handover!", show_alert=True)
            return

        handover.to_user_id = taker.id
        handover.status = HandoverStatus.ACCEPTED
        handover.accepted_at = datetime.utcnow()

        assignment = handover.assignment
        assignment.user_id = taker.id
        assignment.status = AssignmentStatus.PENDING

        from_user = handover.from_user
        taker_name = taker.name
        await record_handover_taken(session, taker)
        await notify_handover_accepted(context.bot, from_user, taker, assignment)

    await query.edit_message_text(
        f"✅ <b>{taker_name}</b> accepted the handover!",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Emergency takeover (overdue tasks)
# ─────────────────────────────────────────────────────────────────────────────

async def emergency_take_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    assignment_id = int(query.data.split(":")[1])
    tg_user = update.effective_user

    async with get_session() as session:
        asgn_res = await session.execute(
            select(Assignment).where(Assignment.id == assignment_id)
        )
        assignment = asgn_res.scalars().first()
        taker = await _get_member(session, tg_user.id)

        if not assignment or not taker:
            await query.answer("Task no longer available.", show_alert=True)
            return

        assignment.user_id = taker.id
        assignment.status = AssignmentStatus.PENDING
        assignment.reminder_sent = False
        assignment.overdue_notified = False
        taker_name = taker.name

        await record_handover_taken(session, taker)

    await query.edit_message_text(
        f"✅ <b>{taker_name}</b> took over the task!",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /vacation
# ─────────────────────────────────────────────────────────────────────────────

async def vacation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await reply_html(
        update,
        "🌴 <b>Vacation Mode</b>\n\n"
        "Enter your <b>vacation start date</b> (DD/MM/YYYY):",
        reply_markup=cancel_keyboard(),
    )
    return VAC_START


async def vacation_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        d = datetime.strptime(update.message.text.strip(), "%d/%m/%Y")
        context.user_data["vac_start"] = d
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use DD/MM/YYYY.")
        return VAC_START

    await update.message.reply_html(
        f"✅ Start: <b>{update.message.text.strip()}</b>\n\n"
        "Now enter your <b>vacation end date</b> (DD/MM/YYYY):",
        reply_markup=cancel_keyboard(),
    )
    return VAC_END


async def vacation_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tg_user = update.effective_user
    try:
        d = datetime.strptime(update.message.text.strip(), "%d/%m/%Y")
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use DD/MM/YYYY.")
        return VAC_END

    start = context.user_data["vac_start"]
    if d < start:
        await update.message.reply_text("❌ End date must be after start date.")
        return VAC_END

    async with get_session() as session:
        member = await _get_member(session, tg_user.id)
        if member:
            member.vacation_start = start
            member.vacation_end = d
            # Regenerate schedule to exclude vacation days
            await generate_schedule(session)

    await update.message.reply_html(
        f"🌴 Vacation set: <b>{start.strftime('%d %b')}</b> → <b>{d.strftime('%d %b')}</b>\n"
        f"Schedule regenerated — you won't be assigned during this period."
    )
    context.user_data.clear()
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /mystats
# ─────────────────────────────────────────────────────────────────────────────

async def mystats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user

    async with get_session() as session:
        member = await _get_member(session, tg_user.id)
        if not member:
            await reply_text(update, "Use /join first.")
            return
        stats = await get_or_create_stats(session, member)
        text = (
            f"📊 <b>Your Statistics — {member.name}</b>\n\n"
            f"✅ Completed: <b>{stats.total_completed}</b>\n"
            f"❌ Missed: <b>{stats.total_missed}</b>\n"
            f"📈 Completion: <b>{stats.completion_percentage}%</b>\n"
            f"🔥 Current Streak: <b>{stats.current_streak}</b>\n"
            f"🏆 Longest Streak: <b>{stats.longest_streak}</b>\n"
            f"🔄 Handovers Given: <b>{stats.handovers_given}</b>\n"
            f"🙋 Handovers Taken: <b>{stats.handovers_taken}</b>"
        )

    await reply_html(update, text)


# ─────────────────────────────────────────────────────────────────────────────
# /leaderboard
# ─────────────────────────────────────────────────────────────────────────────

async def leaderboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        result = await session.execute(
            select(User, Statistics)
            .join(Statistics, Statistics.user_id == User.id)
            .where(User.active == True)
            .order_by(Statistics.total_completed.desc())
        )
        rows = result.all()

        if not rows:
            await reply_text(update, "No stats yet!")
            return

        medals = ["🥇", "🥈", "🥉"]
        lines = ["🏆 <b>Leaderboard</b>\n"]
        for i, (user, stats) in enumerate(rows):
            medal = medals[i] if i < 3 else f"{i+1}."
            lines.append(
                f"{medal} <b>{user.name}</b> — "
                f"✅{stats.total_completed} ❌{stats.total_missed} "
                f"📈{stats.completion_percentage}% 🔥{stats.current_streak}"
            )

    await reply_html(update, "\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# /whoisnext
# ─────────────────────────────────────────────────────────────────────────────

async def whoisnext_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = today_tz()
    today_start = datetime(today.year, today.month, today.day)

    async with get_session() as session:
        result = await session.execute(
            select(Assignment)
            .options(
                selectinload(Assignment.chore),
                selectinload(Assignment.user),
            )
            .where(
                Assignment.scheduled_date >= today_start,
                Assignment.status == AssignmentStatus.PENDING,
            )
            .order_by(Assignment.scheduled_date)
            .limit(20)
        )
        assignments = list(result.scalars().all())

        if not assignments:
            await reply_text(update, "No upcoming assignments found.")
            return

        lines = ["👀 <b>Who's Next?</b>\n"]
        for asgn in assignments:
            chore = asgn.chore
            user = asgn.user
            d = format_date(asgn.scheduled_date)
            lines.append(f"🧹 <b>{chore.name}</b> — {user.name} ({d})")

    await reply_html(update, "\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# /markdone — admin: credit someone who already did today's chore
# Standalone callbacks (not ConversationHandler) so taps are never swallowed.
# ─────────────────────────────────────────────────────────────────────────────

def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == settings.ADMIN_TELEGRAM_ID)


async def markdone_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show today's open assignments to credit as already done."""
    if not _is_admin(update):
        await reply_text(update, "⛔ This command is for admins only.")
        return

    today = today_tz()
    today_start = datetime(today.year, today.month, today.day, 0, 0, 0)
    today_end = today_start + timedelta(days=1)

    async with get_session() as session:
        result = await session.execute(
            select(Assignment)
            .options(
                selectinload(Assignment.chore),
                selectinload(Assignment.user),
            )
            .where(
                Assignment.scheduled_date >= today_start,
                Assignment.scheduled_date < today_end,
                Assignment.status.in_([
                    AssignmentStatus.PENDING,
                    AssignmentStatus.MISSED,
                    AssignmentStatus.HANDED_OVER,
                ]),
            )
            .order_by(Assignment.chore_id)
        )
        assignments = list(result.scalars().all())

        if not assignments:
            await reply_text(
                update,
                "No open assignments for today. Create a chore first with /addchore."
            )
            return

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        rows = []
        for asgn in assignments:
            label = f"{asgn.chore.name} → {asgn.user.name} ({asgn.status.value})"
            rows.append([
                InlineKeyboardButton(
                    label[:60],
                    callback_data=f"markdone_asgn:{asgn.id}",
                )
            ])
        rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
        keyboard = InlineKeyboardMarkup(rows)

    text = (
        "✅ <b>Mark Already Done</b>\n\n"
        "Someone already finished a chore today? Pick the assignment to match:\n"
        "<i>Next you'll choose who actually did it — rotation updates from them.</i>"
    )

    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await reply_html(update, text, reply_markup=keyboard)


async def markdone_select_assignment(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer("Admins only.", show_alert=True)
        return
    await query.answer()

    try:
        assignment_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Invalid selection.")
        return

    async with get_session() as session:
        asgn_res = await session.execute(
            select(Assignment)
            .options(selectinload(Assignment.chore), selectinload(Assignment.user))
            .where(Assignment.id == assignment_id)
        )
        assignment = asgn_res.scalars().first()
        if not assignment:
            await query.edit_message_text("❌ Assignment not found.")
            return

        members_res = await session.execute(
            select(User).where(User.active == True).order_by(User.name)
        )
        members = list(members_res.scalars().all())
        chore_name = assignment.chore.name
        current_name = assignment.user.name

    if not members:
        await query.edit_message_text("No active members.")
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    # Encode both ids in callback so we don't rely on conversation user_data
    rows = [
        [InlineKeyboardButton(
            f"{'✅ ' if m.name == current_name else ''}{m.name}",
            callback_data=f"markdone_user:{assignment_id}:{m.id}",
        )]
        for m in members
    ]
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    await query.edit_message_text(
        f"🧹 <b>{chore_name}</b> (currently → {current_name})\n\n"
        f"Who <b>already did</b> this chore today?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def markdone_select_member(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer("Admins only.", show_alert=True)
        return
    await query.answer()

    try:
        parts = query.data.split(":")
        # markdone_user:{assignment_id}:{user_id}
        assignment_id = int(parts[1])
        user_id = int(parts[2])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Invalid selection.")
        return

    try:
        async with get_session() as session:
            asgn_res = await session.execute(
                select(Assignment)
                .options(selectinload(Assignment.chore))
                .where(Assignment.id == assignment_id)
            )
            assignment = asgn_res.scalars().first()
            member_res = await session.execute(select(User).where(User.id == user_id))
            member = member_res.scalars().first()

            if not assignment or not member:
                await query.edit_message_text("❌ Could not update assignment.")
                return

            if assignment.status == AssignmentStatus.COMPLETED:
                await query.edit_message_text("✅ That chore is already marked done.")
                return

            # Reassign to the person who actually did it, then complete
            assignment.user_id = member.id
            assignment.status = AssignmentStatus.COMPLETED
            assignment.completed_at = datetime.utcnow()
            assignment.reminder_sent = True

            await record_completion(session, member)
            await generate_schedule(session)

            chore_name = assignment.chore.name
            member_name = member.name
            await notify_group_task_done(context.bot, member, assignment)

        await query.edit_message_text(
            f"✅ Matched! <b>{chore_name}</b> credited to <b>{member_name}</b>.\n"
            f"Schedule regenerated so the next rotation continues after them.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("markdone failed: %s", e, exc_info=True)
        try:
            await query.edit_message_text(
                "❌ Could not mark as done. Please try /markdone again."
            )
        except Exception:
            pass


async def _cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        try:
            await query.edit_message_text("❌ Cancelled.")
        except Exception:
            pass
    elif update.message:
        await update.message.reply_text("❌ Cancelled.")
    context.user_data.clear()
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# ConversationHandler builders
# ─────────────────────────────────────────────────────────────────────────────

def get_vacation_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("vacation", vacation_start),
            CallbackQueryHandler(vacation_start, pattern="^menu:vacation$"),
        ],
        states={
            VAC_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, vacation_start_date)],
            VAC_END: [MessageHandler(filters.TEXT & ~filters.COMMAND, vacation_end_date)],
        },
        fallbacks=[
            CallbackQueryHandler(_cancel_conversation, pattern="^cancel$"),
            CommandHandler("cancel", _cancel_conversation),
        ],
        per_message=False,
        allow_reentry=True,
    )


def get_handover_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handover_request_callback, pattern="^handover_request:"),
        ],
        states={
            HANDOVER_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, handover_reason_handler)],
        },
        fallbacks=[
            CallbackQueryHandler(_cancel_conversation, pattern="^cancel$"),
            CommandHandler("cancel", _cancel_conversation),
        ],
        per_message=False,
    )


