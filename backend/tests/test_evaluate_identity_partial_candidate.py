from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_identity_partial_candidate import _atomic_write_directory, _file_hashes


class EvaluateIdentityPartialCandidateTests(unittest.TestCase):
    def test_atomic_directory_replace_removes_previous_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "candidate"
            _atomic_write_directory(root, {"old.json": {"generation": 1}})
            _atomic_write_directory(root, {"new.json": {"generation": 2}})

            self.assertFalse((root / "old.json").exists())
            self.assertTrue((root / "new.json").exists())
            self.assertFalse(root.with_name(f".{root.name}.backup").exists())

    def test_hash_snapshot_does_not_modify_production_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resolved_player_stats.json").write_text("{}\n", encoding="utf-8")
            names = ("resolved_player_stats.json", "player_heatmaps.json")

            before = _file_hashes(root, names)
            _atomic_write_directory(root / "candidate", {"candidate.json": {"status": "partial"}})
            after = _file_hashes(root, names)

            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
