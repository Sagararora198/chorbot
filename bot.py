from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application
import json
import random
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz
import asyncio
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

tz = pytz.timezone("Asia/Kolkata") 
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TIMES = ["morning", "night"]
DATA_FILE = 'data.json'

# Use AsyncIOScheduler instead of BackgroundScheduler
scheduler = AsyncIOScheduler(timezone=tz)

def load_data():
    try:
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            # Validate and fix data structure if needed
            if "users" not in data:
                data["users"] = []
            if "assignments" not in data:
                data["assignments"] = {day: {"morning": "", "night": ""} for day in DAYS}
            if "completed" not in data:
                data["completed"] = []
            if "unavailable" not in data:
                data["unavailable"] = []
            if "mode" not in data:
                data["mode"] = "auto"
            return data
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Error loading data file: {e}. Creating new data file.")
        # Create default data structure if file doesn't exist or is corrupted
        default_data = {
            "users": [],
            "assignments": {day: {"morning": "", "night": ""} for day in DAYS},
            "completed": [],
            "unavailable": [],
            "mode": "auto"
        }
        save_data(default_data)
        return default_data

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome to the Chore Bot!\nUse /join to be added to the rotation.")

async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    data = load_data()

    if any(u["username"] == user.username for u in data["users"]):
        await update.message.reply_text("You're already in the list.")
        return

    data["users"].append({"username": user.username, "user_id": user.id})
    save_data(data)
    await update.message.reply_text(f"✅ You have been added to the rotation, @{user.username}.")

# Assign a shift manually
async def setshift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 3:
        await update.message.reply_text("Usage: /setshift <day> <morning/night> <@username>")
        return

    day, time, user = context.args
    day = day.capitalize()
    time = time.lower()
    user = user.replace("@", "")

    if day not in DAYS or time not in TIMES:
        await update.message.reply_text("Invalid day or time.")
        return

    data = load_data()
    # Fix: Check if user exists in users list properly
    if not any(u["username"] == user for u in data["users"]):
        await update.message.reply_text(f"❌ User @{user} has not joined yet.")
        return

    data["assignments"][day][time] = user
    save_data(data)
    await update.message.reply_text(f"✅ Assigned @{user} to {day} {time} shift.")

# View the full schedule
async def viewshifts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    message = "📅 Weekly Shift Schedule:\n\n"
    for day in DAYS:
        message += f"{day}:\n"
        for time in TIMES:
            person = data["assignments"][day][time]
            message += f"  {time.capitalize()}: @{person if person else 'Unassigned'}\n"
        message += "\n"
    await update.message.reply_text(message)

# Auto assign all 7 days evenly
async def autoschedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    users = data["users"]
    if not users:
        await update.message.reply_text("❌ No users have joined yet.")
        return

    # Shuffle users for random fair distribution
    user_list = [u["username"] for u in users]
    random.shuffle(user_list)
    total_shifts = 7 * 2
    assignment_order = (user_list * ((total_shifts // len(user_list)) + 1))[:total_shifts]

    i = 0
    for day in DAYS:
        for time in TIMES:
            data["assignments"][day][time] = assignment_order[i]
            i += 1

    save_data(data)
    await update.message.reply_text("✅ Shifts have been auto-assigned evenly among users.")

async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user.username
    today = datetime.now(tz).strftime("%A")
    data = load_data()

    for time in TIMES:
        if data["assignments"][today][time] == user:
            data["completed"].append({
                "user": user,
                "day": today,
                "time": time,
                "timestamp": datetime.now(tz).isoformat()
            })
            save_data(data)
            await update.message.reply_text(f"✅ Thanks @{user}, you've completed the {time} shift today!")
            return

    await update.message.reply_text("❌ You are not assigned to any shift today.")

async def notavailable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user.username
    today = datetime.now(tz).strftime("%A")
    data = load_data()

    reassigned = False
    for time in TIMES:
        if data["assignments"][today][time] == user:
            if data["mode"] == "auto":
                # Find least loaded person
                user_list = [u["username"] for u in data["users"]]
                counts = {u: 0 for u in user_list}
                for d in DAYS:
                    for t in TIMES:
                        assigned = data["assignments"][d][t]
                        if assigned and assigned in counts:
                            counts[assigned] += 1
                counts[user] = 999  # exclude self
                new_user = min(counts, key=counts.get)
                data["assignments"][today][time] = new_user
                reassigned = True
                await update.message.reply_text(f"⚠️ @{user} is unavailable. Shift reassigned to @{new_user}.")
            else:
                data["assignments"][today][time] = ""
                await update.message.reply_text(f"⚠️ @{user} is unavailable. Anyone can take this shift using /take {today} {time}")
            save_data(data)
            return

    await update.message.reply_text("❌ You don't have a shift today.")

async def take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /take <day> <morning/night>")
        return

    day, time = context.args
    day = day.capitalize()
    time = time.lower()
    user = update.message.from_user.username

    if day not in DAYS or time not in TIMES:
        await update.message.reply_text("Invalid day or time.")
        return

    data = load_data()
    if data["assignments"][day][time] == "":
        data["assignments"][day][time] = user
        save_data(data)
        await update.message.reply_text(f"✅ @{user} has taken over the {time} shift on {day}.")
    else:
        await update.message.reply_text("❌ That shift is already assigned.")    

# Fix the notification system
async def send_reminder_job(application: Application, day: str, time: str):
    try:
        data = load_data()
        username = data["assignments"][day][time]
        
        if not username:
            logger.info(f"No user assigned for {day} {time}")
            return
            
        user_obj = next((u for u in data["users"] if u["username"] == username), None)
        if user_obj:
            user_id = user_obj["user_id"]
            message = f"⏰ Reminder: You are assigned to the {time} shift on {day}. Please reply with /done after completing it."
            
            await application.bot.send_message(chat_id=user_id, text=message)
            logger.info(f"✅ Sent reminder to user ID {user_id} ({username})")
        else:
            logger.warning(f"User {username} not found in users list")
            
    except Exception as e:
        logger.error(f"❌ Error sending reminder: {e}")

def schedule_reminders(application: Application):
    scheduler.remove_all_jobs()
    for day in DAYS:
        for time in TIMES:
            hour = 8 if time == "morning" else 15  # 8 AM for morning, 8 PM for night
            minute = 50
            
            # Map day names to cron day numbers
            day_mapping = {
                "Monday": "mon", "Tuesday": "tue", "Wednesday": "wed", 
                "Thursday": "thu", "Friday": "fri", "Saturday": "sat", "Sunday": "sun"
            }
            
            scheduler.add_job(
                send_reminder_job,
                trigger="cron",
                day_of_week=day_mapping[day],
                hour=hour,
                minute=minute,
                args=[application, day, time],
                id=f"{day}-{time}"
            )
            logger.info(f"Scheduled reminder for {day} {time} at {hour}:{minute:02d}")

async def main():
    # Build the application
    app = ApplicationBuilder().token("7803750356:AAHM0upUy91CFZ2EigRxd6lPKXWTWkVcl40").build()
    
    # Add command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("setshift", setshift))
    app.add_handler(CommandHandler("viewshifts", viewshifts))
    app.add_handler(CommandHandler("autoschedule", autoschedule))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(CommandHandler("notavailable", notavailable))
    app.add_handler(CommandHandler("take", take))
    
    # Schedule reminders
    schedule_reminders(app)
    scheduler.start()
    
    # Start the bot
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Keep the bot running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        scheduler.shutdown()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())