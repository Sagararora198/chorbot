"""
Database engine and session factory.
Supports SQLite (local dev), PostgreSQL (Supabase, Neon, Render), etc.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
import urllib.parse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import settings
from bot.models import Base

db_url = settings.DATABASE_URL
connect_args = {}

if db_url.startswith("sqlite"):
    db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///")
    connect_args["check_same_thread"] = False
else:
    # Handle postgres:// or postgresql:// scheme
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Supabase / Neon SSL support
    if "sslmode=require" in db_url or "supabase" in db_url or "neon.tech" in db_url:
        connect_args["ssl"] = "require"

engine = create_async_engine(
    db_url,
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=True,  # Auto-reconnect if cloud connection drops
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def init_db() -> None:
    """Create all tables if they do not exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager that provides a DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
