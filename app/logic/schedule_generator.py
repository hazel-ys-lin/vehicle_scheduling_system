"""Auto-schedule generator (Bonus 3).

Greedy round-trip generator:
  - For each vehicle, enumerate candidate departure times starting at `start_time`
    and offset by `departure_interval_minutes / len(vehicles)` per additional
    vehicle so multiple vehicles don't collide at the yard in the same slot.
  - For each candidate, compute block and platform times using BlockConfig
    traversal_seconds and a configurable dwell time at each platform.
  - Reject the candidate if it introduces any INTERLOCKING or VEHICLE_*
    conflict with already-committed services.
  - After placement, verify passenger wait time ≤ departure_interval_minutes
    at every platform (per the Bonus 3 requirement).
"""

from datetime import datetime, timedelta

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.logic.battery import (
    battery_at,
    battery_before,
    emit_service_consume,
    emit_yard_charge,
    service_consume_delta,
)
from app.logic.conflict_detector import (
    ServiceSnapshot,
    detect_battery_conflicts,
    detect_block_conflicts,
    detect_vehicle_conflicts,
)
from app.logic.snapshots import build_snapshots
from app.models.block_config import BlockConfig
from app.models.service import Service, ServiceStop
from app.models.vehicle import Vehicle
from app.topology import (
    BATTERY_CHARGE_RATE,
    BATTERY_MAX,
    YARD,
    is_block,
    is_platform,
)

# Default round-trip path: Y → B1 → P1A → B3 → B5 → P2A → B6 → B7 → P3A
#                           → B10 → B11 → P2B → B12 → B13 → P1A → B1 → Y
DEFAULT_ROUND_TRIP: list[str] = [
    "Y",
    "B1",
    "P1A",
    "B3",
    "B5",
    "P2A",
    "B6",
    "B7",
    "P3A",
    "B10",
    "B11",
    "P2B",
    "B12",
    "B13",
    "P1A",
    "B1",
    "Y",
]

PLATFORM_DWELL_SECONDS: int = 120


class GenerateRequest(BaseModel):
    vehicle_ids: list[int] = Field(..., min_length=1)
    start_time: AwareDatetime
    end_time: AwareDatetime
    departure_interval_minutes: int = Field(30, gt=0)
    platform_dwell_seconds: int = Field(PLATFORM_DWELL_SECONDS, ge=0)

    @model_validator(mode="after")
    def end_after_start(self) -> "GenerateRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be strictly after start_time")
        return self


def _compute_stops(
    path: list[str],
    departure: datetime,
    block_traversal: dict[str, int],
    dwell_seconds: int,
) -> list[tuple[str, datetime, datetime]]:
    """Compute (node_id, enter, exit) for every node in the path."""
    stops: list[tuple[str, datetime, datetime]] = []
    current = departure
    for node in path:
        if is_block(node):
            traversal = timedelta(seconds=block_traversal.get(node, 60))
            stops.append((node, current, current + traversal))
            current = current + traversal
        else:
            dwell = timedelta(seconds=dwell_seconds) if is_platform(node) else timedelta(0)
            stops.append((node, current, current + dwell))
            current = current + dwell
    return stops


def _check_passenger_wait(snapshots: list[ServiceSnapshot], interval: timedelta) -> list[str]:
    """Verify that at every platform the gap between consecutive departures ≤ interval.

    Returns a list of error strings; empty means the constraint holds.
    """
    per_platform: dict[str, list[datetime]] = {}
    for snap in snapshots:
        for node_id, _enter, exit_ in snap.stops:
            if is_platform(node_id):
                per_platform.setdefault(node_id, []).append(exit_)

    errors: list[str] = []
    for platform, times in per_platform.items():
        times.sort()
        for t1, t2 in zip(times, times[1:], strict=False):
            gap = t2 - t1
            if gap > interval:
                errors.append(
                    f"Platform {platform}: gap between departures {t1.isoformat()} and "
                    f"{t2.isoformat()} is {gap}, exceeds interval {interval}."
                )
    return errors


async def generate_schedule(req: GenerateRequest, db: AsyncSession) -> list[Service]:
    """Generate and persist services; caller is responsible for commit()."""
    block_cfg_result = await db.execute(select(BlockConfig))
    block_traversal = {bc.block_id: bc.traversal_seconds for bc in block_cfg_result.scalars()}

    vehicles_result = await db.execute(select(Vehicle).where(Vehicle.id.in_(req.vehicle_ids)))
    vehicles = list(vehicles_result.scalars().all())
    found_ids = {v.id for v in vehicles}
    missing = [vid for vid in req.vehicle_ids if vid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Vehicles not found: {missing}",
        )

    existing_result = await db.execute(
        select(Service).options(selectinload(Service.stops), selectinload(Service.vehicle))
    )
    existing_services = list(existing_result.scalars().all())
    committed_snapshots: list[ServiceSnapshot] = build_snapshots(existing_services, block_traversal)

    interval = timedelta(minutes=req.departure_interval_minutes)
    per_vehicle_offset = interval / len(vehicles)

    created: list[Service] = []
    for idx, vehicle in enumerate(vehicles):
        slot = req.start_time + per_vehicle_offset * idx
        # Trip end of the most recently committed service for this vehicle, iff
        # it ended at the yard (so the vehicle is idle-charging until the next
        # departure). None means no prior service yet.
        last_yard_idle_since: datetime | None = None
        while slot < req.end_time:
            stop_tuples = _compute_stops(
                DEFAULT_ROUND_TRIP, slot, block_traversal, req.platform_dwell_seconds
            )
            trip_end = stop_tuples[-1][2]
            if trip_end > req.end_time:
                break

            # If the vehicle has been sitting at the yard since its previous
            # trip ended, credit that idle time as charging. We compute the
            # delta in memory first so we can reject this slot cleanly before
            # any event is written.
            charge_delta = 0.0
            if last_yard_idle_since is not None and slot > last_yard_idle_since:
                battery_at_idle_start = await battery_at(db, vehicle.id, last_yard_idle_since)
                gap_seconds = (slot - last_yard_idle_since).total_seconds()
                charge_available = gap_seconds * BATTERY_CHARGE_RATE
                charge_delta = max(
                    0.0,
                    min(charge_available, BATTERY_MAX - battery_at_idle_start),
                )

            dep_battery = await battery_before(db, vehicle.id, slot) + charge_delta

            candidate = ServiceSnapshot(
                service_id=-(len(created) + 1),
                vehicle_id=vehicle.id,
                stops=stop_tuples,
                departure_battery=dep_battery,
            )
            all_for_check = committed_snapshots + [candidate]
            if (
                detect_block_conflicts(all_for_check)
                or detect_vehicle_conflicts(all_for_check)
                or detect_battery_conflicts(candidate)
            ):
                slot += interval
                continue

            if charge_delta > 0:
                # occurred_at strictly before `slot` so battery_before(slot)
                # picks it up on subsequent reads.
                await emit_yard_charge(
                    db,
                    vehicle.id,
                    slot - timedelta(microseconds=1),
                    charge_delta,
                )

            svc = Service(vehicle_id=vehicle.id, departure_battery=dep_battery)
            db.add(svc)
            await db.flush()

            for seq, (node_id, enter, exit_) in enumerate(stop_tuples):
                is_blk = is_block(node_id)
                db.add(
                    ServiceStop(
                        service_id=svc.id,
                        sequence=seq,
                        node_id=node_id,
                        arrival_time=None if is_blk else enter,
                        departure_time=None if is_blk else exit_,
                    )
                )
            await emit_service_consume(
                db,
                vehicle_id=vehicle.id,
                service_id=svc.id,
                occurred_at=trip_end,
                delta=service_consume_delta(stop_tuples),
            )
            await db.flush()

            committed_snapshots.append(
                ServiceSnapshot(
                    service_id=svc.id,
                    vehicle_id=vehicle.id,
                    stops=stop_tuples,
                    departure_battery=dep_battery,
                )
            )
            created.append(svc)
            last_yard_idle_since = trip_end if stop_tuples[-1][0] == YARD else None
            slot += interval

    wait_errors = _check_passenger_wait(committed_snapshots, interval)
    if wait_errors:
        await db.rollback()
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Generated schedule cannot satisfy passenger wait time ≤ interval "
                    "with the given parameters; try a smaller interval or more vehicles."
                ),
                "errors": wait_errors,
            },
        )

    if not created:
        return []

    result = await db.execute(
        select(Service)
        .where(Service.id.in_([s.id for s in created]))
        .options(selectinload(Service.stops), selectinload(Service.vehicle))
    )
    return list(result.scalars().all())
