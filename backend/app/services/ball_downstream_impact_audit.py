from __future__ import annotations

"""Read-only A/B audit for automatic versus operator-resolved ball tracks.

The production rebuild service owns writes.  This module deliberately runs the
same downstream builder twice in memory and compares semantic output only.
"""

import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.services.analysis import load_pitch_config
from app.services.attacking_momentum import build_attacking_momentum_document
from app.services.artifact_lineage import canonical_json_sha256
from app.services.ball_event_rebuild import build_analytics_readiness
from app.services.ball_possession import append_restart_pass_candidates, build_ball_possession_analysis
from app.services.candidate_keys import contact_candidate_key, restart_candidate_key
from app.services.contact_auto_review import apply_auto_contact_review
from app.services.effective_ball_tracks import (
    BALL_TRACKS_FILENAME,
    effective_ball_track_digest,
    load_effective_ball_tracks,
)
from app.services.event_candidates import build_event_candidate_artifacts
from app.services.match_phase_config import load_match_phase_config_read_only
from app.services.pass_candidates import apply_existing_pass_reviews, build_pass_review_report
from app.services.player_event_timeline import load_player_event_timeline


AUDIT_SCHEMA_VERSION = "ball-downstream-impact-audit:v1"
DEFAULT_LOCALITY_THRESHOLD_SEC = 5.0
VOLATILE_FIELDS = {
    "generated_at",
    "generation_id",
    "generated_from",
    "artifacts",
    "updated_at",
    "created_at",
}
DOWNSTREAM_ARTIFACTS = (
    "possession_candidates",
    "possession_segments",
    "contact_candidates",
    "event_candidates",
    "restart_candidates",
    "pass_candidates",
    "pass_review_report",
    "attacking_momentum",
    "possession_report",
    "analytics_readiness",
)


class BallDownstreamImpactAuditError(ValueError):
    pass


def build_ball_downstream_impact_audit(
    match_dir: Path,
    *,
    locality_threshold_sec: float = DEFAULT_LOCALITY_THRESHOLD_SEC,
) -> dict[str, Any]:
    """Build a deterministic, JSON-serializable no-write audit document."""

    match_dir = Path(match_dir)
    threshold = max(0.0, float(locality_threshold_sec))
    automatic = _load_automatic_tracks(match_dir)
    resolved = load_effective_ball_tracks(match_dir)
    raw_digest = effective_ball_track_digest(automatic)
    resolved_digest = resolved.input_digest
    ball_changes = _compare_ball_tracks(automatic, resolved.document)
    inputs = {
        "match_id": _match_id(match_dir),
        "raw_ball_digest": raw_digest,
        "resolved_ball_digest": resolved_digest,
        "resolved_provenance": resolved.provenance,
        "resolved_artifact": resolved.artifact,
        "locality_threshold_sec": threshold,
    }
    if raw_digest == resolved_digest:
        return _no_change_report(inputs, ball_changes)

    metadata = _load_object(match_dir / "match.json", "match metadata")
    players = load_player_event_timeline(match_dir)
    pitch = load_pitch_config(match_dir)
    video = metadata.get("video") if isinstance(metadata.get("video"), dict) else {}
    phase = load_match_phase_config_read_only(match_dir, {"video": video})
    before = build_ball_possession_analysis(
        match_dir, match_dir / "video.mp4", pitch, video, automatic, players.document,
        write_overlay_video=False, persist_artifacts=False, match_phase_config_doc=phase,
    )
    after = build_ball_possession_analysis(
        match_dir, match_dir / "video.mp4", pitch, video, resolved.document, players.document,
        write_overlay_video=False, persist_artifacts=False, match_phase_config_doc=phase,
    )
    # The generator starts from automatic review decisions. Re-apply only
    # durable operator decisions by stable keys, in memory, to both variants.
    # This is the same review state, without pretending an old review belongs
    # to a newly shaped candidate that no longer has its canonical key.
    existing_contacts = _load_optional_object(match_dir / "contact_candidates.json")
    existing_passes = _load_optional_object(match_dir / "pass_candidates.json")
    _apply_review_state(before, existing_contacts, existing_passes, phase, pitch, video)
    _apply_review_state(after, existing_contacts, existing_passes, phase, pitch, video)
    before_docs = _downstream_documents(before)
    after_docs = _downstream_documents(after)
    before_docs["analytics_readiness"] = _readiness(before_docs)
    after_docs["analytics_readiness"] = _readiness(after_docs)

    downstream = {
        "possession_candidates": _compare_possession_frames(
            before_docs["possession_candidates"], after_docs["possession_candidates"], ball_changes, threshold,
        ),
        "possession_segments": _compare_collection(
            before_docs["possession_segments"], after_docs["possession_segments"], "segments", _segment_key,
            ball_changes, threshold,
        ),
        "contact_candidates": _compare_collection(
            before_docs["contact_candidates"], after_docs["contact_candidates"], "candidates", _contact_key,
            ball_changes, threshold,
        ),
        "event_candidates": _compare_collection(
            before_docs["event_candidates"], after_docs["event_candidates"], "events", _event_key,
            ball_changes, threshold,
        ),
        "restart_candidates": _compare_collection(
            before_docs["restart_candidates"], after_docs["restart_candidates"], "candidates", _restart_key,
            ball_changes, threshold,
        ),
        "pass_candidates": _compare_collection(
            before_docs["pass_candidates"], after_docs["pass_candidates"], "candidates", _pass_key,
            ball_changes, threshold,
        ),
        "pass_review_report": _compare_document(before_docs["pass_review_report"], after_docs["pass_review_report"]),
        "attacking_momentum": _compare_momentum(
            before_docs["attacking_momentum"], after_docs["attacking_momentum"], ball_changes, threshold,
        ),
        "possession_report": _compare_document(before_docs["possession_report"], after_docs["possession_report"]),
        "analytics_readiness": _compare_document(before_docs["analytics_readiness"], after_docs["analytics_readiness"]),
    }
    locality = _aggregate_locality(downstream)
    changed_total = sum(int(item.get("changed_count") or 0) for item in downstream.values())
    classification = "downstream_change_with_remote_effects" if locality["remote_change_count"] else "local_downstream_change"
    if not changed_total:
        classification = "inconclusive"
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "inputs": {**inputs, "player_event_timeline_digest": players.input_digest, "player_event_timeline_provenance": players.provenance},
        "classification": classification,
        "ball_track_changes": ball_changes,
        "downstream": downstream,
        "final_possession_summary": _possession_summary(before_docs, after_docs),
        "locality": locality,
        "regression_flags": _regression_flags(locality, ball_changes),
    }


def compact_ball_downstream_impact_audit(report: Mapping[str, Any]) -> dict[str, Any]:
    """Drop frame/candidate excerpts while retaining review-relevant totals."""

    def compact(value: Any) -> Any:
        if isinstance(value, list):
            return value if len(value) <= 12 else {"omitted_item_count": len(value)}
        if isinstance(value, dict):
            return {
                str(key): compact(item)
                for key, item in value.items()
                if key not in {"changed_ball_frames", "added", "removed", "modified", "changed_frames", "changed_points"}
            }
        return value

    return compact(dict(report))


def render_ball_downstream_impact_markdown(report: Mapping[str, Any]) -> str:
    inputs = report.get("inputs") if isinstance(report.get("inputs"), Mapping) else {}
    ball = report.get("ball_track_changes") if isinstance(report.get("ball_track_changes"), Mapping) else {}
    downstream = report.get("downstream") if isinstance(report.get("downstream"), Mapping) else {}
    lines = [
        "# Ball downstream impact audit", "",
        "## Inputs", "",
        f"- Raw ball digest: `{inputs.get('raw_ball_digest', 'n/a')}`",
        f"- Resolved ball digest: `{inputs.get('resolved_ball_digest', 'n/a')}`",
        f"- Locality threshold: {inputs.get('locality_threshold_sec', 'n/a')} s", "",
        "## Resolved ball correction summary", "",
        f"- Classification: **{report.get('classification', 'inconclusive')}**",
        f"- Changed ball frames: {ball.get('changed_ball_frame_count', 0)}",
        f"- Changed regions: {_format_regions(ball.get('contiguous_changed_regions'))}", "",
        "## Downstream impact", "",
        "| Artifact | Changed | Added | Removed | Modified | Affected ranges |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for name in DOWNSTREAM_ARTIFACTS:
        item = downstream.get(name) if isinstance(downstream.get(name), Mapping) else {}
        lines.append(
            f"| {name} | {item.get('changed_count', 0)} | {item.get('added_count', 0)} | "
            f"{item.get('removed_count', 0)} | {item.get('modified_count', 0)} | {_format_regions(item.get('affected_regions'))} |"
        )
    locality = report.get("locality") if isinstance(report.get("locality"), Mapping) else {}
    lines.extend([
        "", "## Locality / propagation", "",
        f"- Near or overlapping changes: {locality.get('near_change_count', 0)}",
        f"- Remote changes: {locality.get('remote_change_count', 0)}",
        f"- Maximum distance: {locality.get('max_distance_from_ball_change_sec', 0)} s", "",
        "## Conclusion", "",
        f"{report.get('classification', 'inconclusive')} — this is a diagnostic result, not a production decision.", "",
    ])
    return "\n".join(lines)


def _load_automatic_tracks(match_dir: Path) -> dict[str, Any]:
    document = _load_object(match_dir / BALL_TRACKS_FILENAME, "automatic ball tracks")
    # The production semantic digest validates the exact downstream contract.
    effective_ball_track_digest(document)
    return document


def _no_change_report(inputs: dict[str, Any], ball_changes: dict[str, Any]) -> dict[str, Any]:
    zero = {"before_count": 0, "after_count": 0, "changed_count": 0, "added_count": 0, "removed_count": 0, "modified_count": 0, "affected_regions": [], "locality": _empty_locality()}
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "inputs": inputs,
        "classification": "no_effective_ball_change",
        "ball_track_changes": ball_changes,
        "downstream": {name: dict(zero) for name in DOWNSTREAM_ARTIFACTS},
        "final_possession_summary": {"before": {}, "after": {}, "delta": {}},
        "locality": _empty_locality(),
        "regression_flags": [],
    }


def _compare_ball_tracks(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    before_rows = {_integer(row.get("frame")): _ball_row(row) for row in before.get("positions") or [] if isinstance(row, Mapping) and _integer(row.get("frame")) is not None}
    after_rows = {_integer(row.get("frame")): _ball_row(row) for row in after.get("positions") or [] if isinstance(row, Mapping) and _integer(row.get("frame")) is not None}
    changed = []
    deltas = []
    for frame in sorted(set(before_rows) | set(after_rows)):
        left, right = before_rows.get(frame), after_rows.get(frame)
        if left == right:
            continue
        entry = {"frame": frame, "before": left, "after": right, "time_sec": _first_time(left, right)}
        changed.append(entry)
        delta = _position_delta(left, right)
        if delta is not None:
            deltas.append(delta)
    return {
        "raw_ball_digest": effective_ball_track_digest(before),
        "resolved_ball_digest": effective_ball_track_digest(after),
        "changed_ball_frame_count": len(changed),
        "changed_detected_frame_count": sum(1 for row in changed if "detected" in _sources(row)),
        "changed_interpolated_frame_count": sum(1 for row in changed if "interpolated" in _sources(row)),
        "changed_ball_frames": changed,
        "contiguous_changed_regions": _ball_regions(changed),
        "max_position_delta_m": round(max(deltas), 6) if deltas else None,
    }


def _downstream_documents(result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: dict(result.get(name) or {}) for name in DOWNSTREAM_ARTIFACTS if name != "analytics_readiness"}


def _apply_review_state(
    result: dict[str, Any],
    existing_contacts: Mapping[str, Any] | None,
    existing_passes: Mapping[str, Any] | None,
    phase: Mapping[str, Any],
    pitch: Any,
    video: Mapping[str, Any],
) -> None:
    contact_doc = dict(result["contact_candidates"])
    _apply_existing_contact_reviews(contact_doc, existing_contacts)
    event_docs = build_event_candidate_artifacts(contact_doc, dict(phase), dict(result["possession_candidates"]))
    pass_doc = event_docs["pass_candidates"]
    append_restart_pass_candidates(pass_doc, result["restart_candidates"])
    apply_existing_pass_reviews(pass_doc, dict(existing_passes) if existing_passes else None)
    result["contact_candidates"] = contact_doc
    result["event_candidates"] = event_docs["event_candidates"]
    result["pass_candidates"] = pass_doc
    result["pass_review_report"] = build_pass_review_report(pass_doc)
    result["attacking_momentum"] = build_attacking_momentum_document(
        result["possession_candidates"],
        dict(phase),
        pitch_width_m=float(getattr(pitch, "width_m", 30.0) or 30.0),
        pitch_length_m=float(getattr(pitch, "length_m", 47.4) or 47.4),
        pass_candidates_doc=pass_doc,
        restart_candidates_doc=result["restart_candidates"],
        possession_segments_doc=result["possession_segments"],
        match_duration_sec=video.get("duration_sec"),
    )


def _apply_existing_contact_reviews(contact_doc: dict[str, Any], existing: Mapping[str, Any] | None) -> None:
    reviewed: dict[str, Mapping[str, Any]] = {}
    for row in (existing or {}).get("candidates") or []:
        if not isinstance(row, Mapping):
            continue
        source = str(row.get("review_source") or "")
        if source not in {"manual", "manual_legacy"}:
            continue
        reviewed[str(row.get("candidate_key") or contact_candidate_key(dict(row)))] = row
    for candidate in contact_doc.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        prior = reviewed.get(str(candidate.get("candidate_key") or contact_candidate_key(candidate)))
        if prior is None:
            continue
        candidate["review_status"] = prior.get("review_status") or prior.get("status") or "needs_review"
        candidate["status"] = candidate["review_status"]
        candidate["review_source"] = str(prior.get("review_source") or "manual")
        candidate["review_notes"] = str(prior.get("review_notes") or "")
    apply_auto_contact_review(contact_doc, preserve_manual=True)


def _readiness(documents: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return build_analytics_readiness(
        possession_doc=dict(documents["possession_candidates"]),
        pass_doc=dict(documents["pass_candidates"]),
        momentum_doc=dict(documents["attacking_momentum"]),
        trigger="ball_downstream_impact_audit",
    )


def _compare_possession_frames(before: Mapping[str, Any], after: Mapping[str, Any], ball: Mapping[str, Any], threshold: float) -> dict[str, Any]:
    left = {_integer(row.get("frame")): _possession_row(row) for row in before.get("frames") or [] if isinstance(row, Mapping) and _integer(row.get("frame")) is not None}
    right = {_integer(row.get("frame")): _possession_row(row) for row in after.get("frames") or [] if isinstance(row, Mapping) and _integer(row.get("frame")) is not None}
    changed = []
    for frame in sorted(set(left) | set(right)):
        old, new = left.get(frame), right.get(frame)
        if old == new:
            continue
        changed.append({"frame": frame, "time_sec": _first_time(old, new), "classification": _possession_change_kind(old, new), "before": old, "after": new})
    locality = _locality(changed, ball, threshold)
    return {
        "before_count": len(left), "after_count": len(right), "changed_count": len(changed),
        "changed_frames": changed, "change_classification_counts": _counts(changed, "classification"),
        "affected_regions": _regions(_times_from_items(changed)), "locality": locality,
    }


def _compare_collection(before: Mapping[str, Any], after: Mapping[str, Any], field: str, key_fn: Any, ball: Mapping[str, Any], threshold: float) -> dict[str, Any]:
    left = _indexed(before.get(field), key_fn)
    right = _indexed(after.get(field), key_fn)
    added = [{"key": key, "after": right[key]} for key in sorted(set(right) - set(left))]
    removed = [{"key": key, "before": left[key]} for key in sorted(set(left) - set(right))]
    modified = [{"key": key, "before": left[key], "after": right[key], "changed_fields": _changed_fields(left[key], right[key])} for key in sorted(set(left) & set(right)) if left[key] != right[key]]
    changed = [*added, *removed, *modified]
    locality = _locality(changed, ball, threshold)
    return {
        "before_count": len(left), "after_count": len(right), "changed_count": len(changed),
        "added_count": len(added), "removed_count": len(removed), "modified_count": len(modified),
        "added": added, "removed": removed, "modified": modified,
        "affected_regions": _regions(_times_from_items(changed)), "locality": locality,
    }


def _compare_momentum(before: Mapping[str, Any], after: Mapping[str, Any], ball: Mapping[str, Any], threshold: float) -> dict[str, Any]:
    left = {_momentum_key(row): _semantic(row) for row in before.get("points") or [] if isinstance(row, Mapping)}
    right = {_momentum_key(row): _semantic(row) for row in after.get("points") or [] if isinstance(row, Mapping)}
    changed = [{"time_sec": _number(key), "before": left.get(key), "after": right.get(key)} for key in sorted(set(left) | set(right), key=lambda item: _number(item) or 0.0) if left.get(key) != right.get(key)]
    deltas = [_momentum_delta(row.get("before"), row.get("after")) for row in changed]
    locality = _locality(changed, ball, threshold)
    return {
        "before_count": len(left), "after_count": len(right), "changed_count": len(changed), "changed_point_count": len(changed),
        "changed_points": changed, "changed_time_buckets": [row.get("time_sec") for row in changed],
        "max_absolute_value_delta": round(max((item for item in deltas if item is not None), default=0.0), 6),
        "first_changed_time_sec": changed[0].get("time_sec") if changed else None,
        "last_changed_time_sec": changed[-1].get("time_sec") if changed else None,
        "affected_regions": _regions(_times_from_items(changed)), "locality": locality,
    }


def _compare_document(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    left, right = _semantic(before), _semantic(after)
    changed = left != right
    return {"before_count": 1, "after_count": 1, "changed_count": int(changed), "modified_count": int(changed), "changed_fields": _changed_fields(left, right) if changed else [], "affected_regions": [], "locality": _empty_locality()}


def _indexed(rows: Any, key_fn: Any) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if isinstance(row, Mapping):
            key = str(key_fn(row, index))
            indexed[key] = _semantic(row)
    return indexed


def _ball_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"frame": _integer(row.get("frame")), "time_sec": _number(row.get("time_sec")), "candidate_id": _text(row.get("candidate_id")), "position_m": _point(row.get("position_m")), "position_px": _point(row.get("position_px")), "source": _text(row.get("source")), "confidence": _number(row.get("confidence")), "interpolated_from": _pair(row.get("interpolated_from")), "resolution_source": _text(row.get("resolution_source"))}


def _possession_row(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("frame", "time_sec", "status", "stable_player_id", "stable_subject_id", "team_label", "team_id", "ball_candidate_id", "ball_source", "ball_position_m", "nearest_players", "nearest_player_source", "reason", "confidence")
    return {key: _semantic(row.get(key)) for key in keys}


def _segment_key(row: Mapping[str, Any], _: int) -> str:
    return canonical_json_sha256({key: _semantic(row.get(key)) for key in ("status", "stable_player_id", "stable_subject_id", "team_label", "team_id", "start_frame", "end_frame", "start_time_sec", "end_time_sec")})


def _contact_key(row: Mapping[str, Any], _: int) -> str:
    return str(row.get("candidate_key") or contact_candidate_key(dict(row)))


def _event_key(row: Mapping[str, Any], _: int) -> str:
    return str(
        row.get("source_candidate_key")
        or row.get("candidate_key")
        or row.get("event_key")
        or row.get("candidate_id")
        or canonical_json_sha256(_semantic(row))
    )


def _restart_key(row: Mapping[str, Any], _: int) -> str:
    return str(row.get("candidate_key") or restart_candidate_key(dict(row)))


def _pass_key(row: Mapping[str, Any], _: int) -> str:
    return str(row.get("candidate_key") or canonical_json_sha256({key: _semantic(row.get(key)) for key in ("source_candidate_key", "target_candidate_key", "restart_candidate_key", "source_candidate_id", "target_candidate_id")}))


def _momentum_key(row: Mapping[str, Any]) -> str:
    return str(row.get("time_sec") if row.get("time_sec") is not None else row.get("bin_start_sec") or row.get("index") or "unknown")


def _semantic(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _semantic(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0])) if str(key) not in VOLATILE_FIELDS}
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    if isinstance(value, float):
        return round(value, 8) if math.isfinite(value) else None
    return value


def _changed_fields(before: Any, after: Any, prefix: str = "") -> list[str]:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        fields: list[str] = []
        for key in sorted(set(before) | set(after)):
            fields.extend(_changed_fields(before.get(key), after.get(key), f"{prefix}.{key}" if prefix else str(key)))
        return fields
    return [] if before == after else [prefix or "value"]


def _possession_change_kind(before: Any, after: Any) -> str:
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return "other_semantic_change"
    if before.get("stable_player_id") != after.get("stable_player_id"):
        return "owner_changed"
    if before.get("team_label") != after.get("team_label") or before.get("team_id") != after.get("team_id"):
        return "team_changed"
    if before.get("status") != after.get("status"):
        return "status_changed"
    if before.get("nearest_players") != after.get("nearest_players"):
        return "nearest_player_changed"
    if before.get("ball_position_m") != after.get("ball_position_m") or before.get("ball_candidate_id") != after.get("ball_candidate_id"):
        return "ball_position_only"
    return "other_semantic_change"


def _locality(items: list[Mapping[str, Any]], ball: Mapping[str, Any], threshold: float) -> dict[str, Any]:
    regions = ball.get("contiguous_changed_regions") if isinstance(ball.get("contiguous_changed_regions"), list) else []
    annotated = []
    for item in items:
        time_sec = _item_time(item)
        distance = _nearest_region_distance(time_sec, regions)
        annotated.append({"time_sec": time_sec, "nearest_ball_change_distance_sec": distance, "classification": "unlocalized" if distance is None else "local" if distance <= threshold else "remote"})
    return {
        "changed_count": len(items),
        "overlapping_ball_region_count": sum(1 for row in annotated if row["nearest_ball_change_distance_sec"] == 0.0),
        "near_change_count": sum(1 for row in annotated if row["classification"] == "local"),
        "remote_change_count": sum(1 for row in annotated if row["classification"] == "remote"),
        "unlocalized_change_count": sum(1 for row in annotated if row["classification"] == "unlocalized"),
        "max_distance_from_ball_change_sec": round(max((float(row["nearest_ball_change_distance_sec"]) for row in annotated if row["nearest_ball_change_distance_sec"] is not None), default=0.0), 6),
        "items": annotated,
    }


def _aggregate_locality(downstream: Mapping[str, Any]) -> dict[str, Any]:
    values = [item.get("locality") for item in downstream.values() if isinstance(item, Mapping) and isinstance(item.get("locality"), Mapping)]
    return {
        "changed_count": sum(int(item.get("changed_count") or 0) for item in values),
        "overlapping_ball_region_count": sum(int(item.get("overlapping_ball_region_count") or 0) for item in values),
        "near_change_count": sum(int(item.get("near_change_count") or 0) for item in values),
        "remote_change_count": sum(int(item.get("remote_change_count") or 0) for item in values),
        "unlocalized_change_count": sum(int(item.get("unlocalized_change_count") or 0) for item in values),
        "max_distance_from_ball_change_sec": round(max((float(item.get("max_distance_from_ball_change_sec") or 0.0) for item in values), default=0.0), 6),
    }


def _empty_locality() -> dict[str, Any]:
    return {"changed_count": 0, "overlapping_ball_region_count": 0, "near_change_count": 0, "remote_change_count": 0, "unlocalized_change_count": 0, "max_distance_from_ball_change_sec": 0.0, "items": []}


def _regions(times: Iterable[float | None], *, gap_sec: float = 1.0) -> list[dict[str, Any]]:
    values = sorted({round(float(value), 6) for value in times if value is not None})
    regions: list[list[float]] = []
    for value in values:
        if not regions or value - regions[-1][-1] > gap_sec:
            regions.append([value])
        else:
            regions[-1].append(value)
    return [{"start_time_sec": region[0], "end_time_sec": region[-1], "frame_count": len(region)} for region in regions]


def _ball_regions(changes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Group changed ball rows by contiguous source frame, preserving exact bounds."""

    rows = sorted(
        (
            {"frame": _integer(change.get("frame")), "time_sec": _item_time(change)}
            for change in changes
            if _integer(change.get("frame")) is not None
        ),
        key=lambda row: int(row["frame"]),
    )
    groups: list[list[dict[str, Any]]] = []
    for row in rows:
        if not groups or int(row["frame"]) != int(groups[-1][-1]["frame"]) + 1:
            groups.append([row])
        else:
            groups[-1].append(row)
    return [
        {
            "start_frame": int(group[0]["frame"]),
            "end_frame": int(group[-1]["frame"]),
            "start_time_sec": group[0]["time_sec"],
            "end_time_sec": group[-1]["time_sec"],
            "frame_count": len(group),
        }
        for group in groups
    ]


def _times_from_items(items: Iterable[Mapping[str, Any]]) -> list[float | None]:
    return [_item_time(item) for item in items]


def _item_time(item: Mapping[str, Any]) -> float | None:
    for row in (item.get("after"), item.get("before"), item):
        if not isinstance(row, Mapping):
            continue
        for key in ("time_sec", "start_time_sec", "release_time_sec", "end_time_sec"):
            value = _number(row.get(key))
            if value is not None:
                return value
    return None


def _nearest_region_distance(time_sec: float | None, regions: list[Any]) -> float | None:
    if time_sec is None or not regions:
        return None
    distances = []
    for region in regions:
        if not isinstance(region, Mapping):
            continue
        start, end = _number(region.get("start_time_sec")), _number(region.get("end_time_sec"))
        if start is None or end is None:
            continue
        distances.append(0.0 if start <= time_sec <= end else min(abs(time_sec - start), abs(time_sec - end)))
    return round(min(distances), 6) if distances else None


def _possession_summary(before: Mapping[str, Mapping[str, Any]], after: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    def summary(documents: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        candidates = documents["possession_candidates"].get("summary") if isinstance(documents["possession_candidates"].get("summary"), Mapping) else {}
        report = documents["possession_report"].get("summary") if isinstance(documents["possession_report"].get("summary"), Mapping) else {}
        return {"possession_candidates": _semantic(candidates), "possession_report": _semantic(report), "segment_count": len(documents["possession_segments"].get("segments") or [])}
    left, right = summary(before), summary(after)
    return {"before": left, "after": right, "delta": _numeric_delta(left, right)}


def _numeric_delta(before: Any, after: Any) -> Any:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        return {key: _numeric_delta(before.get(key), after.get(key)) for key in sorted(set(before) | set(after))}
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return round(float(after) - float(before), 8)
    return None


def _regression_flags(locality: Mapping[str, Any], ball: Mapping[str, Any]) -> list[str]:
    flags = []
    if int(locality.get("remote_change_count") or 0):
        flags.append("remote_downstream_effects_present")
    if not int(ball.get("changed_ball_frame_count") or 0):
        flags.append("no_effective_ball_change")
    return flags


def _position_delta(before: Any, after: Any) -> float | None:
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return None
    left, right = _point(before.get("position_m")), _point(after.get("position_m"))
    if left is None or right is None:
        return None
    return math.dist(left, right)


def _momentum_delta(before: Any, after: Any) -> float | None:
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return None
    fields = ("net_momentum", "team_a_score", "team_b_score", "value")
    return max((abs(float(after.get(field) or 0.0) - float(before.get(field) or 0.0)) for field in fields if _number(after.get(field)) is not None and _number(before.get(field)) is not None), default=0.0)


def _sources(change: Mapping[str, Any]) -> set[str]:
    return {
        str(row["source"])
        for row in (change.get("after"), change.get("before"))
        if isinstance(row, Mapping) and row.get("source")
    }


def _first_time(*rows: Any) -> float | None:
    for row in rows:
        if isinstance(row, Mapping) and _number(row.get("time_sec")) is not None:
            return _number(row.get("time_sec"))
    return None


def _match_id(match_dir: Path) -> str:
    metadata = _load_object(match_dir / "match.json", "match metadata")
    return str(metadata.get("id") or match_dir.name)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BallDownstreamImpactAuditError(f"Unable to read {label}: {error}") from error
    if not isinstance(value, dict):
        raise BallDownstreamImpactAuditError(f"{label} must be a JSON object")
    return value


def _load_optional_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _load_object(path, path.name)


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


def _text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _point(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    numbers = [_number(item) for item in value]
    return [numbers[0], numbers[1]] if numbers[0] is not None and numbers[1] is not None else None


def _pair(value: Any) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    numbers = [_integer(item) for item in value]
    return [numbers[0], numbers[1]] if numbers[0] is not None and numbers[1] is not None else None


def _counts(rows: Iterable[Mapping[str, Any]], field: str) -> dict[str, int]:
    output: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or "unknown")
        output[key] = output.get(key, 0) + 1
    return dict(sorted(output.items()))


def _format_regions(regions: Any) -> str:
    if not isinstance(regions, list) or not regions:
        return "none"
    return ", ".join(f"{float(row.get('start_time_sec') or 0):.1f}–{float(row.get('end_time_sec') or 0):.1f}s" for row in regions if isinstance(row, Mapping))
