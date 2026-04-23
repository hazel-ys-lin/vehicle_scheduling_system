"""Strawberry types mirroring the REST read shapes.

Kept intentionally close to `app/schemas/*` so clients comparing REST and
GraphQL payloads see matching field names (snake_case exposed as camelCase by
Strawberry's default `auto_camel_case`).
"""

from datetime import datetime
from enum import Enum

import strawberry


@strawberry.enum
class VehicleStatusGQL(Enum):
    IDLE = "idle"
    AT_YARD = "at_yard"
    AT_PLATFORM = "at_platform"
    TRAVERSING_BLOCK = "traversing_block"


@strawberry.enum
class PositionModeGQL(Enum):
    SIMULATION = "simulation"
    STRICT = "strict"


@strawberry.enum
class ConflictTypeGQL(Enum):
    INTERLOCKING = "interlocking"
    INSUFFICIENT_CHARGE = "insufficient_charge"
    LOW_BATTERY = "low_battery"
    VEHICLE_OVERLAP = "vehicle_overlap"
    VEHICLE_DISCONTINUITY = "vehicle_discontinuity"


@strawberry.type
class StopGQL:
    id: int
    sequence: int
    node_id: str
    arrival_time: datetime | None
    departure_time: datetime | None


@strawberry.type
class VehicleGQL:
    id: int
    name: str
    battery_level: float


@strawberry.type
class ServiceGQL:
    id: int
    vehicle_id: int
    departure_battery: float
    created_at: datetime
    updated_at: datetime
    stops: list[StopGQL]


@strawberry.type
class BlockGQL:
    id: str
    traversal_seconds: int
    interlocking_group_id: int | None
    bidirectional: bool


@strawberry.type
class PlatformGQL:
    id: str
    station: str


@strawberry.type
class InterlockingGroupGQL:
    id: int
    blocks: list[str]


@strawberry.type
class EdgeGQL:
    from_node: str
    to: str


@strawberry.type
class BatteryConstantsGQL:
    initial: float
    max: float
    min_departure: float
    threshold: float
    cost_per_block: float
    charge_rate_per_second: float


@strawberry.type
class TopologyGQL:
    yard: str
    platforms: list[PlatformGQL]
    blocks: list[BlockGQL]
    interlocking_groups: list[InterlockingGroupGQL]
    edges: list[EdgeGQL]
    battery: BatteryConstantsGQL


@strawberry.type
class VehiclePositionGQL:
    vehicle_id: int
    status: VehicleStatusGQL
    current_node: str
    next_node: str | None
    service_id: int | None
    enter_time: datetime | None
    exit_time: datetime | None
    battery_level: float


@strawberry.type
class ConflictGQL:
    conflict_type: ConflictTypeGQL
    service_ids: list[int]
    description: str
    locations: list[str]
