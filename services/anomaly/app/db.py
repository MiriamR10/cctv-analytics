import re
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class Base(DeclarativeBase):
    pass


def _is_postgres() -> bool:
    return settings.database_url.startswith("postgresql")


def _schema() -> str | None:
    if not (settings.db_schema and _is_postgres()):
        return None
    if not IDENTIFIER.match(settings.db_schema):
        raise ValueError(f"Invalid DB_SCHEMA {settings.db_schema!r}")
    return settings.db_schema


def _connect_args() -> dict:
    schema = _schema()
    return {"server_settings": {"search_path": schema}} if schema else {}


engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=_connect_args(),
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    from app import models  # noqa: F401  (registers tables on Base.metadata)

    schema = _schema()
    if schema:
        # Must exist before the main engine connects: SQLAlchemy reads the
        # default schema (current_schema()) on the first connection.
        bootstrap = create_async_engine(settings.database_url)
        async with bootstrap.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        await bootstrap.dispose()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
