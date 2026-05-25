"""Database setup and session management."""

import asyncio
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from cyber_team.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.postgres_dsn,
    echo=False,
    pool_size=20,
    max_overflow=10,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def run_migrations() -> None:
    from alembic.config import Config

    from alembic import command

    backend_root = Path(__file__).resolve().parents[3]
    config_path = backend_root / "alembic.ini"
    script_location = backend_root / "alembic"
    alembic_cfg = Config(str(config_path))
    alembic_cfg.set_main_option("script_location", str(script_location))
    command.upgrade(alembic_cfg, "head")


async def init_db():
    if settings.database_migrations_on_startup:
        await asyncio.to_thread(run_migrations)
        return

    if settings.database_create_all_fallback:
        logger.warning("Using create_all database fallback; Alembic migrations are disabled")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
