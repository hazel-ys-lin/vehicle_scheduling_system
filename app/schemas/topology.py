from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PlatformInfo(BaseModel):
    id: str
    station: str


class BlockInfo(BaseModel):
    id: str
    traversal_seconds: int
    interlocking_group_id: int | None
    bidirectional: bool


class InterlockingGroupInfo(BaseModel):
    id: int
    blocks: list[str]


class EdgeInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_node: str = Field(alias="from")
    to: str


class BatteryConstants(BaseModel):
    initial: float
    max: float
    min_departure: float
    threshold: float
    cost_per_block: float
    charge_rate_per_second: float


class TopologyRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    yard: str
    platforms: list[PlatformInfo]
    blocks: list[BlockInfo]
    interlocking_groups: list[InterlockingGroupInfo]
    edges: list[EdgeInfo]
    battery: BatteryConstants


class VehicleStatus(StrEnum):
    IDLE = "idle"
    AT_YARD = "at_yard"
    AT_PLATFORM = "at_platform"
    TRAVERSING_BLOCK = "traversing_block"


class VehiclePosition(BaseModel):
    vehicle_id: int
    status: VehicleStatus
    current_node: str
    next_node: str | None
    service_id: int | None
    enter_time: datetime | None
    exit_time: datetime | None
    battery_level: float
