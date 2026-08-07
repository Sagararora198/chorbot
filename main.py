"""
Roommate Chore Manager — Main entry point.

Wires up all handlers, starts the scheduler and runs the bot.
"""
from __future__ import annotations

import asyncio
import logging

from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler
)

from bot.config import settings
from bot.database import get_session, init_db
from bot.handlers import (
    # General
    start_handler, join_handler, help_handler,
    # Admin
    admin_handler, addmember_handler, deletechore_handler,
    regen_handler, backup_handler,
    get_addchore_conv, get_removemember_conv,
    # Member
    today_handler, upcoming_handler, mystats_handler,
    leaderboard_handler, whoisnext_handler,
    done_callback, handover_accept_callback, emergency_take_callback,
    schedule_callback,
    get_vacation_conv, get_handover_conv,
    # Callbacks
    admin_callback_router, chore_select_callback,
    delete_chore_confirm_callback, all_stats_handler,
)
from bot.scheduler import build_scheduler
from bot.services.assignment_engine import generate_schedule

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application) -> None:
    """Called after the application is initialised."""
    # Ensure all DB tables exist
    await init_db()
    logger.info("Database initialised.")

    # Generate initial schedule
    async with get_session() as session:
        await generate_schedule(session)
    logger.info("Initial schedule generated.")

    # Build and start the scheduler
    scheduler = build_scheduler(application.bot)
    scheduler.start()
    application.bot_data["scheduler"] = scheduler
    logger.info("Scheduler started.")


def main() -> None:
    settings.validate()

    app = (
        ApplicationBuilder()
        .token(settings.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ── General ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("join", join_handler))
    app.add_handler(CommandHandler("help", help_handler))

    # ── Admin commands ────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("admin", admin_handler))
    app.add_handler(CommandHandler("addmember", addmember_handler))
    app.add_handler(CommandHandler("deletechore", deletechore_handler))
    app.add_handler(CommandHandler("reset", regen_handler))
    app.add_handler(CommandHandler("backup", backup_handler))
    app.add_handler(CommandHandler("stats", all_stats_handler))

    # ── Conversation handlers (order matters — most specific first) ───────────
    app.add_handler(get_addchore_conv())
    app.add_handler(get_removemember_conv())
    app.add_handler(get_vacation_conv())
    app.add_handler(get_handover_conv())

    # ── Member commands ───────────────────────────────────────────────────────
    app.add_handler(CommandHandler("today", today_handler))
    app.add_handler(CommandHandler("upcoming", upcoming_handler))
    app.add_handler(CommandHandler("mystats", mystats_handler))
    app.add_handler(CommandHandler("leaderboard", leaderboard_handler))
    app.add_handler(CommandHandler("whoisnext", whoisnext_handler))
    app.add_handler(CommandHandler("schedule", upcoming_handler))

    # ── Inline callback handlers ──────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(done_callback, pattern="^done:"))
    app.add_handler(CallbackQueryHandler(handover_accept_callback, pattern="^handover_accept:"))
    app.add_handler(CallbackQueryHandler(emergency_take_callback, pattern="^emergency_take:"))
    app.add_handler(CallbackQueryHandler(schedule_callback, pattern="^schedule:"))
    app.add_handler(CallbackQueryHandler(admin_callback_router, pattern="^admin:"))
    app.add_handler(CallbackQueryHandler(chore_select_callback, pattern="^chore_select:"))
    app.add_handler(CallbackQueryHandler(delete_chore_confirm_callback, pattern="^delete_chore_confirm:"))

    # Main menu navigation
    app.add_handler(CallbackQueryHandler(today_handler, pattern="^menu:today$"))
    app.add_handler(CallbackQueryHandler(upcoming_handler, pattern="^menu:schedule$"))
    app.add_handler(CallbackQueryHandler(mystats_handler, pattern="^menu:mystats$"))
    app.add_handler(CallbackQueryHandler(leaderboard_handler, pattern="^menu:leaderboard$"))
    app.add_handler(CallbackQueryHandler(help_handler, pattern="^menu:help$"))

    if settings.WEBHOOK_URL:
        logger.info(f"🌐 Starting Webhook server on port {settings.PORT}…")
        app.run_webhook(
            listen="0.0.0.0",
            port=settings.PORT,
            url_path=settings.BOT_TOKEN,
            webhook_url=f"{settings.WEBHOOK_URL.rstrip('/')}/{settings.BOT_TOKEN}",
            allowed_updates=["message", "callback_query"],
        )
    else:
        logger.info("🤖 Roommate Chore Manager Bot starting (Polling mode)…")
        app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()

