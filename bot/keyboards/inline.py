"""
All inline keyboards used throughout the bot.
Centralised here to avoid duplication.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ---------------------------------------------------------------------------
# Reminder buttons
# ---------------------------------------------------------------------------

def reminder_keyboard(assignment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Done", callback_data=f"done:{assignment_id}"),
            InlineKeyboardButton("🔄 Handover", callback_data=f"handover_request:{assignment_id}"),
        ]
    ])


def take_handover_keyboard(handover_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🙋 I'll Take It!", callback_data=f"handover_accept:{handover_id}")]
    ])


def emergency_takeover_keyboard(assignment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🙋 I'll Take It!", callback_data=f"emergency_take:{assignment_id}")]
    ])


# ---------------------------------------------------------------------------
# Swap buttons
# ---------------------------------------------------------------------------

def swap_response_keyboard(swap_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"swap_accept:{swap_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"swap_reject:{swap_id}"),
        ]
    ])


# ---------------------------------------------------------------------------
# Main menu / quick actions
# ---------------------------------------------------------------------------

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Today's Tasks", callback_data="menu:today"),
            InlineKeyboardButton("📅 Schedule", callback_data="menu:schedule"),
        ],
        [
            InlineKeyboardButton("📊 My Stats", callback_data="menu:mystats"),
            InlineKeyboardButton("🏆 Leaderboard", callback_data="menu:leaderboard"),
        ],
        [
            InlineKeyboardButton("🌴 Vacation Mode", callback_data="menu:vacation"),
            InlineKeyboardButton("❓ Help", callback_data="menu:help"),
        ],
    ])


def schedule_nav_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Today", callback_data="schedule:today"),
            InlineKeyboardButton("Tomorrow", callback_data="schedule:tomorrow"),
        ],
        [
            InlineKeyboardButton("This Week", callback_data="schedule:week"),
            InlineKeyboardButton("Next Week", callback_data="schedule:next_week"),
        ],
    ])


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Member", callback_data="admin:addmember"),
            InlineKeyboardButton("➖ Remove Member", callback_data="admin:removemember"),
        ],
        [
            InlineKeyboardButton("🧹 Add Chore", callback_data="admin:addchore"),
            InlineKeyboardButton("✏️ Edit Chore", callback_data="admin:editchore"),
        ],
        [
            InlineKeyboardButton("🗑 Delete Chore", callback_data="admin:deletechore"),
            InlineKeyboardButton("⚙️ Settings", callback_data="admin:settings"),
        ],
        [
            InlineKeyboardButton("📊 All Stats", callback_data="admin:stats"),
            InlineKeyboardButton("🔄 Regenerate Schedule", callback_data="admin:regen"),
        ],
    ])


def confirm_keyboard(confirm_data: str, cancel_data: str = "cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data=confirm_data),
            InlineKeyboardButton("❌ Cancel", callback_data=cancel_data),
        ]
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])


def frequency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📆 Daily", callback_data="freq:daily")],
        [InlineKeyboardButton("📅 Weekly", callback_data="freq:weekly")],
        [InlineKeyboardButton("🗓 Monthly", callback_data="freq:monthly")],
        [InlineKeyboardButton("🔁 Every N Days", callback_data="freq:every_n_days")],
        [InlineKeyboardButton("📌 Specific Weekdays", callback_data="freq:specific_weekdays")],
    ])


def weekday_keyboard(selected: list[str] | None = None) -> InlineKeyboardMarkup:
    selected = selected or []
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    rows = []
    for day in days:
        mark = "✅ " if day in selected else ""
        rows.append([InlineKeyboardButton(f"{mark}{day}", callback_data=f"weekday:{day}")])
    rows.append([InlineKeyboardButton("✅ Done Selecting", callback_data="weekday:done")])
    return InlineKeyboardMarkup(rows)


def chore_list_keyboard(chores: list) -> InlineKeyboardMarkup:
    """Dynamic keyboard listing all chores for selection."""
    rows = []
    for chore in chores:
        status = "🟢" if chore.enabled else "🔴"
        rows.append([
            InlineKeyboardButton(
                f"{status} {chore.name}",
                callback_data=f"chore_select:{chore.id}"
            )
        ])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def member_list_keyboard(members: list) -> InlineKeyboardMarkup:
    rows = []
    for m in members:
        rows.append([
            InlineKeyboardButton(m.name, callback_data=f"member_select:{m.id}")
        ])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)
