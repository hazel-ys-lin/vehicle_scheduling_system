from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.logic.battery import (
    battery_before,
    emit_service_consume,
    service_consume_delta,
)
from app.logic.conflict_detector import (
    ConflictType,
    ServiceSnapshot,
    detect_all_conflicts,
)
from app.logic.path_validator import validate_path
from app.logic.snapshots import build_snapshots, get_block_traversal
from app.models.battery_event import BatteryEvent, BatteryEventType
from app.models.service import Service, ServiceStop
from app.models.vehicle import Vehicle
from app.schemas.service import (
    ServiceCreate,
    ServiceRead,
    ServiceStopCreate,
    ServiceUpdate,
)
from app.topology import BATTERY_INITIAL, BLOCKS, PLATFORMS, YARD

router = APIRouter(prefix="/services", tags=["services"])

# Write-time conflicts that represent an impossible physical state for a single
# vehicle and must be rejected at the write boundary. Cross-service block /
# interlocking conflicts are reported via GET /schedule/conflicts but not
# rejected here, matching DESIGN §6.2.
WRITE_TIME_CONFLICTS = {
    ConflictType.VEHICLE_OVERLAP,
    ConflictType.VEHICLE_DISCONTINUITY,
}


def _timing_errors(stops: list[ServiceStopCreate]) -> list[str]:
    errors: list[str] = []
    for stop in stops:
        if stop.node_id in PLATFORMS or stop.node_id == YARD:
            if stop.arrival_time is None or stop.departure_time is None:
                errors.append(
                    f"Node '{stop.node_id}' (platform/yard) requires arrival_time "
                    "and departure_time."
                )
            elif stop.departure_time < stop.arrival_time:
                errors.append(f"Node '{stop.node_id}': departure_time must be >= arrival_time.")
        elif stop.node_id in BLOCKS:
            if stop.arrival_time is not None or stop.departure_time is not None:
                errors.append(
                    f"Node '{stop.node_id}' (block) must not carry arrival/departure "
                    "times; block timing is derived from BlockConfig."
                )
    return errors


def _compute_stop_tuples(
    stops: list[ServiceStopCreate], block_traversal: dict[str, int]
) -> list[tuple[str, datetime, datetime]]:
    """Expand a payload into (node_id, enter, exit) tuples used for conflict + events."""
    stops_data: list[tuple[str, datetime, datetime]] = []
    current: datetime | None = None
    for stop in stops:
        if stop.node_id in PLATFORMS or stop.node_id == YARD:
            enter = stop.arrival_time or current
            exit_ = stop.departure_time or enter
            if enter and exit_:
                stops_data.append((stop.node_id, enter, exit_))
                current = exit_
        elif stop.node_id in BLOCKS:
            traversal = block_traversal.get(stop.node_id, 60)
            if current:
                block_exit = current + timedelta(seconds=traversal)
                stops_data.append((stop.node_id, current, block_exit))
                current = block_exit
    return stops_data


async def _load_service(service_id: int, db: AsyncSession) -> Service:
    result = await db.execute(
        select(Service)
        .where(Service.id == service_id)
        .options(selectinload(Service.stops), selectinload(Service.vehicle))
    )
    service = result.scalar_one_or_none()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    return service


async def _load_vehicle_services(
    db: AsyncSession, vehicle_id: int, exclude_service_id: int | None = None
) -> list[Service]:
    stmt = (
        select(Service)
        .where(Service.vehicle_id == vehicle_id)
        .options(selectinload(Service.stops), selectinload(Service.vehicle))
    )
    if exclude_service_id is not None:
        stmt = stmt.where(Service.id != exclude_service_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _lock_vehicle(db: AsyncSession, vehicle_id: int) -> Vehicle | None:
    result = await db.execute(
        select(Vehicle).where(Vehicle.id == vehicle_id).with_for_update()
    )
    return result.scalar_one_or_none()


async def _raise_if_vehicle_conflict(
    db: AsyncSession,
    vehicle_id: int,
    pending_snapshot: ServiceSnapshot,
    *,
    exclude_service_id: int | None = None,
) -> None:
    siblings = await _load_vehicle_services(db, vehicle_id, exclude_service_id=exclude_service_id)
    if not siblings:
        return

    block_traversal = await get_block_traversal(db)
    sibling_snapshots = build_snapshots(siblings, block_traversal)
    all_snapshots = sibling_snapshots + [pending_snapshot]
    conflicts = [
        c for c in detect_all_conflicts(all_snapshots) if c.conflict_type in WRITE_TIME_CONFLICTS
    ]
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail=[
                {
                    "conflict_type": c.conflict_type.value,
                    "service_ids": c.service_ids,
                    "description": c.description,
                    "locations": c.locations,
                }
                for c in conflicts
            ],
        )


def _service_start(stop_tuples: list[tuple[str, datetime, datetime]]) -> datetime:
    return stop_tuples[0][1]


def _service_end(stop_tuples: list[tuple[str, datetime, datetime]]) -> datetime:
    return stop_tuples[-1][2]


@router.get("", response_model=list[ServiceRead])
async def list_services(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Service).options(selectinload(Service.stops)).order_by(Service.id)
    )
    return result.scalars().all()


@router.post("", response_model=ServiceRead, status_code=201)
async def create_service(payload: ServiceCreate, db: AsyncSession = Depends(get_db)):
    vehicle = await _lock_vehicle(db, payload.vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    node_ids = [s.node_id for s in payload.stops]
    all_errors = validate_path(node_ids) + _timing_errors(payload.stops)
    if all_errors:
        raise HTTPException(status_code=422, detail=all_errors)

    block_traversal = await get_block_traversal(db)
    stop_tuples = _compute_stop_tuples(payload.stops, block_traversal)
    start_at = _service_start(stop_tuples)
    end_at = _service_end(stop_tuples)

    # Departure battery = ledger state strictly before this service starts.
    dep_battery = await battery_before(db, payload.vehicle_id, start_at)

    candidate = ServiceSnapshot(
        service_id=0,
        vehicle_id=payload.vehicle_id,
        stops=stop_tuples,
        departure_battery=dep_battery,
    )
    await _raise_if_vehicle_conflict(db, payload.vehicle_id, candidate)

    service = Service(vehicle_id=payload.vehicle_id, departure_battery=dep_battery)
    db.add(service)
    await db.flush()

    for stop_data in payload.stops:
        db.add(ServiceStop(service_id=service.id, **stop_data.model_dump()))

    await emit_service_consume(
        db,
        vehicle_id=payload.vehicle_id,
        service_id=service.id,
        occurred_at=end_at,
        delta=service_consume_delta(stop_tuples),
    )

    await db.commit()
    return await _load_service(service.id, db)


@router.get("/{service_id}", response_model=ServiceRead)
async def get_service(service_id: int, db: AsyncSession = Depends(get_db)):
    return await _load_service(service_id, db)


@router.put("/{service_id}", response_model=ServiceRead)
async def update_service(
    service_id: int, payload: ServiceUpdate, db: AsyncSession = Depends(get_db)
):
    service = await _load_service(service_id, db)

    target_vehicle_id = payload.vehicle_id if payload.vehicle_id is not None else service.vehicle_id
    target_vehicle = await _lock_vehicle(db, target_vehicle_id)
    if not target_vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    if payload.stops is not None:
        node_ids = [s.node_id for s in payload.stops]
        all_errors = validate_path(node_ids) + _timing_errors(payload.stops)
        if all_errors:
            raise HTTPException(status_code=422, detail=all_errors)

    stops_for_check = payload.stops if payload.stops is not None else [
        ServiceStopCreate(
            sequence=s.sequence,
            node_id=s.node_id,
            arrival_time=s.arrival_time,
            departure_time=s.departure_time,
        )
        for s in service.stops
    ]

    reassigning = payload.vehicle_id is not None and payload.vehicle_id != service.vehicle_id
    block_traversal = await get_block_traversal(db)
    stop_tuples = _compute_stop_tuples(stops_for_check, block_traversal)

    if payload.stops is not None or reassigning:
        # Wipe this service's prior consume events so battery_before sees pre-update state.
        await db.execute(
            delete(BatteryEvent).where(
                BatteryEvent.service_id == service.id,
                BatteryEvent.event_type == BatteryEventType.SERVICE_CONSUME,
            )
        )
        await db.flush()

        start_at = _service_start(stop_tuples)
        end_at = _service_end(stop_tuples)
        dep_battery = await battery_before(db, target_vehicle_id, start_at)

        candidate = ServiceSnapshot(
            service_id=service.id,
            vehicle_id=target_vehicle_id,
            stops=stop_tuples,
            departure_battery=dep_battery or BATTERY_INITIAL,
        )
        await _raise_if_vehicle_conflict(
            db, target_vehicle_id, candidate, exclude_service_id=service.id
        )

        service.departure_battery = dep_battery

        await emit_service_consume(
            db,
            vehicle_id=target_vehicle_id,
            service_id=service.id,
            occurred_at=end_at,
            delta=service_consume_delta(stop_tuples),
        )

    if reassigning:
        service.vehicle_id = target_vehicle_id
        service.vehicle = target_vehicle

    if payload.stops is not None:
        for stop in service.stops:
            await db.delete(stop)
        await db.flush()
        for stop_data in payload.stops:
            db.add(ServiceStop(service_id=service.id, **stop_data.model_dump()))

    service.updated_at = datetime.now(UTC)
    await db.commit()
    return await _load_service(service_id, db)


@router.delete("/{service_id}", status_code=204)
async def delete_service(service_id: int, db: AsyncSession = Depends(get_db)):
    service = await _load_service(service_id, db)
    # battery_events with service_id FK cascade-delete.
    await db.delete(service)
    await db.commit()
