from __future__ import annotations

import unittest

from app.services.identity_fragment_consolidation_shadow import (
    build_identity_fragment_consolidation_shadow,
)


class IdentityFragmentConsolidationShadowTests(unittest.TestCase):
    def test_proposes_only_adjacent_same_team_safe_anchor_fragments(self) -> None:
        documents = build_identity_fragment_consolidation_shadow(
            _candidate_doc(
                _subject("left", "A01", "A", "slot-A01", 0, 9),
                _subject("right", "A01~2", "A", "slot-A01", 11, 20),
            ),
            _overlay_doc(
                _player("left", 9, [10.0, 10.0]),
                _player("right", 11, [10.5, 10.0]),
            ),
            _active_doc("left", "right"),
            fps=10.0,
            generated_at="fixed",
        )

        proposals = documents["identity_fragment_consolidation_shadow"]["proposals"]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["decision"], "recommended_review")
        self.assertTrue(proposals[0]["requires_visual_review"])
        self.assertEqual(proposals[0]["shared_production_anchor"], "slot-A01")

    def test_rejects_parallel_fragments_instead_of_merging_them(self) -> None:
        documents = build_identity_fragment_consolidation_shadow(
            _candidate_doc(
                _subject("left", "A01", "A", "slot-A01", 0, 20),
                _subject("right", "A01~2", "A", "slot-A01", 10, 30),
            ),
            _overlay_doc(
                _player("left", 20, [10.0, 10.0]),
                _player("right", 10, [10.0, 10.0]),
            ),
            _active_doc("left", "right"),
            fps=10.0,
            generated_at="fixed",
        )

        proposal = documents["identity_fragment_consolidation_shadow"]
        report = documents["identity_fragment_consolidation_shadow_report"]
        self.assertEqual(proposal["proposals"], [])
        self.assertEqual(report["rejected_pairs"][0]["rejection_reason"], "parallel_temporal_overlap")

    def test_excludes_cross_team_anchor_mismatch(self) -> None:
        mismatched = _subject("wrong", "B-new01", "B", "slot-A06", 0, 10)
        mismatched["quality_flags"] = ["production_anchor_team_mismatch"]
        documents = build_identity_fragment_consolidation_shadow(
            _candidate_doc(mismatched),
            _overlay_doc(_player("wrong", 10, [10.0, 10.0], team="B")),
            _active_doc("wrong"),
            fps=10.0,
            generated_at="fixed",
        )

        report = documents["identity_fragment_consolidation_shadow_report"]
        self.assertEqual(report["summary"]["eligible_fragment_subjects"], 0)
        self.assertEqual(report["excluded_subjects"][0]["reason"], "production_anchor_team_mismatch")
        self.assertTrue(all(report["gates"].values()))


def _subject(
    subject_id: str,
    player_id: str,
    team: str,
    anchor: str,
    start_frame: int,
    end_frame: int,
) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "candidate_player_id": player_id,
        "team_label": team,
        "production_subject_ids": [anchor],
        "quality_flags": ["splits_production_subject"],
        "start_frame": start_frame,
        "end_frame": end_frame,
    }


def _candidate_doc(*subjects: dict) -> dict:
    return {"algorithm": {"name": "candidate"}, "subjects": list(subjects)}


def _player(subject_id: str, frame: int, pitch_m: list[float], *, team: str = "A") -> dict:
    return {
        "candidate_subject_id": subject_id,
        "team_label": team,
        "overlay_positions": [
            {
                "frame": frame,
                "pitch_m": pitch_m,
                "bbox_xyxy": [10, 10, 20, 40],
                "confidence": 0.9,
                "source": "detected",
            }
        ],
    }


def _overlay_doc(*players: dict) -> dict:
    return {"players": list(players)}


def _active_doc(*subject_ids: str) -> dict:
    return {
        "algorithm": {"name": "active"},
        "subjects": [
            {
                "candidate_subject_id": subject_id,
                "active_frames": 10,
                "suppressed_frames": 0,
            }
            for subject_id in subject_ids
        ],
    }


if __name__ == "__main__":
    unittest.main()
