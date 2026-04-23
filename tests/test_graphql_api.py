"""Integration tests for the read-only GraphQL gateway at /graphql."""

from datetime import UTC, datetime, timedelta

T0 = datetime(2026, 4, 18, 8, 0, tzinfo=UTC)


def t(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


async def gql(client, query: str, variables: dict | None = None) -> dict:
    r = await client.post(
        "/graphql", json={"query": query, "variables": variables or {}}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _create_vehicle(client, name: str = "v1", battery: float = 80.0) -> int:
    r = await client.post(
        "/api/v1/vehicles", json={"name": name, "battery_level": battery}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _create_service(client, vehicle_id: int, offset: float = 0.0) -> int:
    payload = {
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
    r = await client.post("/api/v1/services", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestGraphQLVehicles:
    async def test_list_vehicles_with_battery_level(self, client):
        await _create_vehicle(client, name="alpha", battery=65.5)
        body = await gql(client, "{ vehicles { id name batteryLevel } }")
        assert body["data"]["vehicles"][0]["name"] == "alpha"
        assert abs(body["data"]["vehicles"][0]["batteryLevel"] - 65.5) < 0.01

    async def test_battery_reflects_service_consume(self, client):
        vid = await _create_vehicle(client, battery=80.0)
        await _create_service(client, vid)
        body = await gql(
            client,
            "query($id: Int!){ vehicles(id: $id) { batteryLevel } }",
            {"id": vid},
        )
        assert abs(body["data"]["vehicles"][0]["batteryLevel"] - 79.0) < 0.01


class TestGraphQLServices:
    async def test_services_with_nested_stops(self, client):
        vid = await _create_vehicle(client)
        sid = await _create_service(client, vid)
        body = await gql(
            client,
            """
            query($id: Int!){
              services(id: $id) {
                id vehicleId departureBattery stops { sequence nodeId }
              }
            }
            """,
            {"id": sid},
        )
        svc = body["data"]["services"][0]
        assert svc["id"] == sid
        assert svc["vehicleId"] == vid
        assert [s["nodeId"] for s in svc["stops"]] == ["Y", "B1", "P1A"]

    async def test_services_filter_by_vehicle(self, client):
        v1 = await _create_vehicle(client, name="v1")
        v2 = await _create_vehicle(client, name="v2")
        await _create_service(client, v1)
        await _create_service(client, v2)
        body = await gql(
            client,
            "query($vid: Int!){ services(vehicleId: $vid) { id vehicleId } }",
            {"vid": v2},
        )
        services = body["data"]["services"]
        assert len(services) == 1
        assert services[0]["vehicleId"] == v2


class TestGraphQLTopology:
    async def test_topology_query(self, client):
        body = await gql(
            client,
            """
            {
              topology {
                yard
                platforms { id station }
                blocks { id traversalSeconds bidirectional }
                interlockingGroups { id blocks }
                battery { initial threshold costPerBlock }
              }
            }
            """,
        )
        topo = body["data"]["topology"]
        assert topo["yard"] == "Y"
        assert len(topo["blocks"]) == 14
        assert topo["battery"]["initial"] == 80.0

    async def test_blocks_query(self, client):
        body = await gql(
            client,
            "{ blocks { id traversalSeconds interlockingGroupId } }",
        )
        ids = {b["id"] for b in body["data"]["blocks"]}
        assert "B1" in ids and "B14" in ids


class TestGraphQLPositions:
    async def test_positions_at_timestamp(self, client):
        vid = await _create_vehicle(client)
        await _create_service(client, vid)
        body = await gql(
            client,
            """
            query($at: DateTime!){
              positions(at: $at) {
                vehicleId status currentNode batteryLevel
              }
            }
            """,
            {"at": t(0.5)},
        )
        (pos,) = body["data"]["positions"]
        assert pos["status"] == "TRAVERSING_BLOCK"
        assert pos["currentNode"] == "B1"
        assert abs(pos["batteryLevel"] - 79.5) < 0.01

    async def test_positions_strict_mode(self, client):
        vid = await _create_vehicle(client)
        await _create_service(client, vid)
        body = await gql(
            client,
            """
            query($at: DateTime!){
              positions(at: $at, mode: STRICT) { batteryLevel }
            }
            """,
            {"at": t(0.5)},
        )
        assert body["data"]["positions"][0]["batteryLevel"] == 80.0


class TestGraphQLSchedule:
    async def test_schedule_and_conflicts(self, client):
        v1 = await _create_vehicle(client, name="a")
        v2 = await _create_vehicle(client, name="b")
        await _create_service(client, v1, offset=0)
        await _create_service(client, v2, offset=0)

        body = await gql(
            client,
            "{ schedule { id } conflicts { conflictType serviceIds locations } }",
        )
        assert len(body["data"]["schedule"]) == 2
        types = {c["conflictType"] for c in body["data"]["conflicts"]}
        assert "INTERLOCKING" in types

    async def test_schedule_empty(self, client):
        body = await gql(client, "{ schedule { id } conflicts { conflictType } }")
        assert body["data"]["schedule"] == []
        assert body["data"]["conflicts"] == []
