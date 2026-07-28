from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_operator_seed_digest import (
    identity_operator_seed_decisions_digest,
)
from app.services.identity_seeded_subject_review_rebuild import (
    rebuild_identity_seeded_subject_review,
    seeded_assignments_as_roster_assignments,
)


def candidate(subject_id: str, production_subject_id: str, tracklet_id: str) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "candidate_player_id": production_subject_id.replace("slot-", ""),
        "team_label": "A",
        "role": "field_player",
        "tracklet_ids": [tracklet_id],
        "production_subject_ids": [production_subject_id],
        "start_frame": 0,
        "end_frame": 9,
        "detected_frames": 10,
        "quality_flags": [],
    }


def timeline_subject(subject_id: str, tracklet_id: str, x: float) -> dict:
    return {
        "shadow_subject_id": subject_id,
        "team_label": "A",
        "tracklet_ids": [tracklet_id],
        "start_frame": 0,
        "end_frame": 9,
        "observations": [
            {
                "frame": frame,
                "time_sec": frame / 30.0,
                "status": "detected",
                "tracklet_id": tracklet_id,
                "pitch_m": [x, 20.0],
                "bbox_xyxy": [x * 10.0, 100.0, x * 10.0 + 40.0, 210.0],
                "confidence": 0.95,
                "quality_class": "trusted",
                "team_confidence": 1.0,
                "appearance_reliable_ratio": 1.0,
                "appearance_reliable": True,
                "footpoint_reliable": True,
                "play_area_status": "inside_play",
            }
            for frame in (0, 4, 9)
        ],
    }


def accepted_assignment(subject_id: str, player_id: str) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "team_label": "A",
        "start_frame": 0,
        "end_frame": 9,
        "assigned_player": {
            "player_id": player_id,
            "player_name": "Player One",
            "team_label": "A",
        },
        "seed_observations": [
            {
                "observation_key": "observation-1",
                "frame_number": 4,
                "tracklet_id": "track-1",
            }
        ],
    }


MATCH = {
    "id": "match-1",
    "teams": [
        {
            "id": "team-a",
            "name": "A",
            "players": [
                {"id": "p1", "name": "Player One"},
                {"id": "p2", "name": "Player Two"},
            ],
        },
        {"id": "team-b", "name": "B", "players": []},
    ],
}


class IdentitySeededSubjectReviewRebuildTests(unittest.TestCase):
    def test_seed_adapter_maps_candidate_to_production_subject(self) -> None:
        result = seeded_assignments_as_roster_assignments(
            {"subjects": [candidate("shadow-a-1", "slot-A01", "track-1")]},
            {"accepted_assignments": [accepted_assignment("shadow-a-1", "p1")]},
        )

        self.assertEqual(result["summary"]["assignments"], 1)
        self.assertEqual(
            result["assignments"][0]["stable_subject_id"],
            "slot-A01",
        )
        self.assertEqual(
            result["assignments"][0]["assignment_source"],
            "initial_identity_audit",
        )
        self.assertEqual(
            result["assignments"][0]["anchor_artifacts"],
            ["observation-1"],
        )

    def test_fresh_match_bootstraps_review_and_reduces_seeded_card(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates = {
                "algorithm": {"name": "candidate"},
                "subjects": [
                    candidate("shadow-a-1", "slot-A01", "track-1"),
                    candidate("shadow-a-2", "slot-A02", "track-2"),
                ],
            }
            timeline = {
                "algorithm": {"name": "timeline"},
                "subjects": [
                    timeline_subject("shadow-a-1", "track-1", 10.0),
                    timeline_subject("shadow-a-2", "track-2", 20.0),
                ],
            }
            seeds = {"schema_version": "0.1.0", "decisions": []}
            seeded = {
                "algorithm": {"name": "seeded"},
                "source": {
                    "operator_seed_decisions_digest": (
                        identity_operator_seed_decisions_digest(seeds)
                    )
                },
                "accepted_assignments": [
                    accepted_assignment("shadow-a-1", "p1")
                ],
                "conflicts": [],
                "summary": {
                    "subjects_resolved_after_seeding": 1,
                    "tracklets_resolved_after_seeding": 1,
                    "frames_resolved_after_seeding": 10,
                    "conflicts_created": 0,
                },
                "safety": {"production_identity_untouched": True},
            }
            documents = {
                "identity_candidate_shadow.json": candidates,
                "identity_offline_shadow_timeline.json": timeline,
                "identity_seeded_candidate_assignments.json": seeded,
                "identity_operator_seeds.json": seeds,
            }
            for filename, document in documents.items():
                (root / filename).write_text(
                    json.dumps(document),
                    encoding="utf-8",
                )
            video_path = root / "video.mp4"
            video_path.touch()
            rendered: list[str] = []

            def fake_renderer(
                _video_path: Path,
                _output_root: Path,
                artifact: dict,
            ) -> set[str]:
                rendered.extend(
                    str(crop["artifact"])
                    for card in artifact.get("cards") or []
                    for crop in card.get("anchor_crops") or []
                )
                return set(rendered)

            result = rebuild_identity_seeded_subject_review(
                root,
                MATCH,
                video_path=video_path,
                crop_renderer=fake_renderer,
                appearance_embedder_loader=lambda _models_dir: (
                    None,
                    {"available": False, "reason": "test"},
                ),
            )

            self.assertTrue(
                (root / "identity_roster_subject_review_shadow.json").exists()
            )
            self.assertTrue(
                (root / "identity_seeded_review_reduction_report.json").exists()
            )
            self.assertEqual(result["status"], "fresh")
            self.assertEqual(result["summary"]["cards"], 2)
            self.assertEqual(
                result["summary"]["initial_audit_completed_cards"],
                1,
            )
            self.assertEqual(result["summary"]["pending_cards"], 1)
            self.assertEqual(
                result["initial_audit_integration"]["metrics"][
                    "review_cards_reduced"
                ],
                1,
            )
            self.assertEqual(result["seed_adapter_summary"]["assignments"], 1)
            self.assertEqual(result["rendered_anchor_crops"], 6)
            self.assertEqual(
                result["approved_appearance_gallery_summary"]["players"],
                1,
            )
            self.assertEqual(
                result["approved_appearance_gallery_summary"][
                    "operator_actions_required"
                ],
                0,
            )
            self.assertTrue(
                (
                    root
                    / "identity_approved_appearance_gallery.json"
                ).exists()
            )
            self.assertTrue(
                (
                    root
                    / "identity_approved_appearance_reid_shadow.json"
                ).exists()
            )
            self.assertTrue(result["safety"]["production_identity_untouched"])


if __name__ == "__main__":
    unittest.main()
