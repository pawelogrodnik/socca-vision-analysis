"""Read-only baseline for automatic contact/pass candidates against Goldset v1.

This is deliberately an evaluation adapter, not a generalized benchmark
framework.  It rebuilds the current downstream candidate layer in memory from
the current effective ball tracks and never imports operator review decisions.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.services.analysis import load_pitch_config
from app.services.ball_possession import build_ball_possession_analysis
from app.services.effective_ball_tracks import load_effective_ball_tracks
from app.services.match_phase_config import load_match_phase_config_read_only
from app.services.player_event_timeline import load_player_event_timeline


SCHEMA_VERSION = "contact-action-baseline-evaluation:v1"
PASS_ACTIONS = frozenset({"PASS", "RESTART"})
STRICT_CONTACT_ACTIONS = frozenset({"PASS", "RESTART", "INTERVENTION", "GK_COLLECTION"})
PASS_OUTCOME_BY_GOLD = {
    "COMPLETED": "completed_pass",
    "INTERCEPTED": "failed_pass",
    "MISCONTROLLED": "failed_pass",
    "OUT": "failed_pass",
}
PASS_OUTCOMES = frozenset({"completed_pass", "failed_pass"})
DIAGNOSTIC_RADIUS_SEC = 1.0
SHOT_PASS_TOLERANCE_SEC = 1.0


def snapshot_source_tree(match_dirs: Mapping[str, Path]) -> dict[str, list[dict[str, Any]]]:
    """Snapshot production inputs without writing or reading video payloads."""

    snapshots: dict[str, list[dict[str, Any]]] = {}
    for source_match_id, match_dir in sorted(match_dirs.items()):
        root = Path(match_dir)
        rows: list[dict[str, Any]] = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            stat = path.stat()
            rows.append({
                "path": str(path.relative_to(root)),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            })
        snapshots[source_match_id] = rows
    return snapshots


def assert_source_tree_unchanged(
    before: Mapping[str, Sequence[Mapping[str, Any]]],
    match_dirs: Mapping[str, Path],
) -> None:
    after = snapshot_source_tree(match_dirs)
    if _canonical(before) != _canonical(after):
        raise RuntimeError("Read-only baseline mutated a production source directory.")


def summarize_source_tree_snapshot(snapshot: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """Return an auditable, compact record without copying production inventory."""

    return [
        {
            "source_match_id": source_match_id,
            "file_count": len(rows),
            "tree_fingerprint": hashlib.sha256(_canonical(rows).encode("utf-8")).hexdigest(),
        }
        for source_match_id, rows in sorted(snapshot.items())
    ]


def build_current_automatic_documents(match_dir: Path) -> dict[str, dict[str, Any]]:
    """Run the actual downstream builder in memory using current effective inputs.

    No stored contact/pass review document is read or re-applied.  Thus the
    resulting statuses are auto-review statuses rather than historical operator
    decisions.
    """

    match_dir = Path(match_dir)
    metadata = _read_object(match_dir / "match.json")
    video = metadata.get("video") if isinstance(metadata.get("video"), Mapping) else {}
    effective = load_effective_ball_tracks(match_dir)
    players = load_player_event_timeline(match_dir)
    phase = load_match_phase_config_read_only(match_dir, {"video": dict(video)})
    result = build_ball_possession_analysis(
        match_dir,
        match_dir / "video.mp4",
        load_pitch_config(match_dir),
        dict(video),
        effective.document,
        players.document,
        write_overlay_video=False,
        persist_artifacts=False,
        match_phase_config_doc=phase,
    )
    return {
        name: dict(result.get(name) or {})
        for name in ("contact_candidates", "event_candidates", "restart_candidates", "pass_candidates")
    }


def evaluate_contact_action_baseline(
    goldset: Mapping[str, Any],
    source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Evaluate automatically generated candidates against merged-timeline truth."""

    windows = {str(row["window_id"]): dict(row) for row in goldset.get("windows") or [] if isinstance(row, Mapping)}
    events = [dict(row) for row in goldset.get("events") or [] if isinstance(row, Mapping)]
    intervals = [dict(row) for row in goldset.get("game_state_intervals") or [] if isinstance(row, Mapping)]
    identity_notes = [dict(row) for row in goldset.get("identity_notes") or [] if isinstance(row, Mapping)]

    candidates = _project_candidates(windows, source_documents)
    scoped = _scope_candidates(windows, candidates)
    gold_passes = [row for row in events if _is_scored_pass(row)]
    pass_candidates = [row for row in scoped["pass"] if str(row.get("outcome")) in PASS_OUTCOMES]
    pass_matches, unmatched_gold_indexes, unmatched_pass_indexes = _match_passes(gold_passes, pass_candidates)
    pass_rows = _score_pass_matches(gold_passes, pass_candidates, pass_matches, identity_notes)
    pass_misses = _build_pass_misses(gold_passes, unmatched_gold_indexes, scoped["contact"], pass_rows)
    pass_false_positives = _classify_pass_false_positives(
        [pass_candidates[index] for index in unmatched_pass_indexes], events, intervals,
    )

    strict_contact_gold = [row for row in events if _is_strict_contact_anchor(row)]
    contact_matches, unmatched_contact_gold, unmatched_contact_candidate = _match_contacts(strict_contact_gold, scoped["contact"])
    contact_rows = _score_contact_matches(strict_contact_gold, scoped["contact"], contact_matches)
    shot_confusions = _shot_pass_confusions(events, pass_candidates)
    dead_ball = _dead_ball_leakage(intervals, scoped)
    hard_negative = _attempted_contact_hard_negative(events, scoped["contact"])

    failure_rows = [*pass_rows, *pass_misses]
    failures = Counter(row["primary_failure_stage"] for row in failure_rows)
    pass_summary = _pass_summary(gold_passes, pass_candidates, pass_rows, pass_misses, pass_false_positives)
    by_window = _window_summary(windows, gold_passes, pass_rows, pass_misses, pass_false_positives, scoped)
    report = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_layer": {
            "candidate_generation": "current effective ball tracks + current player event timeline",
            "contact_review": "auto_contact_review_v1 regenerated in memory",
            "manual_review_status_used_for_headline_metrics": False,
            "production_artifacts_written": False,
        },
        "goldset": {
            "schema_version": goldset.get("schema_version"),
            "published_match_id": goldset.get("published_match_id"),
            "window_count": len(windows),
            "manual_event_count": sum(1 for row in events if row.get("event_type") != "SHOT_REFERENCE"),
            "canonical_shot_reference_count": sum(1 for row in events if row.get("event_type") == "SHOT_REFERENCE"),
        },
        "sources": _source_summary(windows, source_documents),
        "summary": {
            "pass": pass_summary,
            "contact": _contact_summary(strict_contact_gold, scoped["contact"], contact_rows, unmatched_contact_gold),
            "shot_pass_confusion": {
                "canonical_shots_in_scope": len(shot_confusions),
                "shots_with_nearby_pass_candidate": sum(bool(row["nearby_pass_candidates"]) for row in shot_confusions),
                "shot_as_pass_confusion_count": sum(bool(row["nearby_pass_candidates"]) for row in shot_confusions),
                "shot_as_pass_confusion_rate": _ratio(sum(bool(row["nearby_pass_candidates"]) for row in shot_confusions), len(shot_confusions)),
            },
            "dead_ball_leakage": _dead_ball_summary(dead_ball),
        },
        "windows": by_window,
        "pass_matches": pass_rows,
        "pass_misses": pass_misses,
        "pass_false_positives": pass_false_positives,
        "failure_breakdown": {
            "by_primary_stage": dict(sorted(failures.items(), key=lambda item: (-item[1], item[0]))),
            "representative_examples": _representative_failures(failure_rows),
        },
        "contact_matches": contact_rows,
        "contact_misses": [_gold_public(strict_contact_gold[index]) for index in unmatched_contact_gold],
        "unmatched_contact_candidate_count": len(unmatched_contact_candidate),
        "shot_pass_confusions": shot_confusions,
        "dead_ball_candidates": dead_ball,
        "hard_negative_results": hard_negative,
        "identity_context_matches": [row for row in pass_rows if row.get("identity_context")],
        "ambiguous_annotations": [_gold_public(row) for row in events if "AMBIGUOUS" in (row.get("modifiers") or [])],
    }
    return report


def render_contact_action_baseline_markdown(report: Mapping[str, Any]) -> str:
    summary = _mapping(report.get("summary"))
    passing = _mapping(summary.get("pass"))
    contact = _mapping(summary.get("contact"))
    confusion = _mapping(summary.get("shot_pass_confusion"))
    dead_ball = _mapping(summary.get("dead_ball_leakage"))
    lines = [
        "# Contact / Action baseline — current pipeline vs Goldset v1", "",
        "## Executive summary", "",
        "This is a read-only measurement of regenerated automatic candidates. Historical manual reviews are not used to turn a miss into a hit.", "",
        "### PASS", "",
        f"- Gold attempts: **{passing.get('gold_pass_attempts', 0)}**",
        f"- Matched / missed: **{passing.get('matched_pass_attempts', 0)} / {passing.get('missed_pass_attempts', 0)}**",
        f"- Unmatched pass candidates: **{passing.get('unmatched_pass_candidates', 0)}**",
        f"- Precision / recall / F1: **{_percentage(passing.get('precision'))} / {_percentage(passing.get('recall'))} / {_percentage(passing.get('f1'))}**",
        f"- Broad outcome accuracy: **{_percentage(passing.get('outcome_accuracy'))}** ({passing.get('outcome_scorable_matches', 0)} scorable matches)", "",
        "### CONTACT", "",
        f"- Strict-anchor recall: **{_percentage(contact.get('recall'))}** ({contact.get('matched_strict_anchors', 0)} / {contact.get('strict_contact_anchors', 0)})", "",
        "### RESTART / confusion / dead ball", "",
        f"- Restart recall: **{_percentage(passing.get('restart_recall'))}**",
        f"- Shot-as-pass: **{confusion.get('shot_as_pass_confusion_count', 0)} / {confusion.get('canonical_shots_in_scope', 0)}**",
        f"- Dead-ball spurious pass candidates: **{dead_ball.get('spurious_pass_candidates', 0)}**",
        f"- Dead-ball spurious contact candidates: **{dead_ball.get('spurious_contact_candidates', 0)}**", "",
        "## Results by window", "",
        "| Window | Gold passes | Matched | Missed | Pass candidates | Unmatched candidates |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for window_id, row in _mapping(report.get("windows")).items():
        data = _mapping(row)
        lines.append(
            f"| {window_id} | {data.get('gold_pass_attempts', 0)} | {data.get('matched_pass_attempts', 0)} | "
            f"{data.get('missed_pass_attempts', 0)} | {data.get('pass_candidates', 0)} | {data.get('unmatched_pass_candidates', 0)} |"
        )
    lines.extend(["", "## Missed gold passes", ""])
    for row in report.get("pass_misses") or []:
        lines.append(_pass_diagnostic_line(row))
    lines.extend(["", "## False-positive passes", ""])
    for row in report.get("pass_false_positives") or []:
        lines.append(
            f"- {_mmss(row.get('merged_release_time_sec'))} · `{row.get('candidate_id')}` · "
            f"{row.get('classification')} · nearest: {row.get('nearest_gold_event_id') or 'none'}"
        )
    lines.extend(["", "## Outcome errors", ""])
    for row in report.get("pass_matches") or []:
        if "PASS_OUTCOME_ERROR" in (row.get("error_tags") or []):
            lines.append(
                f"- {_mmss(row.get('gold_time_sec'))} · `{row.get('gold_event_id')}`: "
                f"gold {row.get('expected_broad_outcome')} vs system {row.get('actual_outcome')}"
            )
    lines.extend(["", "## Identity context", ""])
    identity_rows = report.get("identity_context_matches") or []
    if not identity_rows:
        lines.append("- No temporally matched pass events overlap a recorded identity-flicker note.")
    for row in identity_rows:
        lines.append(
            f"- {_mmss(row.get('gold_time_sec'))} · `{row.get('gold_event_id')}` · "
            f"team correct={row.get('actor_team_correct')} · source slot={row.get('actual_actor_stable_player_id') or 'unknown'}"
        )
    lines.extend(["", "## Shot/pass confusion", ""])
    for row in report.get("shot_pass_confusions") or []:
        candidates = row.get("nearby_pass_candidates") or []
        if candidates:
            ids = ", ".join(f"{item['candidate_id']} ({item['time_delta_sec']:+.2f}s)" for item in candidates)
            lines.append(f"- {_mmss(row.get('canonical_time_sec'))} · `{row.get('shot_id')}` · {ids}")
    lines.extend(["", "## Dead-ball leakage", ""])
    for row in report.get("dead_ball_candidates") or []:
        leakage = _mapping(row.get("spurious"))
        lines.append(
            f"- {row.get('window_id')} {row.get('state')} {_mmss(row.get('start_time_sec'))}–{_mmss(row.get('end_time_sec'))}: "
            f"contacts {leakage.get('contact_candidates', 0)}, events {leakage.get('event_candidates', 0)}, passes {leakage.get('pass_candidates', 0)}"
        )
    lines.extend(["", "## Contact hard negative", ""])
    for row in report.get("hard_negative_results") or []:
        lines.append(
            f"- {_mmss(row.get('gold_time_sec'))} · {row.get('gold_event_id')}: "
            f"automatic Corgi/Mateusz-like contact candidates = {row.get('matching_contact_candidate_count', 0)}"
        )
    lines.extend(["", "## Top failure categories", ""])
    for category, count in _mapping(_mapping(report.get("failure_breakdown")).get("by_primary_stage")).items():
        lines.append(f"- `{category}`: **{count}**")
    lines.extend(["", "This report is descriptive baseline evidence only; it does not change production behavior.", ""])
    return "\n".join(lines)


def _project_candidates(
    windows: Mapping[str, Mapping[str, Any]],
    source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    offsets_by_source: dict[str, float] = {}
    for window in windows.values():
        offsets_by_source[str(window["source_match_id"])] = float(window["source_to_merged_offset_sec"])
    projected: dict[str, list[dict[str, Any]]] = {"contact": [], "event": [], "restart": [], "pass": []}
    fields = {"contact": ("contact_candidates", "candidates"), "event": ("event_candidates", "events"), "restart": ("restart_candidates", "candidates"), "pass": ("pass_candidates", "candidates")}
    for source_match_id, documents in source_documents.items():
        offset = offsets_by_source.get(str(source_match_id), 0.0)
        for kind, (document_name, rows_key) in fields.items():
            document = _mapping(documents.get(document_name))
            for row in document.get(rows_key) or []:
                if not isinstance(row, Mapping):
                    continue
                item = dict(row)
                item["source_match_id"] = source_match_id
                item["source_start_time_sec"] = _number(item.get("start_time_sec"))
                item["source_end_time_sec"] = _number(item.get("end_time_sec"), item["source_start_time_sec"])
                item["merged_start_time_sec"] = _round(item["source_start_time_sec"] + offset)
                item["merged_end_time_sec"] = _round(item["source_end_time_sec"] + offset)
                item["merged_release_time_sec"] = item["merged_start_time_sec"]
                projected[kind].append(item)
    for rows in projected.values():
        rows.sort(key=lambda row: (row["merged_start_time_sec"], row["merged_end_time_sec"], str(row.get("candidate_id") or row.get("event_id") or "")))
    return projected


def _scope_candidates(windows: Mapping[str, Mapping[str, Any]], candidates: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    ranges = [
        (str(row["source_match_id"]), float(row["source_start_time_sec"]), float(row["source_end_time_sec"]))
        for row in windows.values()
    ]
    return {
        kind: [dict(row) for row in rows if any(
            str(row.get("source_match_id")) == source and start <= _number(row.get("source_start_time_sec")) <= end
            for source, start, end in ranges
        )]
        for kind, rows in candidates.items()
    }


def _match_passes(gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    return _sequence_match(gold, candidates, _pass_compatible, _pass_match_cost)


def _match_contacts(gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    return _sequence_match(gold, candidates, _contact_compatible, _contact_match_cost)


def _sequence_match(
    gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], compatible: Any, cost: Any,
) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    """Chronological one-to-one alignment: max pairs, then min normalized time error."""

    count_gold, count_candidates = len(gold), len(candidates)
    cells: list[list[tuple[tuple[int, float, int], list[tuple[int, int]]]]] = [
        [((0, 0.0, 0), []) for _ in range(count_candidates + 1)] for _ in range(count_gold + 1)
    ]
    for gold_index in range(count_gold + 1):
        for candidate_index in range(count_candidates + 1):
            if gold_index == 0 and candidate_index == 0:
                continue
            options: list[tuple[tuple[int, float, int], list[tuple[int, int]]]] = []
            if gold_index:
                options.append(cells[gold_index - 1][candidate_index])
            if candidate_index:
                options.append(cells[gold_index][candidate_index - 1])
            if gold_index and candidate_index and compatible(gold[gold_index - 1], candidates[candidate_index - 1]):
                prior_score, prior_pairs = cells[gold_index - 1][candidate_index - 1]
                normalized_cost, semantic_bonus = cost(gold[gold_index - 1], candidates[candidate_index - 1])
                options.append(((prior_score[0] + 1, prior_score[1] - normalized_cost, prior_score[2] + semantic_bonus), prior_pairs + [(gold_index - 1, candidate_index - 1)]))
            cells[gold_index][candidate_index] = max(options, key=lambda item: item[0])
    pairs = cells[count_gold][count_candidates][1]
    return pairs, set(range(count_gold)) - {left for left, _ in pairs}, set(range(count_candidates)) - {right for _, right in pairs}


def _pass_compatible(gold: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    return abs(_number(candidate.get("merged_release_time_sec")) - _number(gold.get("approx_time_sec"))) <= _number(gold.get("timing_tolerance_sec"), 1.0)


def _pass_match_cost(gold: Mapping[str, Any], candidate: Mapping[str, Any]) -> tuple[float, int]:
    tolerance = max(_number(gold.get("timing_tolerance_sec"), 1.0), 0.001)
    error = abs(_number(candidate.get("merged_release_time_sec")) - _number(gold.get("approx_time_sec"))) / tolerance
    bonus = int(_candidate_actor_team(candidate) == _gold_team(gold)) + int(_candidate_broad_outcome(candidate) == PASS_OUTCOME_BY_GOLD.get(str(gold.get("outcome"))))
    return error, bonus


def _contact_compatible(gold: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    lower = _number(gold.get("approx_time_sec")) - _number(gold.get("timing_tolerance_sec"), 1.0)
    upper = _number(gold.get("approx_time_sec")) + _number(gold.get("timing_tolerance_sec"), 1.0)
    return _number(candidate.get("merged_start_time_sec")) <= upper and _number(candidate.get("merged_end_time_sec")) >= lower


def _contact_match_cost(gold: Mapping[str, Any], candidate: Mapping[str, Any]) -> tuple[float, int]:
    time = _number(gold.get("approx_time_sec"))
    start, end = _number(candidate.get("merged_start_time_sec")), _number(candidate.get("merged_end_time_sec"))
    distance = 0.0 if start <= time <= end else min(abs(time - start), abs(time - end))
    return distance / max(_number(gold.get("timing_tolerance_sec"), 1.0), 0.001), int(_candidate_team(candidate) == _gold_team(gold))


def _score_pass_matches(gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], pairs: Sequence[tuple[int, int]], identity_notes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for gold_index, candidate_index in pairs:
        expected, actual = gold[gold_index], candidates[candidate_index]
        expected_outcome = PASS_OUTCOME_BY_GOLD.get(str(expected.get("outcome")))
        actual_outcome = _candidate_broad_outcome(actual)
        actor_ok = not _gold_team(expected) or _candidate_actor_team(actual) == _gold_team(expected)
        receiver_gold = _gold_target_team(expected)
        receiver_ok = receiver_gold is None or _candidate_receiver_team(actual) == receiver_gold
        outcome_ok = expected_outcome is None or actual_outcome == expected_outcome
        restart_ok = bool(actual.get("from_restart")) == (str(expected.get("action")) == "RESTART")
        tags: list[str] = []
        if not outcome_ok:
            tags.append("PASS_OUTCOME_ERROR")
        if not actor_ok:
            tags.append("ACTOR_TEAM_ERROR")
        if not receiver_ok:
            tags.append("RECEIVER_TEAM_ERROR")
        if not restart_ok:
            tags.append("RESTART_ATTRIBUTION_ERROR")
        primary = tags[0] if tags else "CORRECT"
        identity_context = _has_identity_context(expected, identity_notes)
        if identity_context:
            tags.append("IDENTITY_CONTEXT")
        results.append({
            "gold_event_id": expected.get("event_id"), "window_id": expected.get("window_id"),
            "gold_time_sec": _round(_number(expected.get("approx_time_sec"))),
            "candidate_id": actual.get("candidate_id"), "candidate_source_match_id": actual.get("source_match_id"),
            "candidate_release_source_time_sec": actual.get("source_start_time_sec"),
            "candidate_release_merged_time_sec": actual.get("merged_release_time_sec"),
            "time_delta_sec": _round(_number(actual.get("merged_release_time_sec")) - _number(expected.get("approx_time_sec"))),
            "expected_action": expected.get("action"), "expected_outcome": expected.get("outcome"),
            "expected_broad_outcome": expected_outcome, "actual_outcome": actual_outcome,
            "expected_actor_team": _gold_team(expected), "actual_actor_team": _candidate_actor_team(actual), "actor_team_correct": actor_ok,
            "expected_receiver_team": receiver_gold, "actual_receiver_team": _candidate_receiver_team(actual), "receiver_team_correct": receiver_ok if receiver_gold else None,
            "outcome_correct": outcome_ok if expected_outcome else None,
            "actual_actor_stable_player_id": actual.get("from_stable_player_id"),
            "actual_receiver_stable_player_id": actual.get("to_stable_player_id"),
            "identity_context": identity_context,
            "from_restart": bool(actual.get("from_restart")), "restart_attribution_correct": restart_ok,
            "auto_review_status": actual.get("auto_review_status"), "review_status": actual.get("review_status"),
            "primary_failure_stage": primary, "error_tags": tags,
        })
    return results


def _build_pass_misses(gold: Sequence[Mapping[str, Any]], missing: set[int], contacts: Sequence[Mapping[str, Any]], matches: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index in sorted(missing):
        expected = gold[index]
        local = [row for row in contacts if _contact_compatible(expected, row)]
        statuses = Counter(str(row.get("review_status") or row.get("status") or "unknown") for row in local)
        if not local:
            primary, tags = "CONTACT_MISS", ["CONTACT_MISS"]
        elif statuses.get("rejected") and not (statuses.get("accepted") or statuses.get("uncertain")):
            primary, tags = "CONTACT_FILTERED_OR_REJECTED", ["CONTACT_FILTERED_OR_REJECTED"]
        else:
            primary, tags = "PASS_CONSTRUCTION_MISS", ["PASS_CONSTRUCTION_MISS"]
            if len(local) > 1:
                tags.append("MULTIPLE_CANDIDATES_AMBIGUOUS")
        items.append({
            **_gold_public(expected), "primary_failure_stage": primary, "error_tags": tags,
            "nearby_contact_candidate_count": len(local), "nearby_contact_review_statuses": dict(sorted(statuses.items())),
        })
    return items


def _classify_pass_false_positives(candidates: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]], intervals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        time = _number(candidate.get("merged_release_time_sec"))
        nearest = _nearest_event(time, events)
        classification = "UNMATCHED_PASS_CANDIDATE"
        if any(_number(interval.get("start_time_sec")) < time < _number(interval.get("end_time_sec")) for interval in intervals if interval.get("state") == "NOT_IN_PLAY"):
            classification = "DEAD_BALL_PASS"
        elif any(_number(interval.get("start_time_sec")) < time < _number(interval.get("end_time_sec")) for interval in intervals if interval.get("state") == "GK_HOLD"):
            classification = "GK_HOLD_PASS"
        elif nearest and abs(time - _number(nearest.get("approx_time_sec"))) <= DIAGNOSTIC_RADIUS_SEC:
            if nearest.get("event_type") == "SHOT_REFERENCE":
                classification = "SHOT_AS_PASS"
            elif "AMBIGUOUS" in (nearest.get("modifiers") or []):
                classification = "AMBIGUOUS_ACTION_AS_PASS"
            else:
                classification = {"INTERVENTION": "INTERVENTION_AS_PASS", "CONTROL": "CONTROL_AS_PASS", "CONTEST": "CONTEST_AS_PASS"}.get(str(nearest.get("action")), classification)
        output.append({
            "candidate_id": candidate.get("candidate_id"), "source_match_id": candidate.get("source_match_id"),
            "merged_release_time_sec": candidate.get("merged_release_time_sec"), "outcome": _candidate_broad_outcome(candidate),
            "actor_team": _candidate_actor_team(candidate), "classification": classification,
            "nearest_gold_event_id": nearest.get("event_id") if nearest else None,
            "nearest_gold_action": nearest.get("action") if nearest else nearest.get("event_type") if nearest else None,
        })
    return output


def _shot_pass_confusions(events: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for shot in (row for row in events if row.get("event_type") == "SHOT_REFERENCE" and not row.get("context_only")):
        time = _number(shot.get("canonical_time_sec"))
        nearby = [
            {"candidate_id": row.get("candidate_id"), "source_match_id": row.get("source_match_id"), "time_delta_sec": _round(_number(row.get("merged_release_time_sec")) - time)}
            for row in candidates if abs(_number(row.get("merged_release_time_sec")) - time) <= SHOT_PASS_TOLERANCE_SEC
        ]
        output.append({"shot_id": shot.get("shot_id"), "canonical_time_sec": time, "outcome": shot.get("outcome"), "nearby_pass_candidates": nearby})
    return output


def _dead_ball_leakage(intervals: Sequence[Mapping[str, Any]], scoped: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for interval in intervals:
        start, end = _number(interval.get("start_time_sec")), _number(interval.get("end_time_sec"))
        inside = {kind: [row for row in rows if start < _number(row.get("merged_start_time_sec")) < end] for kind, rows in scoped.items()}
        boundary_restart_ids = {str(row.get("candidate_id")) for row in inside["pass"] if row.get("from_restart") and _number(row.get("merged_release_time_sec")) >= end - 1.0}
        output.append({
            "window_id": interval.get("window_id"), "state": interval.get("state"), "reason": interval.get("reason"), "start_time_sec": start, "end_time_sec": end,
            "generated": {kind + "_candidates": len(rows) for kind, rows in inside.items()},
            "legitimate_restart_at_boundary": sorted(boundary_restart_ids),
            "spurious": {
                "contact_candidates": len(inside["contact"]), "event_candidates": len(inside["event"]),
                "pass_candidates": sum(str(row.get("candidate_id")) not in boundary_restart_ids for row in inside["pass"]),
            },
        })
    return output


def _attempted_contact_hard_negative(events: Sequence[Mapping[str, Any]], contacts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for event in events:
        if event.get("contact_confirmed") is not False or "ATTEMPTED_CONTACT" not in (event.get("modifiers") or []):
            continue
        matching = [row for row in contacts if _contact_compatible(event, row) and _candidate_team(row) == _gold_team(event)]
        output.append({
            "gold_event_id": event.get("event_id"), "gold_time_sec": event.get("approx_time_sec"), "actor": _mapping(event.get("actor")),
            "matching_contact_candidate_count": len(matching), "candidate_ids": [row.get("candidate_id") for row in matching],
            "result": "false_positive_contact" if matching else "correct_negative",
        })
    return output


def _pass_summary(gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], matches: Sequence[Mapping[str, Any]], misses: Sequence[Mapping[str, Any]], false_positives: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    matched = len(matches)
    precision, recall = _ratio(matched, matched + len(false_positives)), _ratio(matched, len(gold))
    scorable_outcomes = [row for row in matches if row.get("outcome_correct") is not None]
    restart_gold = [row for row in gold if row.get("action") == "RESTART"]
    restart_matched = sum(row.get("expected_action") == "RESTART" for row in matches)
    open_gold = [row for row in gold if row.get("action") == "PASS"]
    open_matched = sum(row.get("expected_action") == "PASS" for row in matches)
    known_actor = [row for row in matches if row.get("expected_actor_team")]
    known_receiver = [row for row in matches if row.get("expected_receiver_team")]
    return {
        "gold_pass_attempts": len(gold), "pass_candidates_in_scope": len(candidates), "matched_pass_attempts": matched,
        "missed_pass_attempts": len(misses), "unmatched_pass_candidates": len(false_positives),
        "precision": precision, "recall": recall, "f1": _f1(precision, recall),
        "open_play_gold_passes": len(open_gold), "open_play_recall": _ratio(open_matched, len(open_gold)),
        "restart_gold_passes": len(restart_gold), "restart_recall": _ratio(restart_matched, len(restart_gold)),
        "outcome_scorable_matches": len(scorable_outcomes), "outcome_accuracy": _ratio(sum(bool(row.get("outcome_correct")) for row in scorable_outcomes), len(scorable_outcomes)),
        "actor_team_accuracy": _ratio(sum(bool(row.get("actor_team_correct")) for row in known_actor), len(known_actor)),
        "receiver_team_accuracy": _ratio(sum(bool(row.get("receiver_team_correct")) for row in known_receiver), len(known_receiver)),
    }


def _contact_summary(gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], matches: Sequence[Mapping[str, Any]], missing: set[int]) -> dict[str, Any]:
    statuses = Counter(str(row.get("auto_review_status") or row.get("review_status") or "unknown") for row in matches)
    all_statuses = Counter(str(row.get("review_status") or row.get("status") or "unknown") for row in candidates)
    return {
        "strict_contact_anchors": len(gold), "contact_candidates_in_scope": len(candidates),
        "matched_strict_anchors": len(matches), "contact_misses": len(missing), "recall": _ratio(len(matches), len(gold)),
        "matched_auto_review_statuses": dict(sorted(statuses.items())),
        "candidate_auto_review_statuses": dict(sorted(all_statuses.items())),
    }


def _score_contact_matches(gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], pairs: Sequence[tuple[int, int]]) -> list[dict[str, Any]]:
    return [{
        "gold_event_id": gold[left].get("event_id"), "candidate_id": candidates[right].get("candidate_id"),
        "window_id": gold[left].get("window_id"), "gold_time_sec": gold[left].get("approx_time_sec"),
        "candidate_start_time_sec": candidates[right].get("merged_start_time_sec"), "candidate_end_time_sec": candidates[right].get("merged_end_time_sec"),
        "auto_review_status": candidates[right].get("review_status"),
    } for left, right in pairs]


def _window_summary(windows: Mapping[str, Mapping[str, Any]], gold: Sequence[Mapping[str, Any]], matches: Sequence[Mapping[str, Any]], misses: Sequence[Mapping[str, Any]], false_positives: Sequence[Mapping[str, Any]], scoped: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for window_id, window in sorted(windows.items()):
        start, end = _number(window.get("merged_start_time_sec")), _number(window.get("merged_end_time_sec"))
        gold_rows = [row for row in gold if row.get("window_id") == window_id]
        output[window_id] = {
            "merged_range_sec": [start, end], "gold_pass_attempts": len(gold_rows),
            "matched_pass_attempts": sum(row.get("window_id") == window_id for row in matches),
            "missed_pass_attempts": sum(row.get("window_id") == window_id for row in misses),
            "pass_candidates": sum(start <= _number(row.get("merged_release_time_sec")) <= end for row in scoped["pass"] if str(row.get("outcome")) in PASS_OUTCOMES),
            "unmatched_pass_candidates": sum(start <= _number(row.get("merged_release_time_sec")) <= end for row in false_positives),
        }
    return output


def _source_summary(windows: Mapping[str, Mapping[str, Any]], documents: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    return [{"source_match_id": source, "window_ids": sorted(key for key, window in windows.items() if window.get("source_match_id") == source), "documents_regenerated_in_memory": sorted(documents.get(source, {}))} for source in sorted(documents)]


def _representative_failures(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        stage = str(row.get("primary_failure_stage"))
        if stage != "CORRECT" and len(result[stage]) < 5:
            result[stage].append({key: row.get(key) for key in ("gold_event_id", "gold_time_sec", "candidate_id", "expected_action", "expected_broad_outcome", "actual_outcome", "error_tags")})
    return dict(sorted(result.items()))


def _is_scored_pass(event: Mapping[str, Any]) -> bool:
    return bool(event.get("event_type") != "SHOT_REFERENCE" and not event.get("context_only") and event.get("action") in PASS_ACTIONS and "AMBIGUOUS" not in (event.get("modifiers") or []))


def _is_strict_contact_anchor(event: Mapping[str, Any]) -> bool:
    if event.get("context_only"):
        return False
    return event.get("event_type") == "SHOT_REFERENCE" or (event.get("action") in STRICT_CONTACT_ACTIONS and "AMBIGUOUS" not in (event.get("modifiers") or []))


def _gold_team(event: Mapping[str, Any]) -> str | None:
    actor = _mapping(event.get("actor"))
    return _team_text(actor.get("team") or event.get("team"))


def _gold_target_team(event: Mapping[str, Any]) -> str | None:
    return _team_text(_mapping(event.get("target")).get("team"))


def _candidate_team(candidate: Mapping[str, Any]) -> str | None:
    return _team_text(candidate.get("team_name") or candidate.get("team_label"))


def _candidate_actor_team(candidate: Mapping[str, Any]) -> str | None:
    return _team_text(candidate.get("from_team_name") or candidate.get("count_for_team_label") or candidate.get("from_team_label") or candidate.get("actor_team_name") or candidate.get("actor_team_label"))


def _candidate_receiver_team(candidate: Mapping[str, Any]) -> str | None:
    return _team_text(candidate.get("to_team_name") or candidate.get("to_team_label") or candidate.get("receiver_team_name") or candidate.get("receiver_team_label"))


def _candidate_broad_outcome(candidate: Mapping[str, Any]) -> str | None:
    outcome = str(candidate.get("outcome") or "")
    if outcome in PASS_OUTCOMES:
        return outcome
    return None


def _team_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return {"A": "Corgi", "B": "Verisk"}.get(text, text or None)


def _nearest_event(time: float, events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    if not events:
        return None
    return min(events, key=lambda event: (abs(time - _number(event.get("approx_time_sec"))), str(event.get("event_id") or "")))


def _has_identity_context(event: Mapping[str, Any], notes: Sequence[Mapping[str, Any]]) -> bool:
    return any(note.get("window_id") == event.get("window_id") and abs(_number(note.get("approx_time_sec")) - _number(event.get("approx_time_sec"))) <= 2.0 for note in notes)


def _gold_public(event: Mapping[str, Any]) -> dict[str, Any]:
    return {"gold_event_id": event.get("event_id"), "window_id": event.get("window_id"), "gold_time_sec": event.get("approx_time_sec"), "action": event.get("action"), "outcome": event.get("outcome"), "actor": _mapping(event.get("actor")), "target": _mapping(event.get("target")), "manual_note": event.get("manual_note")}


def _dead_ball_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {
        f"spurious_{key}": sum(int(_mapping(row.get("spurious")).get(key, 0)) for row in rows)
        for key in ("contact_candidates", "event_candidates", "pass_candidates")
    }


def _pass_diagnostic_line(row: Mapping[str, Any]) -> str:
    actor, target = _mapping(row.get("actor")), _mapping(row.get("target"))
    return f"- {_mmss(row.get('gold_time_sec'))} · `{row.get('gold_event_id')}` · {actor.get('display_name') or actor.get('team') or '?'} → {target.get('display_name') or target.get('team') or '?'} · {row.get('outcome')} · `{row.get('primary_failure_stage')}`"


def _read_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Expected object at {path}")
    return document


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value: float) -> float:
    return round(float(value), 4)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def _percentage(value: Any) -> str:
    return f"{_number(value) * 100:.1f}%"


def _mmss(value: Any) -> str:
    seconds = max(_number(value), 0.0)
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
