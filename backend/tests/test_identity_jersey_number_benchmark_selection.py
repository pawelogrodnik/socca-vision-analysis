from __future__ import annotations

import unittest

from app.services.identity_jersey_number_benchmark_selection import (
    build_targeted_jersey_number_benchmark,
)


class JerseyNumberBenchmarkSelectionTests(unittest.TestCase):
    def test_selects_only_one_seed_tracklet_and_hides_targets(self) -> None:
        result = build_targeted_jersey_number_benchmark(
            _anchor_doc(),
            _candidate_doc(),
            generated_at="fixed",
        )

        self.assertEqual(result["summary"]["selected_subjects"], 1)
        self.assertEqual(result["summary"]["selected_crops"], 4)
        card = result["cards"][0]
        self.assertEqual({crop["tracklet_id"] for crop in card["anchor_crops"]}, {"t2"})
        self.assertEqual(card["benchmark_selection"]["target_tracklet_ids"], ["t1"])
        self.assertTrue(card["benchmark_selection"]["target_crops_intentionally_hidden"])

    def test_excludes_other_team_and_single_tracklet_subjects(self) -> None:
        anchor = _anchor_doc()
        anchor["cards"].append(_card("s2", "B", "b1", 4))
        anchor["cards"].append(_card("s3", "A", "a1", 4))
        candidate = _candidate_doc()
        candidate["subjects"].extend(
            [
                {"candidate_subject_id": "s2", "tracklet_ids": ["b1", "b2"]},
                {"candidate_subject_id": "s3", "tracklet_ids": ["a1"]},
            ]
        )

        result = build_targeted_jersey_number_benchmark(
            anchor,
            candidate,
            generated_at="fixed",
        )

        self.assertEqual([card["candidate_subject_id"] for card in result["cards"]], ["s1"])
        self.assertEqual(result["summary"]["rejection_counts"]["different_team"], 1)
        self.assertEqual(result["summary"]["rejection_counts"]["single_tracklet_subject"], 1)

    def test_limits_are_deterministic(self) -> None:
        anchor = {"cards": [_card("s1", "A", "t1", 4), _card("s2", "A", "u1", 5)]}
        candidate = {
            "subjects": [
                {"candidate_subject_id": "s1", "tracklet_ids": ["t1", "t2"]},
                {"candidate_subject_id": "s2", "tracklet_ids": ["u1", "u2"]},
            ]
        }

        first = build_targeted_jersey_number_benchmark(
            anchor,
            candidate,
            max_subjects=1,
            max_crops=5,
            generated_at="fixed",
        )
        second = build_targeted_jersey_number_benchmark(
            anchor,
            candidate,
            max_subjects=1,
            max_crops=5,
            generated_at="fixed",
        )

        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["selected_subjects"], 1)
        self.assertLessEqual(first["summary"]["selected_crops"], 5)

    def test_rejects_consecutive_crops_that_cannot_satisfy_consensus(self) -> None:
        anchor = {"cards": [_card("s1", "A", "t1", 4, frame_step=1)]}
        candidate = {
            "subjects": [{"candidate_subject_id": "s1", "tracklet_ids": ["t1", "t2"]}]
        }

        result = build_targeted_jersey_number_benchmark(
            anchor,
            candidate,
            generated_at="fixed",
        )

        self.assertEqual(result["summary"]["selected_subjects"], 0)
        self.assertEqual(
            result["summary"]["rejection_counts"]["no_consensus_eligible_seed_tracklet"],
            1,
        )


def _anchor_doc() -> dict:
    card = _card("s1", "A", "t1", 3)
    card["anchor_crops"].extend(_crops("t2", 4, frame_offset=100))
    return {"cards": [card]}


def _candidate_doc() -> dict:
    return {"subjects": [{"candidate_subject_id": "s1", "tracklet_ids": ["t1", "t2"]}]}


def _card(
    subject_id: str,
    team: str,
    tracklet_id: str,
    crop_count: int,
    *,
    frame_step: int = 50,
) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "team_label": team,
        "status": "ready_for_visual_audit",
        "anchor_crops": _crops(tracklet_id, crop_count, frame_step=frame_step),
        "selected_crop_count": crop_count,
    }


def _crops(
    tracklet_id: str,
    count: int,
    *,
    frame_offset: int = 0,
    frame_step: int = 50,
) -> list[dict]:
    return [
        {
            "anchor_crop_id": f"{tracklet_id}-{index}",
            "tracklet_id": tracklet_id,
            "frame": frame_offset + index * frame_step,
            "bbox_xyxy": [0, 0, 40 + index, 80 + index],
            "detection_confidence": 0.9,
            "artifact": f"anchor_crops/s/{tracklet_id}-{index}.jpg",
        }
        for index in range(count)
    ]


if __name__ == "__main__":
    unittest.main()
