from __future__ import annotations

import unittest
from unittest.mock import patch

from app import config
from app.services.key_moment_editor import (
    KeyMomentEditorError,
    _candidate_document,
    _physical_anchor,
    generated_editorial_key,
    key_moment_editor_capability,
    resolve_effective_key_moments,
)


class KeyMomentEditorTests(unittest.TestCase):
    def test_capability_fails_closed_outside_local_analysis(self) -> None:
        with patch.object(config, "APP_MODE", "production-viewer"):
            self.assertEqual(key_moment_editor_capability(), {"key_moment_editor_allowed": False, "reason": "production_viewer"})

    def test_physical_terminal_time_uses_last_valid_frame(self) -> None:
        report = {"id": "published-one", "source_match_id": "one", "match": {"duration_sec": 4.0}}
        with patch("app.services.key_moment_editor.get_published_match", return_value={"package": {"match": {"video": {"fps": 25, "decoded_frame_count": 100}}}}):
            anchor = _physical_anchor(report, 4.0)
        self.assertEqual(anchor["source_frame"], 99)
        self.assertEqual(anchor["source_time_sec"], 3.96)

    def test_generated_target_survives_logical_offset_change(self) -> None:
        moment = {"type": "momentum_peak", "team_id": "team-a", "time_sec": 112.0, "window_start_sec": 110.0, "window_end_sec": 114.0, "evidence": {"signals": [{"source": "attacking_momentum", "intensity": 0.9}]}}
        first = [{"published_id": "published-b", "source_match_id": "b", "logical_start_sec": 100.0, "logical_end_sec": 200.0}]
        shifted = [{"published_id": "published-b", "source_match_id": "b", "logical_start_sec": 300.0, "logical_end_sec": 400.0}]
        shifted_moment = {**moment, "time_sec": 312.0, "window_start_sec": 310.0, "window_end_sec": 314.0}
        self.assertEqual(generated_editorial_key(moment, first), generated_editorial_key(shifted_moment, shifted))

    def test_effective_list_suppresses_only_exact_generated_target_and_sorts(self) -> None:
        report = {"id": "published-one", "match": {"duration_sec": 120.0}, "teams": [], "players": []}
        generated = [
            {"moment_id": "g1", "generated_editorial_key": "exact", "time_sec": 40.0, "window_start_sec": 35.0, "window_end_sec": 45.0, "type": "chance", "team_id": "a", "headline": "G1", "evidence": {}},
            {"moment_id": "g2", "generated_editorial_key": "nearby", "time_sec": 42.0, "window_start_sec": 40.0, "window_end_sec": 44.0, "type": "chance", "team_id": "a", "headline": "G2", "evidence": {}},
        ]
        editorial = {"generated_suppressions": [{"generated_editorial_key": "exact"}], "generated_overrides": [], "manual_moments": []}
        with patch("app.services.key_moment_editor._source_kind", return_value="physical"):
            resolved = resolve_effective_key_moments(report, generated, editorial)
        self.assertEqual([row["moment_id"] for row in resolved["moments"]], ["g2"])

    def test_invalid_timestamp_is_rejected(self) -> None:
        report = {"id": "published-one", "source_match_id": "one", "match": {"duration_sec": 4.0}}
        with self.assertRaises(KeyMomentEditorError) as raised:
            _physical_anchor(report, -0.1)
        self.assertEqual(raised.exception.code, "key_moment_timestamp_invalid")

    def test_save_keeps_orphaned_generated_decisions(self) -> None:
        current = {
            "published_id": "published-one", "manual_moments": [],
            "generated_suppressions": [{"generated_editorial_key": "old-key"}],
            "generated_overrides": [{"generated_editorial_key": "old-key", "presentation": {"headline": "Old"}}],
        }
        payload = {"manual_moments": [], "generated_suppressions": [], "generated_overrides": []}
        report = {"id": "published-one", "match": {"duration_sec": 10.0}, "teams": [], "players": []}
        candidate = _candidate_document(current, payload, report, {"new-key"})
        self.assertEqual(candidate["generated_suppressions"], current["generated_suppressions"])
        self.assertEqual(candidate["generated_overrides"], current["generated_overrides"])
