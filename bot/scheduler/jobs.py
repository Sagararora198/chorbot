"""
APScheduler job definitions.

Jobs:
  - send_chore_reminders: runs every minute, checks which reminders are due
  - check_overdue_tasks: runs every hour, detects missed tasks
  - regenerate_schedule: runs daily at midnight to regenerate 30-day assignments
  - send_weekly_report: runs every Sunday at 20:00 to post weekly summary
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

from bot.config import settings
from bot.database import get_session
from bot.models import Assignment, AssignmentStatus, User
from bot.services.assignment_engine import generate_schedule
from bot.services.notification_service import (
    notify_task_overdue, notify_weekly_report, send_reminder
)
from bot.services.stats_service import get_weekly_report_data, record_missed

from sqlalchemy import select
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)
TZ = pytz.timezone(settings.TIMEZONE)


async def send_chore_reminders_job(bot: Bot) -> None:
    """
    Runs every minute.
    Finds assignments whose reminder time falls within the current minute
    and sends the reminder if not already sent.
    """
    now = datetime.now(TZ)
    current_hhmm = now.strftime("%H:%M")
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0)

    async with get_session() as session:
        result = await session.execute(
            select(Assignment)
            .options(selectinload(Assignment.chore))
            .where(
                Assignment.scheduled_date >= today_start,
                Assignment.scheduled_date < today_start + timedelta(days=1),
                Assignment.status == AssignmentStatus.PENDING,
                Assignment.reminder_sent == False,
            )
        )
        assignments = result.scalars().all()

        for assignment in assignments:
            chore = assignment.chore
            reminder_times: list[str] = chore.reminder_times or []
            # reminder_index = next reminder slot to fire (supports multi-reminder chores)
            next_idx = assignment.reminder_index or 0
            if next_idx >= len(reminder_times):
                assignment.reminder_sent = True
                continue

            # Fire when current time matches the next due reminder (in configured order)
            if current_hhmm == reminder_times[next_idx]:
                user_res = await session.execute(
                    select(User).where(User.id == assignment.user_id)
                )
                user = user_res.scalars().first()
                if user:
                    await send_reminder(bot, assignment, user, current_hhmm)
                    assignment.reminder_index = next_idx + 1
                    # Only mark fully sent after the last configured reminder
                    if assignment.reminder_index >= len(reminder_times):
                        assignment.reminder_sent = True
                    logger.info(
                        f"Sent reminder {assignment.reminder_index}/{len(reminder_times)}: "
                        f"{chore.name} → {user.name} at {current_hhmm}"
                    )


async def check_overdue_tasks_job(bot: Bot) -> None:
    """
    Runs every hour.
    Marks tasks as MISSED if the last reminder + timeout has passed and still PENDING.
    Also sends overdue notification to the group.
    """
    timeout = settings.REMINDER_TIMEOUT_MINUTES
    now = datetime.now(TZ)
    cutoff = now - timedelta(minutes=timeout)
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0)
    cutoff_naive = cutoff.replace(tzinfo=None)

    async with get_session() as session:
        result = await session.execute(
            select(Assignment)
            .options(selectinload(Assignment.chore))
            .where(
                Assignment.scheduled_date >= today_start,
                Assignment.scheduled_date < today_start + timedelta(days=1),
                Assignment.status == AssignmentStatus.PENDING,
                Assignment.reminder_sent == True,
                Assignment.overdue_notified == False,
            )
        )
        assignments = result.scalars().all()

        for assignment in assignments:
            chore = assignment.chore
            reminder_times: list[str] = chore.reminder_times or []
            if not reminder_times:
                continue
            # Wait until AFTER the last reminder + timeout (not the first)
            last_rtime = max(reminder_times)
            h, m = map(int, last_rtime.split(":"))
            reminder_dt = datetime(now.year, now.month, now.day, h, m, 0)
            if reminder_dt <= cutoff_naive:
                assignment.status = AssignmentStatus.MISSED
                assignment.overdue_notified = True

                user_res = await session.execute(
                    select(User).where(User.id == assignment.user_id)
                )
                user = user_res.scalars().first()
                if user:
                    await record_missed(session, user)
                    await notify_task_overdue(bot, user, assignment)
                    logger.info(f"Marked overdue: {chore.name} for {user.name}")


async def regenerate_schedule_job() -> None:
    """Runs daily at midnight to regenerate the 30-day assignment schedule."""
    logger.info("Regenerating schedule…")
    async with get_session() as session:
        await generate_schedule(session)
    logger.info("Schedule regeneration complete.")


async def send_weekly_report_job(bot: Bot) -> None:
    """Runs every Sunday evening to send the weekly summary to the group."""
    async with get_session() as session:
        data = await get_weekly_report_data(session)
    await notify_weekly_report(bot, data)
    logger.info("Weekly report sent.")


def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Build and configure the APScheduler instance."""
    scheduler = AsyncIOScheduler(timezone=TZ)

    # Every minute: send reminders
    scheduler.add_job(
        send_chore_reminders_job,
        trigger="cron",
        minute="*",
        id="send_reminders",
        args=[bot],
        replace_existing=True,
        misfire_grace_time=30,
    )

    # Every hour: check overdue
    scheduler.add_job(
        check_overdue_tasks_job,
        trigger="cron",
        minute=5,
        id="check_overdue",
        args=[bot],
        replace_existing=True,
        misfire_grace_time=120,
    )

    # Daily at 00:01: regenerate schedule
    scheduler.add_job(
        regenerate_schedule_job,
        trigger="cron",
        hour=0,
        minute=1,
        id="regen_schedule",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Every Sunday at 20:00: weekly report
    scheduler.add_job(
        send_weekly_report_job,
        trigger="cron",
        day_of_week="sun",
        hour=20,
        minute=0,
        id="weekly_report",
        args=[bot],
        replace_existing=True,
        misfire_grace_time=600,
    )

    return scheduler
