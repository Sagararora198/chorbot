"""
Statistics service — computes and updates per-user stats.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List

import pytz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.models import Assignment, AssignmentStatus, Statistics, User

logger = logging.getLogger(__name__)
TZ = pytz.timezone(settings.TIMEZONE)


async def get_or_create_stats(session: AsyncSession, user: User) -> Statistics:
    """Fetch statistics row for a user, creating it if absent."""
    result = await session.execute(
        select(Statistics).where(Statistics.user_id == user.id)
    )
    stats = result.scalars().first()
    if not stats:
        stats = Statistics(user_id=user.id)
        session.add(stats)
        await session.flush()
    return stats


async def record_completion(session: AsyncSession, user: User) -> None:
    """Increment completed count and update streak."""
    stats = await get_or_create_stats(session, user)
    stats.total_completed += 1
    stats.current_streak += 1
    if stats.current_streak > stats.longest_streak:
        stats.longest_streak = stats.current_streak
    await session.flush()


async def record_missed(session: AsyncSession, user: User) -> None:
    """Increment missed count and reset streak."""
    stats = await get_or_create_stats(session, user)
    stats.total_missed += 1
    stats.current_streak = 0
    await session.flush()


async def record_handover_given(session: AsyncSession, user: User) -> None:
    stats = await get_or_create_stats(session, user)
    stats.handovers_given += 1
    await session.flush()


async def record_handover_taken(session: AsyncSession, user: User) -> None:
    stats = await get_or_create_stats(session, user)
    stats.handovers_taken += 1
    await session.flush()


async def get_weekly_report_data(session: AsyncSession) -> List[dict]:
    """Compute weekly stats for all active users."""
    now = datetime.now(TZ)
    week_start = now - timedelta(days=now.weekday())  # Monday
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    result = await session.execute(select(User).where(User.active == True))
    members: List[User] = list(result.scalars().all())

    report = []
    for member in members:
        completed_res = await session.execute(
            select(Assignment).where(
                Assignment.user_id == member.id,
                Assignment.status == AssignmentStatus.COMPLETED,
                Assignment.completed_at >= week_start.replace(tzinfo=None),
            )
        )
        missed_res = await session.execute(
            select(Assignment).where(
                Assignment.user_id == member.id,
                Assignment.status == AssignmentStatus.MISSED,
                Assignment.scheduled_date >= week_start.replace(tzinfo=None),
            )
        )
        completed = len(completed_res.scalars().all())
        missed = len(missed_res.scalars().all())
        total = completed + missed
        pct = round((completed / total) * 100, 1) if total else 0.0
        report.append({
            "name": member.name,
            "completed": completed,
            "missed": missed,
            "pct": pct,
        })

    return sorted(report, key=lambda x: -x["pct"])
