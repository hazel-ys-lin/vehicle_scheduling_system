from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal, engine
from app.errors import register_exception_handlers
from app.graphql.router import graphql_router
from app.models import BlockConfig, Service, ServiceStop, Vehicle  # noqa: F401
from app.routers import blocks, schedule, services, topology, vehicles
from app.topology import BLOCKS


async def _seed_block_configs() -> None:
    """Upsert default BlockConfig rows (safe to call on every startup).

    Schema is managed by Alembic migrations; this only seeds configuration
    defaults for blocks the topology knows about.
    """
    rows = [{"block_id": b, "traversal_seconds": 60} for b in sorted(BLOCKS)]
    async with AsyncSessionLocal() as session:
        stmt = (
            pg_insert(BlockConfig).values(rows).on_conflict_do_nothing(index_elements=["block_id"])
        )
        await session.execute(stmt)
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _seed_block_configs()
    yield
    await engine.dispose()


app = FastAPI(title="Vehicle Scheduling System", version="1.0.0", lifespan=lifespan)

register_exception_handlers(app)

app.include_router(vehicles.router, prefix="/api/v1")
app.include_router(blocks.router, prefix="/api/v1")
app.include_router(services.router, prefix="/api/v1")
app.include_router(schedule.router, prefix="/api/v1")
app.include_router(topology.router, prefix="/api/v1")
app.include_router(graphql_router, prefix="/graphql")


@app.get("/health")
async def health():
    return {"status": "ok"}
