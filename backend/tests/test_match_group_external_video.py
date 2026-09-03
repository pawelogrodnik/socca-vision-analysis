from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.services.match_group_external_video as external


GROUP = {"group_id": "match-group-one"}
READY = {
    "status": "ready",
    "manifest": {
        "generation_id": "generation-a",
        "input_semantic_digest": "input-a",
        "logical_timeline": {"timeline_span_sec": 120.0},
        "output": {"semantic_digest": "output-a"},
    },
}


class MatchGroupExternalVideoTests(unittest.TestCase):
    def test_youtube_url_allowlist_canonicalizes_supported_forms(self) -> None:
        for url in (
            "https://www.youtube.com/watch?v=AbCdEfGhI_1&t=20",
            "https://youtube.com/watch?v=AbCdEfGhI_1",
            "https://youtu.be/AbCdEfGhI_1?si=noise",
            "https://www.youtube.com/shorts/AbCdEfGhI_1",
        ):
            self.assertEqual(external.parse_youtube_url(url)["source_url"], "https://www.youtube.com/watch?v=AbCdEfGhI_1")
        for url in ("http://youtube.com/watch?v=AbCdEfGhI_1", "https://evil.example/watch?v=AbCdEfGhI_1", "javascript:alert(1)", "https://youtube.com/watch?list=one", "https://youtube.com@evil.example/watch?v=AbCdEfGhI_1"):
            with self.assertRaises(external.MatchGroupExternalVideoError):
                external.parse_youtube_url(url)

    def test_provenance_currentness_staleness_and_invalid_document_fail_closed(self) -> None:
        with self._store() as temporary, self._service(temporary):
            root = Path(temporary)
            saved = external.save_match_group_external_video("match-group-one", "https://youtu.be/AbCdEfGhI_1")
            self.assertEqual(saved["status"], "current")
            document_path = root / "match-group-one" / external.EXTERNAL_VIDEO_FILENAME
            document = json.loads(document_path.read_text())
            self.assertEqual(document["linked_video"]["input_semantic_digest"], "input-a")
            same_input = {**READY, "manifest": {**READY["manifest"], "generation_id": "generation-b"}}
            with patch.object(external, "get_match_group_video_status", return_value=same_input):
                self.assertEqual(external.get_match_group_external_video("match-group-one")["status"], "current")
            with patch.object(external, "get_match_group_video_status", return_value={**READY, "manifest": {**READY["manifest"], "input_semantic_digest": "input-b"}}):
                stale = external.get_match_group_external_video("match-group-one")
            self.assertEqual(stale["status"], "stale")
            self.assertIsNone(stale["external_video"]["embed_url"])
            document["video_id"] = "wrongwrong1"
            document_path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(external.get_match_group_external_video("match-group-one")["status"], "invalid")

    def test_unready_save_rejects_without_writing_and_delete_preserves_other_bytes(self) -> None:
        with self._store() as temporary, self._service(temporary):
            root = Path(temporary)
            manifest = root / "match-group-one" / "manifest.json"
            before = manifest.read_bytes()
            with patch.object(external, "get_match_group_video_status", return_value={"status": "generating"}), self.assertRaises(external.MatchGroupExternalVideoError) as error:
                external.save_match_group_external_video("match-group-one", "https://youtu.be/AbCdEfGhI_1")
            self.assertEqual(error.exception.code, "combined_video_not_ready")
            self.assertFalse((root / "match-group-one" / external.EXTERNAL_VIDEO_FILENAME).exists())
            self.assertEqual(manifest.read_bytes(), before)
            external.save_match_group_external_video("match-group-one", "https://youtu.be/AbCdEfGhI_1")
            external.delete_match_group_external_video("match-group-one")
            self.assertEqual(manifest.read_bytes(), before)

    def _store(self):
        return tempfile.TemporaryDirectory()

    def _service(self, directory: str):
        root = Path(directory)
        group_dir = root / "match-group-one"
        group_dir.mkdir()
        (group_dir / "manifest.json").write_text('{"group_id":"match-group-one"}', encoding="utf-8")
        return patch.multiple(external, MATCH_GROUPS_DIR=root, get_match_group=lambda _: GROUP, get_match_group_video_status=lambda _: READY)


if __name__ == "__main__":
    unittest.main()
