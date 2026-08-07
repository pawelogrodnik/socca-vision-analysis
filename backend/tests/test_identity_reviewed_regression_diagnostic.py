from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from app.services.identity_reviewed_regression_diagnostic import (
    build_reviewed_identity_regression_diagnostic,
    classify_observation,
    render_markdown_report,
)


class ReviewedIdentityRegressionDiagnosticTests(unittest.TestCase):
    def test_classifies_stable_and_reviewed_identity_outcomes(self) -> None:
        self.assertEqual(
            classify_observation(_row(global_slot="A07", reviewed_slot="A07")),
            "same",
        )
        self.assertEqual(
            classify_observation(_row(global_slot="A07", reviewed_slot="A02")),
            "reviewed_identity_regression",
        )
        self.assertEqual(
            classify_observation(
                _row(
                    global_slot="A07",
                    tracklet_team="U",
                    reviewed_team="U",
                )
            ),
            "team_only_regression",
        )
        self.assertEqual(
            classify_observation(
                _row(global_slot=None, tracklet_team="U", reviewed_team="U")
            ),
            "upstream_team_unknown",
        )
        self.assertEqual(
            classify_observation(
                _row(
                    global_slot="A02",
                    reviewed_slot="A02",
                    upstream_global_switch=True,
                )
            ),
            "core_stabilization_switch",
        )
        self.assertEqual(
            classify_observation(
                {
                    **_row(global_slot="A07", reviewed_slot="A02"),
                    "reviewed_identity_source": "operator_review",
                }
            ),
            "operator_decision_interaction",
        )
        self.assertEqual(
            classify_observation(_row(global_slot=None, tracklet_team="A")),
            "missing_lineage",
        )

    def test_builds_frame_level_report_without_mutating_source_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            before = _artifact_hashes(root)

            report = build_reviewed_identity_regression_diagnostic(root)

            self.assertEqual(report["summary"]["observations_analyzed"], 5)
            self.assertEqual(report["summary"]["reviewed_identity_regressions"], 1)
            self.assertEqual(report["summary"]["team_u_regressions"], 1)
            self.assertEqual(report["summary"]["upstream_team_unknown"], 1)
            self.assertEqual(report["summary"]["core_stabilization_switches"], 1)
            self.assertEqual(
                report["team_unknown_cases"][
                    "stable_slot_with_tracklet_team_u_observations"
                ],
                1,
            )
            self.assertEqual(report["conclusion"]["verdict"], "mixed_or_inconclusive")
            self.assertEqual(before, _artifact_hashes(root))
            self.assertIn("Reviewed identity regression diagnostic", render_markdown_report(report))

    def test_requires_the_frozen_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(FileNotFoundError, "tracklets.json"):
                build_reviewed_identity_regression_diagnostic(Path(temporary))

    def test_cli_refuses_to_overwrite_a_frozen_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            before = (root / "global_identity.json").read_bytes()
            result = subprocess.run(
                [
                    sys.executable,
                    "backend/scripts/diagnose_reviewed_identity_regression.py",
                    "--match-root",
                    str(root),
                    "--output",
                    str(root / "global_identity.json"),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not overwrite", result.stderr)
            self.assertEqual((root / "global_identity.json").read_bytes(), before)


def _row(
    *,
    global_slot: str | None,
    reviewed_slot: str | None = None,
    tracklet_team: str = "A",
    reviewed_team: str = "A",
    upstream_global_switch: bool = False,
) -> dict[str, object]:
    return {
        "global_stable_player_id": global_slot,
        "stable_player_id": global_slot,
        "reviewed_stable_slot_id": reviewed_slot,
        "tracklet_team_label": tracklet_team,
        "reviewed_team_label": reviewed_team,
        "upstream_global_switch": upstream_global_switch,
    }


def _write_fixture(root: Path) -> None:
    (root / "match.json").write_text(
        json.dumps(
            {
                "id": "diagnostic-match",
                "teams": [{"players": [{"id": "mati", "name": "Mati GK"}]}],
            }
        ),
        encoding="utf-8",
    )
    (root / "tracklets.json").write_text(
        json.dumps(
            {
                "fps": 10,
                "tracklets": [
                    _tracklet("t1", "A", 0),
                    _tracklet("t2", "A", 1),
                    _tracklet("t3", "U", 2),
                    _tracklet("t4", "A", 3),
                    _tracklet("t5", "U", 4),
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "global_identity.json").write_text(
        json.dumps(
            {
                "schema_version": "global-v1",
                "slots": [
                    {
                        "stable_player_id": "A07",
                        "stable_subject_id": "stable-mati",
                        "team_label": "A",
                        "tracklet_ids": ["t1", "t2", "t3"],
                    },
                    {
                        "stable_player_id": "A02",
                        "stable_subject_id": "stable-mati",
                        "team_label": "A",
                        "tracklet_ids": ["t4"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "stable_players.json").write_text(
        json.dumps(
            {
                "schema_version": "stable-v1",
                "players": [
                    {
                        "stable_player_id": "A07",
                        "team_label": "A",
                        "tracklet_ids": ["t1", "t2", "t3"],
                    },
                    {
                        "stable_player_id": "A02",
                        "team_label": "A",
                        "tracklet_ids": ["t4"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "identity_candidate_shadow.json").write_text(
        json.dumps(
            {
                "subjects": [
                    {
                        "candidate_subject_id": "candidate-mati",
                        "candidate_player_id": "A07",
                        "tracklet_ids": ["t1", "t2", "t3", "t4"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "reviewed_identity_snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": "reviewed-v1",
                "tracklet_assignments": [
                    _assignment("t1", "A07", "A", "Mati GK", "mati"),
                    _assignment("t2", "A02", "A", "A02", None),
                    _assignment("t3", None, "U", "U?", None),
                    _assignment("t4", "A02", "A", "A02", None),
                    _assignment("t5", None, "U", "U?", None),
                ],
                "observation_overrides": [],
                "observation_demotions": [],
            }
        ),
        encoding="utf-8",
    )


def _tracklet(tracklet_id: str, team_label: str, frame: int) -> dict[str, object]:
    return {
        "tracklet_id": tracklet_id,
        "team_label": team_label,
        "positions_m": [{"frame": frame, "status": "detected", "source": "detected"}],
    }


def _assignment(
    tracklet_id: str,
    slot: str | None,
    team: str,
    label: str,
    player_id: str | None,
) -> dict[str, object]:
    return {
        "tracklet_id": tracklet_id,
        "stable_anonymous_slot_id": slot,
        "team_label": team,
        "identity_status": "confirmed" if player_id else "unresolved",
        "display_label": label,
        "fallback_label": label,
        "canonical_player_id": player_id,
    }


def _artifact_hashes(root: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.glob("*.json")
    }


if __name__ == "__main__":
    unittest.main()
