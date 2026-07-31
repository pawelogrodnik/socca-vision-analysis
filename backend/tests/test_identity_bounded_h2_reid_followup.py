from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_bounded_h2_reid_followup import (
    _select_temporally_diverse,
    save_bounded_h2_reid_decisions,
    verify_frozen_bounded_h2_rankings,
)
from app.services.identity_jersey_number_common import canonical_digest


class BoundedH2ReIdFollowupTests(unittest.TestCase):
    def test_selection_is_bounded_unique_and_uses_no_truth_field(self) -> None:
        eligible = [
            _eligible(f"subject-{index}", 100 + index % 2, index / 100.0)
            for index in range(8)
        ]
        selected = _select_temporally_diverse(eligible)

        self.assertLessEqual(len(selected), 5)
        self.assertEqual(
            len({row["subject"]["candidate_subject_id"] for row in selected}),
            len(selected),
        )
        self.assertTrue(
            all("ground_truth_player_id" not in row for row in selected)
        )

    def test_operator_decision_cannot_change_frozen_ranking_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = _selection()
            ranking = {
                "schema_version": "1.0.0",
                "rankings": [{"candidate_subject_id": "subject-a"}],
            }
            ranking_digest = canonical_digest(ranking)
            selection["ranking_digest"] = ranking_digest
            selection["selection_digest"] = canonical_digest(selection)
            _write(root / "bounded_h2_selection.json", selection)
            _write(root / "preferred_rankings_frozen.json", ranking)
            _write(
                root / "operator_decisions.json",
                {
                    "schema_version": "1.0.0",
                    "session_id": selection["session_id"],
                    "selection_digest": selection["selection_digest"],
                    "ranking_digest": ranking_digest,
                    "decisions": [],
                    "finished": False,
                },
            )

            result = save_bounded_h2_reid_decisions(
                root,
                updates=[{
                    "selection_digest": selection["selection_digest"],
                    "candidate_subject_id": "subject-a",
                    "observation_key": "observation-a",
                    "frame": 100,
                    "tracklet_id": "tracklet-a",
                    "action": "player",
                    "player_id": "player-a",
                }],
            )

            self.assertEqual(result["ranking_digest"], ranking_digest)
            self.assertEqual(
                canonical_digest(
                    json.loads(
                        (root / "preferred_rankings_frozen.json").read_text()
                    )
                ),
                ranking_digest,
            )
            self.assertFalse(
                result["cards"][0]["preferred_advisory"]["visible"]
            )
            self.assertEqual(
                result["cards"][0]["preferred_advisory"]["top3"],
                [],
            )

    def test_cross_team_player_decision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = _selection()
            selection["roster"].append({
                "team_label": "B",
                "players": [{
                    "player_id": "player-b",
                    "player_name": "B",
                }],
            })
            selection["selection_digest"] = canonical_digest(selection)
            _write(root / "bounded_h2_selection.json", selection)
            _write(
                root / "operator_decisions.json",
                {
                    "selection_digest": selection["selection_digest"],
                    "decisions": [],
                    "finished": False,
                },
            )

            with self.assertRaisesRegex(ValueError, "Cross-team"):
                save_bounded_h2_reid_decisions(
                    root,
                    updates=[{
                        "selection_digest": selection["selection_digest"],
                        "candidate_subject_id": "subject-a",
                        "observation_key": "observation-a",
                        "frame": 100,
                        "tracklet_id": "tracklet-a",
                        "action": "player",
                        "player_id": "player-b",
                    }],
                )

    def test_session_cannot_finish_with_missing_card_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = _selection()
            second_card = {**selection["cards"][0]}
            second_card["candidate_subject_id"] = "subject-b"
            second_card["observation_key"] = "observation-b"
            second_card["tracklet_id"] = "tracklet-b"
            selection["cards"].append(second_card)
            selection["selection_digest"] = canonical_digest(selection)
            _write(root / "bounded_h2_selection.json", selection)
            _write(
                root / "operator_decisions.json",
                {"selection_digest": selection["selection_digest"], "decisions": [], "finished": False},
            )

            with self.assertRaisesRegex(ValueError, "missing card decisions"):
                save_bounded_h2_reid_decisions(
                    root,
                    updates=[{
                        "selection_digest": selection["selection_digest"],
                        "candidate_subject_id": "subject-a",
                        "observation_key": "observation-a",
                        "frame": 100,
                        "tracklet_id": "tracklet-a",
                        "action": "unknown",
                    }],
                    finished=True,
                )

    def test_frozen_ranking_verification_detects_roster_and_team_errors(self) -> None:
        selection = _selection()
        selection["selection_digest"] = canonical_digest(selection)
        frozen = {
            "rankings": [{
                "candidate_subject_id": "subject-a",
                "team_label": "A",
                "suggestions": [
                    {"player_id": "player-a"},
                    {"player_id": "player-a"},
                    {"player_id": "player-missing"},
                ],
            }],
        }
        verification = verify_frozen_bounded_h2_rankings(selection, frozen)

        self.assertEqual(verification["totals"]["duplicate_ranked_players"], 1)
        self.assertEqual(verification["totals"]["missing_roster_players"], 1)
        self.assertEqual(verification["totals"]["invalid_ranked_players"], 2)
        self.assertFalse(verification["historical_result_independently_confirmed"])


def _eligible(subject_id: str, frame: int, score: float) -> dict:
    return {
        "subject": {"candidate_subject_id": subject_id},
        "observation": {"frame": frame},
        "selection_score": (score, 0.0, frame, subject_id),
    }


def _selection() -> dict:
    return {
        "schema_version": "1.0.0",
        "session_id": "test-session",
        "status": "BOUNDED_H2_OPERATOR_INPUT_REQUIRED",
        "ranking_digest": "pending",
        "cards": [{
            "candidate_subject_id": "subject-a",
            "observation_key": "observation-a",
            "frame": 100,
            "tracklet_id": "tracklet-a",
            "bbox_xyxy": [1, 2, 3, 4],
            "source_artifact_digest": "source",
            "team_label": "A",
            "preferred_advisory": {
                "visible": False,
                "top3": [],
            },
        }],
        "roster": [{
            "team_label": "A",
            "players": [{
                "player_id": "player-a",
                "player_name": "A",
            }],
        }],
    }


def _write(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")
