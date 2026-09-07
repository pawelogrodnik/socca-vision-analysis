from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.ball_event_rebuild import (
    _momentum_uses_current_phase_duration,
    artifact_freshness_status,
    atomic_write_rebuild_documents,
    ensure_ball_event_artifacts_fresh,
    rebuild_ball_event_artifacts,
)


class BallEventRebuildTests(unittest.TestCase):
    def test_momentum_duration_compatibility_distinguishes_missing_and_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            match_path = Path(temp_dir)
            self._write_inputs(match_path)

            self.assertTrue(_momentum_uses_current_phase_duration(match_path, {"summary": {}}))
            self.assertTrue(_momentum_uses_current_phase_duration(match_path, {"summary": {"duration_sec": 5.0}}))
            self.assertFalse(_momentum_uses_current_phase_duration(match_path, {"summary": {"duration_sec": 4.0}}))
            self.assertFalse(_momentum_uses_current_phase_duration(match_path, {"summary": {"duration_sec": "garbage"}}))
            self.assertFalse(_momentum_uses_current_phase_duration(match_path, {"summary": {"duration_sec": "NaN"}}))
            self.assertFalse(_momentum_uses_current_phase_duration(match_path, {"summary": {"duration_sec": "Infinity"}}))

    def test_timebase_change_rebuilds_lineage_fresh_terminal_momentum_bins(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            match_path = Path(temp_dir)
            self._write_inputs(match_path)
            rebuild_ball_event_artifacts(match_path, trigger="package_publish")

            meta_path = match_path / "match.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["video"]["duration_sec"] = 2.0
            meta_path.write_text(json.dumps(meta), encoding="utf-8")

            readiness = ensure_ball_event_artifacts_fresh(match_path)
            momentum = json.loads((match_path / "attacking_momentum.json").read_text(encoding="utf-8"))
            phase_config = json.loads((match_path / "match_phase_config.json").read_text(encoding="utf-8"))

            self.assertEqual(readiness["trigger"], "package_publish")
            self.assertEqual(momentum["summary"]["duration_sec"], 2.0)
            self.assertTrue(all(point["start_time_sec"] < 2.0 for point in momentum["points"]))
            self.assertEqual(phase_config["periods"][0]["end_time_sec"], 2.0)

    def test_rebuild_preserves_manual_review_by_stable_candidate_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            match_path = Path(temp_dir)
            self._write_inputs(match_path)
            first = rebuild_ball_event_artifacts(match_path, trigger="package_publish")
            pass_doc = first["pass_candidates"]
            pass_doc["candidates"][0].update(
                {"review_status": "accepted", "review_source": "manual", "reviewed_at": "2026-01-01"}
            )
            (match_path / "pass_candidates.json").write_text(json.dumps(pass_doc), encoding="utf-8")

            second = rebuild_ball_event_artifacts(match_path, trigger="contact_review")

            restored = second["pass_candidates"]["candidates"][0]
            self.assertEqual(restored["review_status"], "accepted")
            self.assertEqual(restored["review_migration_matched_by"], "candidate_key")
            momentum = json.loads((match_path / "attacking_momentum.json").read_text())
            self.assertEqual(momentum["algorithm"]["version"], "1.1.0")
            self.assertEqual(
                artifact_freshness_status(match_path, momentum),
                "fresh",
            )

    def test_package_publish_persists_normalized_restart_candidate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            match_path = Path(temp_dir)
            self._write_inputs(match_path)
            restart_path = match_path / "restart_candidates.json"
            restart_path.write_text(json.dumps({"candidates": [{
                "candidate_id": "restart-1", "setup_start_frame": 3,
                "release_frame": 8, "boundary_line": "touchline",
            }]}), encoding="utf-8")

            result = rebuild_ball_event_artifacts(match_path, trigger="package_publish")
            persisted = json.loads(restart_path.read_text(encoding="utf-8"))

            self.assertIn("restart_candidates.json", result["artifacts"])
            self.assertTrue(persisted["candidates"][0]["candidate_key"].startswith("restart:v1:"))

    def test_atomic_write_rolls_back_on_replace_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            match_path = Path(temp_dir)
            (match_path / "one.json").write_text('{"old": 1}', encoding="utf-8")
            calls = 0

            def failing_replace(source: str | Path, target: str | Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("injected replace failure")
                Path(source).replace(target)

            with self.assertRaises(OSError):
                atomic_write_rebuild_documents(
                    match_path,
                    {"one.json": {"new": 1}, "two.json": {"new": 2}},
                    {"generation_id": "test"},
                    replace_file=failing_replace,
                )

            self.assertEqual(json.loads((match_path / "one.json").read_text()), {"old": 1})
            self.assertFalse((match_path / "two.json").exists())
            self.assertFalse((match_path / "ball_event_generation.json").exists())

    @staticmethod
    def _write_inputs(match_path: Path) -> None:
        (match_path / "match.json").write_text(
            json.dumps({"id": "test", "video": {"duration_sec": 5.0}}), encoding="utf-8"
        )
        contacts = {
            "candidates": [
                {
                    "candidate_id": "contact-0001",
                    "stable_player_id": "A01",
                    "stable_subject_id": "A01",
                    "team_label": "A",
                    "start_frame": 0,
                    "end_frame": 2,
                    "start_time_sec": 0.0,
                    "end_time_sec": 0.1,
                    "start_ball_position_m": [10.0, 30.0],
                    "end_ball_position_m": [10.0, 29.0],
                    "mean_confidence": 0.9,
                    "review_status": "accepted",
                },
                {
                    "candidate_id": "contact-0002",
                    "stable_player_id": "A02",
                    "stable_subject_id": "A02",
                    "team_label": "A",
                    "start_frame": 20,
                    "end_frame": 22,
                    "start_time_sec": 0.7,
                    "end_time_sec": 0.8,
                    "start_ball_position_m": [10.0, 20.0],
                    "end_ball_position_m": [10.0, 19.0],
                    "mean_confidence": 0.9,
                    "review_status": "accepted",
                },
            ]
        }
        possession = {
            "parameters": {"pitch_width_m": 30.0, "pitch_length_m": 47.4},
            "frames": [
                {"frame": 0, "time_sec": 0.0, "status": "controlled", "team_label": "A", "ball_position_m": [10.0, 30.0], "confidence": 0.9},
                {"frame": 20, "time_sec": 0.7, "status": "controlled", "team_label": "A", "ball_position_m": [10.0, 20.0], "confidence": 0.9},
            ],
        }
        phase = {
            "periods": [{"period_id": "full", "start_time_sec": 0.0, "end_time_sec": 5.0, "team_attack_directions": {"A": "towards_y_min", "B": "towards_y_max"}}],
            "summary": {"needs_review": False},
        }
        for filename, document in {
            "contact_candidates.json": contacts,
            "possession_candidates.json": possession,
            "possession_segments.json": {"segments": [{"segment_id": "s1", "status": "controlled", "start_frame": 0, "end_frame": 22}]},
            "restart_candidates.json": {"candidates": []},
            "match_phase_config.json": phase,
        }.items():
            (match_path / filename).write_text(json.dumps(document), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
