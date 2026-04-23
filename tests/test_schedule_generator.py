"""Tests for the auto-schedule generator (Bonus 3)."""

from datetime import UTC, datetime, timedelta

T0 = datetime(2026, 4, 18, 8, 0, tzinfo=UTC)


async def _make_vehicle(client, name: str, battery: float = 80.0) -> int:
    r = await client.post("/api/v1/vehicles", json={"name": name, "battery_level": battery})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestGenerateRequestValidation:
    async def test_end_before_start_rejected(self, client):
        vid = await _make_vehicle(client, "v1")
        r = await client.post(
            "/api/v1/schedule/generate",
            json={
                "vehicle_ids": [vid],
                "start_time": (T0 + timedelta(hours=1)).isoformat(),
                "end_time": T0.isoformat(),
            },
        )
        assert r.status_code == 422

    async def test_interval_must_be_positive(self, client):
        vid = await _make_vehicle(client, "v1")
        r = await client.post(
            "/api/v1/schedule/generate",
            json={
                "vehicle_ids": [vid],
                "start_time": T0.isoformat(),
                "end_time": (T0 + timedelta(hours=2)).isoformat(),
                "departure_interval_minutes": 0,
            },
        )
        assert r.status_code == 422

    async def test_nonexistent_vehicle_returns_404(self, client):
        r = await client.post(
            "/api/v1/schedule/generate",
            json={
                "vehicle_ids": [9999],
                "start_time": T0.isoformat(),
                "end_time": (T0 + timedelta(hours=2)).isoformat(),
            },
        )
        assert r.status_code == 404


class TestGenerateSchedule:
    async def test_single_vehicle_produces_services(self, client):
        vid = await _make_vehicle(client, "v1")
        r = await client.post(
            "/api/v1/schedule/generate",
            json={
                "vehicle_ids": [vid],
                "start_time": T0.isoformat(),
                "end_time": (T0 + timedelta(hours=3)).isoformat(),
                "departure_interval_minutes": 30,
                "platform_dwell_seconds": 60,
            },
        )
        assert r.status_code == 201, r.text
        created = r.json()
        assert len(created) >= 1
        for svc in created:
            assert svc["vehicle_id"] == vid

    async def test_multi_vehicles_are_offset_and_no_conflicts(self, client):
        v1 = await _make_vehicle(client, "v1")
        v2 = await _make_vehicle(client, "v2")
        r = await client.post(
            "/api/v1/schedule/generate",
            json={
                "vehicle_ids": [v1, v2],
                "start_time": T0.isoformat(),
                "end_time": (T0 + timedelta(hours=4)).isoformat(),
                "departure_interval_minutes": 30,
                "platform_dwell_seconds": 60,
            },
        )
        assert r.status_code == 201, r.text
        # Verify both vehicles were used
        vehicles_used = {svc["vehicle_id"] for svc in r.json()}
        assert vehicles_used == {v1, v2}

        # No resulting block/interlocking conflicts
        conflicts = (await client.get("/api/v1/schedule/conflicts")).json()
        assert not any(
            c["conflict_type"] in ("interlocking", "vehicle_overlap", "vehicle_discontinuity")
            for c in conflicts
        )
