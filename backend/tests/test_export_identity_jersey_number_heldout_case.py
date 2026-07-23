from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.evaluate_identity_jersey_number_heldout_suite import _load_case
from scripts.export_identity_jersey_number_heldout_case import (
    build_production_identity_snapshot,
    production_hashes_from_snapshot,
)


class ExportIdentityJerseyNumberHeldoutCaseTests(unittest.TestCase):
    def test_snapshot_hashes_required_production_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            match_dir = Path(directory) / "match"
            match_dir.mkdir()
            for name in (
                "global_identity.json",
                "stable_players.json",
                "player_identity_assignments.json",
            ):
                (match_dir / name).write_text(f"{name}\n", encoding="utf-8")

            snapshot = build_production_identity_snapshot(
                match_dir,
                generated_at="fixed",
            )
            hashes = production_hashes_from_snapshot(snapshot)

            self.assertTrue(snapshot["complete"])
            self.assertEqual(snapshot["missing_required_artifacts"], [])
            self.assertTrue(all(hashes.values()))

    def test_snapshot_reports_missing_required_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = build_production_identity_snapshot(
                Path(directory),
                generated_at="fixed",
            )

            self.assertFalse(snapshot["complete"])
            self.assertEqual(
                set(snapshot["missing_required_artifacts"]),
                {
                    "global_identity.json",
                    "stable_players.json",
                    "player_identity_assignments.json",
                },
            )

    def test_suite_manifest_loads_canonical_case_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = {
                "schema_version": "0.1.0",
                "algorithm": {
                    "name": "identity_jersey_number_heldout_case_contract",
                    "version": "0.1.0",
                },
            }
            (root / "case.json").write_text(
                json.dumps(contract),
                encoding="utf-8",
            )

            loaded = _load_case({"case_contract": "case.json"}, root)

            self.assertEqual(loaded, {"case_contract_doc": contract})


if __name__ == "__main__":
    unittest.main()
