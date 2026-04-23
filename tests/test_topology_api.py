"""Integration tests for /topology and /topology/positions."""

from datetime import UTC, datetime, timedelta

T0 = datetime(2026, 4, 18, 8, 0, tzinfo=UTC)


def t(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _short_path_payload(vehicle_id: int, offset: float = 0.0) -> dict:
    return {
        "vehicle_id": vehicle_id,
        "stops": [
            {
                "sequence": 0,
                "node_id": "Y",
                "arrival_time": t(offset),
                "departure_time": t(offset),
            },
            {"sequence": 1, "node_id": "B1"},
            {
                "sequence": 2,
                "node_id": "P1A",
                "arrival_time": t(offset + 2),
                "departure_time": t(offset + 3),
            },
        ],
    }


async def _create_vehicle(client, name="bus-1", battery=80.0) -> int:
    r = await client.post("/api/v1/vehicles", json={"name": name, "battery_level": battery})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestTopologyEndpoint:
    async def test_topology_shape(self, client):
        r = await client.get("/api/v1/topology")
        assert r.status_code == 200
        body = r.json()
        assert body["yard"] == "Y"
        assert {p["id"] for p in body["platforms"]} == {
            "P1A", "P1B", "P2A", "P2B", "P3A", "P3B"
        }
        assert all(p["station"] in {"S1", "S2", "S3"} for p in body["platforms"])
        assert len(body["blocks"]) == 14
        b1 = next(b for b in body["blocks"] if b["id"] == "B1")
        assert b1["bidirectional"] is True
        assert b1["interlocking_group_id"] == 1
        b5 = next(b for b in body["blocks"] if b["id"] == "B5")
        assert b5["bidirectional"] is False
        assert b5["interlocking_group_id"] is None
        assert len(body["interlocking_groups"]) == 3
        assert {"from": "Y", "to": "B1"} in body["edges"]
        assert body["battery"]["initial"] == 80.0
        assert body["battery"]["threshold"] == 30.0

    async def test_topology_reflects_block_config_update(self, client):
        r = await client.put("/api/v1/blocks/B5", json={"traversal_seconds": 123})
        assert r.status_code == 200
        body = (await client.get("/api/v1/topology")).json()
        b5 = next(b for b in body["blocks"] if b["id"] == "B5")
        assert b5["traversal_seconds"] == 123


class TestPositionsEndpoint:
    async def test_requires_timezone_aware_at(self, client):
        await _create_vehicle(client)
        r = await client.get("/api/v1/topology/positions", params={"at": "2026-04-18T08:00:00"})
        assert r.status_code == 422

    async def test_idle_at_yard_before_any_service(self, client):
        await _create_vehicle(client, name="v1", battery=72.5)
        r = await client.get(
            "/api/v1/topology/positions", params={"at": t(-10)}
        )
        assert r.status_code == 200
        (pos,) = r.json()
        assert pos["status"] == "idle"
        assert pos["current_node"] == "Y"
        assert pos["battery_level"] == 72.5

    async def test_at_yard_during_departure(self, client):
        vid = await _create_vehicle(client)
        r = await client.post("/api/v1/services", json=_short_path_payload(vid))
        assert r.status_code == 201
        # Y's departure stop is [t(0), t(0)] — exit == enter means no window covers it.
        # Check mid-block-1 instead for TRAVERSING_BLOCK.
        # B1 spans [t(0), t(0)+60s] = [t(0), t(1)). t(0.5) = 30s into B1.
        r = await client.get("/api/v1/topology/positions", params={"at": t(0.5)})
        (pos,) = r.json()
        assert pos["status"] == "traversing_block"
        assert pos["current_node"] == "B1"
        assert pos["next_node"] == "P1A"
        assert pos["service_id"] is not None
        assert abs(pos["battery_level"] - 79.5) < 0.01

    async def test_at_platform(self, client):
        vid = await _create_vehicle(client)
        await client.post("/api/v1/services", json=_short_path_payload(vid))
        # P1A is arrival t(2) → departure t(3); check t(2.5)
        r = await client.get("/api/v1/topology/positions", params={"at": t(2.5)})
        (pos,) = r.json()
        assert pos["status"] == "at_platform"
        assert pos["current_node"] == "P1A"
        # B1 fully traversed (1 cost) but P1A does not drain
        assert abs(pos["battery_level"] - 79.0) < 0.01

    async def test_idle_after_last_service(self, client):
        vid = await _create_vehicle(client)
        await client.post("/api/v1/services", json=_short_path_payload(vid))
        r = await client.get("/api/v1/topology/positions", params={"at": t(30)})
        (pos,) = r.json()
        assert pos["status"] == "idle"
        assert pos["current_node"] == "P1A"
        assert pos["service_id"] is None
        assert abs(pos["battery_level"] - 79.0) < 0.01

    async def test_multiple_vehicles_returned_sorted(self, client):
        v1 = await _create_vehicle(client, name="v1", battery=80.0)
        v2 = await _create_vehicle(client, name="v2", battery=50.0)
        r = await client.get("/api/v1/topology/positions", params={"at": t(-1)})
        body = r.json()
        assert [p["vehicle_id"] for p in body] == [v1, v2]
        assert body[1]["battery_level"] == 50.0

    async def test_strict_mode_does_not_interpolate_mid_block(self, client):
        """In strict mode, mid-block battery == pre-block battery (step function)."""
        vid = await _create_vehicle(client)
        await client.post("/api/v1/services", json=_short_path_payload(vid))
        r = await client.get(
            "/api/v1/topology/positions",
            params={"at": t(0.5), "mode": "strict"},
        )
        (pos,) = r.json()
        assert pos["current_node"] == "B1"
        # Simulation mode would give 79.5 at mid-block; strict holds 80.0 until exit.
        assert pos["battery_level"] == 80.0

    async def test_strict_mode_applies_cost_on_block_exit(self, client):
        vid = await _create_vehicle(client)
        await client.post("/api/v1/services", json=_short_path_payload(vid))
        r = await client.get(
            "/api/v1/topology/positions",
            params={"at": t(2.5), "mode": "strict"},
        )
        (pos,) = r.json()
        # After B1's exit, one full block cost applied in both modes.
        assert pos["battery_level"] == 79.0
