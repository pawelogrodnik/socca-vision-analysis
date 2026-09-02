from __future__ import annotations

"""Provenance for the final operator-visible video in a publication.

Published logical videos must never depend on a mutable physical match folder.
This module creates the small descriptor used while a reviewed publication is
atomically staged, then validates the copied published artifact later.
"""

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from app.services.artifact_lineage import canonical_json_sha256


PUBLISHED_VIDEO_DESCRIPTOR_FILENAME = "published_video.json"
PUBLISHED_VIDEO_ARTIFACT = "reviewed_video.mp4"
PUBLISHED_VIDEO_SCHEMA_VERSION = "1.0.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_publication_video_descriptor(match_path: Path) -> dict[str, Any] | None:
    """Describe only a completed reviewed render; absence remains explicit."""

    output = _load_object(match_path / "reviewed_output_manifest.json")
    rendered = _load_object(match_path / "reviewed_video_manifest.json")
    video = output.get("video") if isinstance(output.get("video"), dict) else {}
    path = match_path / PUBLISHED_VIDEO_ARTIFACT
    if (
        video.get("status") != "completed"
        or rendered.get("status") != "completed"
        or rendered.get("path") != PUBLISHED_VIDEO_ARTIFACT
        or not path.is_file()
        or path.stat().st_size <= 0
    ):
        return None
    digest = str(rendered.get("digest") or "").strip().lower()
    if len(digest) != 64 or sha256_file(path) != digest or str(video.get("digest") or "") != digest:
        return None
    resolution = rendered.get("resolution") if isinstance(rendered.get("resolution"), list) else []
    if len(resolution) != 2 or not all(isinstance(value, (int, float)) for value in resolution):
        return None
    descriptor = {
        "schema_version": PUBLISHED_VIDEO_SCHEMA_VERSION,
        "status": "available",
        "artifact": PUBLISHED_VIDEO_ARTIFACT,
        "semantic_digest": digest,
        "duration_sec": float(rendered.get("duration_sec") or 0),
        "width": int(resolution[0]),
        "height": int(resolution[1]),
        "fps": float(rendered.get("fps") or 0),
        "codec": "h264",
        "pix_fmt": "yuv420p",
        "source_reviewed_identity_digest": str(rendered.get("source_snapshot_digest") or ""),
        "source_review_scope_digest": str(rendered.get("source_review_scope_digest") or ""),
    }
    if descriptor["duration_sec"] <= 0 or descriptor["width"] <= 0 or descriptor["height"] <= 0 or descriptor["fps"] <= 0:
        return None
    return descriptor


def stage_published_video(
    *,
    descriptor: dict[str, Any] | None,
    source_match_dir: Path,
    target_dir: Path,
    public_report_semantic_digest: str,
) -> dict[str, Any] | None:
    """Copy and prove the final reviewed video inside one staged publication."""

    if descriptor is None:
        return None
    source = source_match_dir / PUBLISHED_VIDEO_ARTIFACT
    if not source.is_file() or sha256_file(source) != descriptor.get("semantic_digest"):
        raise ValueError("Final reviewed video changed before publication could pin it.")
    target = target_dir / PUBLISHED_VIDEO_ARTIFACT
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if sha256_file(target) != descriptor.get("semantic_digest"):
        raise ValueError("Published reviewed video copy failed digest verification.")
    document = {
        **descriptor,
        "source_public_report_semantic_digest": public_report_semantic_digest,
    }
    document["descriptor_semantic_digest"] = canonical_json_sha256(document)
    _write_json(target_dir / PUBLISHED_VIDEO_DESCRIPTOR_FILENAME, document)
    return document


def load_published_video(source_dir: Path, *, expected_public_report_digest: str) -> dict[str, Any] | None:
    """Return a proven immutable source video or ``None`` without guessing."""

    descriptor = _load_object(source_dir / PUBLISHED_VIDEO_DESCRIPTOR_FILENAME)
    if not descriptor or descriptor.get("status") != "available":
        return None
    claimed = str(descriptor.get("descriptor_semantic_digest") or "")
    digest_document = dict(descriptor)
    digest_document.pop("descriptor_semantic_digest", None)
    if canonical_json_sha256(digest_document) != claimed:
        return None
    if descriptor.get("source_public_report_semantic_digest") != expected_public_report_digest:
        return None
    artifact = source_dir / str(descriptor.get("artifact") or "")
    if artifact.name != PUBLISHED_VIDEO_ARTIFACT or not artifact.is_file() or artifact.stat().st_size <= 0:
        return None
    if sha256_file(artifact) != descriptor.get("semantic_digest"):
        return None
    return descriptor


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
