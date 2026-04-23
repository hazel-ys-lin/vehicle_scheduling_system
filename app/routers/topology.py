"""Read-only topology + vehicle-position API (Bonus 2 backend support).

Client use cases:
- `/topology`: render the static graph (nodes, edges, groups) plus current
  `traversal_seconds` and domain battery constants in one call.
- `/positions?at=<iso>`: derive each vehicle's node + battery at a given
  instant, for simulation playback.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.logic.battery import non_service_battery
from app.logic.positions import PositionMode, compute_positions_at
from app.logic.snapshots import build_snapshot, get_block_traversal
from app.models.service import Service
from app.models.vehicle import Vehicle
from app.schemas.topology import (
    BatteryConstants,
    BlockInfo,
    EdgeInfo,
    InterlockingGroupInfo,
    PlatformInfo,
    TopologyRead,
    VehiclePosition,
)
from app.topology import (
    ADJACENCY,
    BATTERY_CHARGE_RATE,
    BATTERY_COST_PER_BLOCK,
    BATTERY_INITIAL,
    BATTERY_MAX,
    BATTERY_MIN_DEPARTURE,
    BATTERY_THRESHOLD,
    BIDIRECTIONAL_BLOCKS,
    BLOCKS,
    INTERLOCKING_GROUPS,
    PLATFORMS,
    YARD,
)

router = APIRouter(prefix="/topology", tags=["topology"])


def _station_of(platform: str) -> str:
    # "P1A" -> "S1" (positions 1 of the platform id is the station number)
    return f"S{platform[1]}"


@router.get("", response_model=TopologyRead)
async def get_topology(db: AsyncSession = Depends(get_db)):
    traversal = await get_block_traversal(db)

    group_id_for: dict[str, int] = {}
    groups: list[InterlockingGroupInfo] = []
    for idx, members in enumerate(INTERLOCKING_GROUPS, start=1):
        sorted_members = sorted(members)
        groups.append(InterlockingGroupInfo(id=idx, blocks=sorted_members))
        for b in sorted_members:
            group_id_for[b] = idx

    platforms = [PlatformInfo(id=p, station=_station_of(p)) for p in sorted(PLATFORMS)]
    blocks_out = [
        BlockInfo(
            id=b,
            traversal_seconds=traversal.get(b, 60),
            interlocking_group_id=group_id_for.get(b),
            bidirectional=b in BIDIRECTIONAL_BLOCKS,
        )
        for b in sorted(BLOCKS)
    ]
    edges = [
        EdgeInfo(from_node=src, to=dst)
        for src, dsts in ADJACENCY.items()
        for dst in dsts
    ]

    return TopologyRead(
        yard=YARD,
        platforms=platforms,
        blocks=blocks_out,
        interlocking_groups=groups,
        edges=edges,
        battery=BatteryConstants(
            initial=BATTERY_INITIAL,
            max=BATTERY_MAX,
            min_departure=BATTERY_MIN_DEPARTURE,
            threshold=BATTERY_THRESHOLD,
            cost_per_block=BATTERY_COST_PER_BLOCK,
            charge_rate_per_second=BATTERY_CHARGE_RATE,
        ),
    )


@router.get("/positions", response_model=list[VehiclePosition])
async def get_positions(
    at: Annotated[datetime, Query(description="ISO-8601 timezone-aware timestamp")],
    mode: Annotated[
        PositionMode,
        Query(
            description=(
                "simulation = linear-interp battery (smooth UI); "
                "strict = step-function battery (audit, matches conflict detector)"
            ),
        ),
    ] = PositionMode.SIMULATION,
    db: AsyncSession = Depends(get_db),
):
    if at.tzinfo is None:
        raise HTTPException(status_code=422, detail="`at` must be timezone-aware")

    vehicles = (await db.execute(select(Vehicle).order_by(Vehicle.id))).scalars().all()
    vehicles_base_battery = {v.id: await non_service_battery(db, v.id) for v in vehicles}

    services_result = await db.execute(
        select(Service).options(selectinload(Service.stops), selectinload(Service.vehicle))
    )
    traversal = await get_block_traversal(db)

    snapshots_by_vehicle: dict[int, list] = {}
    for svc in services_result.scalars():
        snap = build_snapshot(svc, traversal)
        snapshots_by_vehicle.setdefault(svc.vehicle_id, []).append(snap)

    positions = compute_positions_at(at, vehicles_base_battery, snapshots_by_vehicle, mode)
    return [
        VehiclePosition(
            vehicle_id=p.vehicle_id,
            status=p.status,
            current_node=p.current_node,
            next_node=p.next_node,
            service_id=p.service_id,
            enter_time=p.enter_time,
            exit_time=p.exit_time,
            battery_level=p.battery_level,
        )
        for p in positions
    ]
