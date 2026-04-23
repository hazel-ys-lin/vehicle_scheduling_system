"""Top-level GraphQL Query resolvers.

Reuse REST business logic (snapshots, positions, battery, conflict_detector)
so GraphQL and REST never drift. Per-request loaders live on the context and
are accessed via `info.context["loaders"]`.
"""

from datetime import UTC, datetime

import strawberry
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from strawberry.types import Info

from app.graphql.types import (
    BatteryConstantsGQL,
    BlockGQL,
    ConflictGQL,
    ConflictTypeGQL,
    EdgeGQL,
    InterlockingGroupGQL,
    PlatformGQL,
    PositionModeGQL,
    ServiceGQL,
    StopGQL,
    TopologyGQL,
    VehicleGQL,
    VehiclePositionGQL,
    VehicleStatusGQL,
)
from app.logic.battery import non_service_battery
from app.logic.conflict_detector import detect_all_conflicts
from app.logic.positions import PositionMode, compute_positions_at
from app.logic.snapshots import build_snapshot, build_snapshots, get_block_traversal
from app.models.block_config import BlockConfig
from app.models.service import Service
from app.models.vehicle import Vehicle
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


def _station_of(platform: str) -> str:
    return f"S{platform[1]}"


def _stop_to_gql(stop) -> StopGQL:
    return StopGQL(
        id=stop.id,
        sequence=stop.sequence,
        node_id=stop.node_id,
        arrival_time=stop.arrival_time,
        departure_time=stop.departure_time,
    )


def _service_to_gql(svc: Service) -> ServiceGQL:
    return ServiceGQL(
        id=svc.id,
        vehicle_id=svc.vehicle_id,
        departure_battery=svc.departure_battery,
        created_at=svc.created_at,
        updated_at=svc.updated_at,
        stops=[_stop_to_gql(s) for s in svc.stops],
    )


@strawberry.type
class Query:
    @strawberry.field
    async def vehicles(self, info: Info, id: int | None = None) -> list[VehicleGQL]:
        async with info.context["db_lock"]:
            db = info.context["db"]
            loaders = info.context["loaders"]

            stmt = select(Vehicle).order_by(Vehicle.id)
            if id is not None:
                stmt = stmt.where(Vehicle.id == id)
            result = await db.execute(stmt)
            vehicles = list(result.scalars().all())

            levels = await loaders.current_battery.load_many([v.id for v in vehicles])
            return [
                VehicleGQL(id=v.id, name=v.name, battery_level=lvl)
                for v, lvl in zip(vehicles, levels, strict=True)
            ]

    @strawberry.field
    async def services(
        self, info: Info, id: int | None = None, vehicle_id: int | None = None
    ) -> list[ServiceGQL]:
        async with info.context["db_lock"]:
            db = info.context["db"]
            stmt = (
                select(Service)
                .options(selectinload(Service.stops))
                .order_by(Service.id)
            )
            if id is not None:
                stmt = stmt.where(Service.id == id)
            if vehicle_id is not None:
                stmt = stmt.where(Service.vehicle_id == vehicle_id)
            result = await db.execute(stmt)
            return [_service_to_gql(s) for s in result.scalars()]

    @strawberry.field
    async def schedule(self, info: Info) -> list[ServiceGQL]:
        async with info.context["db_lock"]:
            db = info.context["db"]
            result = await db.execute(
                select(Service)
                .options(selectinload(Service.stops))
                .order_by(Service.id)
            )
            services = list(result.scalars().all())
            far_past = datetime.min.replace(tzinfo=UTC)

            def first_departure(svc: Service) -> datetime:
                for stop in svc.stops:
                    if stop.departure_time:
                        return stop.departure_time
                return far_past

            return [_service_to_gql(s) for s in sorted(services, key=first_departure)]

    @strawberry.field
    async def conflicts(self, info: Info) -> list[ConflictGQL]:
        async with info.context["db_lock"]:
            db = info.context["db"]
            result = await db.execute(
                select(Service)
                .options(selectinload(Service.stops), selectinload(Service.vehicle))
                .order_by(Service.id)
            )
            services = list(result.scalars().all())
            block_traversal = await get_block_traversal(db)
            snapshots = build_snapshots(services, block_traversal)
            return [
                ConflictGQL(
                    conflict_type=ConflictTypeGQL(c.conflict_type.value),
                    service_ids=c.service_ids,
                    description=c.description,
                    locations=c.locations,
                )
                for c in detect_all_conflicts(snapshots)
            ]

    @strawberry.field
    async def blocks(self, info: Info) -> list[BlockGQL]:
        async with info.context["db_lock"]:
            db = info.context["db"]
            result = await db.execute(select(BlockConfig).order_by(BlockConfig.block_id))
            rows = list(result.scalars())

            group_id_for: dict[str, int] = {}
            for idx, members in enumerate(INTERLOCKING_GROUPS, start=1):
                for b in members:
                    group_id_for[b] = idx

            return [
                BlockGQL(
                    id=bc.block_id,
                    traversal_seconds=bc.traversal_seconds,
                    interlocking_group_id=group_id_for.get(bc.block_id),
                    bidirectional=bc.block_id in BIDIRECTIONAL_BLOCKS,
                )
                for bc in rows
            ]

    @strawberry.field
    async def topology(self, info: Info) -> TopologyGQL:
        async with info.context["db_lock"]:
            db = info.context["db"]
            traversal = await get_block_traversal(db)

            group_id_for: dict[str, int] = {}
            groups: list[InterlockingGroupGQL] = []
            for idx, members in enumerate(INTERLOCKING_GROUPS, start=1):
                sorted_members = sorted(members)
                groups.append(InterlockingGroupGQL(id=idx, blocks=sorted_members))
                for b in sorted_members:
                    group_id_for[b] = idx

            platforms = [
                PlatformGQL(id=p, station=_station_of(p)) for p in sorted(PLATFORMS)
            ]
            blocks_out = [
                BlockGQL(
                    id=b,
                    traversal_seconds=traversal.get(b, 60),
                    interlocking_group_id=group_id_for.get(b),
                    bidirectional=b in BIDIRECTIONAL_BLOCKS,
                )
                for b in sorted(BLOCKS)
            ]
            edges = [
                EdgeGQL(from_node=src, to=dst)
                for src, dsts in ADJACENCY.items()
                for dst in dsts
            ]

            return TopologyGQL(
                yard=YARD,
                platforms=platforms,
                blocks=blocks_out,
                interlocking_groups=groups,
                edges=edges,
                battery=BatteryConstantsGQL(
                    initial=BATTERY_INITIAL,
                    max=BATTERY_MAX,
                    min_departure=BATTERY_MIN_DEPARTURE,
                    threshold=BATTERY_THRESHOLD,
                    cost_per_block=BATTERY_COST_PER_BLOCK,
                    charge_rate_per_second=BATTERY_CHARGE_RATE,
                ),
            )

    @strawberry.field
    async def positions(
        self,
        info: Info,
        at: datetime,
        mode: PositionModeGQL = PositionModeGQL.SIMULATION,
    ) -> list[VehiclePositionGQL]:
        if at.tzinfo is None:
            raise ValueError("`at` must be timezone-aware")

        async with info.context["db_lock"]:
            db = info.context["db"]

            vehicles = (
                await db.execute(select(Vehicle).order_by(Vehicle.id))
            ).scalars().all()
            vehicles_base_battery = {
                v.id: await non_service_battery(db, v.id) for v in vehicles
            }

            services_result = await db.execute(
                select(Service).options(
                    selectinload(Service.stops), selectinload(Service.vehicle)
                )
            )
            traversal = await get_block_traversal(db)
            snapshots_by_vehicle: dict[int, list] = {}
            for svc in services_result.scalars():
                snap = build_snapshot(svc, traversal)
                snapshots_by_vehicle.setdefault(svc.vehicle_id, []).append(snap)

            positions = compute_positions_at(
                at,
                vehicles_base_battery,
                snapshots_by_vehicle,
                PositionMode(mode.value),
            )
            return [
                VehiclePositionGQL(
                    vehicle_id=p.vehicle_id,
                    status=VehicleStatusGQL(p.status.value),
                    current_node=p.current_node,
                    next_node=p.next_node,
                    service_id=p.service_id,
                    enter_time=p.enter_time,
                    exit_time=p.exit_time,
                    battery_level=p.battery_level,
                )
                for p in positions
            ]
