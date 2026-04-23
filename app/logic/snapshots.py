"""Build ServiceSnapshot objects from ORM Service instances.

Shared between the conflicts endpoint and the auto-schedule generator so that
both see the same view of a service (single source of truth for block times
and departure battery).

departure_battery is computed at write-time and cached on `Service.departure_battery`.
The authoritative ledger lives in the `battery_events` table; this projection is
rebuilt whenever a service is created or updated.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.conflict_detector import ServiceSnapshot
from app.models.block_config import BlockConfig
from app.models.service import Service
from app.topology import is_block


async def get_block_traversal(db: AsyncSession) -> dict[str, int]:
    """Single authoritative place to load {block_id: traversal_seconds} from DB."""
    result = await db.execute(select(BlockConfig))
    return {bc.block_id: bc.traversal_seconds for bc in result.scalars()}


def build_snapshot(svc: Service, block_traversal: dict[str, int]) -> ServiceSnapshot:
    """Build a snapshot for one loaded Service (stops must be eager-loaded)."""
    stops_data: list[tuple[str, datetime, datetime]] = []
    current_time: datetime | None = None

    for stop in svc.stops:
        if not is_block(stop.node_id):
            enter = stop.arrival_time or current_time
            exit_ = stop.departure_time or enter
            if enter and exit_:
                stops_data.append((stop.node_id, enter, exit_))
                current_time = exit_
        else:
            traversal_secs = block_traversal.get(stop.node_id, 60)
            if current_time:
                block_exit = current_time + timedelta(seconds=traversal_secs)
                stops_data.append((stop.node_id, current_time, block_exit))
                current_time = block_exit

    return ServiceSnapshot(
        service_id=svc.id,
        vehicle_id=svc.vehicle_id,
        stops=stops_data,
        departure_battery=svc.departure_battery,
    )


def build_snapshots(
    services: list[Service], block_traversal: dict[str, int]
) -> list[ServiceSnapshot]:
    return [build_snapshot(svc, block_traversal) for svc in services]
