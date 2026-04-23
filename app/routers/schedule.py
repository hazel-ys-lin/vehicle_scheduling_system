from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.logic.conflict_detector import detect_all_conflicts
from app.logic.schedule_generator import GenerateRequest, generate_schedule
from app.logic.snapshots import build_snapshots, get_block_traversal
from app.models.service import Service
from app.schemas.service import ConflictRead, ServiceRead

router = APIRouter(prefix="/schedule", tags=["schedule"])


async def _load_all_services(db: AsyncSession) -> list[Service]:
    result = await db.execute(
        select(Service)
        .options(selectinload(Service.stops), selectinload(Service.vehicle))
        .order_by(Service.id)
    )
    return list(result.scalars().all())


_FAR_PAST = datetime.min.replace(tzinfo=UTC)


@router.get("", response_model=list[ServiceRead])
async def get_schedule(db: AsyncSession = Depends(get_db)):
    """Return all services ordered by the first stop's departure time."""
    services = await _load_all_services(db)

    def first_departure(svc: Service) -> datetime:
        for stop in svc.stops:
            if stop.departure_time:
                return stop.departure_time
        return _FAR_PAST

    return sorted(services, key=first_departure)


@router.get("/conflicts", response_model=list[ConflictRead])
async def get_conflicts(db: AsyncSession = Depends(get_db)):
    """Detect and return all conflicts across the current schedule."""
    services = await _load_all_services(db)
    block_traversal = await get_block_traversal(db)
    snapshots = build_snapshots(services, block_traversal)
    conflicts = detect_all_conflicts(snapshots)
    return [
        ConflictRead(
            conflict_type=c.conflict_type,
            service_ids=c.service_ids,
            description=c.description,
            locations=c.locations,
        )
        for c in conflicts
    ]


@router.post("/generate", response_model=list[ServiceRead], status_code=201)
async def auto_generate_schedule(payload: GenerateRequest, db: AsyncSession = Depends(get_db)):
    """Auto-generate conflict-free services and persist them."""
    services = await generate_schedule(payload, db)
    await db.commit()
    return services
