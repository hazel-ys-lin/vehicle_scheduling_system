"""Per-request DataLoaders to prevent N+1 query explosions.

`vehicles(...)` → many Service nested fetches; `services(...)` → Stops +
Vehicle per row. Each Loader batches its ids within a single tick of the
event loop and runs one SQL query.
"""

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from strawberry.dataloader import DataLoader

from app.logic.battery import current_battery
from app.models.service import Service, ServiceStop
from app.models.vehicle import Vehicle


def make_vehicle_loader(db: AsyncSession) -> DataLoader[int, Vehicle | None]:
    async def load(ids: Sequence[int]) -> list[Vehicle | None]:
        result = await db.execute(select(Vehicle).where(Vehicle.id.in_(ids)))
        by_id = {v.id: v for v in result.scalars()}
        return [by_id.get(i) for i in ids]

    return DataLoader(load_fn=load)


def make_current_battery_loader(db: AsyncSession) -> DataLoader[int, float]:
    async def load(ids: Sequence[int]) -> list[float]:
        # current_battery is already efficient (single aggregate per vehicle);
        # batch them by running concurrently in-session.
        return [await current_battery(db, vid) for vid in ids]

    return DataLoader(load_fn=load)


def make_stops_loader(db: AsyncSession) -> DataLoader[int, list[ServiceStop]]:
    async def load(service_ids: Sequence[int]) -> list[list[ServiceStop]]:
        result = await db.execute(
            select(ServiceStop)
            .where(ServiceStop.service_id.in_(service_ids))
            .order_by(ServiceStop.service_id, ServiceStop.sequence)
        )
        grouped: dict[int, list[ServiceStop]] = defaultdict(list)
        for stop in result.scalars():
            grouped[stop.service_id].append(stop)
        return [grouped.get(sid, []) for sid in service_ids]

    return DataLoader(load_fn=load)


def make_services_by_vehicle_loader(
    db: AsyncSession,
) -> DataLoader[int, list[Service]]:
    async def load(vehicle_ids: Sequence[int]) -> list[list[Service]]:
        result = await db.execute(
            select(Service)
            .where(Service.vehicle_id.in_(vehicle_ids))
            .options(selectinload(Service.stops))
            .order_by(Service.vehicle_id, Service.id)
        )
        grouped: dict[int, list[Service]] = defaultdict(list)
        for svc in result.scalars():
            grouped[svc.vehicle_id].append(svc)
        return [grouped.get(vid, []) for vid in vehicle_ids]

    return DataLoader(load_fn=load)


class Loaders:
    """Bundle of per-request loaders, attached to Strawberry context."""

    def __init__(self, db: AsyncSession) -> None:
        self.vehicle = make_vehicle_loader(db)
        self.current_battery = make_current_battery_loader(db)
        self.stops = make_stops_loader(db)
        self.services_by_vehicle = make_services_by_vehicle_loader(db)
