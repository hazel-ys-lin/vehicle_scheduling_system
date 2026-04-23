"""Event-sourced battery helpers.

Single source of truth: the `battery_events` ledger.

- `current_battery(db, vehicle_id)` → sum of all deltas so far.
- `battery_at(db, vehicle_id, at)` → sum of deltas with occurred_at <= at.
- `battery_before(db, vehicle_id, at)` → sum of deltas with occurred_at < at
  (useful for a service's departure_battery: events emitted at exactly the
  service's start must not be counted against that service itself).
- `emit_*` helpers append events. Caller is responsible for commit().
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.battery_event import BatteryEvent, BatteryEventType
from app.topology import BATTERY_COST_PER_BLOCK, BATTERY_INITIAL, is_block


async def _sum(
    db: AsyncSession, vehicle_id: int, upper: datetime | None, strict: bool
) -> float:
    stmt = select(func.coalesce(func.sum(BatteryEvent.delta), 0.0)).where(
        BatteryEvent.vehicle_id == vehicle_id
    )
    if upper is not None:
        stmt = stmt.where(
            BatteryEvent.occurred_at < upper if strict else BatteryEvent.occurred_at <= upper
        )
    result = await db.execute(stmt)
    return float(result.scalar_one())


async def current_battery(db: AsyncSession, vehicle_id: int) -> float:
    return await _sum(db, vehicle_id, upper=None, strict=False)


async def non_service_battery(db: AsyncSession, vehicle_id: int) -> float:
    """Sum of BASELINE + MANUAL_ADJUST deltas (excludes SERVICE_CONSUME).

    Used as the base for position computation: the positions module already
    applies per-block drain from snapshots, so including SERVICE_CONSUME events
    here would double-count.
    """
    stmt = select(func.coalesce(func.sum(BatteryEvent.delta), 0.0)).where(
        BatteryEvent.vehicle_id == vehicle_id,
        BatteryEvent.event_type != BatteryEventType.SERVICE_CONSUME,
    )
    result = await db.execute(stmt)
    return float(result.scalar_one())


async def battery_at(db: AsyncSession, vehicle_id: int, at: datetime) -> float:
    return await _sum(db, vehicle_id, upper=at, strict=False)


async def battery_before(db: AsyncSession, vehicle_id: int, at: datetime) -> float:
    return await _sum(db, vehicle_id, upper=at, strict=True)


async def emit_baseline(
    db: AsyncSession, vehicle_id: int, level: float, occurred_at: datetime
) -> None:
    db.add(
        BatteryEvent(
            vehicle_id=vehicle_id,
            service_id=None,
            event_type=BatteryEventType.BASELINE,
            occurred_at=occurred_at,
            delta=level,
        )
    )


async def emit_manual_adjust(
    db: AsyncSession, vehicle_id: int, new_level: float, occurred_at: datetime
) -> None:
    """Record a delta that moves current battery to `new_level`."""
    current = await current_battery(db, vehicle_id)
    delta = new_level - current
    db.add(
        BatteryEvent(
            vehicle_id=vehicle_id,
            service_id=None,
            event_type=BatteryEventType.MANUAL_ADJUST,
            occurred_at=occurred_at,
            delta=delta,
        )
    )


def service_consume_delta(stops: list[tuple[str, datetime, datetime]]) -> float:
    """Total battery consumption for a service path (-cost_per_block per block)."""
    block_count = sum(1 for node, _, _ in stops if is_block(node))
    return -BATTERY_COST_PER_BLOCK * block_count


async def emit_service_consume(
    db: AsyncSession,
    vehicle_id: int,
    service_id: int,
    occurred_at: datetime,
    delta: float,
) -> None:
    db.add(
        BatteryEvent(
            vehicle_id=vehicle_id,
            service_id=service_id,
            event_type=BatteryEventType.SERVICE_CONSUME,
            occurred_at=occurred_at,
            delta=delta,
        )
    )


DEFAULT_BASELINE = BATTERY_INITIAL
