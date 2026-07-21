from __future__ import annotations

import unittest

from app.services.identity_candidate_stats_validation import build_identity_candidate_stats_validation
from app.services.identity_promotion_safety import canonical_document_digest


def _row(
    frame: int,
    *,
    status: str = "detected",
    point: list[float] | None = None,
    subject: str = "s1",
    tracklet: str = "t1",
    review: str = "r1",
) -> dict:
    return {
        "frame": frame,
        "time_sec": frame / 10,
        "status": status,
        "source": status,
        "pitch_m": point if point is not None else [frame / 10, 2.0],
        "candidate_subject_id": subject,
        "tracklet_id": tracklet,
        "review_card_key": review,
        "play_area_status": "inside_play",
        "confidence": 0.9,
    }


class IdentityCandidateStatsValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.match = {"video": {"fps": 10, "duration_sec": 10}}
        self.manifest = {"status": "complete_candidate", "safety": {"hard_conflicts": 0}}

    def _build(
        self,
        rows: list[dict],
        *,
        diff: dict | None = None,
        manifest: dict | None = None,
        possession: dict | None = None,
        passes: dict | None = None,
        events: dict | None = None,
    ) -> dict[str, dict]:
        return build_identity_candidate_stats_validation(
            candidate_timeline={"players": [{"player_id": "p1", "player_name": "One", "observations": rows}]},
            candidate_stats={
                "players": [{
                    "player_id": "p1",
                    "player_name": "One",
                    "fragment_count": 1,
                    "time": {"playing_time_sec": len(rows) / 10},
                    "distance": {"total_distance_m": 1.0},
                }],
            },
            candidate_diff={"players": [{"player_id": "p1", **(diff or {})}]},
            candidate_manifest=manifest or self.manifest,
            match_doc=self.match,
            candidate_heatmaps={
                "heatmaps": [{
                    "player_id": "p1",
                    "samples": 1,
                    "bins": [{"x": 1, "y": 2, "count": 1}],
                }],
            },
            possession_doc=possession,
            passes_doc=passes,
            events_doc=events,
            generated_at="fixed",
        )

    def test_optional_inputs_do_not_lower_identity_readiness(self) -> None:
        artifacts = self._build([_row(1), _row(2)])
        readiness = artifacts["identity_feature_readiness_candidate.json"]["features"]
        self.assertEqual(readiness["player_identity"]["status"], "ready")
        self.assertEqual(readiness["player_possession"]["status"], "not_available")
        self.assertEqual(readiness["player_passes"]["status"], "not_available")
        self.assertEqual(readiness["player_events"]["status"], "not_available")

    def test_present_event_inputs_are_experimental(self) -> None:
        artifacts = self._build(
            [_row(1), _row(2)],
            possession={"segments": []},
            passes={"candidates": []},
            events={"events": []},
        )
        readiness = artifacts["identity_feature_readiness_candidate.json"]["features"]
        self.assertEqual(readiness["player_possession"]["status"], "experimental")
        self.assertEqual(readiness["player_passes"]["status"], "experimental")
        self.assertEqual(readiness["player_turnovers"]["status"], "experimental")

    def test_predicted_is_counted_but_reported_separately(self) -> None:
        artifacts = self._build([_row(1), _row(2, status="predicted")])
        player = artifacts["identity_candidate_stats_validation.json"]["players"][0]
        self.assertEqual(player["known_on_pitch"]["frames"], 2)
        self.assertEqual(player["status_counts"], {"detected": 1, "predicted": 1})
        self.assertEqual(player["predicted_occluded_share"], 0.5)

    def test_parallel_observation_blocks_identity(self) -> None:
        artifacts = self._build([
            _row(1, subject="s1", tracklet="t1"),
            _row(1, subject="s2", tracklet="t2"),
        ])
        validation = artifacts["identity_candidate_stats_validation.json"]
        self.assertEqual(validation["status"], "blocked")
        self.assertEqual(validation["summary"]["parallel_observations"], 1)

    def test_impossible_jump_is_excluded_by_movement_filter(self) -> None:
        artifacts = self._build([
            _row(1, point=[0.0, 0.0]),
            _row(2, point=[20.0, 0.0]),
        ])
        validation = artifacts["identity_candidate_stats_validation.json"]
        jump = validation["players"][0]["large_spatial_jumps"][0]
        self.assertTrue(jump["excluded_by_distance_calculator"])
        self.assertFalse(jump["affects_stats"])
        self.assertEqual(validation["summary"]["stats_affecting_impossible_jumps"], 0)

    def test_large_delta_links_source_evidence(self) -> None:
        artifacts = self._build(
            [_row(10, subject="s1", review="r1"), _row(20, subject="s2", review="r2")],
            diff={"playing_time_delta_sec": 40, "distance_delta_m": 120},
        )
        delta = artifacts["identity_candidate_stats_validation.json"]["players"][0]["explainable_deltas"][0]
        self.assertEqual(delta["candidate_subject_ids"], ["s1", "s2"])
        self.assertEqual(delta["source_review_card_keys"], ["r1", "r2"])
        self.assertEqual(delta["frame_range"], [10, 20])

    def test_missing_production_baseline_is_not_reported_as_large_delta(self) -> None:
        artifacts = self._build(
            [_row(10, subject="s1", review="r1"), _row(20, subject="s2", review="r2")],
            diff={
                "production_detected_frames": 0,
                "candidate_detected_frames": 2,
                "playing_time_delta_sec": 40,
                "distance_delta_m": 120,
            },
        )
        validation = artifacts["identity_candidate_stats_validation.json"]
        player = validation["players"][0]
        self.assertEqual(player["production_comparison"]["status"], "production_baseline_unavailable")
        self.assertEqual(player["explainable_deltas"], [])
        self.assertEqual(validation["summary"]["large_stat_deltas"], 0)
        self.assertEqual(validation["summary"]["production_baseline_unavailable_players"], 1)
        self.assertIn(
            "production_stats_baseline_unavailable",
            [warning["code"] for warning in validation["warnings"]],
        )

    def test_available_production_baseline_keeps_large_delta(self) -> None:
        artifacts = self._build(
            [_row(10), _row(20)],
            diff={
                "production_detected_frames": 30,
                "candidate_detected_frames": 2,
                "playing_time_delta_sec": 40,
                "distance_delta_m": 120,
            },
        )
        player = artifacts["identity_candidate_stats_validation.json"]["players"][0]
        self.assertEqual(player["production_comparison"]["status"], "available")
        self.assertEqual(len(player["explainable_deltas"]), 1)

    def test_output_is_deterministic(self) -> None:
        left = self._build([_row(1), _row(2)])
        right = self._build([_row(1), _row(2)])
        self.assertEqual(canonical_document_digest(left), canonical_document_digest(right))


if __name__ == "__main__":
    unittest.main()
