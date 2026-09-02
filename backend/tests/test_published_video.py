from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.artifact_lineage import canonical_json_sha256
from app.services.published_video import (
    PUBLISHED_VIDEO_ARTIFACT,
    PUBLISHED_VIDEO_DESCRIPTOR_FILENAME,
    build_publication_video_descriptor,
    load_published_video,
    stage_published_video,
)


class PublishedVideoTests(unittest.TestCase):
    def test_completed_reviewed_video_is_copied_and_pinned_to_the_public_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            target = Path(temporary) / "published"
            source.mkdir()
            video = source / PUBLISHED_VIDEO_ARTIFACT
            video.write_bytes(b"final-reviewed-video")
            digest = _sha256(video)
            _write(source / "reviewed_video_manifest.json", {
                "status": "completed", "path": PUBLISHED_VIDEO_ARTIFACT,
                "digest": digest, "duration_sec": 12.5, "resolution": [1280, 720], "fps": 25,
                "source_snapshot_digest": "snapshot",
            })
            _write(source / "reviewed_output_manifest.json", {
                "video": {"status": "completed", "path": PUBLISHED_VIDEO_ARTIFACT, "digest": digest},
            })

            descriptor = build_publication_video_descriptor(source)
            self.assertIsNotNone(descriptor)
            public_digest = canonical_json_sha256({"id": "published-source"})
            stage_published_video(
                descriptor=descriptor,
                source_match_dir=source,
                target_dir=target,
                public_report_semantic_digest=public_digest,
            )

            self.assertEqual((target / PUBLISHED_VIDEO_ARTIFACT).read_bytes(), b"final-reviewed-video")
            self.assertTrue((target / PUBLISHED_VIDEO_DESCRIPTOR_FILENAME).is_file())
            self.assertIsNotNone(load_published_video(target, expected_public_report_digest=public_digest))
            self.assertIsNone(load_published_video(target, expected_public_report_digest="another-generation"))

    def test_incomplete_or_changed_render_never_becomes_a_published_video_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / PUBLISHED_VIDEO_ARTIFACT).write_bytes(b"unverified")
            _write(source / "reviewed_video_manifest.json", {"status": "completed", "path": PUBLISHED_VIDEO_ARTIFACT, "digest": "0" * 64})
            _write(source / "reviewed_output_manifest.json", {"video": {"status": "completed", "digest": "0" * 64}})
            self.assertIsNone(build_publication_video_descriptor(source))


def _write(path: Path, document: dict[str, object]) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _sha256(path: Path) -> str:
    from app.services.published_video import sha256_file
    return sha256_file(path)


if __name__ == "__main__":
    unittest.main()
