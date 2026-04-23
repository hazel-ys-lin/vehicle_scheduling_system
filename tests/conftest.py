"""Shared pytest fixtures for API integration tests.

Spins up a real Postgres container (once per session) via testcontainers,
applies alembic migrations against it, and truncates tables between tests.
This matches the production DB engine exactly, so timezone / JSONB / cascade
semantics tested here behave identically in prod.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from alembic import command
from app.config import settings
from app.database import get_db
from app.main import _seed_block_configs, app


@pytest.fixture(scope="session")
def _pg_container():
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg


@pytest.fixture(scope="session")
def engine(_pg_container):
    url = _pg_container.get_connection_url()
    # env.py reads settings.database_url; point it at the container before migrating
    settings.database_url = url
    command.upgrade(Config("alembic.ini"), "head")

    # NullPool: each checkout opens a fresh connection, so pytest-asyncio's
    # per-test event loops don't reuse asyncpg connections bound to a dead loop.
    eng = create_async_engine(url, echo=False, poolclass=NullPool)
    yield eng
    asyncio.run(eng.dispose())


@pytest.fixture(scope="session")
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def _truncate(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE battery_events, service_stops, services, vehicles, "
                "block_configs RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture(autouse=True)
async def _clean_db(engine) -> AsyncIterator[None]:
    # Truncate on both ends so a crashed prior session can't leak state into
    # the first test, and so the DB is clean after the run too.
    await _truncate(engine)
    yield
    await _truncate(engine)


@pytest.fixture
async def client(engine, session_factory, monkeypatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr("app.main.engine", engine)
    monkeypatch.setattr("app.main.AsyncSessionLocal", session_factory)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    await _seed_block_configs()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
