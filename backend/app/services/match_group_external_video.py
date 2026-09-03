from __future__ import annotations

"""Safe external-video metadata for logical match groups.

The document is deliberately separate from the generated local-video pointer.
An external URL never participates in rendering or report lineage; it can only
be displayed when it is pinned to the current, ready logical-video inputs.
"""

import copy
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.services.artifact_lineage import canonical_json_sha256
from app.services.match_group_video import get_match_group_video_status
from app.services.match_groups import MATCH_GROUPS_DIR, MatchGroupError, get_match_group


EXTERNAL_VIDEO_FILENAME = "external_video.json"
EXTERNAL_VIDEO_SCHEMA_VERSION = "1.0.0"
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


class MatchGroupExternalVideoError(MatchGroupError):
    pass


def parse_youtube_url(value: str) -> dict[str, str]:
    """Accept a deliberately small YouTube URL allowlist and canonicalize it."""

    if not isinstance(value, str) or not value.strip():
        raise MatchGroupExternalVideoError("unsupported_youtube_url", "A valid HTTPS YouTube URL is required.")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as error:
        raise MatchGroupExternalVideoError("unsupported_youtube_url", "The YouTube URL is malformed.") from error
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or port is not None:
        raise MatchGroupExternalVideoError("unsupported_youtube_url", "Use a direct HTTPS YouTube URL without credentials or a port.")
    video_id: str | None = None
    path_parts = [part for part in parsed.path.split("/") if part]
    if host in _YOUTUBE_HOSTS and parsed.path == "/watch":
        values = parse_qs(parsed.query, keep_blank_values=True).get("v", [])
        if len(values) == 1:
            video_id = values[0]
    elif host == "youtu.be" and len(path_parts) == 1:
        video_id = path_parts[0]
    elif host in _YOUTUBE_HOSTS and len(path_parts) == 2 and path_parts[0] == "shorts":
        video_id = path_parts[1]
    if not video_id or not _VIDEO_ID_RE.fullmatch(video_id):
        raise MatchGroupExternalVideoError("unsupported_youtube_url", "The URL must identify one valid YouTube video.")
    return {
        "provider": "youtube",
        "video_id": video_id,
        "source_url": f"https://www.youtube.com/watch?v={video_id}",
    }


def get_match_group_external_video(group_id: str) -> dict[str, Any]:
    group = get_match_group(group_id)
    document = _load_document(_group_dir(str(group["group_id"])))
    if document is None:
        return _state(group, "not_configured")
    validated = _valid_document(document, str(group["group_id"]))
    if validated is None:
        return _state(group, "invalid", reason="external_video_document_invalid")
    video = get_match_group_video_status(str(group["group_id"]))
    if video.get("status") != "ready":
        return _state(group, "stale", validated, reason="combined_video_not_ready")
    manifest = video.get("manifest") or {}
    if validated["linked_video"]["input_semantic_digest"] != manifest.get("input_semantic_digest"):
        return _state(group, "stale", validated, reason="combined_video_generation_changed")
    return _state(group, "current", validated)


def save_match_group_external_video(group_id: str, url: str) -> dict[str, Any]:
    group = get_match_group(group_id)
    normalized = parse_youtube_url(url)
    video = get_match_group_video_status(str(group["group_id"]))
    if video.get("status") != "ready":
        raise MatchGroupExternalVideoError("combined_video_not_ready", "Generate a current combined video before linking YouTube.")
    manifest = video.get("manifest") or {}
    output = manifest.get("output") or {}
    timing = manifest.get("logical_timeline") or {}
    input_digest = manifest.get("input_semantic_digest")
    generation_id = manifest.get("generation_id")
    output_digest = output.get("semantic_digest")
    span = timing.get("timeline_span_sec")
    if not all(isinstance(value, str) and value for value in (input_digest, generation_id, output_digest)) or not isinstance(span, (int, float)):
        raise MatchGroupExternalVideoError("combined_video_not_ready", "The current combined video does not have complete provenance.")
    document: dict[str, Any] = {
        "schema_version": EXTERNAL_VIDEO_SCHEMA_VERSION,
        "group_id": str(group["group_id"]),
        **normalized,
        "linked_video": {
            "generation_id": generation_id,
            "input_semantic_digest": input_digest,
            "output_semantic_digest": output_digest,
            "timeline_span_sec": float(span),
        },
        "updated_at": _now(),
    }
    document["document_semantic_digest"] = canonical_json_sha256(document)
    _write_existing_group_document(_group_dir(str(group["group_id"])), document)
    return get_match_group_external_video(str(group["group_id"]))


def delete_match_group_external_video(group_id: str) -> dict[str, Any]:
    group = get_match_group(group_id)
    group_dir = _group_dir(str(group["group_id"]))
    if not (group_dir / "manifest.json").is_file():
        raise KeyError(group_id)
    try:
        (group_dir / EXTERNAL_VIDEO_FILENAME).unlink()
    except FileNotFoundError:
        pass
    return _state(group, "not_configured")


def _state(group: dict[str, Any], status: str, document: dict[str, Any] | None = None, *, reason: str | None = None) -> dict[str, Any]:
    external: dict[str, Any] | None = None
    if document is not None:
        external = {
            "provider": "youtube",
            "video_id": document["video_id"],
            "source_url": document["source_url"],
            "embed_url": f"https://www.youtube-nocookie.com/embed/{document['video_id']}" if status == "current" else None,
            "linked_video": copy.deepcopy(document["linked_video"]),
            "updated_at": document["updated_at"],
        }
    return {"group_id": str(group["group_id"]), "status": status, "reason": reason, "external_video": external}


def _valid_document(document: Any, group_id: str) -> dict[str, Any] | None:
    if not isinstance(document, dict):
        return None
    digest = document.get("document_semantic_digest")
    copy_without_digest = copy.deepcopy(document)
    copy_without_digest.pop("document_semantic_digest", None)
    if not isinstance(digest, str) or digest != canonical_json_sha256(copy_without_digest):
        return None
    if document.get("schema_version") != EXTERNAL_VIDEO_SCHEMA_VERSION or document.get("group_id") != group_id:
        return None
    try:
        parsed = parse_youtube_url(str(document.get("source_url") or ""))
    except MatchGroupExternalVideoError:
        return None
    if parsed["video_id"] != document.get("video_id") or parsed["source_url"] != document.get("source_url") or document.get("provider") != "youtube":
        return None
    linked = document.get("linked_video")
    if not isinstance(linked, dict) or not all(isinstance(linked.get(key), str) and linked[key] for key in ("generation_id", "input_semantic_digest", "output_semantic_digest")) or not isinstance(linked.get("timeline_span_sec"), (int, float)) or not isinstance(document.get("updated_at"), str):
        return None
    return document


def _load_document(group_dir: Path) -> Any | None:
    path = group_dir / EXTERNAL_VIDEO_FILENAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_existing_group_document(group_dir: Path, document: dict[str, Any]) -> None:
    manifest = group_dir / "manifest.json"
    if not manifest.is_file():
        raise KeyError(group_dir.name)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{EXTERNAL_VIDEO_FILENAME}.", suffix=".tmp", dir=group_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        if not manifest.is_file():
            raise KeyError(group_dir.name)
        os.replace(temporary, group_dir / EXTERNAL_VIDEO_FILENAME)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _group_dir(group_id: str) -> Path:
    return MATCH_GROUPS_DIR / group_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
