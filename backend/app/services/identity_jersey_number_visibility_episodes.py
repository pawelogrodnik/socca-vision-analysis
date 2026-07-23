from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services.identity_jersey_number_common import stable_key, team_label


EPISODE_CONTRACT_VERSION = "1"


def partition_jersey_visibility_episodes(
    rows: list[dict[str, Any]],
    maximum_gap_frames: int = 45,
) -> list[list[dict[str, Any]]]:
    """Partition scoped jersey reads into explicit or frame-contiguous episodes."""
    if maximum_gap_frames < 0:
        raise ValueError("maximum_gap_frames must be non-negative")
    scoped_rows = _scoped_rows(rows)
    explicit_scopes = _explicit_scopes(scoped_rows)
    episodes: list[list[dict[str, Any]]] = []
    for scope, values in sorted(scoped_rows.items()):
        if scope in explicit_scopes:
            by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row, _, explicit_id in values:
                by_episode[explicit_id].append(row)
            episodes.extend(
                sorted(
                    (sorted(group, key=_frame) for group in by_episode.values()),
                    key=lambda group: (_frame(group[0]), _explicit_episode_id(group[0])),
                )
            )
            continue
        current: list[dict[str, Any]] = []
        previous_frame: int | None = None
        for row, _, _ in sorted(values, key=lambda value: _frame(value[0])):
            frame = _frame(row)
            if current and previous_frame is not None and frame - previous_frame > maximum_gap_frames:
                episodes.append(current)
                current = []
            current.append(row)
            previous_frame = frame
        if current:
            episodes.append(current)
    return episodes


def attach_jersey_visibility_episode_ids(
    rows: list[dict[str, Any]],
    maximum_gap_frames: int = 45,
) -> list[dict[str, Any]]:
    """Return copied rows with explicit or deterministic visibility episode IDs."""
    episodes = partition_jersey_visibility_episodes(rows, maximum_gap_frames)
    attached: dict[int, str] = {}
    for episode in episodes:
        first = episode[0]
        explicit_id = _explicit_episode_id(first)
        episode_id = explicit_id or stable_key(
            "jersey-visibility-episode",
            {
                "contract_version": EPISODE_CONTRACT_VERSION,
                "scope": _scope(first),
                "start_frame": _frame(first),
            },
        )
        attached.update({id(row): episode_id for row in episode})
    return [{**row, "visibility_episode_id": attached[id(row)]} for row in rows]


def _scoped_rows(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str, str, str, str], list[tuple[dict[str, Any], tuple[str, str, str, str, str, str], str]]]:
    scoped: dict[tuple[str, str, str, str, str, str], list[tuple[dict[str, Any], tuple[str, str, str, str, str, str], str]]] = defaultdict(list)
    explicit_scopes: dict[str, tuple[str, str, str, str, str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("visibility episode rows must be objects")
        scope = _scope(row)
        explicit_id = _explicit_episode_id(row)
        if explicit_id:
            existing_scope = explicit_scopes.setdefault(explicit_id, scope)
            if existing_scope != scope:
                raise ValueError("visibility episode ID reused across scopes")
        scoped[scope].append((row, scope, explicit_id))
    return scoped


def _explicit_scopes(
    scoped_rows: dict[tuple[str, str, str, str, str, str], list[tuple[dict[str, Any], tuple[str, str, str, str, str, str], str]]],
) -> set[tuple[str, str, str, str, str, str]]:
    result = set()
    for scope, values in scoped_rows.items():
        explicit = [bool(value[2]) for value in values]
        if any(explicit) and not all(explicit):
            raise ValueError("mixed explicit and missing visibility episode IDs within scope")
        if all(explicit):
            result.add(scope)
    return result


def _scope(row: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    source_match_key = _required(row, "source_match_key")
    source_video_key = _required(row, "source_video_key")
    candidate_subject_id = _required(row, "candidate_subject_id")
    tracklet_id = _required(row, "tracklet_id")
    label_value = str(row.get("team_label") or "").strip()
    if not label_value:
        raise ValueError("team_label is required")
    label = team_label(label_value)
    team_id = str(row.get("team_id") or "").strip()
    if not team_id and label == "U":
        raise ValueError("team_id requires a known team_label")
    return source_match_key, source_video_key, candidate_subject_id, tracklet_id, team_id, label


def _required(row: dict[str, Any], field: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _frame(row: dict[str, Any]) -> int:
    value = row.get("frame")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("frame is required and must be an integer")
    return value


def _explicit_episode_id(row: dict[str, Any]) -> str:
    return str(row.get("visibility_episode_id") or "").strip()
