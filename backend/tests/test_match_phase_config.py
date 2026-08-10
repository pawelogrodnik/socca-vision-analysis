from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.match_phase_config import (
    build_default_match_phase_config,
    build_two_half_match_phase_config,
    configured_active_periods,
    direction_for_team_at_time,
    load_match_phase_config,
    match_phase_directions_are_trusted,
    save_match_phase_config,
)


META = {"video": {"duration_sec": 10.0}}


class MatchPhaseConfigTests(unittest.TestCase):
    def test_default_config_uses_single_period_with_opposite_team_directions(self) -> None:
        document = build_default_match_phase_config(META)

        self.assertEqual(document["summary"]["periods"], 1)
        self.assertFalse(document["summary"]["has_second_half"])
        self.assertTrue(document["summary"]["needs_review"])
        self.assertFalse(match_phase_directions_are_trusted(document))
        self.assertEqual(direction_for_team_at_time(document, "A", 1.0)["attack_direction"], "towards_y_min")
        self.assertEqual(direction_for_team_at_time(document, "B", 1.0)["attack_direction"], "towards_y_max")

    def test_two_half_config_switches_directions_after_second_half_start(self) -> None:
        document = build_two_half_match_phase_config(META, second_half_start_time_sec=5.0)

        self.assertEqual(document["summary"]["periods"], 2)
        self.assertTrue(document["summary"]["has_second_half"])
        self.assertTrue(match_phase_directions_are_trusted(document))
        self.assertEqual(direction_for_team_at_time(document, "A", 2.0)["attack_direction"], "towards_y_min")
        self.assertEqual(direction_for_team_at_time(document, "A", 7.0)["attack_direction"], "towards_y_max")
        self.assertEqual(direction_for_team_at_time(document, "B", 7.0)["attack_direction"], "towards_y_min")

    def test_two_half_config_preserves_explicit_halftime_gap(self) -> None:
        meta = {"video": {"duration_sec": 2700.0}}
        document = build_two_half_match_phase_config(
            meta,
            first_half_end_time_sec=1200.0,
            second_half_start_time_sec=1500.0,
            second_half_end_time_sec=2700.0,
        )

        self.assertEqual(document["periods"][0]["start_time_sec"], 0.0)
        self.assertEqual(document["periods"][0]["end_time_sec"], 1200.0)
        self.assertEqual(document["periods"][1]["start_time_sec"], 1500.0)
        self.assertEqual(document["periods"][1]["end_time_sec"], 2700.0)
        self.assertEqual(
            configured_active_periods(document, duration_sec=2700.0),
            [(0.0, 1200.0), (1500.0, 2700.0)],
        )

    def test_two_half_config_rejects_reversed_halftime_boundaries(self) -> None:
        with self.assertRaisesRegex(ValueError, "First-half end"):
            build_two_half_match_phase_config(
                {"video": {"duration_sec": 2700.0}},
                first_half_end_time_sec=1500.0,
                second_half_start_time_sec=1200.0,
            )

    def test_save_config_persists_and_refreshes_without_event_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            match_path = Path(tmp)
            initial = load_match_phase_config(match_path, META)
            self.assertEqual(initial["summary"]["periods"], 1)

            saved = save_match_phase_config(match_path, META, {"second_half_start_time_sec": 5.0})

            self.assertTrue((match_path / "match_phase_config.json").exists())
            self.assertEqual(saved["second_half_start_time_sec"], 5.0)
            self.assertEqual(direction_for_team_at_time(saved, "A", 7.0)["attack_direction"], "towards_y_max")

    def test_explicit_single_period_save_marks_direction_as_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saved = save_match_phase_config(
                Path(tmp),
                META,
                {"second_half_start_time_sec": None, "team_a_first_half_direction": "towards_y_max"},
            )

            self.assertFalse(saved["summary"]["needs_review"])
            self.assertTrue(match_phase_directions_are_trusted(saved))
            self.assertEqual(saved["periods"][0]["direction_source"], "configured_single_period")


if __name__ == "__main__":
    unittest.main()
