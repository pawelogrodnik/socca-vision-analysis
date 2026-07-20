from __future__ import annotations

import unittest

from app.services.identity_fragment_endpoint_reliability import (
    assess_fragment_endpoint_reliability,
    evaluate_fragment_endpoint_reliability,
    summarize_endpoint_pair,
)


class IdentityFragmentEndpointReliabilityTests(unittest.TestCase):
    def test_marks_locally_consistent_endpoint_reliable(self) -> None:
        result = assess_fragment_endpoint_reliability(
            _player(*[_position(frame, [frame * 0.05, 2.0]) for frame in range(1, 6)]),
            at_end=True,
            fps=10.0,
        )

        self.assertEqual(result["quality"], "locally_consistent")
        self.assertEqual(result["reason_codes"], [])
        self.assertEqual(result["metrics"]["context_observations"], 4)

    def test_marks_sudden_tiny_endpoint_for_review(self) -> None:
        rows = [_position(frame, [frame * 0.05, 2.0]) for frame in range(1, 6)]
        rows[-1]["bbox_xyxy"] = [10.0, 10.0, 12.0, 13.0]

        result = assess_fragment_endpoint_reliability(
            _player(*rows),
            at_end=True,
            fps=10.0,
        )

        self.assertEqual(result["quality"], "review")
        self.assertIn("endpoint_area_inconsistent_with_context", result["reason_codes"])

    def test_missing_bbox_is_invalid(self) -> None:
        rows = [_position(frame, [frame * 0.05, 2.0]) for frame in range(1, 6)]
        rows[-1]["bbox_xyxy"] = None

        result = assess_fragment_endpoint_reliability(
            _player(*rows),
            at_end=True,
            fps=10.0,
        )

        self.assertEqual(result["quality"], "invalid")
        self.assertIn("invalid_endpoint_bbox", result["reason_codes"])

    def test_sparse_context_is_review_not_reliable(self) -> None:
        result = assess_fragment_endpoint_reliability(
            _player(_position(10, [1.0, 2.0])),
            at_end=True,
            fps=10.0,
        )

        self.assertEqual(result["quality"], "review")
        self.assertIn("insufficient_local_context", result["reason_codes"])

    def test_pair_summary_never_authorizes_identity_merge(self) -> None:
        summary = summarize_endpoint_pair(
            {"quality": "locally_consistent", "reason_codes": []},
            {"quality": "locally_consistent", "reason_codes": []},
        )

        self.assertEqual(summary["quality"], "locally_consistent")
        self.assertFalse(summary["safe_for_automatic_identity_merge"])
        self.assertFalse(summary["visual_content_verified"])

    def test_evaluation_keeps_endpoint_quality_advisory(self) -> None:
        report = evaluate_fragment_endpoint_reliability(
            {
                "items": [
                    {
                        "benchmark_id": "easy",
                        "candidate_key": "link:1",
                        "review_status": "confirmed_same",
                    }
                ]
            },
            {
                "easy": {
                    "proposals": [
                        {
                            "proposal_key": "link:1",
                            "endpoint_reliability": {
                                "quality": "locally_consistent",
                                "safe_for_automatic_identity_merge": False,
                            },
                        }
                    ]
                }
            },
            generated_at="fixed",
        )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(
            report["summary"]["matrix"]["confirmed_same"]["locally_consistent"],
            1,
        )
        self.assertTrue(report["gates"]["endpoint_quality_is_advisory_only"])


def _player(*positions: dict) -> dict:
    return {"overlay_positions": list(positions)}


def _position(frame: int, pitch_m: list[float]) -> dict:
    return {
        "frame": frame,
        "source": "detected",
        "pitch_m": pitch_m,
        "bbox_xyxy": [10.0, 10.0, 20.0, 40.0],
        "confidence": 0.9,
        "play_area_status": "inside_play",
        "quality_class": "trusted",
        "footpoint_reliable": True,
        "appearance_reliable": True,
    }


if __name__ == "__main__":
    unittest.main()
