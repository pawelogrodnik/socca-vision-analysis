from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.main import get_match, get_match_tracklets


class MatchDetailPayloadTests(unittest.TestCase):
    def test_generic_match_omits_raw_tracklets_but_keeps_report_fields(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root / "match.json", {"id": "m1", "title": "Match"})
            _write(root / "pitch_config.json", {"pitch": "kept"})
            _write(root / "analysis_report.json", {"status": "completed"})
            _write(root / "stable_players.json", {"players": []})
            # Deliberately invalid raw input proves the generic route neither
            # parses nor exposes the dedicated tracklet artifact.
            (root / "tracklets.json").write_text("raw-tracklets-must-not-leak", encoding="utf-8")

            with patch("app.main.match_dir", return_value=root):
                response = get_match("m1")

            self.assertNotIn("tracklets", response)
            self.assertEqual(response["pitch_config"], {"pitch": "kept"})
            self.assertEqual(response["analysis_report"], {"status": "completed"})
            self.assertEqual(response["stable_players"], {"players": []})

    def test_dedicated_tracklet_route_still_returns_summaries(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root / "match.json", {"id": "m1", "teams": []})
            _write(root / "tracks.json", [{
                "track_id": 7,
                "start_time_sec": 1.0,
                "end_time_sec": 2.0,
                "duration_sec": 1.0,
                "positions": [{"confidence": 0.8, "pitch_m": [1, 2]}],
            }])

            with patch("app.main.match_dir", return_value=root):
                response = get_match_tracklets("m1")

            self.assertEqual(response["tracklets"][0]["tracklet_id"], 7)
            self.assertEqual(response["assignments"][0]["tracklet_id"], 7)
            self.assertIn("summary", response)


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
