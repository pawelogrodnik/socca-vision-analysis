from __future__ import annotations

"""Canonical input selection for downstream ball analytics.

``ball_tracks.json`` remains the immutable automatic detector output.  A
resolved sidecar is an optional, operator-corrected projection of that output.
Downstream consumers use this module rather than selecting one of those files
ad hoc, which makes the provenance and freshness contract explicit.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.services.artifact_lineage import canonical_json_sha256
from app.services.resolved_ball_tracks import RESOLUTION_SCHEMA_VERSION, RESOLVED_BALL_TRACKS_FILENAME


BALL_TRACKS_FILENAME = "ball_tracks.json"
EFFECTIVE_BALL_TRACK_DIGEST_VERSION = "effective-ball-track:v1"


class EffectiveBallTracksError(ValueError):
    """Raised when a persisted resolved projection cannot safely be consumed."""


@dataclass(frozen=True)
class EffectiveBallTracks:
    document: dict[str, Any]
    input_digest: str
    provenance: str
    artifact: str


def load_effective_ball_tracks(
    match_dir: Path,
    *,
    automatic_tracks: Mapping[str, Any] | None = None,
) -> EffectiveBallTracks:
    """Load resolved tracks when present, otherwise the automatic baseline.

    A present but malformed resolved sidecar is an integrity failure.  Falling
    back silently would discard an operator decision and produce misleading
    possession/event artifacts, so only a genuinely absent sidecar may use the
    legacy automatic path.
    """

    resolved_path = match_dir / RESOLVED_BALL_TRACKS_FILENAME
    if resolved_path.exists():
        document = _load_document(resolved_path, label="resolved ball tracks")
        _validate_resolved(document)
        return EffectiveBallTracks(
            document=document,
            input_digest=effective_ball_track_digest(document),
            provenance="resolved_operator_projection",
            artifact=RESOLVED_BALL_TRACKS_FILENAME,
        )

    document = dict(automatic_tracks) if automatic_tracks is not None else _load_document(
        match_dir / BALL_TRACKS_FILENAME,
        label="automatic ball tracks",
    )
    _validate_positions(document, label="automatic ball tracks")
    return EffectiveBallTracks(
        document=document,
        input_digest=effective_ball_track_digest(document),
        provenance="automatic_legacy_fallback",
        artifact=BALL_TRACKS_FILENAME,
    )


def effective_ball_track_digest(document: Mapping[str, Any]) -> str:
    """Hash only semantics that affect possession/contact/event derivation.

    Volatile metadata such as ``generated_at`` deliberately does not take part
    in freshness.  The projection retains the selected candidate and all
    interpolation lineage needed to distinguish a real correction from a
    cosmetic regeneration.
    """

    _validate_positions(document, label="effective ball tracks")
    rows = []
    for position in document.get("positions") or []:
        if not isinstance(position, Mapping):
            continue
        rows.append({
            "frame": _integer(position.get("frame")),
            "source": str(position.get("source") or "unknown"),
            "candidate_id": _optional_text(position.get("candidate_id")),
            "position_m": _point(position.get("position_m")),
            "confidence": _number(position.get("confidence")),
            "interpolated_from": _frame_pair(position.get("interpolated_from")),
            "resolution_source": _optional_text(position.get("resolution_source")),
        })
    return canonical_json_sha256({
        "schema_version": EFFECTIVE_BALL_TRACK_DIGEST_VERSION,
        "positions": sorted(rows, key=lambda row: row["frame"]),
    })


def _validate_resolved(document: Mapping[str, Any]) -> None:
    resolution = document.get("resolution")
    if not isinstance(resolution, Mapping):
        raise EffectiveBallTracksError("resolved_ball_tracks.json is missing its resolution contract")
    if str(resolution.get("schema_version") or "") != RESOLUTION_SCHEMA_VERSION:
        raise EffectiveBallTracksError("resolved_ball_tracks.json has an incompatible resolution schema")
    _validate_positions(document, label="resolved ball tracks")


def _validate_positions(document: Mapping[str, Any], *, label: str) -> None:
    positions = document.get("positions")
    if not isinstance(positions, list):
        raise EffectiveBallTracksError(f"{label} must contain a positions list")
    for position in positions:
        if not isinstance(position, Mapping):
            raise EffectiveBallTracksError(f"{label} contains an invalid position row")
        if _integer(position.get("frame")) is None:
            raise EffectiveBallTracksError(f"{label} position is missing a frame")
        point = position.get("position_m")
        if point is not None and _point(point) is None:
            raise EffectiveBallTracksError(f"{label} position has an invalid position_m")


def _load_document(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists():
        raise EffectiveBallTracksError(f"{label} artifact is missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EffectiveBallTracksError(f"{label} artifact is unreadable") from error
    if not isinstance(value, dict):
        raise EffectiveBallTracksError(f"{label} artifact must be a JSON object")
    return value


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None and str(value) else None


def _point(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    numbers = [_number(item) for item in value]
    return [numbers[0], numbers[1]] if numbers[0] is not None and numbers[1] is not None else None


def _frame_pair(value: Any) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    frames = [_integer(item) for item in value]
    return [frames[0], frames[1]] if frames[0] is not None and frames[1] is not None else None
