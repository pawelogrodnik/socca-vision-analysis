from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.services.identity_jersey_number_common import canonical_digest


BENCHMARK_ROOT = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "player_identity"
    / "jersey-number"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class JerseyNumberBenchmarkContractTests(unittest.TestCase):
    def test_tracked_bundles_have_valid_output_digests(self) -> None:
        for benchmark_name in ("easy90-v1", "hard3m-targeted-v1"):
            root = BENCHMARK_ROOT / benchmark_name
            manifest = _load(root / "benchmark_manifest.json")
            self.assertFalse(manifest["activation_eligible"])
            self.assertIn(
                "heldout_multi_match_validation_required",
                manifest["activation_blockers"],
            )
            for filename, expected_digest in manifest["outputs"].items():
                self.assertEqual(canonical_digest(_load(root / filename)), expected_digest)

    def test_easy90_baseline_has_no_false_assignment_or_automatic_write(self) -> None:
        root = BENCHMARK_ROOT / "easy90-v1"
        evaluation = _load(root / "goldset_summary.json")["evaluation"]
        assignment = _load(root / "assignment_gate_report.json")
        propagation = _load(root / "propagation_report.json")

        self.assertEqual(evaluation["false_positive"], 0)
        self.assertEqual(evaluation["identity_false_assignments"], 0)
        self.assertEqual(evaluation["precision"], 1.0)
        self.assertEqual(assignment["summary"]["would_assign_if_enabled"], 0)
        self.assertEqual(propagation["summary"]["automatic_assignments"], 0)
        self.assertEqual(propagation["summary"]["cross_subject_propagations"], 0)

    def test_hard3m_targeted_baseline_has_safe_hidden_tracklet_match(self) -> None:
        root = BENCHMARK_ROOT / "hard3m-targeted-v1"
        targeted = _load(root / "targeted_evaluation.json")["summary"]

        self.assertTrue(targeted["safety_passed"])
        self.assertEqual(targeted["eligible_hidden_target_tracklets"], 1)
        self.assertEqual(targeted["eligible_matched_hidden_target_tracklets"], 1)
        self.assertEqual(targeted["unexpected_propagated_tracklets"], 0)
        self.assertEqual(targeted["cross_subject_propagations"], 0)
        self.assertEqual(targeted["automatic_assignments"], 0)


if __name__ == "__main__":
    unittest.main()
