"""
Central configuration loader.
Reads all settings from environment variables (.env file).
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application-wide settings loaded from environment."""

    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_TELEGRAM_ID: int = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
    GROUP_CHAT_ID: int = int(os.getenv("GROUP_CHAT_ID", "0"))
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///chorebot.db")
    TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Kolkata")
    REMINDER_TIMEOUT_MINUTES: int = int(os.getenv("REMINDER_TIMEOUT_MINUTES", "60"))
    SCHEDULE_DAYS_AHEAD: int = int(os.getenv("SCHEDULE_DAYS_AHEAD", "30"))

    # Render / Webhook settings
    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")  # e.g. https://your-app.onrender.com
    PORT: int = int(os.getenv("PORT", "10000"))  # Render passes PORT dynamically

    @classmethod
    def validate(cls) -> None:
        """Raise if required settings are missing."""
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is not set in .env")
        if not cls.ADMIN_TELEGRAM_ID:
            raise ValueError("ADMIN_TELEGRAM_ID is not set in .env")


settings = Settings()
