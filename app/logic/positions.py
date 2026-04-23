"""Derive each vehicle's location and battery at a given timestamp.

Pure function over ServiceSnapshot (no DB dependency). The snapshot already
encodes every stop's enter/exit times, so position at `at` is a scan over that
timeline.

Two battery modes are supported:
- PositionMode.SIMULATION (default): linear interpolation within a block so the
  UI renders smooth drain. Mid-block queries return fractional cost.
- PositionMode.STRICT: step function — cost is only applied once the block's
  exit_time has passed. Matches detect_battery_conflicts, suitable for audit /
  safety-critical displays.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.logic.conflict_detector import ServiceSnapshot
from app.schemas.topology import VehicleStatus
from app.topology import BATTERY_COST_PER_BLOCK, YARD, is_block, is_platform, is_yard


class PositionMode(StrEnum):
    SIMULATION = "simulation"
    STRICT = "strict"


@dataclass
class VehiclePositionData:
    vehicle_id: int
    status: VehicleStatus
    current_node: str
    next_node: str | None
    service_id: int | None
    enter_time: datetime | None
    exit_time: datetime | None
    battery_level: float


def _node_status(node_id: str) -> VehicleStatus:
    if is_yard(node_id):
        return VehicleStatus.AT_YARD
    if is_platform(node_id):
        return VehicleStatus.AT_PLATFORM
    if is_block(node_id):
        return VehicleStatus.TRAVERSING_BLOCK
    return VehicleStatus.IDLE  # should not happen for known topology


def _battery_cost_at(
    snapshots: list[ServiceSnapshot], at: datetime, mode: PositionMode
) -> float:
    """Cumulative battery drain across all this vehicle's snapshots by `at`.

    - A block whose exit_time <= at contributes a full BATTERY_COST_PER_BLOCK.
    - In SIMULATION mode, a block currently being traversed contributes a
      fractional cost proportional to time spent inside it so far.
    - In STRICT mode, a block currently being traversed contributes 0; cost
      lands atomically on exit.
    """
    total = 0.0
    for snap in snapshots:
        for node_id, enter, exit_ in snap.stops:
            if not is_block(node_id):
                continue
            if at >= exit_:
                total += BATTERY_COST_PER_BLOCK
            elif enter <= at < exit_ and mode is PositionMode.SIMULATION:
                span = (exit_ - enter).total_seconds()
                if span > 0:
                    total += BATTERY_COST_PER_BLOCK * ((at - enter).total_seconds() / span)
    return total


def _find_active(
    snapshot: ServiceSnapshot, at: datetime
) -> tuple[str, datetime, datetime, str | None] | None:
    """Return (node_id, enter, exit, next_node) for the stop covering `at`, or None."""
    stops = snapshot.stops
    for i, (node_id, enter, exit_) in enumerate(stops):
        if enter <= at < exit_:
            next_node = stops[i + 1][0] if i + 1 < len(stops) else None
            return node_id, enter, exit_, next_node
    return None


def _last_known_node(snapshots: list[ServiceSnapshot], at: datetime) -> str:
    """The last stop whose exit_time <= at (vehicle's last confirmed location)."""
    best_exit: datetime | None = None
    best_node = YARD
    for snap in snapshots:
        for node_id, _enter, exit_ in snap.stops:
            if exit_ <= at and (best_exit is None or exit_ > best_exit):
                best_exit = exit_
                best_node = node_id
    return best_node


def compute_positions_at(
    at: datetime,
    vehicles_base_battery: dict[int, float],
    snapshots_by_vehicle: dict[int, list[ServiceSnapshot]],
    mode: PositionMode = PositionMode.SIMULATION,
) -> list[VehiclePositionData]:
    """Compute position + battery at `at` for every known vehicle.

    `vehicles_base_battery`: {vehicle_id: stored battery_level}. Vehicles with
    no snapshots still appear in the output as IDLE at yard.
    """
    results: list[VehiclePositionData] = []
    for vehicle_id, base_battery in sorted(vehicles_base_battery.items()):
        snaps = snapshots_by_vehicle.get(vehicle_id, [])
        battery = max(0.0, base_battery - _battery_cost_at(snaps, at, mode))

        active: tuple[str, datetime, datetime, str | None] | None = None
        active_service_id: int | None = None
        for snap in snaps:
            hit = _find_active(snap, at)
            if hit is not None:
                active = hit
                active_service_id = snap.service_id
                break

        if active is not None:
            node_id, enter, exit_, next_node = active
            results.append(
                VehiclePositionData(
                    vehicle_id=vehicle_id,
                    status=_node_status(node_id),
                    current_node=node_id,
                    next_node=next_node,
                    service_id=active_service_id,
                    enter_time=enter,
                    exit_time=exit_,
                    battery_level=battery,
                )
            )
        else:
            last_node = _last_known_node(snaps, at)
            results.append(
                VehiclePositionData(
                    vehicle_id=vehicle_id,
                    status=VehicleStatus.IDLE,
                    current_node=last_node,
                    next_node=None,
                    service_id=None,
                    enter_time=None,
                    exit_time=None,
                    battery_level=battery,
                )
            )
    return results
