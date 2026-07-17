from __future__ import annotations

from copy import deepcopy
import unittest

from app.services.identity_occlusion_assignment_shadow import build_shadow_occlusion_assignments


FPS = 30.0


def tracklet(tracklet_id: str, start: int, end: int, *, x: float, team: str = "A") -> dict:
    positions = [
        {
            "frame": frame,
            "time_sec": frame / FPS,
            "pitch_m": [x, 20.0],
            "smoothed_pitch_m": [x, 20.0],
            "bbox_xyxy": [int(x * 10), 100, int(x * 10) + 24, 170],
            "confidence": 0.9,
            "play_area_status": "inside_play",
        }
        for frame in range(start, end + 1)
    ]
    return {
        "tracklet_id": tracklet_id,
        "source_tracker_id": int(tracklet_id.split(":")[0]),
        "start_time_sec": start / FPS,
        "end_time_sec": end / FPS,
        "positions": positions,
        "first_pitch_m": [x, 20.0],
        "last_pitch_m": [x, 20.0],
        "first_bbox_xyxy": positions[0]["bbox_xyxy"],
        "last_bbox_xyxy": positions[-1]["bbox_xyxy"],
        "team_label": team,
        "team_confidence": 0.95,
        "role": "field_player",
        "role_confidence": 0.8,
        "appearance_rgb": [120.0, 120.0, 120.0],
    }


def quality_doc(rows: list[dict]) -> dict:
    return {
        "tracklets": [
            {
                "tracklet_id": row["tracklet_id"],
                "status": "clean",
                "quality_class": "recoverable",
                "quality_confidence": 0.9,
                "inside_pitch_ratio": 1.0,
                "team_label": row["team_label"],
            }
            for row in rows
        ]
    }


def occlusion_doc(*, team: str = "A", duplicate: bool = False) -> dict:
    event = {
        "event_id": "occlusion-1",
        "start_frame": 20,
        "end_frame": 21,
        "team_labels": [team],
        "confidence": 0.9,
    }
    events = [event]
    if duplicate:
        events.append({**event, "event_id": "occlusion-2", "start_frame": 21, "end_frame": 22})
    return {"events": events}


def identity(source_one: str, target_one: str, source_two: str, target_two: str) -> dict:
    return {
        "slots": [
            {"stable_subject_id": "A01", "tracklet_ids": [source_one, target_one]},
            {"stable_subject_id": "A02", "tracklet_ids": [source_two, target_two]},
        ]
    }


class ShadowOcclusionAssignmentTests(unittest.TestCase):
    def test_joint_assignment_keeps_current_identity_when_motion_agrees(self) -> None:
        rows = [
            tracklet("1:1", 0, 19, x=2.0),
            tracklet("2:1", 0, 19, x=8.0),
            tracklet("3:1", 22, 40, x=2.1),
            tracklet("4:1", 22, 40, x=8.1),
        ]
        document = build_shadow_occlusion_assignments(
            rows,
            quality_doc(rows),
            occlusion_doc(),
            identity("1:1", "3:1", "2:1", "4:1"),
            fps=FPS,
            generated_at="fixed",
        )

        self.assertEqual(document["summary"]["joint_occlusion_cases"], 1)
        self.assertEqual(document["cases"][0]["decision"]["status"], "keep_current")
        self.assertEqual(document["cases"][0]["decision"]["recommended_assignment_id"], "assignment_a")

    def test_joint_assignment_reports_suspected_swap(self) -> None:
        rows = [
            tracklet("1:1", 0, 19, x=2.0),
            tracklet("2:1", 0, 19, x=8.0),
            tracklet("3:1", 22, 40, x=8.1),
            tracklet("4:1", 22, 40, x=2.1),
        ]
        document = build_shadow_occlusion_assignments(
            rows,
            quality_doc(rows),
            occlusion_doc(),
            identity("1:1", "3:1", "2:1", "4:1"),
            fps=FPS,
            generated_at="fixed",
        )

        decision = document["cases"][0]["decision"]
        self.assertEqual(decision["status"], "suspected_swap")
        self.assertEqual(decision["recommended_assignment_id"], "assignment_b")
        self.assertEqual(decision["current_assignment_id"], "assignment_a")

    def test_close_assignments_remain_ambiguous(self) -> None:
        rows = [
            tracklet("1:1", 0, 19, x=2.0),
            tracklet("2:1", 0, 19, x=2.2),
            tracklet("3:1", 22, 40, x=2.1),
            tracklet("4:1", 22, 40, x=2.3),
        ]
        document = build_shadow_occlusion_assignments(
            rows,
            quality_doc(rows),
            occlusion_doc(),
            identity("1:1", "3:1", "2:1", "4:1"),
            fps=FPS,
            generated_at="fixed",
        )

        self.assertEqual(document["cases"][0]["decision"]["status"], "ambiguous")
        self.assertIn("assignment_margin_too_small", document["cases"][0]["decision"]["reasons"])

    def test_one_unreliable_target_produces_partial_continuation(self) -> None:
        rows = [
            tracklet("1:1", 0, 19, x=2.0),
            tracklet("2:1", 0, 19, x=8.0),
            tracklet("3:1", 22, 40, x=2.1),
            tracklet("4:1", 22, 40, x=8.1),
        ]
        rows[3]["positions"][0]["bbox_xyxy"] = [80, 150, 106, 164]
        rows[3]["positions"][0]["confidence"] = 0.10
        document = build_shadow_occlusion_assignments(
            rows,
            quality_doc(rows),
            occlusion_doc(),
            identity("1:1", "3:1", "2:1", "4:1"),
            fps=FPS,
            generated_at="fixed",
        )

        case = document["cases"][0]
        self.assertFalse(case["endpoint_reliability"]["targets"]["4:1"]["reliable"])
        self.assertEqual(case["decision"]["status"], "partial_continuation")
        self.assertEqual(case["decision"]["recommended_assignment_id"], "partial")
        self.assertEqual(
            case["decision"]["recommended_pairs"],
            [{"source_tracklet_id": "1:1", "target_tracklet_id": "3:1"}],
        )

    def test_truncated_source_endpoint_does_not_break_clear_full_assignment(self) -> None:
        rows = [
            tracklet("1:1", 0, 19, x=2.0),
            tracklet("2:1", 0, 19, x=8.0),
            tracklet("3:1", 22, 40, x=2.1),
            tracklet("4:1", 22, 40, x=8.1),
        ]
        rows[0]["positions"][-1]["bbox_xyxy"] = [20, 140, 40, 168]
        rows[0]["positions"][-1]["confidence"] = 0.06
        document = build_shadow_occlusion_assignments(
            rows,
            quality_doc(rows),
            occlusion_doc(),
            identity("1:1", "3:1", "2:1", "4:1"),
            fps=FPS,
            generated_at="fixed",
        )

        case = document["cases"][0]
        self.assertFalse(case["endpoint_reliability"]["sources"]["1:1"]["reliable"])
        self.assertEqual(case["decision"]["status"], "keep_current")
        self.assertEqual(case["decision"]["recommended_assignment_id"], "assignment_a")

    def test_cross_team_event_is_ignored(self) -> None:
        rows = [
            tracklet("1:1", 0, 19, x=2.0),
            tracklet("2:1", 0, 19, x=8.0),
            tracklet("3:1", 22, 40, x=2.1),
            tracklet("4:1", 22, 40, x=8.1),
        ]
        event = occlusion_doc()
        event["events"][0]["team_labels"] = ["A", "B"]
        document = build_shadow_occlusion_assignments(
            rows,
            quality_doc(rows),
            event,
            {"slots": []},
            fps=FPS,
            generated_at="fixed",
        )

        self.assertEqual(document["cases"], [])

    def test_neighboring_events_are_deduplicated_and_inputs_are_not_mutated(self) -> None:
        rows = [
            tracklet("1:1", 0, 19, x=2.0),
            tracklet("2:1", 0, 19, x=8.0),
            tracklet("3:1", 23, 40, x=2.1),
            tracklet("4:1", 23, 40, x=8.1),
        ]
        original = deepcopy(rows)
        args = (rows, quality_doc(rows), occlusion_doc(duplicate=True), {"slots": []})
        first = build_shadow_occlusion_assignments(*args, fps=FPS, generated_at="fixed")
        second = build_shadow_occlusion_assignments(*args, fps=FPS, generated_at="fixed")

        self.assertEqual(first, second)
        self.assertEqual(len(first["cases"]), 1)
        self.assertEqual(first["cases"][0]["occlusion_event_ids"], ["occlusion-1", "occlusion-2"])
        self.assertEqual(rows, original)


if __name__ == "__main__":
    unittest.main()
