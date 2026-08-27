from __future__ import annotations

"""Lossless compact ownership codec for the derived review hot state.

Canonical review artifacts keep explicit per-frame ownership.  The disposable
hot-state cache may store the same exact sets as deterministic frame runs to
avoid parsing, normalizing and serializing hundreds of thousands of pairs on
every operator click.  Encoding is exact: a decode(encode(pairs)) round trip
reproduces the identical pair set, including sparse gaps and tracklet identity,
so digests computed over decoded pairs never change their meaning.

Decoding is deliberately STRICT.  The compact document is an exact-source
cache: malformed or non-canonical runs invalidate the whole cache instead of
being silently skipped, because a partially decoded ownership set would be an
invisible correctness lie.
"""

from bisect import bisect_right
from collections import Counter
from collections.abc import Iterator, Mapping
from typing import Any

Pair = tuple[str, int]

PAIR_RUN_KEYS = frozenset({
    "detected_pair_runs",
    "owned_observation_runs",
    "_potential_named_observation_runs",
    "observed_pair_runs",
})
INDEX_RUN_KEY = "pair_index_runs"


class CompactOwnershipError(ValueError):
    """Raised when durable compact ownership is malformed or non-canonical."""


def normalize_pairs(raw: Any) -> list[Pair]:
    output: list[Pair] = []
    for pair in raw or []:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            try:
                output.append((str(pair[0]), int(pair[1])))
            except (TypeError, ValueError):
                continue
    return sorted(set(output))


def encode_pair_runs(raw: Any) -> dict[str, list[list[int]]]:
    """Encode an iterable of (tracklet_id, frame) as per-tracklet inclusive runs."""
    runs: dict[str, list[list[int]]] = {}
    for tracklet_id, frame in normalize_pairs(raw):
        tracklet_runs = runs.setdefault(tracklet_id, [])
        if tracklet_runs and tracklet_runs[-1][1] + 1 == frame:
            tracklet_runs[-1][1] = frame
        else:
            tracklet_runs.append([frame, frame])
    return {tracklet_id: tracklet_runs for tracklet_id, tracklet_runs in sorted(runs.items())}


def _checked_frame(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CompactOwnershipError(f"{where}: frame must be an integer")
    return value


def validate_pair_runs(runs: Any) -> None:
    """Strictly validate ``{tracklet_id: [[start, end], ...]}``.

    Runs must be sorted, non-overlapping and non-adjacent per tracklet; that
    canonical form is what :func:`encode_pair_runs` produces and anything else
    means the durable cache was corrupted or hand-edited.
    """
    if not isinstance(runs, dict):
        raise CompactOwnershipError("ownership runs must be an object")
    for tracklet_id, tracklet_runs in runs.items():
        if not isinstance(tracklet_id, str) or not tracklet_id:
            raise CompactOwnershipError("ownership runs tracklet id must be a non-empty string")
        if not isinstance(tracklet_runs, list):
            raise CompactOwnershipError(f"runs for {tracklet_id} must be a list")
        previous_end: int | None = None
        for run in tracklet_runs:
            if not isinstance(run, list) or len(run) != 2:
                raise CompactOwnershipError(f"run for {tracklet_id} must be [start, end]")
            start = _checked_frame(run[0], f"run start for {tracklet_id}")
            end = _checked_frame(run[1], f"run end for {tracklet_id}")
            if end < start:
                raise CompactOwnershipError(f"run for {tracklet_id} has end < start")
            if previous_end is not None:
                if start <= previous_end:
                    raise CompactOwnershipError(f"runs for {tracklet_id} overlap or are unsorted")
                if start == previous_end + 1:
                    raise CompactOwnershipError(f"adjacent runs for {tracklet_id} must be merged")
            previous_end = end


def validate_index_runs(encoded: Any) -> None:
    """Strictly validate run-length encoded coverage index rows."""
    if not isinstance(encoded, dict):
        raise CompactOwnershipError("index runs must be an object")
    for tracklet_id, entries in encoded.items():
        if not isinstance(tracklet_id, str) or not tracklet_id:
            raise CompactOwnershipError("index runs tracklet id must be a non-empty string")
        if not isinstance(entries, list):
            raise CompactOwnershipError(f"index runs for {tracklet_id} must be a list")
        previous_end: int | None = None
        previous_value: Any = None
        for entry in entries:
            if not isinstance(entry, list) or len(entry) != 3:
                raise CompactOwnershipError(f"index run for {tracklet_id} must be [start, end, value]")
            start = _checked_frame(entry[0], f"index run start for {tracklet_id}")
            end = _checked_frame(entry[1], f"index run end for {tracklet_id}")
            if end < start:
                raise CompactOwnershipError(f"index run for {tracklet_id} has end < start")
            _validate_index_value(entry[2], tracklet_id)
            if previous_end is not None:
                if start <= previous_end:
                    raise CompactOwnershipError(f"index runs for {tracklet_id} overlap or are unsorted")
                if start == previous_end + 1 and entry[2] == previous_value:
                    raise CompactOwnershipError(
                        f"adjacent index runs for {tracklet_id} with equal value must be merged"
                    )
            previous_end = end
            previous_value = entry[2]


INDEX_VALUE_KEYS = frozenset({"identity_status", "team_label", "canonical_player_id"})
SUPPORTED_TEAM_LABELS = frozenset({"A", "B", "U"})


def _validate_index_value(value: Any, where: str) -> None:
    """Validate one coverage observation mapping against the canonical shape.

    ``summarize_effective_observations`` builds exactly
    ``{"identity_status": str, "team_label": "A"|"B"|"U",
    "canonical_player_id": str | None}``; anything else cannot be projected.
    """
    if not isinstance(value, dict):
        raise CompactOwnershipError(f"index run value for {where} must be an observation mapping")
    if set(value.keys()) != INDEX_VALUE_KEYS:
        raise CompactOwnershipError(f"index run value for {where} has unexpected keys")
    if not isinstance(value["identity_status"], str) or not value["identity_status"]:
        raise CompactOwnershipError(f"index run value for {where} has invalid identity_status")
    if value["team_label"] not in SUPPORTED_TEAM_LABELS:
        raise CompactOwnershipError(f"index run value for {where} has unsupported team_label")
    player_id = value["canonical_player_id"]
    if player_id is not None and not isinstance(player_id, str):
        raise CompactOwnershipError(f"index run value for {where} has invalid canonical_player_id")


# Semantic twin pairs: schema-v2 storage must contain exactly one durable
# representation per ownership concept at the same node.
AMBIGUOUS_TWINS: tuple[tuple[str, str], ...] = (
    ("detected_pairs", "detected_pair_runs"),
    ("owned_observations", "owned_observation_runs"),
    ("_potential_named_observation_pairs", "_potential_named_observation_runs"),
    ("observed_pairs", "observed_pair_runs"),
    ("pair_index", "pair_index_runs"),
)

_V2_TOP_LEVEL_CONTRACT: dict[str, type | tuple[type, ...]] = {
    "schema_version": str,
    "state_version": int,
    "match_id": str,
    "progress": dict,
    "internal_review_units": list,
    "unit_lookup": dict,
    "source_index": dict,
    "historical_split_repairs": dict,
    "projection_inputs": dict,
    "roster_options": list,
    "slot_options": list,
    "canonical_segment_slot_options": list,
    "freshness": dict,
}


def validate_v2_hot_state(
    document: Mapping[str, Any],
    *,
    schema_version: str,
) -> None:
    """Validate a schema-v2 hot-state document as a complete cache contract.

    Beyond individually valid runs this enforces structural completeness,
    unambiguous representation (no expanded+compact twins at one node),
    required exact ownership for non-empty review units and cardinality
    consistency between runs and declared observation counts.
    """
    for field, expected in _V2_TOP_LEVEL_CONTRACT.items():
        value = document.get(field)
        if field == "state_version":
            if isinstance(value, bool) or not isinstance(value, expected):
                raise CompactOwnershipError(f"v2 state field {field} has invalid type")
            continue
        if not isinstance(value, expected):
            raise CompactOwnershipError(f"v2 state field {field} has invalid type")
    if document["schema_version"] != schema_version:
        raise CompactOwnershipError("unexpected schema_version")
    if not str(document["match_id"]):
        raise CompactOwnershipError("v2 state match_id must be non-empty")

    inputs = document["projection_inputs"]
    for key in ("pair_index_runs", "observed_pair_runs"):
        if key not in inputs:
            raise CompactOwnershipError(f"v2 projection_inputs missing required {key}")
    for key in ("coverage", "technical_diagnostics"):
        if not isinstance(inputs.get(key), dict):
            raise CompactOwnershipError(f"v2 projection_inputs.{key} must be an object")
    for key in ("mixed_players", "deferred_correction_context"):
        if key in inputs and not isinstance(inputs[key], dict):
            raise CompactOwnershipError(f"v2 projection_inputs.{key} must be an object when stored")

    units = document["internal_review_units"]
    for unit in units:
        if not isinstance(unit, dict):
            raise CompactOwnershipError("internal_review_units entries must be objects")
        detected_runs = unit.get("detected_pair_runs")
        count = unit.get("detected_observation_count")
        has_count = isinstance(count, int) and not isinstance(count, bool) and count > 0
        if has_count:
            if "detected_pair_runs" not in unit:
                raise CompactOwnershipError(
                    f"unit {unit.get('candidate_subject_id')} declares observations without compact ownership"
                )
            validate_pair_runs(detected_runs)
            if count_pair_runs(detected_runs) != count:
                raise CompactOwnershipError(
                    f"unit {unit.get('candidate_subject_id')} ownership cardinality mismatch"
                )
        elif detected_runs is not None:
            validate_pair_runs(detected_runs)
        for member in unit.get("continuity_members") or []:
            if not isinstance(member, dict):
                raise CompactOwnershipError("continuity_members entries must be objects")
            member_runs = member.get("detected_pair_runs")
            if member_runs is not None:
                validate_pair_runs(member_runs)

    stack: list[Any] = [document]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            keys = set(node.keys())
            for left, right in AMBIGUOUS_TWINS:
                if left in keys and right in keys:
                    raise CompactOwnershipError(
                        f"ambiguous durable representation: {left} and {right} coexist"
                    )
            for key, value in node.items():
                if key in PAIR_RUN_KEYS:
                    validate_pair_runs(value)
                elif key == INDEX_RUN_KEY:
                    validate_index_runs(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(item for item in node if isinstance(item, (dict, list)))


def validate_compact_document(document: Mapping[str, Any]) -> None:
    """Validate every durable compact structure found in a hot-state document."""
    stack: list[Any] = [document]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in PAIR_RUN_KEYS:
                    validate_pair_runs(value)
                elif key == INDEX_RUN_KEY:
                    validate_index_runs(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(item for item in node if isinstance(item, (dict, list)))


EXPANSION_STATS: Counter[str] = Counter()


def reset_expansion_stats() -> Counter[str]:
    EXPANSION_STATS.clear()
    return EXPANSION_STATS


def decode_pair_runs(runs: Any) -> list[Pair]:
    """Expand encoded runs back to the exact sorted distinct pair list."""
    validate_pair_runs(runs)
    output: list[Pair] = []
    for tracklet_id, tracklet_runs in runs.items():
        for start, end in tracklet_runs:
            output.extend((tracklet_id, frame) for frame in range(start, end + 1))
    output.sort()
    EXPANSION_STATS["expanded_pairs"] += len(output)
    return output


def count_pair_runs(runs: Any) -> int:
    """Total exact pair count represented by encoded runs."""
    total = 0
    if not isinstance(runs, dict):
        return 0
    for tracklet_runs in runs.values():
        for run in tracklet_runs or []:
            if not isinstance(run, list) or len(run) != 2:
                raise CompactOwnershipError("count encountered a malformed run")
            start = _checked_frame(run[0], "counted run start")
            end = _checked_frame(run[1], "counted run end")
            if end < start:
                raise CompactOwnershipError("counted run has end < start")
            total += end - start + 1
    return total


def encode_index_rows(rows: Any) -> dict[str, list[list[Any]]]:
    """Run-length encode serialized coverage-index rows.

    Input rows use the durable legacy shape
    ``{"tracklet_id", "frame", "value"}`` sorted by ``(tracklet_id, frame)``.
    Consecutive frames of one tracklet sharing an equal value payload collapse
    into ``[start_frame, end_frame, value]`` entries.  The encoding is lossless:
    decoding reproduces exactly the same rows in the same order.
    """
    encoded: dict[str, list[list[Any]]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        tracklet_id = str(row.get("tracklet_id") or "")
        if not tracklet_id:
            continue
        raw_frame = row.get("frame")
        if isinstance(raw_frame, bool) or not isinstance(raw_frame, int):
            continue
        frame = raw_frame
        value = row.get("value")
        value = dict(value) if isinstance(value, dict) else None
        tracklet_entries = encoded.setdefault(tracklet_id, [])
        if (
            tracklet_entries
            and tracklet_entries[-1][1] + 1 == frame
            and tracklet_entries[-1][2] == value
        ):
            tracklet_entries[-1][1] = frame
        else:
            tracklet_entries.append([frame, frame, value])
    return {tracklet_id: entries for tracklet_id, entries in sorted(encoded.items())}


def decode_index_rows(encoded: Any) -> list[dict[str, Any]]:
    """Expand run-length encoded coverage rows back to the legacy row shape."""
    validate_index_runs(encoded)
    rows: list[dict[str, Any]] = []
    for tracklet_id, entries in encoded.items():
        for start, end, value in entries:
            for frame in range(start, end + 1):
                rows.append({
                    "tracklet_id": str(tracklet_id),
                    "frame": frame,
                    "value": dict(value) if isinstance(value, dict) else value,
                })
    rows.sort(key=lambda row: (row["tracklet_id"], row["frame"]))
    EXPANSION_STATS["expanded_pairs"] += len(rows)
    return rows


def encode_observation_rows(rows: Any) -> dict[str, list[list[int]]] | None:
    """Encode ``{"tracklet_id", "frame"}`` rows exactly like plain pairs.

    Returns None when a row carries extra fields, so callers can keep the
    original verbose list instead of risking a lossy transformation.
    """
    pairs: list[Pair] = []
    for row in rows or []:
        if not isinstance(row, dict) or set(row.keys()) != {"tracklet_id", "frame"}:
            return None
        frame = row["frame"]
        if isinstance(frame, bool) or not isinstance(frame, int):
            return None
        pairs.append((str(row["tracklet_id"]), frame))
    return encode_pair_runs(pairs)


def decode_observation_runs(runs: Any) -> list[dict[str, Any]]:
    """Expand observation-row runs back to the original dict row shape."""
    return [
        {"tracklet_id": tracklet_id, "frame": frame}
        for tracklet_id, frame in decode_pair_runs(runs)
    ]


def _merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def runs_union(*run_dicts: dict[str, list[list[int]]]) -> dict[str, list[list[int]]]:
    """Exact interval union of validated run dictionaries."""
    collected: dict[str, list[list[int]]] = {}
    for run_dict in run_dicts:
        for tracklet_id, tracklet_runs in (run_dict or {}).items():
            collected.setdefault(tracklet_id, []).extend([list(run) for run in tracklet_runs])
    return {
        tracklet_id: _merge_intervals(tracklet_runs)
        for tracklet_id, tracklet_runs in sorted(collected.items())
    }


def runs_difference(minuend: dict[str, list[list[int]]], subtrahend: dict[str, list[list[int]]]) -> dict[str, list[list[int]]]:
    """Exact interval difference ``minuend - subtrahend`` of validated runs."""
    output: dict[str, list[list[int]]] = {}
    for tracklet_id, tracklet_runs in minuend.items():
        cuts = sorted(
            (run[0], run[1]) for run in (subtrahend.get(tracklet_id) or [])
        )
        remaining: list[list[int]] = []
        for start, end in tracklet_runs:
            cursor = start
            for cut_start, cut_end in cuts:
                if cut_end < cursor:
                    continue
                if cut_start > end:
                    break
                if cut_start > cursor:
                    remaining.append([cursor, cut_start - 1])
                cursor = max(cursor, cut_end + 1)
                if cursor > end:
                    break
            if cursor <= end:
                remaining.append([cursor, end])
        if remaining:
            output[tracklet_id] = remaining
    return output


def runs_intersection_size(left: dict[str, list[list[int]]], right: dict[str, list[list[int]]]) -> int:
    """Exact cardinality of ``left ∩ right`` without materializing pairs."""
    total = 0
    for tracklet_id, tracklet_runs in left.items():
        other = right.get(tracklet_id)
        if not other:
            continue
        cursor = 0
        for start, end in tracklet_runs:
            index = cursor
            while index < len(other) and other[index][1] < start:
                index += 1
            cursor = index
            walk = index
            while walk < len(other) and other[walk][0] <= end:
                total += min(end, other[walk][1]) - max(start, other[walk][0]) + 1
                walk += 1
    return total


class CompactPairIndexView(Mapping):
    """Read-only lazy mapping over run-length encoded coverage index rows.

    Consumers of the coverage pair index perform per-pair lookups and one
    filtering full scan.  This view serves both directly from the compact
    runs without ever materializing a dict entry per frame.  Returned value
    dicts are shared and must be treated as read-only, which every current
    coverage consumer already does.
    """

    def __init__(self, encoded: Mapping[str, list[list[Any]]]) -> None:
        self._encoded = encoded
        self._starts = {
            tracklet_id: [entry[0] for entry in entries]
            for tracklet_id, entries in encoded.items()
            if isinstance(entries, list)
        }
        # Consecutive frame lookups hit the same run, so a one-entry-per-
        # tracklet hint turns repeated bisects into constant-time checks.
        self._hint: dict[str, int] = {}

    def tracklets(self) -> Mapping[str, list[list[Any]]]:
        """Expose the underlying validated per-tracklet value segments."""
        return self._encoded

    def _find(self, key: Pair) -> dict[str, Any] | None:
        tracklet_id, frame = key
        entries = self._encoded.get(tracklet_id)
        if not isinstance(entries, list):
            return None
        hinted = self._hint.get(tracklet_id)
        if (
            hinted is not None
            and entries[hinted][0] <= frame <= entries[hinted][1]
        ):
            return entries[hinted][2]
        index = bisect_right(self._starts.get(tracklet_id, []), int(frame)) - 1
        if index < 0:
            return None
        entry = entries[index]
        if frame > entry[1]:
            return None
        self._hint[tracklet_id] = index
        return entry[2]

    def __getitem__(self, key: Pair) -> dict[str, Any]:
        row = self._find(key)
        if row is None:
            raise KeyError(key)
        return row

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, tuple) or len(key) != 2:
            return False
        return self._find(key) is not None

    def __iter__(self) -> Iterator[Pair]:
        for tracklet_id, entries in self._encoded.items():
            for start, end, _value in entries:
                yield from ((tracklet_id, frame) for frame in range(start, end + 1))

    def __len__(self) -> int:
        return sum(
            max(0, int(entry[1]) - int(entry[0]) + 1)
            for entries in self._encoded.values()
            for entry in entries or []
            if isinstance(entry, (list, tuple)) and len(entry) == 3
        )
