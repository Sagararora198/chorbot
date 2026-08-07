"""
Database engine and session factory.
Supports SQLite (local dev), PostgreSQL (Supabase, Neon, Render), etc.
Handles special character URL-encoding in passwords, SSL certificate verification,
and PgBouncer statement_cache_size=0 automatically.
"""
from __future__ import annotations

import ssl
import urllib.parse
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import settings
from bot.models import Base


def _prepare_db_url(url_str: str) -> tuple[str, dict]:
    """Parse and format database URL for SQLAlchemy + async drivers."""
    connect_args = {}

    if url_str.startswith("sqlite"):
        formatted_url = url_str.replace("sqlite:///", "sqlite+aiosqlite:///")
        connect_args["check_same_thread"] = False
        return formatted_url, connect_args

    is_postgres = url_str.startswith(("postgres://", "postgresql://"))
    if url_str.startswith("postgres://"):
        formatted_url = url_str.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url_str.startswith("postgresql://"):
        formatted_url = url_str.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        formatted_url = url_str

    # Clean up query params if present (e.g. sslmode=require)
    scheme, rest = formatted_url.split("://", 1)
    if "?" in rest:
        rest, _ = rest.split("?", 1)

    # Safely URL-encode user and password if special chars like @ are present
    if "@" in rest:
        userinfo, hostinfo = rest.rsplit("@", 1)
        if ":" in userinfo:
            user, pwd = userinfo.split(":", 1)
            user_enc = urllib.parse.quote_plus(urllib.parse.unquote(user))
            pwd_enc = urllib.parse.quote_plus(urllib.parse.unquote(pwd))
            formatted_url = f"{scheme}://{user_enc}:{pwd_enc}@{hostinfo}"

    if is_postgres:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ctx
        # PgBouncer transaction mode compatibility
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0

    return formatted_url, connect_args


db_url, connect_args = _prepare_db_url(settings.DATABASE_URL)

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
