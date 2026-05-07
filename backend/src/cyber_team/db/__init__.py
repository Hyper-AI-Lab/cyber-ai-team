"""Database setup and session management."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from cyber_team.config import settings

engine = create_async_engine(settings.postgres_dsn, echo=False, pool_size=20, max_overflow=10)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE approval_requests ALTER COLUMN agent_id DROP NOT NULL"))
        await conn.execute(text("ALTER TABLE approval_requests DROP CONSTRAINT IF EXISTS approval_requests_agent_id_fkey"))


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
