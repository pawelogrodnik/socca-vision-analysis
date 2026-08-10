from __future__ import annotations

import math
import json
import tempfile
import unittest
from pathlib import Path

from app.services.match_phase_config import save_match_phase_config
from app.services.team_shape import (
    build_team_shape_document,
    build_team_shape_takeaways,
    calculate_frame_shape,
    ensure_team_shape_artifact_fresh,
    observations_from_tracklets,
    to_team_oriented_coordinates,
)
from app.services.public_match_report import build_public_match_report, write_public_match_report_bundle
from app.services.reviewed_match_report import build_reviewed_match_report


PITCH_WIDTH = 30.0
PITCH_LENGTH = 47.4
POSITIONS = [[5.0, 10.0], [10.0, 15.0], [15.0, 20.0], [20.0, 25.0], [25.0, 30.0]]


def phase(direction_a: str = "towards_y_max", direction_b: str = "towards_y_min") -> dict:
    return {
        "periods": [
            {
                "period_id": "full_video",
                "start_time_sec": 0.0,
                "end_time_sec": 600.0,
                "team_attack_directions": {"A": direction_a, "B": direction_b},
                "direction_source": "configured_single_period",
            }
        ],
        "summary": {"periods": 1, "has_second_half": False, "needs_review": False},
    }


def observations(positions: list[list[float]], *, team: str = "A", time_sec: float = 0.0, frame: int = 0, **extra: object) -> list[dict]:
    return [
        {
            "frame": frame,
            "time_sec": time_sec,
            "team_label": team,
            "pitch_m": point,
            "play_area_status": "inside_play",
            "source": "detected",
            "trusted": True,
            "subject_id": f"{team}{index:02d}",
            **extra,
        }
        for index, point in enumerate(positions, start=1)
    ]


def build(rows: list[dict], *, duration: float = 1.0, phases: dict | None = None) -> dict:
    return build_team_shape_document(
        player_observations=rows,
        pitch_width_m=PITCH_WIDTH,
        pitch_length_m=PITCH_LENGTH,
        match_phase_config=phases or phase(),
        video_duration_sec=duration,
        expected_sample_interval_sec=1.0,
    )


def team(document: dict, label: str = "A") -> dict:
    return next(row for row in document["teams"] if row["team_label"] == label)


def assert_close(actual: float, expected: float, *, absolute_tolerance: float = 1e-9) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=absolute_tolerance):
        raise AssertionError(f"{actual!r} != {expected!r}")


def test_exact_frame_geometry() -> None:
    result = calculate_frame_shape(POSITIONS, "towards_y_max", PITCH_WIDTH, PITCH_LENGTH)
    assert result is not None
    assert_close(result["width_m"], 20.0)
    assert_close(result["depth_m"], 20.0)
    assert_close(result["centroid_lateral_m"], 15.0)
    assert_close(result["centroid_progress_m"], 20.0)
    assert_close(result["compactness_m"], 6 * math.sqrt(50) / 5)
    assert_close(result["block_height_percent"], 20.0 / PITCH_LENGTH * 100.0)


def test_invalid_pitch_dimensions_are_rejected() -> None:
    for width_m, length_m in ((0.0, PITCH_LENGTH), (-1.0, PITCH_LENGTH), (PITCH_WIDTH, math.inf)):
        try:
            build_team_shape_document(
                player_observations=observations(POSITIONS),
                pitch_width_m=width_m,
                pitch_length_m=length_m,
                match_phase_config=phase(),
            )
        except ValueError:
            continue
        raise AssertionError(f"Expected invalid pitch dimensions to fail: {width_m} x {length_m}")


def test_7v7_frame_eligibility() -> None:
    for count, valid in ((7, True), (6, True), (5, True), (4, False), (8, False)):
        points = [[float(index), float(index)] for index in range(count)]
        actual = calculate_frame_shape(points, "towards_y_max", PITCH_WIDTH, PITCH_LENGTH) is not None
        if actual is not valid:
            raise AssertionError(f"Expected {count} positions to be valid={valid}, got {actual}")


def test_identity_swap_and_unresolved_identity_do_not_change_shape() -> None:
    first = observations(POSITIONS)
    swapped = [dict(row, subject_id=first[-index - 1]["subject_id"]) for index, row in enumerate(first)]
    unresolved = [dict(row, subject_id=None, player_id=None) for row in first]
    assert team(build(first))["summary"] == team(build(swapped))["summary"]
    assert team(build(first))["summary"] == team(build(unresolved))["summary"]


def test_tracklet_team_trust_reuses_canonical_cluster_acceptance() -> None:
    def tracklet(**updates: object) -> dict:
        return {
            "team_label": "A",
            "team_cluster_id": "cluster-1",
            "team_confidence": 0.42,
            "positions_m": [{"frame": 0, "time_sec": 0.0, "pitch_m": POSITIONS[0]}],
            **updates,
        }

    trusted = observations_from_tracklets([tracklet(player_id=None)])[0]
    low_confidence = observations_from_tracklets([tracklet(team_confidence=0.4199)])[0]
    missing_cluster = observations_from_tracklets([tracklet(team_cluster_id=None)])[0]
    ambiguous = observations_from_tracklets(
        [tracklet(team_assignment_reason="goalkeeper_outlier_requires_review")]
    )[0]
    unknown = observations_from_tracklets([tracklet(team_label="U")])[0]

    assert trusted["trusted"] is True
    assert low_confidence["trusted"] is False
    assert missing_cluster["trusted"] is False
    assert ambiguous["trusted"] is False
    assert unknown["trusted"] is False


def test_only_canonically_trusted_tracklets_contribute_to_shape() -> None:
    tracklets = []
    trusted_positions = POSITIONS + [[15.0, 35.0]]
    for index, point in enumerate(trusted_positions):
        tracklets.append(
            {
                "tracklet_id": f"A-{index}",
                "team_label": "A",
                "team_cluster_id": "cluster-A",
                "team_confidence": 0.42,
                "player_id": None,
                "positions_m": [{"frame": 0, "time_sec": 0.0, "pitch_m": point}],
            }
        )
    tracklets.extend(
        [
            {
                "tracklet_id": "A-low-confidence",
                "team_label": "A",
                "team_cluster_id": "cluster-A",
                "team_confidence": 0.41,
                "positions_m": [{"frame": 0, "time_sec": 0.0, "pitch_m": [30.0, 47.4]}],
            },
            {
                "tracklet_id": "A-unreviewed-goalkeeper",
                "team_label": "A",
                "team_cluster_id": "cluster-A",
                "team_confidence": 1.0,
                "team_assignment_reason": "goalkeeper_outlier_requires_review",
                "positions_m": [{"frame": 0, "time_sec": 0.0, "pitch_m": [0.0, 0.0]}],
            },
            {
                "tracklet_id": "unknown",
                "team_label": "U",
                "team_confidence": 1.0,
                "positions_m": [{"frame": 0, "time_sec": 0.0, "pitch_m": [0.0, 0.0]}],
            },
        ]
    )

    result = team(build(observations_from_tracklets(tracklets)))

    assert result["diagnostics"]["eligible_frames"] == 1
    assert_close(result["summary"]["average_width_m"], 20.0)


def test_teams_are_isolated_and_invalid_observations_are_excluded() -> None:
    rows = observations(POSITIONS) + observations([[1.0, 1.0]] * 5, team="B")
    rows += observations([[30.0, 47.4]], team="U")
    rows += observations([[30.0, 47.4]], play_area_status="outside_play")
    rows += observations([[100.0, 100.0]])
    document = build(rows)
    assert_close(team(document, "A")["summary"]["average_width_m"], 20.0)
    assert_close(team(document, "B")["summary"]["average_width_m"], 0.0)


def test_direction_transform_stays_within_oriented_pitch() -> None:
    for direction in ("towards_y_min", "towards_y_max", "towards_x_min", "towards_x_max"):
        result = to_team_oriented_coordinates([10.0, 20.0], direction, PITCH_WIDTH, PITCH_LENGTH)
        assert 0.0 <= result["lateral_percent"] <= 100.0
        assert 0.0 <= result["progress_percent"] <= 100.0


def test_opposite_x_directions_produce_equivalent_oriented_geometry() -> None:
    positions = [[10.0, 5.0], [12.0, 10.0], [15.0, 15.0], [18.0, 20.0], [20.0, 25.0]]
    rotated = [[PITCH_WIDTH - x, PITCH_LENGTH - y] for x, y in positions]
    towards_min = calculate_frame_shape(positions, "towards_x_min", PITCH_WIDTH, PITCH_LENGTH)
    towards_max = calculate_frame_shape(rotated, "towards_x_max", PITCH_WIDTH, PITCH_LENGTH)
    assert towards_min is not None
    assert towards_max is not None
    for key in ("width_m", "depth_m", "compactness_m", "block_height_percent"):
        assert_close(towards_min[key], towards_max[key])


def test_reviewed_side_switch_keeps_shape_density_and_height_aligned() -> None:
    team_a_positions = POSITIONS + [[15.0, 35.0]]
    team_b_positions = [[PITCH_WIDTH - x, PITCH_LENGTH - y] for x, y in team_a_positions]
    phases = {
        "periods": [
            {
                "period_id": "first_half",
                "start_time_sec": 0.0,
                "end_time_sec": 60.0,
                "team_attack_directions": {"A": "towards_y_min", "B": "towards_y_max"},
            },
            {
                "period_id": "second_half",
                "start_time_sec": 120.0,
                "end_time_sec": 180.0,
                "team_attack_directions": {"A": "towards_y_max", "B": "towards_y_min"},
            },
        ],
        "summary": {"periods": 2, "has_second_half": True, "needs_review": False},
    }
    rows = []
    for second in range(60):
        rows += observations(team_a_positions, time_sec=float(second), frame=second)
        rows += observations(team_b_positions, team="B", time_sec=float(second), frame=second)
    for second in range(120, 180):
        rows += observations(team_b_positions, time_sec=float(second), frame=second)
        rows += observations(team_a_positions, team="B", time_sec=float(second), frame=second)
    document = build(rows, duration=180.0, phases=phases)
    result = team(document)
    assert document["available"] is True
    assert result["readiness"] == "ready"
    assert_close(result["summary"]["average_width_m"], 20.0)
    assert_close(result["summary"]["average_block_height_percent"], 52.53, absolute_tolerance=0.01)
    assert_close(
        sum(cell["value"] for cell in result["average_shape"]["cells"]),
        1.0,
        absolute_tolerance=1e-5,
    )
    assert result["diagnostics"]["active_period_duration_sec"] == 120.0
    assert result["diagnostics"]["expected_active_samples"] == 120
    assert result["diagnostics"]["temporal_coverage"] == 1.0
    assert result["timeline"][1]["active_period_duration_sec"] == 0.0
    assert result["timeline"][1]["width_m"] is None


def test_real_two_by_twenty_match_excludes_halftime_from_coverage() -> None:
    team_a_first = POSITIONS + [[15.0, 35.0]]
    team_a_second = [[PITCH_WIDTH - x, PITCH_LENGTH - y] for x, y in team_a_first]
    phases = {
        "periods": [
            {
                "period_id": "first_half",
                "start_time_sec": 0.0,
                "end_time_sec": 1200.0,
                "team_attack_directions": {"A": "towards_y_min", "B": "towards_y_max"},
            },
            {
                "period_id": "second_half",
                "start_time_sec": 1500.0,
                "end_time_sec": 2700.0,
                "team_attack_directions": {"A": "towards_y_max", "B": "towards_y_min"},
            },
        ],
        "summary": {"periods": 2, "has_second_half": True, "needs_review": False},
    }
    rows = []
    for second in (*range(0, 1200, 10), *range(1500, 2700, 10)):
        first_half = second < 1200
        rows += observations(
            team_a_first if first_half else team_a_second,
            time_sec=float(second),
            frame=second,
        )
        rows += observations(
            team_a_second if first_half else team_a_first,
            team="B",
            time_sec=float(second),
            frame=second,
        )

    document = build_team_shape_document(
        player_observations=rows,
        pitch_width_m=PITCH_WIDTH,
        pitch_length_m=PITCH_LENGTH,
        match_phase_config=phases,
        video_duration_sec=2700.0,
        expected_sample_interval_sec=10.0,
    )
    result = team(document)

    assert document["available"] is True
    assert result["readiness"] == "ready"
    assert result["diagnostics"]["active_period_duration_sec"] == 2400.0
    assert result["diagnostics"]["expected_active_samples"] == 240
    assert result["diagnostics"]["temporal_coverage"] == 1.0
    halftime = result["timeline"][20:25]
    assert len(halftime) == 5
    assert all(point["active_period_duration_sec"] == 0.0 for point in halftime)
    assert all(
        point[key] is None
        for point in halftime
        for key in ("width_m", "depth_m", "compactness_m", "block_height_percent")
    )


def test_over_cap_frame_is_excluded_instead_of_truncated() -> None:
    result = team(build(observations([[float(index), 10.0] for index in range(8)])))
    assert result["summary"] is None
    assert result["diagnostics"]["over_cap_frames"] == 1
    assert result["diagnostics"]["direction_coverage"] == 1.0


def test_no_ball_or_physical_aggregate_input_is_required() -> None:
    baseline = build(observations(POSITIONS))
    changed = build([dict(row, total_distance_m=99999.0, sprint_count=500) for row in observations(POSITIONS)])
    assert team(baseline)["summary"] == team(changed)["summary"]


def test_single_team_document_is_not_publicly_available() -> None:
    document = build(observations(POSITIONS + [[15.0, 35.0]]))
    assert document["available"] is False
    assert document["readiness"] == "not_available"
    assert team(document, "B")["summary"] is None


def test_takeaways_respect_minimum_differences_and_limit() -> None:
    team_a = {
        "team_label": "A",
        "team_name": "Corgi",
        "summary": {
            "average_width_m": 20.0,
            "average_depth_m": 20.0,
            "average_compactness_m": 8.0,
            "average_block_height_percent": 50.0,
        },
    }
    below_threshold = {
        "team_label": "B",
        "team_name": "Verisk",
        "summary": {
            "average_width_m": 18.01,
            "average_depth_m": 18.01,
            "average_compactness_m": 7.01,
            "average_block_height_percent": 45.01,
        },
    }
    assert build_team_shape_takeaways([team_a, below_threshold]) == []

    at_threshold = {
        **below_threshold,
        "summary": {
            "average_width_m": 18.0,
            "average_depth_m": 18.0,
            "average_compactness_m": 7.0,
            "average_block_height_percent": 45.0,
        },
    }
    takeaways = build_team_shape_takeaways([team_a, at_threshold])
    assert len(takeaways) == 3
    assert takeaways[0] == "Corgi grał średnio o 2.0 m szerzej niż Verisk."
    assert all("lepiej" not in takeaway and "powinien" not in takeaway for takeaway in takeaways)


def test_equal_frame_density_weight_for_five_and_seven_players() -> None:
    five = observations(POSITIONS, time_sec=0.0, frame=0)
    seven = observations(POSITIONS + [[6.0, 12.0], [24.0, 28.0]], time_sec=1.0, frame=1)
    cells = team(build(five + seven, duration=2.0))["average_shape"]["cells"]
    assert_close(sum(cell["value"] for cell in cells), 1.0, absolute_tolerance=1e-5)


def test_timeline_uses_real_positions_and_keeps_missing_bin_null() -> None:
    narrow = [[13.0, 10.0], [14.0, 15.0], [15.0, 20.0], [16.0, 25.0], [17.0, 30.0]]
    rows = []
    for second in range(60):
        rows += observations(narrow, time_sec=float(second), frame=second)
    for second in range(120, 180):
        rows += observations(POSITIONS, time_sec=float(second), frame=second)
    timeline = team(build(rows, duration=180.0))["timeline"]
    assert_close(timeline[0]["width_m"], 4.0)
    assert timeline[1]["width_m"] is None
    assert timeline[1]["depth_m"] is None
    assert_close(timeline[2]["width_m"], 20.0)


def test_constant_positions_produce_constant_timeline() -> None:
    rows = []
    for second in range(120):
        rows += observations(POSITIONS, time_sec=float(second), frame=second)
    timeline = team(build(rows, duration=120.0))["timeline"]
    assert timeline[0]["width_m"] == timeline[1]["width_m"] == 20.0


def test_partial_timeline_bins_use_only_active_period_overlap() -> None:
    phases = {
        "periods": [
            {
                "period_id": "active_clip",
                "start_time_sec": 30.0,
                "end_time_sec": 90.0,
                "team_attack_directions": {"A": "towards_y_max", "B": "towards_y_min"},
            }
        ],
        "summary": {"periods": 1, "has_second_half": False, "needs_review": False},
    }
    positions = POSITIONS + [[15.0, 35.0]]
    rows = []
    for second in range(30, 90):
        rows += observations(positions, time_sec=float(second), frame=second)
    result = team(build(rows, duration=120.0, phases=phases))

    assert result["diagnostics"]["expected_active_samples"] == 60
    assert result["diagnostics"]["temporal_coverage"] == 1.0
    assert [point["active_period_duration_sec"] for point in result["timeline"]] == [30.0, 30.0]
    assert [point["width_m"] for point in result["timeline"]] == [20.0, 20.0]


def ready_document(*, phases: dict | None = None) -> dict:
    team_a = POSITIONS + [[15.0, 35.0]]
    team_b = [[PITCH_WIDTH - x, PITCH_LENGTH - y] for x, y in team_a]
    return build_team_shape_document(
        player_observations=observations(team_a) + observations(team_b, team="B"),
        pitch_width_m=PITCH_WIDTH,
        pitch_length_m=PITCH_LENGTH,
        match_phase_config=phases or phase(),
        team_config={
            "teams": [
                {"team_label": "A", "team_id": "team-a", "team_name": "Corgi"},
                {"team_label": "B", "team_id": "team-b", "team_name": "Verisk"},
            ]
        },
        video_duration_sec=1.0,
        expected_sample_interval_sec=1.0,
    )


def public_package(shape_document: dict | None = None) -> dict:
    package = {
        "match": {"id": "match-1", "title": "Test", "video": {"duration_sec": 1.0}},
        "team_config": {
            "teams": [
                {"team_label": "A", "team_id": "team-a", "team_name": "Corgi"},
                {"team_label": "B", "team_id": "team-b", "team_name": "Verisk"},
            ]
        },
        "team_stats": {
            "teams": [
                {"team_label": "A", "team_id": "team-a", "team_name": "Corgi"},
                {"team_label": "B", "team_id": "team-b", "team_name": "Verisk"},
            ]
        },
        "resolved_player_stats": {"players": []},
    }
    if shape_document is not None:
        package["team_shape"] = shape_document
    return package


def test_public_report_maps_only_coach_facing_team_shape() -> None:
    report = build_public_match_report(
        public_package(ready_document()),
        published_id="published-match-1",
        source_match_dir=None,
        heatmap_dir=None,
        public_heatmap_base="",
    )

    shape = report["team_shape"]
    assert [row["team_name"] for row in shape["teams"]] == ["Corgi", "Verisk"]
    assert_close(shape["teams"][0]["summary"]["average_width_m"], 20.0)
    assert shape["teams"][0]["average_shape"]["cells"]
    assert shape["teams"][0]["timeline"]
    assert "diagnostics" not in shape
    assert "readiness" not in shape
    assert "algorithm_version" not in shape
    assert "sample_count" not in json.dumps(shape)


def test_public_report_omits_missing_or_partial_team_shape_without_zero_fallback() -> None:
    report = build_public_match_report(
        public_package(),
        published_id="published-match-1",
        source_match_dir=None,
        heatmap_dir=None,
        public_heatmap_base="",
    )
    assert "team_shape" not in report

    partial = ready_document()
    del partial["teams"][0]["summary"]["average_width_m"]
    partial_report = build_public_match_report(
        public_package(partial),
        published_id="published-match-1",
        source_match_dir=None,
        heatmap_dir=None,
        public_heatmap_base="",
    )
    assert "team_shape" not in partial_report


def test_unreviewed_attack_direction_cannot_reach_public_report() -> None:
    unreviewed_phase = phase()
    unreviewed_phase["summary"]["needs_review"] = True
    unreviewed_phase["periods"][0]["direction_source"] = "default_single_period"
    document = ready_document(phases=unreviewed_phase)

    assert document["available"] is False
    assert document["readiness"] == "experimental"
    assert all(team_row["diagnostics"]["attack_direction_trusted"] is False for team_row in document["teams"])
    report = build_public_match_report(
        public_package(document),
        published_id="published-match-1",
        source_match_dir=None,
        heatmap_dir=None,
        public_heatmap_base="",
    )
    assert "team_shape" not in report


def test_published_report_bundle_preserves_team_shape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "target"
        target.mkdir()
        mirror = root / "mirror"
        report = write_public_match_report_bundle(
            public_package(ready_document()),
            target_dir=target,
            source_match_dir=None,
            mirror_dir=mirror,
        )

        persisted = json.loads((target / "public_report.json").read_text(encoding="utf-8"))
        assert persisted["team_shape"] == report["team_shape"]
        assert (target / "public" / "public_report.json").exists()


def test_canonical_artifact_flows_into_reviewed_report_and_refreshes_team_name() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reviewed_shape_fixture(root)

        first = build_reviewed_match_report(root)
        assert first["team_shape"]["teams"][0]["team_name"] == "Corgi"

        team_config = json.loads((root / "team_config.json").read_text(encoding="utf-8"))
        team_config["teams"][0]["team_name"] = "Corgi United"
        _write_json(root / "team_config.json", team_config)
        refreshed = build_reviewed_match_report(root)
        assert refreshed["team_shape"]["teams"][0]["team_name"] == "Corgi United"
        generated_from = json.loads((root / "team_shape.json").read_text(encoding="utf-8"))["generated_from"]
        dependencies = {entry["artifact"] for entry in generated_from}
        assert dependencies == {
            "tracklets.json",
            "pitch_config.json",
            "match_phase_config.json",
            "team_config.json",
            "match.json",
        }


def test_incomplete_freshness_dependencies_force_rebuild() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reviewed_shape_fixture(root)
        first = ensure_team_shape_artifact_fresh(root)
        assert first is not None
        first["generated_from"] = [
            entry
            for entry in first["generated_from"]
            if entry["artifact"] != "match_phase_config.json"
        ]
        _write_json(root / "team_shape.json", first)

        rebuilt = ensure_team_shape_artifact_fresh(root)
        assert rebuilt is not None
        assert {entry["artifact"] for entry in rebuilt["generated_from"]} == {
            "tracklets.json",
            "pitch_config.json",
            "match_phase_config.json",
            "team_config.json",
            "match.json",
        }


def test_previous_algorithm_version_forces_rebuild() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reviewed_shape_fixture(root)
        first = ensure_team_shape_artifact_fresh(root)
        assert first is not None
        first["algorithm_version"] = "team_shape_spatial_v1"
        _write_json(root / "team_shape.json", first)

        rebuilt = ensure_team_shape_artifact_fresh(root)
        assert rebuilt is not None
        assert rebuilt["algorithm_version"] == "team_shape_spatial_v1_1"


def test_match_phase_review_state_change_rebuilds_public_availability() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reviewed_shape_fixture(root)
        trusted = ensure_team_shape_artifact_fresh(root)
        assert trusted is not None
        assert trusted["available"] is True

        match_phase = json.loads((root / "match_phase_config.json").read_text(encoding="utf-8"))
        match_phase["summary"]["needs_review"] = True
        match_phase["periods"][0]["direction_source"] = "default_single_period"
        _write_json(root / "match_phase_config.json", match_phase)
        unreviewed = ensure_team_shape_artifact_fresh(root)
        assert unreviewed is not None
        assert unreviewed["available"] is False
        assert unreviewed["readiness"] == "experimental"

        match_phase["summary"]["needs_review"] = False
        match_phase["periods"][0]["direction_source"] = "configured_single_period"
        _write_json(root / "match_phase_config.json", match_phase)
        reviewed = ensure_team_shape_artifact_fresh(root)
        assert reviewed is not None
        assert reviewed["available"] is True


def test_match_phase_save_immediately_rebuilds_team_shape_with_halftime_gap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reviewed_shape_fixture(root)
        match = json.loads((root / "match.json").read_text(encoding="utf-8"))
        match["video"]["duration_sec"] = 180.0
        _write_json(root / "match.json", match)
        previous = ensure_team_shape_artifact_fresh(root)
        assert previous is not None

        save_match_phase_config(
            root,
            match,
            {
                "first_half_end_time_sec": 60.0,
                "second_half_start_time_sec": 120.0,
                "second_half_end_time_sec": 180.0,
                "team_a_first_half_direction": "towards_y_min",
            },
        )

        rebuilt = json.loads((root / "team_shape.json").read_text(encoding="utf-8"))
        assert rebuilt["generated_at"] != previous["generated_at"]
        assert rebuilt["parameters"]["active_period_duration_sec"] == 120.0


def _write_reviewed_shape_fixture(root: Path) -> None:
    team_a = POSITIONS + [[15.0, 35.0]]
    team_b = [[PITCH_WIDTH - x, PITCH_LENGTH - y] for x, y in team_a]
    _write_json(root / "match.json", {"id": "match-1", "title": "Test", "video": {"duration_sec": 1.0}})
    _write_json(root / "pitch_config.json", {"width_m": PITCH_WIDTH, "length_m": PITCH_LENGTH})
    _write_json(root / "match_phase_config.json", phase())
    _write_json(
        root / "team_config.json",
        {
            "teams": [
                {"team_label": "A", "team_id": "team-a", "team_name": "Corgi"},
                {"team_label": "B", "team_id": "team-b", "team_name": "Verisk"},
            ]
        },
    )
    _write_json(
        root / "team_stats.json",
        {
            "teams": [
                {"team_label": "A", "team_id": "team-a", "team_name": "Corgi"},
                {"team_label": "B", "team_id": "team-b", "team_name": "Verisk"},
            ]
        },
    )
    tracklets = []
    for label, points in (("A", team_a), ("B", team_b)):
        for index, point in enumerate(points):
            tracklets.append(
                {
                    "tracklet_id": f"{label}-{index}",
                    "team_label": label,
                    "team_cluster_id": f"cluster-{label}",
                    "team_confidence": 1.0,
                    "positions_m": [
                        {
                            "frame": 0,
                            "time_sec": 0.0,
                            "pitch_m": point,
                            "smoothed_pitch_m": point,
                            "play_area_status": "inside_play",
                        }
                    ],
                }
            )
    _write_json(root / "tracklets.json", {"tracklets": tracklets})
    _write_json(root / "reviewed_player_stats.json", {"source_snapshot_digest": "digest-1", "players": []})
    _write_json(
        root / "reviewed_player_heatmaps.json",
        {"source_snapshot_digest": "digest-1", "pitch_dimensions_m": {"width_m": PITCH_WIDTH, "length_m": PITCH_LENGTH}, "heatmaps": []},
    )
    _write_json(root / "reviewed_stats_readiness.json", {"source_snapshot_digest": "digest-1", "status": "completed"})
    _write_json(
        root / "reviewed_output_manifest.json",
        {
            "reviewed_identity": {"status": "fresh", "digest": "digest-1"},
            "stats": {"status": "completed", "source_snapshot_digest": "digest-1"},
            "stale": False,
        },
    )


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def load_tests(_loader: unittest.TestLoader, _tests: unittest.TestSuite, _pattern: str | None) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value, description=name))
    return suite

