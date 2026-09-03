from __future__ import annotations

import copy
import unittest

from app.services.match_group_key_moments import MAX_KEY_MOMENTS, build_logical_match_key_moments


def _report(*, span: float = 900.0, possession: dict | None = None, momentum: dict | None = None) -> dict:
    return {
        "match": {"title": "Unrelated presentation title"},
        "timing": {"timeline_span_sec": span, "analyzed_duration_sec": span, "mapping": "ordered"},
        "teams": [
            {"team_id": "team-corgi", "team_name": "Corgi"},
            {"team_id": "team-verisk", "team_name": "Verisk"},
        ],
        "timelines": {
            "possession": possession or {"status": "ready", "windows": []},
            "attacking_momentum": momentum or {
                "status": "completed",
                "product_readiness": "experimental",
                "signal_quality": "high",
                "quality": "high",
                "points": [],
            },
        },
    }


def _momentum(*points: dict, quality: str = "high", signal_quality: str = "high") -> dict:
    return {
        "status": "completed",
        "product_readiness": "experimental",
        "signal_quality": signal_quality,
        "quality": quality,
        "points": list(points),
    }


def _possession(*windows: dict) -> dict:
    return {"status": "ready", "windows": list(windows)}


class MatchGroupKeyMomentsTests(unittest.TestCase):
    def test_deterministic_momentum_candidate_uses_logical_interval_and_stable_id(self) -> None:
        report = _report(momentum=_momentum({
            "start_time_sec": 720,
            "end_time_sec": 725,
            "team_values_by_team_id": {"team-corgi": 0.82, "team-verisk": 0.18},
            "dominant_team_id": "team-corgi",
            "intensity": 0.82,
            "confidence": 1.0,
        }))

        first = build_logical_match_key_moments(report)
        second = build_logical_match_key_moments(copy.deepcopy(report))

        self.assertEqual(first, second)
        moment = first["moments"][0]
        self.assertEqual(moment["team_id"], "team-corgi")
        self.assertEqual(moment["type"], "momentum_peak")
        self.assertEqual((moment["window_start_sec"], moment["time_sec"], moment["window_end_sec"]), (720.0, 722.5, 725.0))
        self.assertEqual(moment["evidence"]["primary_signal"], "attacking_momentum")

    def test_presentation_title_does_not_change_timeline_digest_or_moment_id(self) -> None:
        report = _report(momentum=_momentum({
            "start_time_sec": 120, "end_time_sec": 130,
            "team_values_by_team_id": {"team-corgi": 0.9}, "dominant_team_id": "team-corgi", "intensity": 0.9, "confidence": 1.0,
        }))
        renamed = copy.deepcopy(report)
        renamed["match"]["title"] = "A changed title only"

        original = build_logical_match_key_moments(report)
        changed = build_logical_match_key_moments(renamed)

        self.assertEqual(original["source_timeline_semantic_digest"], changed["source_timeline_semantic_digest"])
        self.assertEqual(original["moments"][0]["moment_id"], changed["moments"][0]["moment_id"])

    def test_low_or_unavailable_experimental_momentum_is_not_promoted_to_a_moment(self) -> None:
        point = {"start_time_sec": 120, "end_time_sec": 130, "team_values_by_team_id": {"team-corgi": 1}, "dominant_team_id": "team-corgi", "intensity": 1, "confidence": 1}
        low_quality = build_logical_match_key_moments(_report(momentum=_momentum(point, quality="low", signal_quality="low")))
        unavailable = build_logical_match_key_moments(_report(momentum={"status": "not_available", "points": [point]}))
        no_confidence = build_logical_match_key_moments(_report(momentum=_momentum({key: value for key, value in point.items() if key != "confidence"})))

        self.assertEqual(low_quality["status"], "not_available")
        self.assertEqual(unavailable["moments"], [])
        self.assertEqual(no_confidence["moments"], [])

    def test_possession_requires_known_coverage_before_claiming_dominance(self) -> None:
        strong = {
            "start_time_sec": 60, "end_time_sec": 90,
            "known_team_frames": 80, "free_frames": 10, "unknown_frames": 10,
            "possession_share_percent_by_team_id": {"team-verisk": 80, "team-corgi": 20},
        }
        weak = {
            "start_time_sec": 100, "end_time_sec": 130,
            "known_team_frames": 2, "free_frames": 10, "unknown_frames": 88,
            "possession_share_percent_by_team_id": {"team-corgi": 100},
        }
        result = build_logical_match_key_moments(_report(possession=_possession(strong, weak)))

        self.assertEqual(len(result["moments"]), 1)
        moment = result["moments"][0]
        self.assertEqual((moment["type"], moment["team_id"]), ("possession_dominance", "team-verisk"))
        self.assertEqual(moment["evidence"]["signals"][0]["coverage"], 0.8)

    def test_same_team_overlapping_signals_cluster_and_keep_supporting_evidence(self) -> None:
        report = _report(
            possession=_possession({
                "start_time_sec": 123, "end_time_sec": 132,
                "known_team_frames": 90, "free_frames": 5, "unknown_frames": 5,
                "possession_share_percent_by_team_id": {"team-corgi": 78},
            }),
            momentum=_momentum({
                "start_time_sec": 120, "end_time_sec": 125,
                "team_values_by_team_id": {"team-corgi": 0.9}, "dominant_team_id": "team-corgi", "intensity": 0.9, "confidence": 1.0,
            }),
        )

        result = build_logical_match_key_moments(report)

        self.assertEqual(len(result["moments"]), 1)
        moment = result["moments"][0]
        self.assertEqual((moment["window_start_sec"], moment["time_sec"], moment["window_end_sec"]), (120.0, 122.5, 132.0))
        self.assertEqual([signal["source"] for signal in moment["evidence"]["signals"]], ["attacking_momentum", "possession"])

    def test_conflicting_teams_stay_separate_under_the_fail_closed_cluster_policy(self) -> None:
        report = _report(momentum=_momentum(
            {"start_time_sec": 120, "end_time_sec": 125, "team_values_by_team_id": {"team-corgi": 0.9}, "dominant_team_id": "team-corgi", "intensity": 0.9, "confidence": 1.0},
            {"start_time_sec": 121, "end_time_sec": 126, "team_values_by_team_id": {"team-verisk": 0.85}, "dominant_team_id": "team-verisk", "intensity": 0.85, "confidence": 1.0},
        ))

        result = build_logical_match_key_moments(report)

        self.assertEqual([(moment["team_id"], moment["time_sec"]) for moment in result["moments"]], [("team-corgi", 122.5), ("team-verisk", 123.5)])

    def test_ordering_ties_are_stable_and_output_is_bounded(self) -> None:
        points = [
            {"start_time_sec": index * 20, "end_time_sec": index * 20 + 2, "team_values_by_team_id": {"team-corgi": 0.7}, "dominant_team_id": "team-corgi", "intensity": 0.7, "confidence": 1.0}
            for index in range(MAX_KEY_MOMENTS + 2)
        ]
        result = build_logical_match_key_moments(_report(momentum=_momentum(*points)))

        self.assertEqual(len(result["moments"]), MAX_KEY_MOMENTS)
        self.assertEqual([moment["time_sec"] for moment in result["moments"]], [1.0 + index * 20 for index in range(MAX_KEY_MOMENTS)])

    def test_invalid_or_out_of_range_intervals_are_skipped_without_an_invalid_public_moment(self) -> None:
        result = build_logical_match_key_moments(_report(span=100, momentum=_momentum(
            {"start_time_sec": -1, "end_time_sec": 5, "team_values_by_team_id": {"team-corgi": 1}, "dominant_team_id": "team-corgi", "intensity": 1, "confidence": 1},
            {"start_time_sec": 90, "end_time_sec": 101, "team_values_by_team_id": {"team-corgi": 1}, "dominant_team_id": "team-corgi", "intensity": 1, "confidence": 1},
        )))

        self.assertEqual(result["status"], "not_available")
        self.assertEqual(result["moments"], [])

    def test_physical_source_key_moments_are_ignored_in_favor_of_rebased_aggregate_timelines(self) -> None:
        report = _report(momentum=_momentum({
            "start_time_sec": 720, "end_time_sec": 725,
            "team_values_by_team_id": {"team-corgi": 0.9}, "dominant_team_id": "team-corgi", "intensity": 0.9, "confidence": 1.0,
        }))
        report["sources"] = [{"key_moments": {"moments": [{"time_sec": 5, "moment_id": "fake-physical"}]}}]

        result = build_logical_match_key_moments(report)

        self.assertEqual(result["moments"][0]["time_sec"], 722.5)
        self.assertNotIn("fake-physical", str(result))


if __name__ == "__main__":
    unittest.main()
