from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.services.key_moment_editor import (
    _candidate_moments,
    _editorial_content,
    _revision,
    apply_editorial_key_moments,
    editor_state,
    key_moment_editor_capability,
    load_editorial_document,
    save_editorial_document,
)


def _report() -> dict:
    return {
        "id": "published-one",
        "source_kind": "physical",
        "match": {"duration_sec": 120.0},
        "teams": [{"team_id": "team-a", "team_name": "A"}],
        "players": [{"player_id": "player-a", "team_id": "team-a", "player_name": "Ada"}],
        "key_moments": {
            "schema_version": "1.1.0", "policy_version": "generated:v1", "status": "ready",
            "moments": [
                {"moment_id": "generated-momentum", "time_sec": 40.0, "type": "momentum_peak", "headline": "Mocny okres", "team_id": "team-a"},
                {"moment_id": "generated-possession", "time_sec": 60.0, "type": "possession_dominance", "headline": "Przewaga w posiadaniu", "team_id": "team-a"},
            ],
        },
    }


class KeyMomentEditorTests(unittest.TestCase):
    def test_no_app_mode_requirement(self) -> None:
        with patch.object(config, "APP_MODE", "production-viewer"):
            self.assertEqual(key_moment_editor_capability(), {"key_moment_editor_allowed": True, "reason": None})

    def test_no_sidecar_starts_from_generated_moments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("app.services.key_moment_editor.EDITORIAL_DIRECTORY", Path(temporary)), patch(
                "app.services.key_moment_editor.get_published_match", return_value={"public_report": _report(), "source_kind": "physical"},
            ):
                state = editor_state("published-one")
        self.assertFalse(state["has_editorial_sidecar"])
        self.assertEqual([row["category"] for row in state["moments"]], ["momentum_peak", "possession_dominance"])
        self.assertEqual([row["time_sec"] for row in state["moments"]], [40.0, 60.0])

    def test_add_save_reload_edit_delete_shape_is_complete_and_ordered(self) -> None:
        report = _report()
        generated = report["key_moments"]["moments"]
        added = _candidate_moments({"moments": [*generated, {"time_sec": 20.0, "category": "good_action", "headline": "Nowy", "note": "uwaga", "team_id": "team-a", "player_id": "player-a"}]}, report, generated)
        self.assertEqual([row["time_sec"] for row in added], [20.0, 40.0, 60.0])
        manual = added[0]
        self.assertTrue(manual["moment_id"].startswith("manual-km-"))
        self.assertEqual(manual["headline"], "Nowy")

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            document = {"schema_version": "1.0.0", "published_id": "published-one", "moments": added}
            document["revision"] = _revision(document)
            path = directory / "published-one.json"
            path.write_text(json.dumps(_editorial_content(document)), encoding="utf-8")
            with patch("app.services.key_moment_editor.EDITORIAL_DIRECTORY", directory):
                reloaded = load_editorial_document("published-one")
        self.assertEqual(reloaded["moments"][0]["time_sec"], 20.0)

        edited = _candidate_moments({"moments": [{**row, "headline": "Po edycji"} if row["moment_id"] == manual["moment_id"] else row for row in added]}, report, added)
        self.assertEqual(edited[0]["headline"], "Po edycji")
        deleted = _candidate_moments({"moments": [row for row in edited if row["moment_id"] != manual["moment_id"]]}, report, edited)
        self.assertEqual([row["moment_id"] for row in deleted], ["generated-momentum", "generated-possession"])

    def test_curated_sidecar_is_authoritative_during_regeneration(self) -> None:
        report = _report()
        curated = [{"moment_id": "manual-km-1", "time_sec": 12.0, "type": "tactical_note", "public_category": "tactical_note", "headline": "Zachowaj", "origin": "manual"}]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "published-one.json").write_text(json.dumps({"schema_version": "1.0.0", "published_id": "published-one", "moments": curated}), encoding="utf-8")
            with patch("app.services.key_moment_editor.EDITORIAL_DIRECTORY", directory):
                applied = apply_editorial_key_moments(report, "published-one", source_kind="physical")
        self.assertEqual(applied["moments"], curated)
        self.assertEqual(applied["policy_version"], "editorial-curated:v1")

    def test_invalid_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "poza zakresem"):
            _candidate_moments({"moments": [{"time_sec": 121.0, "category": "other", "headline": "Za późno"}]}, _report(), [])

    def test_save_updates_canonical_and_static_projection_without_changing_published_id(self) -> None:
        report = _report()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = root / "published" / "published-one"
            mirror = root / "client-public" / "published-one"
            canonical.mkdir(parents=True); mirror.mkdir(parents=True)
            (canonical / "public_report.json").write_text(json.dumps(report), encoding="utf-8")
            (canonical / "provenance.json").write_text(json.dumps({}), encoding="utf-8")
            (mirror / "public_report.json").write_text(json.dumps(report), encoding="utf-8")
            match = {"public_report": report, "source_kind": "merged"}
            with patch("app.services.key_moment_editor.EDITORIAL_DIRECTORY", root / "editorial"), patch(
                "app.services.key_moment_editor.PUBLISHED_MATCHES_DIR", root / "published"
            ), patch("app.services.key_moment_editor.CLIENT_PUBLIC_MATCHES_DIR", root / "client-public"), patch(
                "app.services.key_moment_editor.get_published_match", return_value=match
            ):
                initial = editor_state("published-one")
                saved = save_editorial_document("published-one", {
                    "expected_revision": initial["revision"],
                    "moments": [*initial["moments"], {"time_sec": 10.0, "category": "other", "headline": "Dodany"}],
                })
            canonical_report = json.loads((canonical / "public_report.json").read_text(encoding="utf-8"))
            static_report = json.loads((mirror / "public_report.json").read_text(encoding="utf-8"))
            sidecar = json.loads((root / "editorial" / "published-one.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["public_report"]["id"], "published-one")
        self.assertEqual(canonical_report, static_report)
        self.assertEqual([row["type"] for row in canonical_report["key_moments"]["moments"]], ["other", "momentum_peak", "possession_dominance"])
        self.assertEqual(set(sidecar), {"schema_version", "published_id", "moments"})

    def test_real_generated_categories_save_edit_and_delete(self) -> None:
        report = _report()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = root / "published" / "published-one"
            mirror = root / "client-public" / "published-one"
            canonical.mkdir(parents=True); mirror.mkdir(parents=True)
            (canonical / "public_report.json").write_text(json.dumps(report), encoding="utf-8")
            (canonical / "provenance.json").write_text(json.dumps({}), encoding="utf-8")
            (mirror / "public_report.json").write_text(json.dumps(report), encoding="utf-8")
            with patch("app.services.key_moment_editor.EDITORIAL_DIRECTORY", root / "editorial"), patch(
                "app.services.key_moment_editor.PUBLISHED_MATCHES_DIR", root / "published"
            ), patch("app.services.key_moment_editor.CLIENT_PUBLIC_MATCHES_DIR", root / "client-public"), patch(
                "app.services.key_moment_editor.get_published_match", return_value={"public_report": report, "source_kind": "merged"}
            ):
                initial = editor_state("published-one")
                unchanged = save_editorial_document("published-one", {"expected_revision": initial["revision"], "moments": initial["moments"]})
                self.assertEqual([row["category"] for row in unchanged["moments"]], ["momentum_peak", "possession_dominance"])

                edited_rows = [{**row, "headline": "Po edycji"} if row["moment_id"] == "generated-momentum" else row for row in unchanged["moments"]]
                edited = save_editorial_document("published-one", {"expected_revision": unchanged["revision"], "moments": edited_rows})
                self.assertEqual(edited["moments"][0]["headline"], "Po edycji")

                deleted = save_editorial_document("published-one", {"expected_revision": edited["revision"], "moments": [row for row in edited["moments"] if row["moment_id"] != "generated-possession"]})
            self.assertEqual([row["moment_id"] for row in deleted["moments"]], ["generated-momentum"])
