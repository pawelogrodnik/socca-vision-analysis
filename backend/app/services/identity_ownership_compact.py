from __future__ import annotations

"""Lossless compact ownership codec for the derived review hot state.

Canonical review artifacts keep explicit per-frame ownership.  The disposable
hot-state cache may store the same exact sets as deterministic frame runs to
avoid parsing, normalizing and serializing hundreds of thousands of pairs on
every operator click.  Encoding is exact: a decode(encode(pairs)) round trip
reproduces the identical pair set, including sparse gaps and tracklet identity,
so digests computed over decoded pairs never change their meaning.
"""

from bisect import bisect_right
from collections.abc import Iterator, Mapping
from typing import Any

Pair = tuple[str, int]


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


def decode_pair_runs(runs: Any) -> list[Pair]:
    """Expand encoded runs back to the exact sorted distinct pair list."""
    output: list[Pair] = []
    if not isinstance(runs, dict):
        return output
    for tracklet_id, tracklet_runs in runs.items():
        if not isinstance(tracklet_runs, list):
            continue
        for run in tracklet_runs:
            if not isinstance(run, (list, tuple)) or len(run) != 2:
                continue
            try:
                start, end = int(run[0]), int(run[1])
            except (TypeError, ValueError):
                continue
            if end < start:
                continue
            output.extend((str(tracklet_id), frame) for frame in range(start, end + 1))
    output.sort()
    return output


def count_pair_runs(runs: Any) -> int:
    """Total exact pair count represented by encoded runs."""
    total = 0
    if isinstance(runs, dict):
        for tracklet_runs in runs.values():
            for run in tracklet_runs or []:
                if isinstance(run, (list, tuple)) and len(run) == 2:
                    total += max(0, int(run[1]) - int(run[0]) + 1)
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
    rows: list[dict[str, Any]] = []
    if not isinstance(encoded, dict):
        return rows
    for tracklet_id, entries in encoded.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, (list, tuple)) or len(entry) != 3:
                continue
            raw_start, raw_end = entry[0], entry[1]
            if isinstance(raw_start, bool) or isinstance(raw_end, bool):
                continue
            if not isinstance(raw_start, int) or not isinstance(raw_end, int):
                continue
            start, end = raw_start, raw_end
            value = entry[2]
            for frame in range(start, end + 1):
                rows.append({
                    "tracklet_id": str(tracklet_id),
                    "frame": frame,
                    "value": dict(value) if isinstance(value, dict) else value,
                })
    rows.sort(key=lambda row: (row["tracklet_id"], row["frame"]))
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
