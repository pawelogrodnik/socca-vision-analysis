from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.services.ball_downstream_impact_audit import (
    _compare_collection,
    _compare_momentum,
    _contact_projection,
    _event_projection,
    _pass_projection,
    _restart_projection,
    _segment_projection,
    build_ball_downstream_impact_audit,
    compact_ball_downstream_impact_audit,
)
from app.services.effective_ball_tracks import EffectiveBallTracksError
from app.services.resolved_ball_tracks import RESOLUTION_SCHEMA_VERSION
from scripts.audit_ball_downstream_impact import _guard_outputs


OUTPUT_ARTIFACTS = {
    "possession_candidates.json", "possession_segments.json", "contact_candidates.json",
    "event_candidates.json", "restart_candidates.json", "pass_candidates.json",
    "pass_review_report.json", "attacking_momentum.json", "possession_report.json",
    "analytics_readiness.json", "ball_downstream_generation.json",
}


def _write(directory: Path, filename: str, document: dict) -> None:
    (directory / filename).write_text(json.dumps(document), encoding="utf-8")


def _tracks(candidate_id: str, *, x: float) -> dict:
    positions = []
    for frame in range(1, 5):
        positions.append({
            "frame": frame,
            "time_sec": frame / 30,
            "candidate_id": f"{candidate_id}-{frame}",
            "position_m": [x + frame * 0.1, 8.0],
            "position_px": [(x + frame * 0.1) * 10, 80.0],
            "source": "detected",
            "confidence": 0.8,
        })
    return {"schema_version": "0.1.0", "generated_at": "2026-09-16T00:00:00Z", "positions": positions, "interpolation_gaps": []}


def _match_fixture(root: Path) -> Path:
    match_dir = root / "audit-source"
    match_dir.mkdir()
    _write(match_dir, "match.json", {"id": "audit-source", "video": {"fps": 30.0, "width": 1920, "height": 1080, "duration_sec": 1.0}})
    _write(match_dir, "pitch_config.json", {"image_points": [[0, 0], [100, 0], [100, 100], [0, 100]], "width_m": 30.0, "length_m": 47.4})
    player = {
        "stable_player_id": "A01", "stable_subject_id": "slot-a01", "team_label": "A", "team_id": "team-a", "team_name": "A",
        "trajectory_m": [{"frame": frame, "time_sec": frame / 30, "pitch_m": [5.2, 8.0], "source": "detected", "status": "detected"} for frame in range(1, 5)],
    }
    _write(match_dir, "stable_players.json", {"players": [player]})
    _write(match_dir, "global_identity.json", {"slots": [{**player, "overlay_positions": player["trajectory_m"]}]})
    _write(match_dir, "ball_tracks.json", _tracks("automatic", x=5.0))
    return match_dir


def _resolved(match_dir: Path, *, x: float = 7.0) -> None:
    document = _tracks("operator", x=x)
    document["resolution"] = {"schema_version": RESOLUTION_SCHEMA_VERSION}
    _write(match_dir, "resolved_ball_tracks.json", document)


def _directory_snapshot(match_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(match_dir).as_posix(): path.read_bytes()
        for path in sorted(match_dir.rglob("*"))
        if path.is_file()
    }


class BallDownstreamImpactAuditTests(unittest.TestCase):
    def test_no_resolved_or_semantically_equal_resolved_short_circuits_without_builder(self) -> None:
        with TemporaryDirectory() as temporary:
            match_dir = _match_fixture(Path(temporary))
            with patch("app.services.ball_downstream_impact_audit.build_ball_possession_analysis") as builder:
                report = build_ball_downstream_impact_audit(match_dir)
            self.assertEqual(report["classification"], "no_effective_ball_change")
            builder.assert_not_called()

            equal = _tracks("automatic", x=5.0)
            equal["resolution"] = {"schema_version": RESOLUTION_SCHEMA_VERSION}
            equal["generated_at"] = "2026-09-16T01:00:00Z"
            _write(match_dir, "resolved_ball_tracks.json", equal)
            self.assertEqual(build_ball_downstream_impact_audit(match_dir)["classification"], "no_effective_ball_change")

    def test_changed_projection_uses_private_timeline_and_never_writes_production_artifacts(self) -> None:
        with TemporaryDirectory() as temporary:
            match_dir = _match_fixture(Path(temporary))
            _resolved(match_dir)
            for filename in OUTPUT_ARTIFACTS:
                (match_dir / filename).write_text(json.dumps({"sentinel": filename}), encoding="utf-8")
            before = _directory_snapshot(match_dir)

            report = build_ball_downstream_impact_audit(match_dir)
            repeated = build_ball_downstream_impact_audit(match_dir)

            self.assertIn(report["classification"], {"local_downstream_change", "downstream_change_with_remote_effects", "inconclusive"})
            self.assertEqual(report["inputs"]["player_event_timeline_provenance"], "reconstructed_global_identity_overlay")
            self.assertEqual(report["ball_track_changes"]["contiguous_changed_regions"][0]["start_frame"], 1)
            self.assertEqual(report["ball_track_changes"]["contiguous_changed_regions"][0]["end_frame"], 4)
            self.assertEqual(report, repeated)
            self.assertEqual(before, _directory_snapshot(match_dir))
            self.assertNotIn("match_phase_config.json", before)

    def test_existing_phase_config_is_read_but_never_modified(self) -> None:
        with TemporaryDirectory() as temporary:
            match_dir = _match_fixture(Path(temporary))
            _resolved(match_dir)
            phase = {"periods": [{"period_id": "one", "start_time_sec": 0, "end_time_sec": 1, "team_a_direction": "towards_y_min"}]}
            _write(match_dir, "match_phase_config.json", phase)
            before = _directory_snapshot(match_dir)
            build_ball_downstream_impact_audit(match_dir)
            self.assertEqual(before, _directory_snapshot(match_dir))

    def test_malformed_resolved_projection_fails_loudly(self) -> None:
        with TemporaryDirectory() as temporary:
            match_dir = _match_fixture(Path(temporary))
            _write(match_dir, "resolved_ball_tracks.json", {"positions": []})
            with self.assertRaises(EffectiveBallTracksError):
                build_ball_downstream_impact_audit(match_dir)

    def test_compact_output_is_deterministic_and_omits_large_excerpts(self) -> None:
        with TemporaryDirectory() as temporary:
            match_dir = _match_fixture(Path(temporary))
            _resolved(match_dir)
            first = compact_ball_downstream_impact_audit(build_ball_downstream_impact_audit(match_dir))
            second = compact_ball_downstream_impact_audit(build_ball_downstream_impact_audit(match_dir))
            self.assertEqual(first, second)
            self.assertNotIn("changed_ball_frames", json.dumps(first))

    def test_collection_diff_is_order_independent_and_detects_same_count_pass_change(self) -> None:
        ball = {"contiguous_changed_regions": [{"start_time_sec": 1.0, "end_time_sec": 1.0}]}
        before = {"candidates": [{"candidate_key": "p1", "time_sec": 1.0, "receiver": "A01"}, {"candidate_key": "p2", "time_sec": 2.0}]}
        reordered = {"candidates": list(reversed(before["candidates"]))}
        unchanged = _compare_collection(before, reordered, "candidates", lambda row, _: row["candidate_key"], ball, 5.0)
        self.assertEqual(unchanged["changed_count"], 0)
        changed = deepcopy(before)
        changed["candidates"][0]["receiver"] = "A02"
        result = _compare_collection(before, changed, "candidates", lambda row, _: row["candidate_key"], ball, 5.0)
        self.assertEqual((result["before_count"], result["after_count"], result["modified_count"]), (2, 2, 1))

    def test_collection_diffs_cover_segments_contacts_restarts_and_volatile_timestamps(self) -> None:
        ball = {"contiguous_changed_regions": [{"start_time_sec": 1.0, "end_time_sec": 1.0}]}
        unchanged = _compare_collection(
            {"segments": [{"id": "one", "time_sec": 1.0, "generated_at": "old"}]},
            {"segments": [{"id": "one", "time_sec": 1.0, "generated_at": "new"}]},
            "segments", lambda row, _: row["id"], ball, 5.0,
        )
        self.assertEqual(unchanged["changed_count"], 0)
        contacts = _compare_collection(
            {"candidates": [{"candidate_key": "old", "time_sec": 1.0}]},
            {"candidates": [{"candidate_key": "new", "time_sec": 1.0}]},
            "candidates", lambda row, _: row["candidate_key"], ball, 5.0,
        )
        self.assertEqual((contacts["added_count"], contacts["removed_count"]), (1, 1))
        restarts = _compare_collection(
            {"candidates": [{"candidate_key": "restart", "time_sec": 1.0, "boundary_line": "left"}]},
            {"candidates": [{"candidate_key": "restart", "time_sec": 1.0, "boundary_line": "right"}]},
            "candidates", lambda row, _: row["candidate_key"], ball, 5.0,
        )
        self.assertEqual(restarts["modified_count"], 1)
        unchanged_segment_id = _compare_collection(
            {"segments": [{"segment_id": "pos-000001", "start_frame": 1, "end_frame": 2}]},
            {"segments": [{"segment_id": "pos-000002", "start_frame": 1, "end_frame": 2}]},
            "segments", lambda row, _: "same", ball, 5.0, projection=_segment_projection,
        )
        self.assertEqual(unchanged_segment_id["changed_count"], 0)
        unchanged_restart_id = _compare_collection(
            {"candidates": [{"candidate_key": "restart", "candidate_id": "restart-0001", "time_sec": 1.0}]},
            {"candidates": [{"candidate_key": "restart", "candidate_id": "restart-0002", "time_sec": 1.0}]},
            "candidates", lambda row, _: row["candidate_key"], ball, 5.0, projection=_restart_projection,
        )
        self.assertEqual(unchanged_restart_id["changed_count"], 0)

    def test_sequential_identifiers_do_not_pollute_contact_event_or_pass_diffs(self) -> None:
        ball = {"contiguous_changed_regions": [{"start_time_sec": 1.0, "end_time_sec": 1.0}]}
        contacts_before = {"candidates": [
            {"candidate_key": "contact-a", "candidate_id": "contact-0001", "time_sec": 1.0},
            {"candidate_key": "contact-b", "candidate_id": "contact-0002", "time_sec": 2.0},
            {"candidate_key": "contact-c", "candidate_id": "contact-0003", "time_sec": 3.0},
        ]}
        contacts_after = {"candidates": [
            {"candidate_key": "contact-a", "candidate_id": "contact-0001", "time_sec": 1.0},
            {"candidate_key": "contact-x", "candidate_id": "contact-0002", "time_sec": 1.5},
            {"candidate_key": "contact-b", "candidate_id": "contact-0003", "time_sec": 2.0},
            {"candidate_key": "contact-c", "candidate_id": "contact-0004", "time_sec": 3.0},
        ]}
        contacts = _compare_collection(
            contacts_before, contacts_after, "candidates", lambda row, _: row["candidate_key"], ball, 5.0,
            projection=_contact_projection,
        )
        self.assertEqual((contacts["added_count"], contacts["removed_count"], contacts["modified_count"]), (1, 0, 0))

        events_before = {"events": [
            {"source_candidate_key": "contact-b", "event_id": "event-0002", "source_candidate_id": "contact-0002", "event_type": "ball_contact"},
            {"source_candidate_key": "contact-c", "event_id": "event-0003", "source_candidate_id": "contact-0003", "event_type": "ball_contact"},
        ]}
        events_after = {"events": [
            {"source_candidate_key": "contact-x", "event_id": "event-0002", "source_candidate_id": "contact-0002", "event_type": "ball_contact"},
            {"source_candidate_key": "contact-b", "event_id": "event-0003", "source_candidate_id": "contact-0003", "event_type": "ball_contact"},
            {"source_candidate_key": "contact-c", "event_id": "event-0004", "source_candidate_id": "contact-0004", "event_type": "ball_contact"},
        ]}
        events = _compare_collection(
            events_before, events_after, "events", lambda row, _: row["source_candidate_key"], ball, 5.0,
            projection=_event_projection,
        )
        self.assertEqual((events["added_count"], events["removed_count"], events["modified_count"]), (1, 0, 0))

        passes_before = {"candidates": [
            {"candidate_key": "pass-b", "candidate_id": "pass-0002", "source_event_id": "event-0002", "target_event_id": "event-0003", "source_candidate_id": "contact-b", "target_candidate_id": "contact-c", "source_candidate_key": "contact-b", "target_candidate_key": "contact-c", "outcome": "completed_pass"},
        ]}
        passes_after = {"candidates": [
            {"candidate_key": "pass-x", "candidate_id": "pass-0002", "source_event_id": "event-0002", "target_event_id": "event-0003", "source_candidate_id": "contact-x", "target_candidate_id": "contact-b", "source_candidate_key": "contact-x", "target_candidate_key": "contact-b", "outcome": "completed_pass"},
            {"candidate_key": "pass-b", "candidate_id": "pass-0003", "source_event_id": "event-0003", "target_event_id": "event-0004", "source_candidate_id": "contact-b", "target_candidate_id": "contact-c", "source_candidate_key": "contact-b", "target_candidate_key": "contact-c", "outcome": "completed_pass"},
        ]}
        passes = _compare_collection(
            passes_before, passes_after, "candidates", lambda row, _: row["candidate_key"], ball, 5.0,
            projection=_pass_projection,
        )
        self.assertEqual((passes["added_count"], passes["removed_count"], passes["modified_count"]), (1, 0, 0))

        changed_outcome = deepcopy(passes_before)
        changed_outcome["candidates"][0]["outcome"] = "failed_pass"
        genuine_pass_change = _compare_collection(
            passes_before, changed_outcome, "candidates", lambda row, _: row["candidate_key"], ball, 5.0,
            projection=_pass_projection,
        )
        self.assertEqual(genuine_pass_change["modified_count"], 1)

    def test_remote_and_momentum_changes_are_reported_but_not_failed(self) -> None:
        ball = {"contiguous_changed_regions": [{"start_time_sec": 1.0, "end_time_sec": 1.0}]}
        remote = _compare_collection({"candidates": []}, {"candidates": [{"candidate_key": "r", "time_sec": 20.0}]}, "candidates", lambda row, _: row["candidate_key"], ball, 5.0)
        self.assertEqual(remote["locality"]["remote_change_count"], 1)
        momentum = _compare_momentum({"points": [{"time_sec": 1.0, "value": 0.1}]}, {"points": [{"time_sec": 1.0, "value": 0.2}]}, ball, 5.0)
        self.assertEqual(momentum["changed_point_count"], 1)

    def test_local_propagation_is_classified_against_ball_regions(self) -> None:
        ball = {"contiguous_changed_regions": [{"start_time_sec": 1.0, "end_time_sec": 1.1}]}
        local = _compare_collection(
            {"candidates": []}, {"candidates": [{"candidate_key": "near", "time_sec": 2.0}]},
            "candidates", lambda row, _: row["candidate_key"], ball, 5.0,
        )
        self.assertEqual((local["locality"]["near_change_count"], local["locality"]["remote_change_count"]), (1, 0))

    def test_cli_guard_rejects_every_existing_match_file(self) -> None:
        with TemporaryDirectory() as temporary:
            match_dir = _match_fixture(Path(temporary))
            for filename in ("match.json", "pitch_config.json", "pass_candidates.json"):
                (match_dir / filename).touch(exist_ok=True)
                with self.assertRaises(SystemExit):
                    _guard_outputs([match_dir], match_dir / filename, parser=_Parser())
            _write(match_dir, "match_phase_config.json", {"periods": []})
            with self.assertRaises(SystemExit):
                _guard_outputs([match_dir], match_dir / "match_phase_config.json", parser=_Parser())


class _Parser:
    def error(self, message: str) -> None:
        raise SystemExit(message)
