"""Path validation against the directed track topology."""

from collections import deque

from app.topology import ADJACENCY, ALL_NODES, PLATFORMS, YARD


def validate_path(nodes: list[str]) -> list[str]:
    """Validate an ordered path of node IDs.

    Returns a list of error strings; empty means the path is valid.

    Rules:
    - ≥ 2 nodes
    - All node ids exist in topology
    - First node must be the yard or a platform (services start at a stop, not
      mid-block). This also prevents the snapshot builder from silently dropping
      a leading block due to missing `current_time`.
    - Consecutive pairs must be directly connected per ADJACENCY.
    """
    errors: list[str] = []

    if len(nodes) < 2:
        errors.append("Path must contain at least 2 nodes.")
        return errors

    for node in nodes:
        if node not in ALL_NODES:
            errors.append(f"Unknown node: '{node}'.")

    if errors:
        return errors

    first = nodes[0]
    if first != YARD and first not in PLATFORMS:
        errors.append(f"Path must start at the yard or a platform, not a block: '{first}'.")

    for i in range(len(nodes) - 1):
        src, dst = nodes[i], nodes[i + 1]
        if dst not in ADJACENCY.get(src, []):
            errors.append(f"No direct connection from '{src}' to '{dst}'.")

    return errors


def platform_nodes(nodes: list[str]) -> list[str]:
    """Return only the platform and yard nodes from a path, preserving order."""
    return [n for n in nodes if n in PLATFORMS or n == YARD]


def reachable_from(start: str, targets: set[str]) -> bool:
    """BFS to check whether all targets are reachable from start in the directed graph."""
    visited: set[str] = set()
    queue: deque[str] = deque([start])
    found: set[str] = set()
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        if node in targets:
            found.add(node)
        for neighbor in ADJACENCY.get(node, []):
            if neighbor not in visited:
                queue.append(neighbor)
    return targets.issubset(found)
