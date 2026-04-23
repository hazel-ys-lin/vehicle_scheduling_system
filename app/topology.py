"""
Fixed track topology for the vehicle scheduling system.

Outbound:  Y → B1/B2 → P1A/P1B → B3/B4 → B5 → P2A → B6 → B7/B8 → P3A/P3B
Return:    P3A/P3B → B9/B10 → B11 → P2B → B12 → B13/B14 → P1A/P1B → B1/B2 → Y
"""

from datetime import UTC, datetime
from typing import Final

# Directed adjacency: node → list of reachable next nodes
ADJACENCY: Final[dict[str, list[str]]] = {
    "Y": ["B1", "B2"],
    "B1": ["P1A", "Y"],
    "B2": ["P1B", "Y"],
    "P1A": ["B3", "B1"],
    "P1B": ["B4", "B2"],
    "B3": ["B5"],
    "B4": ["B5"],
    "B5": ["P2A"],
    "P2A": ["B6"],
    "B6": ["B7", "B8"],
    "B7": ["P3A"],
    "B8": ["P3B"],
    "P3A": ["B10"],
    "P3B": ["B9"],
    "B9": ["B11"],
    "B10": ["B11"],
    "B11": ["P2B"],
    "P2B": ["B12"],
    "B12": ["B13", "B14"],
    "B13": ["P1A"],
    "B14": ["P1B"],
}

# Interlocking groups: at most 1 vehicle may occupy any block within the group at a given time
INTERLOCKING_GROUPS: Final[list[frozenset[str]]] = [
    frozenset({"B1", "B2"}),
    frozenset({"B3", "B4", "B13", "B14"}),
    frozenset({"B7", "B8", "B9", "B10"}),
]

YARD: Final[str] = "Y"
PLATFORMS: Final[frozenset[str]] = frozenset({"P1A", "P1B", "P2A", "P2B", "P3A", "P3B"})
BLOCKS: Final[frozenset[str]] = frozenset(f"B{i}" for i in range(1, 15))
ALL_NODES: Final[frozenset[str]] = PLATFORMS | BLOCKS | {YARD}

# Battery constants
BATTERY_INITIAL: Final[float] = 80.0
BATTERY_MAX: Final[float] = 100.0
BATTERY_COST_PER_BLOCK: Final[float] = 1.0
BATTERY_THRESHOLD: Final[float] = 30.0
BATTERY_MIN_DEPARTURE: Final[float] = 80.0
BATTERY_CHARGE_RATE: Final[float] = 1 / 12  # units per second

# Sentinel timestamp for the BASELINE ledger event: any realistic service
# start_time is strictly greater, so `battery_before(start_time)` always
# includes the vehicle's initial charge, even for schedules created after
# the vehicle row itself.
BATTERY_BASELINE_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)


# Precomputed block → interlocking group lookup (O(1) vs scanning INTERLOCKING_GROUPS)
_GROUP_BY_BLOCK: Final[dict[str, frozenset[str]]] = {
    block: group for group in INTERLOCKING_GROUPS for block in group
}


def _compute_bidirectional_blocks() -> frozenset[str]:
    """A block is bidirectional when both endpoints of its edge reach it back.

    e.g. Y → B1 → P1A and P1A → B1 → Y both exist, so B1 is bidirectional.
    """
    result: set[str] = set()
    for block in BLOCKS:
        exits = ADJACENCY.get(block, [])
        inbound = [src for src, nbrs in ADJACENCY.items() if block in nbrs]
        if any(src in exits for src in inbound):
            result.add(block)
    return frozenset(result)


BIDIRECTIONAL_BLOCKS: Final[frozenset[str]] = _compute_bidirectional_blocks()


def is_block(node: str) -> bool:
    return node in BLOCKS


def is_platform(node: str) -> bool:
    return node in PLATFORMS


def is_yard(node: str) -> bool:
    return node == YARD


def interlocking_group_for(block: str) -> frozenset[str] | None:
    """Return the interlocking group containing this block, or None."""
    return _GROUP_BY_BLOCK.get(block)
