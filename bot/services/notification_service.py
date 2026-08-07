"""
Notification service — all message formatting and sending lives here.
Handlers call these functions; they never call bot.send_message directly.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import Bot, InlineKeyboardMarkup
from telegram.error import TelegramError

from bot.config import settings
from bot.models import Assignment, Handover, Swap, User

logger = logging.getLogger(__name__)


async def send_safe(bot: Bot, chat_id: int, text: str, **kwargs) -> None:
    """Send a message, logging errors instead of raising."""
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", **kwargs)
    except TelegramError as e:
        logger.error(f"Failed to send message to {chat_id}: {e}")


async def send_reminder(
    bot: Bot,
    assignment: Assignment,
    user: User,
    reminder_time: str,
) -> None:
    """Send a reminder message to a member for their assignment."""
    chore = assignment.chore
    msg_template = chore.reminder_message or "Please complete this task."
    date_str = assignment.scheduled_date.strftime("%d %b")

    text = (
        f"⏰ <b>Chore Reminder</b>\n\n"
        f"🧹 <b>{chore.name}</b>\n"
        f"👤 Assigned to: <b>{user.name}</b>\n"
        f"📅 Date: {date_str}  🕐 Time: {reminder_time}\n\n"
        f"💬 {msg_template}"
    )

    from bot.keyboards.inline import reminder_keyboard
    kb = reminder_keyboard(assignment.id)
    await send_safe(bot, user.telegram_id, text, reply_markup=kb)


async def notify_group_task_done(bot: Bot, user: User, assignment: Assignment) -> None:
    """Post completion notification to the group chat."""
    if not settings.GROUP_CHAT_ID:
        return
    chore = assignment.chore
    text = (
        f"✅ <b>{user.name}</b> completed <b>{chore.name}</b> "
        f"on {assignment.scheduled_date.strftime('%d %b')}! 🎉"
    )
    await send_safe(bot, settings.GROUP_CHAT_ID, text)


async def notify_group_handover_request(
    bot: Bot,
    from_user: User,
    assignment: Assignment,
    handover: Handover,
) -> None:
    """Broadcast handover request to group chat."""
    if not settings.GROUP_CHAT_ID:
        return
    chore = assignment.chore
    date_str = assignment.scheduled_date.strftime("%d %b")
    reason = handover.reason or "No reason given"
    text = (
        f"🔄 <b>Handover Request</b>\n\n"
        f"<b>{from_user.name}</b> wants to hand over:\n"
        f"🧹 <b>{chore.name}</b> ({date_str})\n"
        f"📝 Reason: {reason}\n\n"
        f"Who can take it? Tap below!"
    )
    from bot.keyboards.inline import take_handover_keyboard
    kb = take_handover_keyboard(handover.id)
    await send_safe(bot, settings.GROUP_CHAT_ID, text, reply_markup=kb)


async def notify_handover_accepted(
    bot: Bot,
    from_user: User,
    to_user: User,
    assignment: Assignment,
) -> None:
    """Notify both parties that the handover was accepted."""
    chore = assignment.chore
    date_str = assignment.scheduled_date.strftime("%d %b")
    text = (
        f"✅ <b>Shift Transferred</b>\n\n"
        f"🧹 {chore.name} ({date_str})\n"
        f"👤 {from_user.name} → {to_user.name}"
    )
    await send_safe(bot, from_user.telegram_id, text)
    await send_safe(bot, to_user.telegram_id, text)
    if settings.GROUP_CHAT_ID:
        await send_safe(bot, settings.GROUP_CHAT_ID, text)


async def notify_task_overdue(bot: Bot, user: User, assignment: Assignment) -> None:
    """Alert group that a task is overdue."""
    if not settings.GROUP_CHAT_ID:
        return
    chore = assignment.chore
    text = (
        f"⚠️ <b>Task Overdue!</b>\n\n"
        f"🧹 <b>{chore.name}</b>\n"
        f"👤 Assigned: {user.name}\n\n"
        f"Anyone available to take it?"
    )
    from bot.keyboards.inline import emergency_takeover_keyboard
    kb = emergency_takeover_keyboard(assignment.id)
    await send_safe(bot, settings.GROUP_CHAT_ID, text, reply_markup=kb)


async def notify_swap_request(
    bot: Bot,
    requester: User,
    target: User,
    assignment: Assignment,
    swap: Swap,
) -> None:
    """Notify target member of a swap request."""
    chore = assignment.chore
    date_str = assignment.scheduled_date.strftime("%d %b")
    text = (
        f"🔀 <b>Swap Request</b>\n\n"
        f"<b>{requester.name}</b> wants to swap:\n"
        f"🧹 {chore.name} ({date_str})\n\n"
        f"Do you accept?"
    )
    from bot.keyboards.inline import swap_response_keyboard
    kb = swap_response_keyboard(swap.id)
    await send_safe(bot, target.telegram_id, text, reply_markup=kb)


async def notify_weekly_report(bot: Bot, stats_by_user: list[dict]) -> None:
    """Post weekly summary to the group chat."""
    if not settings.GROUP_CHAT_ID:
        return
    lines = ["📊 <b>Weekly Chore Report</b>\n"]
    for entry in stats_by_user:
        pct = entry.get("pct", 0)
        stars = "⭐" * (5 if pct == 100 else 4 if pct >= 80 else 3 if pct >= 60 else 2 if pct >= 40 else 1)
        lines.append(
            f"👤 <b>{entry['name']}</b>\n"
            f"  ✅ Completed: {entry['completed']}   ❌ Missed: {entry['missed']}\n"
            f"  📈 {pct}%  {stars}\n"
        )
    await send_safe(bot, settings.GROUP_CHAT_ID, "\n".join(lines))
