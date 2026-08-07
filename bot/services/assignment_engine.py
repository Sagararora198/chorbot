"""
Assignment Engine — core business logic.

Generates assignments for the next N days by rotating members fairly
across all enabled chores, respecting vacation mode.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import List

import pytz
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.models import (
    Assignment, AssignmentStatus, Chore, FrequencyType, User
)

logger = logging.getLogger(__name__)
TZ = pytz.timezone(settings.TIMEZONE)

# Maps weekday names → isoweekday integers (Monday=1 … Sunday=7)
WEEKDAY_MAP = {
    "Monday": 1, "Tuesday": 2, "Wednesday": 3, "Thursday": 4,
    "Friday": 5, "Saturday": 6, "Sunday": 7,
}


def _chore_occurs_on(chore: Chore, d: date) -> bool:
    """Return True if a chore is scheduled on a given date."""
    cfg = chore.frequency_config or {}

    if chore.frequency == FrequencyType.DAILY:
        return True

    if chore.frequency == FrequencyType.WEEKLY:
        weekday = cfg.get("weekday", "Sunday")
        return d.isoweekday() == WEEKDAY_MAP.get(weekday, 7)

    if chore.frequency == FrequencyType.MONTHLY:
        day_of_month = cfg.get("day_of_month", 1)
        return d.day == day_of_month

    if chore.frequency == FrequencyType.EVERY_N_DAYS:
        n = cfg.get("n", 1)
        epoch = cfg.get("start_date")
        if epoch:
            start = datetime.fromisoformat(epoch).date()
        else:
            start = date(2000, 1, 1)
        return (d - start).days % n == 0

    if chore.frequency == FrequencyType.SPECIFIC_WEEKDAYS:
        weekdays: List[str] = cfg.get("weekdays", [])
        return d.isoweekday() in [WEEKDAY_MAP.get(w, 0) for w in weekdays]

    return False


async def generate_schedule(session: AsyncSession) -> None:
    """
    Generate assignments for the next SCHEDULE_DAYS_AHEAD days.
    Deletes future PENDING assignments and regenerates them.
    Called once daily by the scheduler.
    """
    today = datetime.now(TZ).date()
    horizon = today + timedelta(days=settings.SCHEDULE_DAYS_AHEAD)

    # Fetch active members
    result = await session.execute(
        select(User).where(User.active == True)
    )
    members: List[User] = list(result.scalars().all())

    if not members:
        logger.warning("No active members — skipping schedule generation.")
        return

    # Fetch enabled chores
    result = await session.execute(
        select(Chore).where(Chore.enabled == True)
    )
    chores: List[Chore] = list(result.scalars().all())

    if not chores:
        logger.warning("No enabled chores — skipping schedule generation.")
        return

    # Delete pending future assignments only (keeps completed/missed history)
    await session.execute(
        delete(Assignment).where(
            Assignment.scheduled_date > datetime.combine(today, datetime.min.time()),
            Assignment.status == AssignmentStatus.PENDING,
        )
    )
    await session.flush()

    # For each chore track the "next member index" to keep rotation fair
    # Seed the index from the latest existing completed assignment
    chore_member_index: dict[int, int] = {}
    for chore in chores:
        # Find most recent completed assignment to know who was last
        res = await session.execute(
            select(Assignment)
            .where(
                Assignment.chore_id == chore.id,
                Assignment.status == AssignmentStatus.COMPLETED,
            )
            .order_by(Assignment.scheduled_date.desc())
            .limit(1)
        )
        last = res.scalars().first()
        if last and last.user_id in [m.id for m in members]:
            idx = [m.id for m in members].index(last.user_id)
            chore_member_index[chore.id] = (idx + 1) % len(members)
        else:
            chore_member_index[chore.id] = 0

    # Generate day-by-day assignments
    new_assignments: List[Assignment] = []
    current = today

    while current <= horizon:
        current_dt = datetime.combine(current, datetime.min.time())

        for chore in chores:
            if not _chore_occurs_on(chore, current):
                continue

            # Get available (non-vacation) members
            available = [
                m for m in members
                if not m.is_on_vacation(datetime.combine(current, datetime.min.time()))
            ]
            if not available:
                logger.warning(f"All members on vacation on {current} — skipping {chore.name}.")
                continue

            # Round-robin within available members starting from saved index
            full_idx = chore_member_index[chore.id]
            # Map global index to available list index
            member_ids = [m.id for m in members]
            available_ids = [m.id for m in available]

            # Rotate through all members, pick next available
            assigned_user = None
            for offset in range(len(members)):
                candidate_id = member_ids[(full_idx + offset) % len(members)]
                if candidate_id in available_ids:
                    assigned_user = next(m for m in available if m.id == candidate_id)
                    # Advance index past this member
                    chore_member_index[chore.id] = (member_ids.index(candidate_id) + 1) % len(members)
                    break

            if not assigned_user:
                continue

            new_assignments.append(
                Assignment(
                    chore_id=chore.id,
                    user_id=assigned_user.id,
                    scheduled_date=current_dt,
                    status=AssignmentStatus.PENDING,
                    reminder_sent=False,
                    overdue_notified=False,
                )
            )

        current += timedelta(days=1)

    session.add_all(new_assignments)
    await session.flush()
    logger.info(f"Generated {len(new_assignments)} assignments up to {horizon}.")
