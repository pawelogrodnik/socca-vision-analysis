from __future__ import annotations

import unittest

from app.services.identity_partial_candidate import build_partial_candidate_artifacts


def _row(frame: int, *, status: str = "detected", subject: str = "s1", point=None) -> dict:
    return {
        "frame": frame,
        "time_sec": frame / 10,
        "player_id": "p1",
        "candidate_subject_id": subject,
        "tracklet_id": "t1",
        "review_card_key": "r1",
        "status": status,
        "pitch_m": point if point is not None else [float(frame), 2.0],
        "play_area_status": "inside_play",
        "eligible_for_distance": status == "detected",
        "eligible_for_heatmap": status == "detected",
    }


class IdentityPartialCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.match = {
            "video": {"fps": 10, "duration_sec": 10},
            "teams": [{"id": "ta", "players": [{"id": "p1", "name": "One"}]}],
        }
        self.promotion = {"unresolved_subjects": [], "structural_subjects": []}

    def test_predicted_contributes_time_but_not_distance_or_heatmap(self) -> None:
        remediation = {
            "eligible_observations": [_row(1), _row(2, status="predicted", point=[20, 20])],
            "excluded_fragments": [], "unresolved_fragments": [], "errors": [],
        }
        artifacts = build_partial_candidate_artifacts(self.promotion, remediation, self.match, generated_at="fixed")
        stats = artifacts["resolved_player_stats_candidate_v2.json"]["players"][0]
        heatmap = artifacts["player_heatmaps_candidate_v2.json"]["heatmaps"][0]
        self.assertEqual(stats["time"]["playing_time_sec"], 0.2)
        self.assertEqual(stats["distance"]["total_distance_m"], 0.0)
        self.assertEqual(heatmap["samples"], 1)

    def test_separate_fragments_do_not_create_teleport_distance(self) -> None:
        remediation = {
            "eligible_observations": [
                _row(1, subject="s1", point=[0, 0]),
                _row(2, subject="s1", point=[0.5, 0]),
                _row(8, subject="s2", point=[25, 0]),
                _row(9, subject="s2", point=[25.5, 0]),
            ],
            "excluded_fragments": [], "unresolved_fragments": [], "errors": [],
        }
        artifacts = build_partial_candidate_artifacts(self.promotion, remediation, self.match, generated_at="fixed")
        stats = artifacts["resolved_player_stats_candidate_v2.json"]["players"][0]
        self.assertEqual(stats["distance"]["total_distance_m"], 1.0)

    def test_unresolved_is_partial_and_does_not_contribute(self) -> None:
        remediation = {
            "eligible_observations": [_row(1)],
            "excluded_fragments": [],
            "unresolved_fragments": [{"fragment_key": "u"}],
            "errors": [],
        }
        artifacts = build_partial_candidate_artifacts(self.promotion, remediation, self.match, generated_at="fixed")
        manifest = artifacts["identity_candidate_apply_manifest.json"]
        self.assertEqual(manifest["status"], "partial_candidate")
        self.assertEqual(manifest["coverage"]["eligible_observations"], 1)

    def test_parallel_player_frame_blocks_candidate(self) -> None:
        left = _row(1, subject="s1")
        right = {**_row(1, subject="s2"), "tracklet_id": "t2"}
        remediation = {
            "eligible_observations": [left, right],
            "excluded_fragments": [], "unresolved_fragments": [], "errors": [],
        }
        artifacts = build_partial_candidate_artifacts(self.promotion, remediation, self.match, generated_at="fixed")
        self.assertEqual(artifacts["identity_candidate_apply_manifest.json"]["status"], "blocked")

    def test_candidate_build_is_deterministic(self) -> None:
        from app.services.identity_promotion_safety import canonical_document_digest
        remediation = {
            "eligible_observations": [_row(1), _row(2)],
            "excluded_fragments": [], "unresolved_fragments": [], "errors": [],
        }
        left = build_partial_candidate_artifacts(self.promotion, remediation, self.match, generated_at="fixed")
        right = build_partial_candidate_artifacts(self.promotion, remediation, self.match, generated_at="fixed")
        self.assertEqual(canonical_document_digest(left), canonical_document_digest(right))


if __name__ == "__main__":
    unittest.main()
