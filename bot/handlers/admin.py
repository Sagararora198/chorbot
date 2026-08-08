"""
Admin handlers — add/remove members, chore CRUD, settings.
Uses ConversationHandler for guided multi-step flows.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)

from bot.database import get_session
from bot.keyboards.inline import (
    admin_menu_keyboard, cancel_keyboard, chore_list_keyboard,
    confirm_keyboard, frequency_keyboard, member_list_keyboard, weekday_keyboard
)
from bot.models import Chore, FrequencyType, Statistics, User
from bot.services.assignment_engine import generate_schedule
from bot.utils.helpers import admin_only, parse_time

logger = logging.getLogger(__name__)

# ── Conversation states ──────────────────────────────────────────────────────
# Add Chore flow
(
    AC_NAME, AC_DESC, AC_GROUP, AC_FREQ, AC_FREQ_CONFIG,
    AC_WEEKDAY_SELECT, AC_REMINDER_COUNT, AC_REMINDER_TIMES,
    AC_REMINDER_MSG, AC_CONFIRM
) = range(10)

# Remove member flow
RM_SELECT = 10


# ─────────────────────────────────────────────────────────────────────────────
# Admin panel entry
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(
        "🔧 <b>Admin Panel</b>\nChoose an action:",
        reply_markup=admin_menu_keyboard(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Add Member
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def addmember_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /addmember <telegram_id> <name>"""
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /addmember <telegram_id> <Full Name>\n"
            "Example: /addmember 123456789 Rahul Sharma"
        )
        return

    try:
        tg_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Telegram ID must be a number.")
        return

    name = " ".join(args[1:])

    async with get_session() as session:
        existing = (await session.execute(
            select(User).where(User.telegram_id == tg_id)
        )).scalars().first()

        if existing:
            existing.active = True
            existing.name = name
            msg = f"♻️ Member <b>{name}</b> reactivated."
        else:
            user = User(telegram_id=tg_id, name=name)
            session.add(user)
            await session.flush()
            session.add(Statistics(user_id=user.id))
            msg = f"✅ Member <b>{name}</b> added. They should now use /join in the bot to complete registration."

        # Regenerate schedule to include new member
        await generate_schedule(session)

    await update.message.reply_html(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Remove Member
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def removemember_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    async with get_session() as session:
        result = await session.execute(select(User).where(User.active == True))
        members = result.scalars().all()

    if not members:
        await update.message.reply_text("No active members found.")
        return ConversationHandler.END

    await update.message.reply_html(
        "Select the member to deactivate:",
        reply_markup=member_list_keyboard(members),
    )
    return RM_SELECT


async def removemember_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    member_id = int(query.data.split(":")[1])

    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == member_id))
        member = result.scalars().first()
        if member:
            member.active = False
            await generate_schedule(session)
            await query.edit_message_text(
                f"✅ <b>{member.name}</b> has been deactivated. Schedule regenerated.",
                parse_mode="HTML"
            )
        else:
            await query.edit_message_text("❌ Member not found.")

    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# Add Chore — Multi-step ConversationHandler
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def addchore_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data["chore"] = {}
    await update.message.reply_html(
        "🧹 <b>Add New Chore</b> — Step 1/9\n\n"
        "What is the <b>name</b> of the chore?\n"
        "<i>Example: Utensil Cleaning, Flat Cleaning</i>",
        reply_markup=cancel_keyboard(),
    )
    return AC_NAME


async def addchore_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["chore"]["name"] = update.message.text.strip()
    await update.message.reply_html(
        f"✅ Name: <b>{context.user_data['chore']['name']}</b>\n\n"
        "Step 2/9 — Enter a short <b>description</b> (or send /skip):",
        reply_markup=cancel_keyboard(),
    )
    return AC_DESC


async def addchore_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["chore"]["description"] = None if text == "/skip" else text
    await update.message.reply_html(
        "Step 3/9 — Enter a <b>group name</b> for this chore "
        "(e.g. Kitchen, Bathroom) or /skip:",
        reply_markup=cancel_keyboard(),
    )
    return AC_GROUP


async def addchore_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["chore"]["group_name"] = None if text == "/skip" else text
    await update.message.reply_html(
        "Step 4/9 — Choose the <b>frequency</b>:",
        reply_markup=frequency_keyboard(),
    )
    return AC_FREQ


async def addchore_freq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    freq_str = query.data.split(":")[1]
    context.user_data["chore"]["frequency"] = freq_str

    freq = FrequencyType(freq_str)

    if freq == FrequencyType.DAILY:
        context.user_data["chore"]["frequency_config"] = {}
        return await _ask_reminder_count(query, context)

    elif freq == FrequencyType.WEEKLY:
        await query.edit_message_text(
            "Step 5/9 — Which <b>day of the week</b>?",
            parse_mode="HTML",
            reply_markup=weekday_keyboard(),
        )
        context.user_data["chore"]["_selected_weekdays"] = []
        return AC_FREQ_CONFIG

    elif freq == FrequencyType.MONTHLY:
        await query.edit_message_text(
            "Step 5/9 — Which <b>day of the month</b>? (1–28)\n"
            "Example: 1 for the 1st of every month.",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return AC_FREQ_CONFIG

    elif freq == FrequencyType.EVERY_N_DAYS:
        await query.edit_message_text(
            "Step 5/9 — Every how many <b>days</b>? (e.g. 3 for every 3 days)",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return AC_FREQ_CONFIG

    elif freq == FrequencyType.SPECIFIC_WEEKDAYS:
        context.user_data["chore"]["_selected_weekdays"] = []
        await query.edit_message_text(
            "Step 5/9 — Select <b>which weekdays</b> (tap to toggle, then Done):",
            parse_mode="HTML",
            reply_markup=weekday_keyboard([]),
        )
        return AC_WEEKDAY_SELECT

    return ConversationHandler.END


async def addchore_freq_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text input for monthly/every_n_days, and inline for weekly."""
    freq = FrequencyType(context.user_data["chore"]["frequency"])

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        data = query.data

        if freq == FrequencyType.WEEKLY:
            weekday = data.split(":")[1]
            # SAFETY: weekday keyboard includes "Done Selecting" for multi-select;
            # weekly needs a single real weekday name.
            if weekday == "done" or weekday not in (
                "Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday",
            ):
                await query.answer("Please tap a weekday (Mon–Sun).", show_alert=True)
                return AC_FREQ_CONFIG
            context.user_data["chore"]["frequency_config"] = {"weekday": weekday}
            return await _ask_reminder_count(query, context)

    elif update.message:
        text = update.message.text.strip()

        if freq == FrequencyType.MONTHLY:
            try:
                day = int(text)
                assert 1 <= day <= 28
            except (ValueError, AssertionError):
                await update.message.reply_text("❌ Please enter a number between 1 and 28.")
                return AC_FREQ_CONFIG
            context.user_data["chore"]["frequency_config"] = {"day_of_month": day}
            await update.message.reply_text(
                f"✅ Monthly on day {day}.\n\n"
                "Step 6/9 — How many reminder times? (1–3)"
            )
            return AC_REMINDER_COUNT

        elif freq == FrequencyType.EVERY_N_DAYS:
            try:
                n = int(text)
                assert n >= 1
            except (ValueError, AssertionError):
                await update.message.reply_text("❌ Please enter a positive number.")
                return AC_FREQ_CONFIG
            context.user_data["chore"]["frequency_config"] = {
                "n": n,
                "start_date": datetime.utcnow().isoformat()
            }
            await update.message.reply_text(
                f"✅ Every {n} day(s).\n\nStep 6/9 — How many reminder times? (1–3)"
            )
            return AC_REMINDER_COUNT

    return AC_FREQ_CONFIG


async def addchore_weekday_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle multi-weekday selection for SPECIFIC_WEEKDAYS frequency."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "weekday:done":
        selected = context.user_data["chore"].get("_selected_weekdays", [])
        if not selected:
            await query.answer("Please select at least one day.", show_alert=True)
            return AC_WEEKDAY_SELECT
        context.user_data["chore"]["frequency_config"] = {"weekdays": selected}
        return await _ask_reminder_count(query, context)

    day = data.split(":")[1]
    selected: list[str] = context.user_data["chore"].setdefault("_selected_weekdays", [])
    if day in selected:
        selected.remove(day)
    else:
        selected.append(day)

    await query.edit_message_reply_markup(reply_markup=weekday_keyboard(selected))
    return AC_WEEKDAY_SELECT


async def _ask_reminder_count(msg_or_query, context) -> int:
    text = "Step 6/9 — How many <b>reminder times</b> does this chore need? (1–3)"
    if hasattr(msg_or_query, "edit_message_text"):
        await msg_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=cancel_keyboard())
    else:
        await msg_or_query.reply_html(text, reply_markup=cancel_keyboard())
    return AC_REMINDER_COUNT


async def addchore_reminder_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        count = int(update.message.text.strip())
        assert 1 <= count <= 3
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ Please enter 1, 2, or 3.")
        return AC_REMINDER_COUNT

    context.user_data["chore"]["_reminder_count"] = count
    context.user_data["chore"]["reminder_times"] = []
    await update.message.reply_html(
        f"Step 7/9 — Enter reminder time 1/{count} in <b>HH:MM</b> (24h) format:\n"
        "<i>Example: 09:00 for 9 AM, 20:30 for 8:30 PM</i>",
        reply_markup=cancel_keyboard(),
    )
    return AC_REMINDER_TIMES


async def addchore_reminder_times(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not parse_time(text):
        await update.message.reply_text("❌ Invalid format. Use HH:MM (e.g. 09:00)")
        return AC_REMINDER_TIMES

    times: list = context.user_data["chore"]["reminder_times"]
    times.append(text)
    count = context.user_data["chore"]["_reminder_count"]

    if len(times) < count:
        await update.message.reply_html(
            f"✅ Time {len(times)}: <b>{text}</b>\n\n"
            f"Enter reminder time {len(times)+1}/{count} (HH:MM):",
            reply_markup=cancel_keyboard(),
        )
        return AC_REMINDER_TIMES

    # All times collected
    await update.message.reply_html(
        f"✅ Reminder times: <b>{', '.join(times)}</b>\n\n"
        "Step 8/9 — Enter a custom <b>reminder message</b> (or /skip):\n"
        "<i>Example: Please wash dishes before 10 AM</i>",
        reply_markup=cancel_keyboard(),
    )
    return AC_REMINDER_MSG


async def addchore_reminder_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["chore"]["reminder_message"] = None if text == "/skip" else text

    chore_data = context.user_data["chore"]
    freq = chore_data["frequency"]
    times = ", ".join(chore_data.get("reminder_times", []))
    cfg = chore_data.get("frequency_config", {})

    summary = (
        f"📋 <b>Chore Summary — Step 9/9</b>\n\n"
        f"🧹 <b>Name:</b> {chore_data['name']}\n"
        f"📝 <b>Description:</b> {chore_data.get('description') or '—'}\n"
        f"🏷 <b>Group:</b> {chore_data.get('group_name') or '—'}\n"
        f"🔁 <b>Frequency:</b> {freq}\n"
        f"⚙️ <b>Config:</b> {cfg}\n"
        f"⏰ <b>Reminders:</b> {times}\n"
        f"💬 <b>Message:</b> {chore_data.get('reminder_message') or 'Default'}\n\n"
        f"Confirm to save?"
    )
    await update.message.reply_html(
        summary,
        reply_markup=confirm_keyboard("addchore_confirm", "addchore_cancel"),
    )
    return AC_CONFIRM


async def addchore_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "addchore_cancel":
        await query.edit_message_text("❌ Chore creation cancelled.")
        context.user_data.clear()
        return ConversationHandler.END

    chore_data = context.user_data["chore"]

    async with get_session() as session:
        chore = Chore(
            name=chore_data["name"],
            description=chore_data.get("description"),
            group_name=chore_data.get("group_name"),
            frequency=FrequencyType(chore_data["frequency"]),
            frequency_config=chore_data.get("frequency_config") or {},
            reminder_times=chore_data.get("reminder_times", []),
            reminder_message=chore_data.get("reminder_message"),
            enabled=True,
        )
        session.add(chore)
        await session.flush()
        # Regenerate schedule to include new chore
        await generate_schedule(session)

    await query.edit_message_text(
        f"✅ Chore <b>{chore_data['name']}</b> created and schedule updated!",
        parse_mode="HTML"
    )
    context.user_data.clear()
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# Delete Chore
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def deletechore_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        result = await session.execute(select(Chore))
        chores = result.scalars().all()

    if not chores:
        await update.message.reply_text("No chores found.")
        return

    await update.message.reply_html(
        "🗑 <b>Delete Chore</b>\nSelect the chore to delete:",
        reply_markup=chore_list_keyboard(chores),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Regenerate schedule
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def regen_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        await generate_schedule(session)
    await update.message.reply_text("✅ Schedule regenerated for the next 30 days!")


# ─────────────────────────────────────────────────────────────────────────────
# Backup
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def backup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path = Path("chorebot.db")
    if not db_path.exists():
        await update.message.reply_text("❌ Database file not found.")
        return
    backup_path = Path(f"backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy(db_path, backup_path)
    with open(backup_path, "rb") as f:
        await update.message.reply_document(document=f, filename=backup_path.name)
    backup_path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# ConversationHandler builders
# ─────────────────────────────────────────────────────────────────────────────

def get_addchore_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("addchore", addchore_start)],
        states={
            AC_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addchore_name)],
            AC_DESC: [MessageHandler(filters.TEXT, addchore_desc)],
            AC_GROUP: [MessageHandler(filters.TEXT, addchore_group)],
            AC_FREQ: [CallbackQueryHandler(addchore_freq, pattern="^freq:")],
            AC_FREQ_CONFIG: [
                CallbackQueryHandler(addchore_freq_config, pattern="^weekday:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, addchore_freq_config),
            ],
            AC_WEEKDAY_SELECT: [CallbackQueryHandler(addchore_weekday_select, pattern="^weekday:")],
            AC_REMINDER_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, addchore_reminder_count)],
            AC_REMINDER_TIMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, addchore_reminder_times)],
            AC_REMINDER_MSG: [MessageHandler(filters.TEXT, addchore_reminder_msg)],
            AC_CONFIRM: [CallbackQueryHandler(addchore_confirm, pattern="^addchore_")],
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            CallbackQueryHandler(lambda u, c: ConversationHandler.END, pattern="^cancel$"),
        ],
        allow_reentry=True,
        per_message=False,
    )


def get_removemember_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("removemember", removemember_start)],
        states={
            RM_SELECT: [CallbackQueryHandler(removemember_select, pattern="^member_select:")],
        },
        fallbacks=[
            CallbackQueryHandler(lambda u, c: ConversationHandler.END, pattern="^cancel$"),
        ],
        per_message=False,
    )
