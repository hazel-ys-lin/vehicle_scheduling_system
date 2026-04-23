from datetime import UTC, datetime, timedelta

from app.logic.conflict_detector import (
    ConflictType,
    ServiceSnapshot,
    detect_all_conflicts,
    detect_battery_conflicts,
    detect_block_conflicts,
)
from app.topology import BATTERY_MIN_DEPARTURE

T0 = datetime(2024, 1, 1, 8, 0, tzinfo=UTC)


def t(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def make_snap(
    service_id: int,
    vehicle_id: int,
    stops: list[tuple[str, datetime, datetime]],
    departure_battery: float = 80.0,
) -> ServiceSnapshot:
    return ServiceSnapshot(
        service_id=service_id,
        vehicle_id=vehicle_id,
        stops=stops,
        departure_battery=departure_battery,
    )


class TestBlockConflicts:
    # --- Interlocking group (different blocks, same group) ---

    def test_no_conflict_non_overlapping_same_group(self):
        # B1 and B2 are in the same interlocking group but time windows don't overlap
        s1 = make_snap(1, 1, [("B1", t(0), t(1))])
        s2 = make_snap(2, 2, [("B2", t(2), t(3))])
        assert detect_block_conflicts([s1, s2]) == []

    def test_conflict_interlocking_group1_b1_b2(self):
        s1 = make_snap(1, 1, [("B1", t(0), t(2))])
        s2 = make_snap(2, 2, [("B2", t(1), t(3))])
        conflicts = detect_block_conflicts([s1, s2])
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == ConflictType.INTERLOCKING
        assert 1 in conflicts[0].service_ids and 2 in conflicts[0].service_ids
        assert conflicts[0].locations == ["B1", "B2"]

    def test_conflict_interlocking_group2_b3_b4(self):
        s1 = make_snap(1, 1, [("B3", t(0), t(5))])
        s2 = make_snap(2, 2, [("B4", t(2), t(7))])
        assert len(detect_block_conflicts([s1, s2])) == 1

    def test_conflict_interlocking_group3_b7_b10(self):
        s1 = make_snap(1, 1, [("B7", t(0), t(10))])
        s2 = make_snap(2, 2, [("B10", t(5), t(15))])
        assert len(detect_block_conflicts([s1, s2])) == 1

    def test_no_conflict_blocks_in_different_groups(self):
        # B1 (group1) and B3 (group2) can overlap freely
        s1 = make_snap(1, 1, [("B1", t(0), t(5))])
        s2 = make_snap(2, 2, [("B3", t(0), t(5))])
        assert detect_block_conflicts([s1, s2]) == []

    def test_touching_but_not_overlapping(self):
        # [0, 2) and [2, 4) share endpoint but do not overlap
        s1 = make_snap(1, 1, [("B1", t(0), t(2))])
        s2 = make_snap(2, 2, [("B2", t(2), t(4))])
        assert detect_block_conflicts([s1, s2]) == []

    def test_multiple_group_conflicts(self):
        # Same pair conflicting on two distinct groups aggregates into one
        # Conflict with both blocks listed in locations.
        s1 = make_snap(1, 1, [("B3", t(0), t(10)), ("B7", t(10), t(20))])
        s2 = make_snap(2, 2, [("B4", t(5), t(15)), ("B8", t(15), t(25))])
        conflicts = detect_block_conflicts([s1, s2])
        assert len(conflicts) == 1
        assert set(conflicts[0].locations) == {"B3", "B4", "B7", "B8"}

    # --- Same-block occupancy (applies to ALL blocks, including non-interlocked) ---

    def test_conflict_same_block_non_interlocked_b5(self):
        # B5 is not in any interlocking group — simultaneous occupancy is still a conflict
        s1 = make_snap(1, 1, [("B5", t(0), t(5))])
        s2 = make_snap(2, 2, [("B5", t(3), t(8))])
        conflicts = detect_block_conflicts([s1, s2])
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == ConflictType.INTERLOCKING
        assert conflicts[0].locations == ["B5"]

    def test_conflict_same_block_non_interlocked_b6(self):
        s1 = make_snap(1, 1, [("B6", t(0), t(5))])
        s2 = make_snap(2, 2, [("B6", t(0), t(5))])
        assert len(detect_block_conflicts([s1, s2])) == 1

    def test_conflict_same_block_non_interlocked_b11(self):
        s1 = make_snap(1, 1, [("B11", t(0), t(10))])
        s2 = make_snap(2, 2, [("B11", t(5), t(15))])
        assert len(detect_block_conflicts([s1, s2])) == 1

    def test_conflict_same_block_non_interlocked_b12(self):
        s1 = make_snap(1, 1, [("B12", t(0), t(5))])
        s2 = make_snap(2, 2, [("B12", t(2), t(7))])
        assert len(detect_block_conflicts([s1, s2])) == 1

    def test_no_conflict_same_non_interlocked_block_non_overlapping(self):
        s1 = make_snap(1, 1, [("B5", t(0), t(2))])
        s2 = make_snap(2, 2, [("B5", t(3), t(5))])
        assert detect_block_conflicts([s1, s2]) == []

    def test_same_service_sequential_same_block_no_conflict(self):
        # A round-trip visits B1 twice; the two B1 windows must not overlap
        s1 = make_snap(1, 1, [("B1", t(0), t(1)), ("B1", t(10), t(11))])
        assert detect_block_conflicts([s1]) == []


class TestBatteryConflicts:
    def test_sufficient_charge_no_conflict(self):
        stops = [
            ("Y", t(0), t(0)),
            ("B1", t(0), t(1)),
            ("P1A", t(1), t(5)),
            ("B1", t(5), t(6)),
            ("Y", t(6), t(6)),
        ]
        snap = make_snap(1, 1, stops, departure_battery=80.0)
        assert detect_battery_conflicts(snap) == []

    def test_insufficient_charge_at_departure(self):
        stops = [("Y", t(0), t(0)), ("B1", t(0), t(1)), ("P1A", t(1), t(5))]
        snap = make_snap(1, 1, stops, departure_battery=79.9)
        conflicts = detect_battery_conflicts(snap)
        assert ConflictType.INSUFFICIENT_CHARGE in [c.conflict_type for c in conflicts]

    def test_exactly_at_min_departure_no_conflict(self):
        stops = [("B1", t(0), t(1))]
        snap = make_snap(1, 1, stops, departure_battery=BATTERY_MIN_DEPARTURE)
        assert not any(
            c.conflict_type == ConflictType.INSUFFICIENT_CHARGE
            for c in detect_battery_conflicts(snap)
        )

    def test_low_battery_no_yard_after(self):
        # Battery 31 → after 2 blocks = 29 < 30 threshold, no Y after
        stops = [
            ("P1A", t(0), t(0)),
            ("B3", t(0), t(1)),
            ("B5", t(1), t(2)),
            ("P2A", t(2), t(5)),
        ]
        snap = make_snap(1, 1, stops, departure_battery=31.0)
        assert any(
            c.conflict_type == ConflictType.LOW_BATTERY for c in detect_battery_conflicts(snap)
        )

    def test_low_battery_yard_present_after_no_conflict(self):
        # Battery drops below threshold but Y follows — not a conflict
        stops = [
            ("P1A", t(0), t(0)),
            ("B3", t(0), t(1)),  # battery → 30 (not below 30)
            ("B5", t(1), t(2)),  # battery → 29 < 30, but Y comes after
            ("P2A", t(2), t(5)),
            ("B6", t(5), t(6)),
            ("Y", t(6), t(6)),
        ]
        snap = make_snap(1, 1, stops, departure_battery=31.0)
        assert not any(
            c.conflict_type == ConflictType.LOW_BATTERY for c in detect_battery_conflicts(snap)
        )

    def test_yard_reachable_after_uses_index_not_node_name(self):
        # Directly verify _yard_reachable_after uses position index, not node name.
        # Path: B1 at index 0, Y at index 1 (mid-path), B1 again at index 2, P1A at index 3.
        # At index 0 (first B1): Y exists after it → True.
        # At index 2 (second B1, after Y): Y does NOT exist after it → False.
        # The old node-name implementation would find the first B1 (index 0) each time,
        # see Y at index 1, and return True even for index 2 — which is wrong.
        from app.logic.conflict_detector import _yard_reachable_after

        stops = [
            ("B1", t(0), t(1)),  # idx 0
            ("Y", t(1), t(1)),  # idx 1
            ("B1", t(1), t(2)),  # idx 2 — same node as idx 0, but Y is now behind us
            ("P1A", t(2), t(5)),  # idx 3
        ]
        assert _yard_reachable_after(stops, 0) is True  # Y at idx 1 follows
        assert _yard_reachable_after(stops, 2) is False  # nothing after idx 2 is Y


class TestDetectAllConflicts:
    def test_combines_block_and_battery_checks(self):
        s1 = make_snap(1, 1, [("B1", t(0), t(5))], departure_battery=79.0)
        s2 = make_snap(2, 2, [("B2", t(2), t(7))], departure_battery=80.0)
        conflicts = detect_all_conflicts([s1, s2])
        types = {c.conflict_type for c in conflicts}
        assert ConflictType.INTERLOCKING in types
        assert ConflictType.INSUFFICIENT_CHARGE in types

    def test_no_conflicts_clean_schedule(self):
        s1 = make_snap(1, 1, [("B1", t(0), t(1))], departure_battery=80.0)
        s2 = make_snap(2, 2, [("B2", t(2), t(3))], departure_battery=80.0)
        assert detect_all_conflicts([s1, s2]) == []

    def test_same_non_interlocked_block_detected(self):
        # Ensure detect_all_conflicts also catches non-interlocked same-block conflicts
        s1 = make_snap(1, 1, [("B5", t(0), t(5))], departure_battery=80.0)
        s2 = make_snap(2, 2, [("B5", t(3), t(8))], departure_battery=80.0)
        conflicts = detect_all_conflicts([s1, s2])
        assert any(c.conflict_type == ConflictType.INTERLOCKING for c in conflicts)
