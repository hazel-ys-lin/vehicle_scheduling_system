"""Conflict detection for the vehicle scheduling system.

Conflict types:
  1. INTERLOCKING — two services occupy the same block, or blocks within the same
     interlocking group, during overlapping time windows.
  2. LOW_BATTERY — a vehicle's simulated battery drops below BATTERY_THRESHOLD
     before returning to the Yard.
  3. INSUFFICIENT_CHARGE — a vehicle departs a service with battery < BATTERY_MIN_DEPARTURE.
  4. VEHICLE_OVERLAP — the same vehicle has two services whose time windows overlap.
  5. VEHICLE_DISCONTINUITY — the same vehicle's consecutive services are not
     position-continuous (end of service A != start of service B).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.topology import (
    BATTERY_COST_PER_BLOCK,
    BATTERY_MIN_DEPARTURE,
    BATTERY_THRESHOLD,
    YARD,
    interlocking_group_for,
    is_block,
)


class ConflictType(StrEnum):
    INTERLOCKING = "interlocking"
    LOW_BATTERY = "low_battery"
    INSUFFICIENT_CHARGE = "insufficient_charge"
    VEHICLE_OVERLAP = "vehicle_overlap"
    VEHICLE_DISCONTINUITY = "vehicle_discontinuity"


@dataclass
class BlockInterval:
    service_id: int
    vehicle_id: int
    node_id: str
    enter: datetime
    exit: datetime


@dataclass
class Conflict:
    conflict_type: ConflictType
    service_ids: list[int]
    description: str
    # Populated for INTERLOCKING conflicts: the sorted set of block ids on which
    # this service pair clashes. Empty for battery / vehicle conflicts.
    locations: list[str] = field(default_factory=list)


@dataclass
class ServiceSnapshot:
    """Minimal service data needed for conflict detection (no DB dependency)."""

    service_id: int
    vehicle_id: int
    # Ordered (node_id, enter_time, exit_time). Block times are computed from
    # BlockConfig traversal_seconds by the caller before passing here.
    stops: list[tuple[str, datetime, datetime]]
    departure_battery: float


def _intervals_overlap(
    a_enter: datetime, a_exit: datetime, b_enter: datetime, b_exit: datetime
) -> bool:
    return a_enter < b_exit and b_enter < a_exit


def detect_block_conflicts(snapshots: list[ServiceSnapshot]) -> list[Conflict]:
    """Detect simultaneous block occupancy and interlocking group violations.

    Two kinds of block conflicts:
    - Same block: any two services on the *same* block at overlapping times.
    - Interlocking group: two services on *different* blocks that share an
      interlocking group at overlapping times.

    Output is aggregated **per service pair**: if A and B clash across several
    blocks (or across several time windows on the same block), a single
    Conflict is emitted with `locations` listing every involved block. This
    keeps the response list bounded by O(pairs) rather than O(block_events).
    """
    block_intervals: list[BlockInterval] = []
    for snap in snapshots:
        for node_id, enter, exit_ in snap.stops:
            if is_block(node_id):
                block_intervals.append(
                    BlockInterval(snap.service_id, snap.vehicle_id, node_id, enter, exit_)
                )

    # Sort by enter so the inner loop can stop as soon as b.enter >= a.exit
    # (any later j has enter >= that one, so cannot overlap with a either).
    block_intervals.sort(key=lambda bi: bi.enter)

    pair_locations: dict[tuple[int, int], set[str]] = {}
    n = len(block_intervals)
    for i in range(n):
        a = block_intervals[i]
        for j in range(i + 1, n):
            b = block_intervals[j]
            if b.enter >= a.exit:
                break
            if a.service_id == b.service_id:
                continue
            if not _intervals_overlap(a.enter, a.exit, b.enter, b.exit):
                continue

            involved: set[str] | None = None
            if a.node_id == b.node_id:
                involved = {a.node_id}
            else:
                group_a = interlocking_group_for(a.node_id)
                if group_a and b.node_id in group_a:
                    involved = {a.node_id, b.node_id}

            if involved is not None:
                key = (
                    min(a.service_id, b.service_id),
                    max(a.service_id, b.service_id),
                )
                pair_locations.setdefault(key, set()).update(involved)

    conflicts: list[Conflict] = []
    for (sid_a, sid_b), locs in pair_locations.items():
        sorted_locs = sorted(locs)
        conflicts.append(
            Conflict(
                conflict_type=ConflictType.INTERLOCKING,
                service_ids=[sid_a, sid_b],
                description=(f"Services {sid_a} and {sid_b} conflict on block(s) {sorted_locs}."),
                locations=sorted_locs,
            )
        )
    return conflicts


def detect_battery_conflicts(snap: ServiceSnapshot) -> list[Conflict]:
    """Detect low-battery and insufficient-charge conflicts for a single service."""
    conflicts: list[Conflict] = []

    if snap.departure_battery < BATTERY_MIN_DEPARTURE:
        conflicts.append(
            Conflict(
                conflict_type=ConflictType.INSUFFICIENT_CHARGE,
                service_ids=[snap.service_id],
                description=(
                    f"Service {snap.service_id}: vehicle departs with battery "
                    f"{snap.departure_battery:.1f} < {BATTERY_MIN_DEPARTURE} required."
                ),
            )
        )

    battery = snap.departure_battery
    low_without_rescue = False

    for idx, (node_id, _enter, _exit) in enumerate(snap.stops):
        if is_block(node_id):
            battery -= BATTERY_COST_PER_BLOCK
            if battery < BATTERY_THRESHOLD and not _yard_reachable_after(snap.stops, idx):
                low_without_rescue = True

    if low_without_rescue:
        conflicts.append(
            Conflict(
                conflict_type=ConflictType.LOW_BATTERY,
                service_ids=[snap.service_id],
                description=(
                    f"Service {snap.service_id}: vehicle battery drops below "
                    f"{BATTERY_THRESHOLD} before returning to Yard."
                ),
            )
        )

    return conflicts


def _yard_reachable_after(stops: list[tuple[str, datetime, datetime]], current_index: int) -> bool:
    """Return True if Y appears anywhere after position current_index in the path."""
    return any(node_id == YARD for node_id, _, __ in stops[current_index + 1 :])


def _snap_window(snap: ServiceSnapshot) -> tuple[datetime, datetime] | None:
    """Return (first_enter, last_exit) across all stops, or None if empty."""
    if not snap.stops:
        return None
    return snap.stops[0][1], snap.stops[-1][2]


def detect_vehicle_conflicts(snapshots: list[ServiceSnapshot]) -> list[Conflict]:
    """Detect time-overlap and position-discontinuity conflicts for the same vehicle.

    - VEHICLE_OVERLAP: two services of the same vehicle have overlapping time windows.
    - VEHICLE_DISCONTINUITY: two consecutive (non-overlapping) services of the same
      vehicle are not position-continuous — the last node of A must equal the first
      node of B (otherwise the vehicle has teleported).
    """
    conflicts: list[Conflict] = []
    by_vehicle: dict[int, list[ServiceSnapshot]] = {}
    for snap in snapshots:
        if _snap_window(snap) is None:
            continue
        by_vehicle.setdefault(snap.vehicle_id, []).append(snap)

    for vehicle_id, snaps in by_vehicle.items():
        snaps_sorted = sorted(snaps, key=lambda s: _snap_window(s)[0])  # type: ignore[index]
        for i in range(len(snaps_sorted)):
            a = snaps_sorted[i]
            a_start, a_end = _snap_window(a)  # type: ignore[misc]
            for j in range(i + 1, len(snaps_sorted)):
                b = snaps_sorted[j]
                b_start, b_end = _snap_window(b)  # type: ignore[misc]
                if _intervals_overlap(a_start, a_end, b_start, b_end):
                    conflicts.append(
                        Conflict(
                            conflict_type=ConflictType.VEHICLE_OVERLAP,
                            service_ids=[a.service_id, b.service_id],
                            description=(
                                f"Vehicle {vehicle_id}: service {a.service_id} and "
                                f"service {b.service_id} time windows overlap."
                            ),
                        )
                    )

        for prev, nxt in zip(snaps_sorted, snaps_sorted[1:], strict=False):
            p_start, p_end = _snap_window(prev)  # type: ignore[misc]
            n_start, n_end = _snap_window(nxt)  # type: ignore[misc]
            if _intervals_overlap(p_start, p_end, n_start, n_end):
                continue  # already reported as overlap
            prev_last = prev.stops[-1][0]
            nxt_first = nxt.stops[0][0]
            if prev_last != nxt_first:
                conflicts.append(
                    Conflict(
                        conflict_type=ConflictType.VEHICLE_DISCONTINUITY,
                        service_ids=[prev.service_id, nxt.service_id],
                        description=(
                            f"Vehicle {vehicle_id}: service {prev.service_id} ends at "
                            f"'{prev_last}' but service {nxt.service_id} starts at "
                            f"'{nxt_first}' (must be continuous)."
                        ),
                    )
                )

    return conflicts


def detect_all_conflicts(snapshots: list[ServiceSnapshot]) -> list[Conflict]:
    """Run all conflict checks and return combined results."""
    all_conflicts: list[Conflict] = []
    all_conflicts.extend(detect_block_conflicts(snapshots))
    all_conflicts.extend(detect_vehicle_conflicts(snapshots))
    for snap in snapshots:
        all_conflicts.extend(detect_battery_conflicts(snap))
    return all_conflicts
