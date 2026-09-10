from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.suggested_key_moments import suggested_key_moment_projection


class SuggestedKeyMomentsTests(unittest.TestCase):
    def test_merged_projection_rebases_source_artifacts_and_materializes_no_goldset(self) -> None:
        manifest = {
            "group_id": "match-group-one", "aggregate_semantic_digest": "manifest-a",
            "timing": {"timeline_span_sec": 80},
            "members": [{"source_match_id": "part-one", "logical_start_sec": 20}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); match = root / "matches" / "part-one"; match.mkdir(parents=True)
            (match / "possession_segments.json").write_text(json.dumps({"segments": [{"start_time_sec": 0, "end_time_sec": 1, "status": "controlled", "team_id": "corgi", "mean_confidence": .9}, {"start_time_sec": 2, "end_time_sec": 5, "status": "controlled", "team_id": "verisk", "mean_confidence": .9}]}), encoding="utf-8")
            (match / "pass_candidates.json").write_text(json.dumps({"candidates": [{"start_time_sec": 5, "end_time_sec": 7, "pass_type": "same_team_pass", "from_team_id": "verisk", "confidence": .9, "forward_progress_m": 12, "is_progressive": True}]}), encoding="utf-8")
            for filename, key in (("possession_candidates.json", "frames"), ("restart_candidates.json", "candidates"), ("event_candidates.json", "events"), ("attacking_momentum.json", "points"), ("match_phase_config.json", "periods")):
                (match / filename).write_text(json.dumps({key: []}), encoding="utf-8")
            with patch("app.services.suggested_key_moments.MATCHES_DIR", root / "matches"), patch("app.services.suggested_key_moments.MATCH_GROUPS_DIR", root / "groups"), patch("app.services.suggested_key_moments.group_id_for_merged_published_id", return_value="match-group-one"), patch("app.services.suggested_key_moments.get_match_group", return_value=manifest):
                first = suggested_key_moment_projection("published-merged-one")
                second = suggested_key_moment_projection("published-merged-one")
            artifact = json.loads((root / "groups" / "match-group-one" / "suggested_key_moments.json").read_text(encoding="utf-8"))
        self.assertEqual(first["candidate_generation_digest"], second["candidate_generation_digest"])
        self.assertEqual(first["candidate_count"], 1)
        self.assertEqual(first["candidates"][0]["team_id"], "verisk")
        self.assertEqual(first["candidates"][0]["start_time_sec"], 22)
        self.assertEqual(artifact["policy_version"], "interesting-action-shadow:v1")

    def test_physical_report_keeps_manual_editor_queue_unavailable(self) -> None:
        with patch("app.services.suggested_key_moments.group_id_for_merged_published_id", return_value=None):
            projection = suggested_key_moment_projection("published-physical-one")
        self.assertEqual(projection["status"], "not_available")
        self.assertEqual(projection["candidates"], [])
