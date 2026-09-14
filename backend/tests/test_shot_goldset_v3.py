from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from evaluation.shot_goldset_comparison import compare_shot_goldsets
from evaluation.shot_goldset_v3 import build_shot_goldset_v3


FIXTURES_DIR = Path(__file__).parent / "fixtures"
V1_PATH = FIXTURES_DIR / "shot_goldset_v1.json"
V2_PATH = FIXTURES_DIR / "shot_goldset_v2.json"
V3_PATH = FIXTURES_DIR / "shot_goldset_v3.json"


class ShotGoldsetV3Tests(unittest.TestCase):
    def test_v3_is_a_frozen_canonical_snapshot_and_prior_goldsets_are_byte_stable(self) -> None:
        v3 = _read(V3_PATH)

        self.assertEqual(_digest(V1_PATH), "2cfc5fd7d980c28c51728aa44c0ee25baf96b25aa6a13b9c61ea0a083720d7fb")
        self.assertEqual(_digest(V2_PATH), "35f9dee5675e0666685f97dfecdad3c16751ed7db6d959dead5c6e763fbeff4a")
        self.assertEqual(v3["schema_version"], "shot-goldset:v3")
        self.assertEqual(v3["source_authority"], "canonical_shot_review")
        self.assertEqual(len(v3["shots"]), 35)
        self.assertEqual(v3["hard_negatives"], _read(V2_PATH)["hard_negatives"])
        self.assertEqual([row["timestamp_sec"] for row in v3["shots"]], sorted(row["timestamp_sec"] for row in v3["shots"]))
        added = next(row for row in v3["shots"] if row["id"] == "shot-review-569ab3c8-4932-44e7-a782-22d875882aa6")
        self.assertEqual((added["timestamp_sec"], added["team"], added["outcome"], added["origin"]), (444.5, "Verisk", "blocked", "manual"))
        self.assertEqual((added["timestamp_semantics"], added["timestamp_precision"]), ("pre_event_playback_anchor", "approximate"))
        accepted = next(row for row in v3["shots"] if row["origin"] == "accepted_suggestion")
        self.assertEqual((accepted["timestamp_semantics"], accepted["timestamp_precision"]), ("candidate_event_anchor", "detector_derived"))
        self.assertFalse(any({"revision", "suggested_candidate_id", "confidence", "location_m"} & row.keys() for row in v3["shots"]))

    def test_builder_projects_only_canonical_fields_from_review_state(self) -> None:
        editorial = {
            "schema_version": "shot-review-editorial:v1",
            "canonical_shots": [{
                "shot_id": "canonical-current", "time_sec": 444.5, "team_id": "verisk", "player_id": "b07",
                "outcome": "blocked", "origin": "manual", "revision": 99, "suggested_candidate_id": "private-candidate",
            }],
        }
        report = {
            "teams": [{"team_id": "verisk", "team_name": "Verisk"}],
            "players": [{"player_id": "b07", "player_name": "B07"}],
        }

        first = build_shot_goldset_v3(editorial, report, hard_negatives=[{"id": "hard", "timestamp_sec": 1.0}])
        second = build_shot_goldset_v3(editorial, report, hard_negatives=[{"id": "hard", "timestamp_sec": 1.0}])

        self.assertEqual(first, second)
        self.assertEqual(first["shots"], [{
            "id": "canonical-current", "timestamp_sec": 444.5, "timestamp_display": "07:24.5", "timestamp_semantics": "pre_event_playback_anchor", "timestamp_precision": "approximate",
            "team": "Verisk", "player": "B07", "outcome": "blocked", "origin": "manual", "provenance": "canonical_shot_review",
        }])

    def test_v2_to_v3_reconciliation_identifies_the_added_canonical_shot(self) -> None:
        report = compare_shot_goldsets(_read(V2_PATH), _read(V3_PATH))

        self.assertEqual(report["summary"]["v2_shots"], 34)
        self.assertEqual(report["summary"]["v3_shots"], 35)
        self.assertEqual(report["summary"]["same_action_pairs"], 34)
        self.assertEqual(report["summary"]["v2_only"], 0)
        self.assertEqual(report["summary"]["v3_only"], 1)
        self.assertEqual(report["v3_only"][0]["id"], "shot-review-569ab3c8-4932-44e7-a782-22d875882aa6")

    def test_frozen_v5_audit_covers_exactly_the_incremental_family_and_identity_follow_up(self) -> None:
        audit = _read(FIXTURES_DIR / "shot_v5_weak_boundary_operator_audit_v1.json")
        rows = audit["audits"]

        self.assertEqual(len(rows), 11)
        self.assertEqual(len({row["candidate_id"] for row in rows}), 11)
        self.assertEqual(
            {row["operator_class"] for row in rows},
            {"TRUE_SHOT_MISSING_CANONICAL", "TRUE_SHOT_EXISTING_CANONICAL", "PASS", "PASS_PRE_SHOT_ACTION", "CLEARANCE", "CLEARANCE_OR_LOOSE_BALL_ACTION", "DUEL_OR_TOUCH"},
        )
        identity_follow_up = next(row for row in rows if row["candidate_id"] == "shot-52888cc79b42")
        self.assertTrue(identity_follow_up["identity_followup_required"])

    def test_runtime_never_imports_v3_goldset_or_semantic_audit(self) -> None:
        backend_root = Path(__file__).resolve().parents[1]
        forbidden = (
            "shot_goldset_v3",
            "shot-goldset:v3",
            "shot_v5_weak_boundary_operator_audit",
            "shot_semantic_benchmark",
            "shot_anchor_aware_benchmark",
            "pre_event_playback_anchor",
        )
        for path in (backend_root / "app").rglob("*.py"):
            content = path.read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, content, path.as_posix())


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
