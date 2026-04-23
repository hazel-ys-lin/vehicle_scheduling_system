from app.logic.path_validator import platform_nodes, reachable_from, validate_path


class TestValidatePath:
    def test_valid_yard_to_s1a(self):
        assert validate_path(["Y", "B1", "P1A"]) == []

    def test_valid_yard_to_s1b(self):
        assert validate_path(["Y", "B2", "P1B"]) == []

    def test_valid_full_loop(self):
        path = [
            "Y",
            "B1",
            "P1A",
            "B3",
            "B5",
            "P2A",
            "B6",
            "B7",
            "P3A",
            "B10",
            "B11",
            "P2B",
            "B12",
            "B13",
            "P1A",
            "B1",
            "Y",
        ]
        assert validate_path(path) == []

    def test_valid_return_to_yard(self):
        assert validate_path(["P1A", "B1", "Y"]) == []

    def test_valid_s3_branch_b(self):
        path = [
            "Y",
            "B2",
            "P1B",
            "B4",
            "B5",
            "P2A",
            "B6",
            "B8",
            "P3B",
            "B9",
            "B11",
            "P2B",
            "B12",
            "B14",
            "P1B",
            "B2",
            "Y",
        ]
        assert validate_path(path) == []

    def test_too_short(self):
        errors = validate_path(["Y"])
        assert len(errors) == 1
        assert "at least 2" in errors[0]

    def test_empty(self):
        errors = validate_path([])
        assert len(errors) == 1

    def test_unknown_node(self):
        errors = validate_path(["Y", "B99"])
        assert any("Unknown" in e for e in errors)

    def test_disconnected_pair(self):
        # P2A → B1 is not a valid edge
        errors = validate_path(["Y", "B1", "P1A", "B1", "P2A"])
        assert any("P1A" in e and "B1" in e or "B1" in e and "P2A" in e for e in errors)

    def test_wrong_direction(self):
        # B5 → P1A does not exist
        errors = validate_path(["B5", "P1A"])
        assert errors

    def test_yard_not_connected_to_b3_directly(self):
        errors = validate_path(["Y", "B3"])
        assert errors

    def test_single_block_segment(self):
        assert validate_path(["Y", "B1"]) == []

    def test_multiple_unknown_nodes(self):
        errors = validate_path(["X1", "X2"])
        assert len(errors) >= 2


class TestPlatformNodes:
    def test_extracts_platforms_and_yard(self):
        path = ["Y", "B1", "P1A", "B3", "B5", "P2A"]
        assert platform_nodes(path) == ["Y", "P1A", "P2A"]

    def test_blocks_only(self):
        assert platform_nodes(["B1", "B2", "B3"]) == []

    def test_empty(self):
        assert platform_nodes([]) == []


class TestReachableFrom:
    def test_yard_reaches_all_platforms(self):
        from app.topology import PLATFORMS

        assert reachable_from("Y", set(PLATFORMS))

    def test_p3a_cannot_reach_yard_without_traversal(self):
        # P3A can reach Y via the return path
        assert reachable_from("P3A", {"Y"})

    def test_b5_cannot_reach_yard(self):
        # B5 → P2A → B6 → … → B11 → P2B → B12 → B13/B14 → P1A/P1B → B1/B2 → Y
        assert reachable_from("B5", {"Y"})

    def test_p2a_cannot_reach_p1a_directly(self):
        # P2A has no direct edge back to P1A; must go through S3 loop
        assert reachable_from("P2A", {"P1A"})

    def test_unreachable_nonexistent_target(self):
        assert not reachable_from("Y", {"NONEXISTENT"})
