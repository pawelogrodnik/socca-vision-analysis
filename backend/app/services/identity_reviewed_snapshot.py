from __future__ import annotations

"""Canonical local-only reviewed identity snapshot.

This deliberately consumes review decisions rather than trying to resolve identity
again.  Automation artifacts are advisory; only operator review and a fresh,
safe seeded propagation may create a visible roster name.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest


SNAPSHOT_FILENAME = "reviewed_identity_snapshot.json"
REPORT_FILENAME = "reviewed_identity_report.json"
ALGORITHM_VERSION = "reviewed_identity_snapshot:v1"


def get_reviewed_identity_status(match_path: Path) -> dict[str, Any]:
    snapshot_path = match_path / SNAPSHOT_FILENAME
    if not snapshot_path.exists():
        return {"status": "missing", "summary": None, "source": None}
    snapshot = _load(snapshot_path)
    current = _source_documents(match_path)
    match_doc = _optional(match_path / "match.json")
    stale = snapshot.get("source", {}).get("semantic_input_digest") != _source_digest(current, match_doc)
    return {**snapshot, "status": "stale" if stale else str(snapshot.get("status") or "partial_reviewed"), "stale": stale}


def finalize_reviewed_identity(match_path: Path, match_doc: dict[str, Any]) -> dict[str, Any]:
    documents = _source_documents(match_path)
    roster = _roster(match_doc)
    tracklets = {str(row.get("tracklet_id")): row for row in documents["tracklets"].get("tracklets") or [] if row.get("tracklet_id")}
    subject_by_tracklet = _subject_by_tracklet(documents["subjects"])
    review = _review_decisions(documents["review_decisions"])
    seeded = _safe_seeded_assignments(documents["seeded"])
    seed_exact = _exact_seed_assignments(documents["seeds"])
    fallback = _fallback_labels(tracklets, subject_by_tracklet)
    assignments: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for tracklet_id, tracklet in sorted(tracklets.items()):
        subject_id = subject_by_tracklet.get(tracklet_id)
        decisions = review.get(subject_id or "", [])
        decision = decisions[-1] if decisions else None
        accepted_seed = seeded.get(subject_id or "")
        exact = seed_exact.get(tracklet_id)
        status, player_id, source, evidence, blockers = _resolve_assignment(decision, accepted_seed, exact)
        team_label = str(tracklet.get("team_label") or "U")
        team_id = str(tracklet.get("team_id") or "")
        player = roster.get(player_id or "")
        assignment_conflicts: list[dict[str, Any]] = []
        if _has_conflicting_review_decisions(decisions):
            assignment_conflicts.append({"code": "conflicting_explicit_operator_decisions"})
            status = "conflicted"
            player_id = None
            source = "operator_review"
        if player_id and player is None:
            blockers.append("invalid_roster_player"); status = "blocked"; player_id = None
        elif player_id and player["team_label"] != team_label:
            assignment_conflicts.append({"code": "cross_team_confirmed_assignment", "player_id": player_id}); status = "conflicted"; player_id = None
        label = fallback[tracklet_id]
        assignments.append({
            "tracklet_id": tracklet_id,
            "candidate_subject_id": subject_id,
            "team_id": team_id,
            "team_label": team_label,
            "canonical_player_id": player_id if status == "confirmed" else None,
            "player_name": player["name"] if player and status == "confirmed" else None,
            "roster_number": player.get("number") if player and status == "confirmed" else None,
            "fallback_label": label,
            "display_label": player["name"] if player and status == "confirmed" else f"{label} !" if status == "conflicted" else label,
            "identity_status": status,
            "identity_source": source,
            "eligible_for_player_stats": status == "confirmed" and player_id is not None,
            "frame_start": _frame_start(tracklet),
            "frame_end": _frame_end(tracklet),
            "source_review_keys": [str(item.get("review_card_key") or "") for item in decisions],
            "source_seed_keys": evidence,
            "accepted_evidence": [source] if source else [],
            "rejected_evidence": [],
            "hard_blockers": sorted(set(blockers)),
            "conflicts": assignment_conflicts,
        })
        conflicts.extend({"tracklet_id": tracklet_id, **item} for item in assignment_conflicts)
    _apply_parallel_conflicts(assignments, conflicts)
    summary = _summary(assignments, tracklets)
    source = _source_descriptor(documents, match_doc)
    status = "blocked" if summary["blocked"] == len(assignments) and assignments else "complete_reviewed" if summary["unresolved"] == summary["conflicted"] == summary["blocked"] == 0 else "partial_reviewed"
    snapshot = {
        "schema_version": "1.0.0", "mode": "reviewed_identity_snapshot", "match_id": str(match_doc.get("id") or match_path.name),
        "generated_at": datetime.now(timezone.utc).isoformat(), "status": status, "source": source,
        "display_policy": {"confirmed": "roster_name", "probable": "fallback_label", "unresolved": "fallback_label", "conflicted": "fallback_label_with_marker", "blocked": "hidden_or_fallback"},
        "entities": _entities(assignments), "tracklet_assignments": assignments, "summary": summary, "conflicts": conflicts,
        "readiness": {"identity": "ready_with_review" if summary["confirmed"] else "partial_review_required", "reason": "Names are shown only for explicit review decisions or safe seeded lineage."},
        "safety": {"production_identity_mutated": False, "production_applies": 0, "reran_yolo": False, "reran_tracking": False, "automatic_reid_names_rendered": 0},
    }
    snapshot["semantic_digest"] = _semantic_digest(snapshot)
    report = {"schema_version": "1.0.0", "status": snapshot["status"], "snapshot_digest": snapshot["semantic_digest"], "summary": summary, "conflicts": conflicts, "source": source, "safety": snapshot["safety"]}
    write_identity_json_atomic(match_path / SNAPSHOT_FILENAME, snapshot)
    write_identity_json_atomic(match_path / REPORT_FILENAME, report)
    return snapshot


def reviewed_assignment_at(snapshot: dict[str, Any], time_sec: float, fps: float) -> list[dict[str, Any]]:
    frame = max(0, round(time_sec * fps))
    return [row for row in snapshot.get("tracklet_assignments") or [] if row.get("frame_start") is not None and int(row["frame_start"]) <= frame <= int(row.get("frame_end") or row["frame_start"])]


def _source_documents(path: Path) -> dict[str, dict[str, Any]]:
    return {"tracklets": _optional(path / "tracklets.json"), "subjects": _optional(path / "identity_candidate_shadow.json"), "timeline": _optional(path / "identity_offline_shadow_timeline.json"), "seeds": _optional(path / "identity_operator_seeds.json"), "seeded": _optional(path / "identity_seeded_candidate_assignments.json"), "review_decisions": _optional(path / "identity_roster_subject_review_decisions_shadow.json"), "remediation": _optional(path / "identity_structural_remediation_shadow.json")}


def _source_descriptor(documents: dict[str, dict[str, Any]], match_doc: dict[str, Any]) -> dict[str, Any]:
    values = {key: canonical_digest(_semantic_input(value)) if value else None for key, value in documents.items()}
    return {"match_digest": canonical_digest(_semantic_input(match_doc)), "roster_digest": canonical_digest(_semantic_input(match_doc.get("teams") or [])), "tracklets_digest": values["tracklets"], "subjects_digest": values["subjects"], "operator_seed_decisions_digest": _decisions_digest(documents["seeds"]), "whole_subject_review_decisions_digest": _decisions_digest(documents["review_decisions"]), "remediation_decisions_digest": values["remediation"], "resolver_evidence_digest": None, "algorithm_version": ALGORITHM_VERSION, "optional_inputs": {key: "available" if value else "not_available" for key, value in documents.items()}, "semantic_input_digest": _source_digest(documents, match_doc)}


def _source_digest(documents: dict[str, dict[str, Any]], match_doc: dict[str, Any] | None = None) -> str:
    value = {key: canonical_digest(_semantic_input(document)) if document else None for key, document in documents.items()}
    if match_doc is not None: value["match"] = canonical_digest(_semantic_input(match_doc))
    return canonical_digest(value)


def _review_decisions(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    values: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in doc.get("decisions") or []:
        subject = str(row.get("candidate_subject_id") or "")
        if subject and str(row.get("decision")) in {"assign_roster_player", "confirm_recommended_player", "mark_unresolved"}:
            values[subject].append(dict(row))
    return values


def _safe_seeded_assignments(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = {}
    for row in doc.get("accepted_assignments") or []:
        provenance = row.get("propagation_provenance") or {}
        if provenance.get("team_consistency") and provenance.get("structural_gates_passed") and provenance.get("local_tracklet_continuity"):
            values[str(row.get("candidate_subject_id") or "")] = row
    return values


def _exact_seed_assignments(doc: dict[str, Any]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = defaultdict(list)
    for row in doc.get("decisions") or []:
        if row.get("action") == "assign_roster_player":
            tracklet = str((row.get("provenance") or {}).get("tracklet_id") or "")
            if tracklet: values[tracklet].append(str(row.get("observation_key") or ""))
    return values


def _resolve_assignment(decision: dict[str, Any] | None, seed: dict[str, Any] | None, exact: list[str] | None) -> tuple[str, str | None, str | None, list[str], list[str]]:
    if decision:
        if decision.get("decision") == "mark_unresolved": return "unresolved", None, "operator_review", [], []
        return "confirmed", str(decision.get("player_id") or "") or None, "operator_review", [], []
    if seed:
        player = (seed.get("assigned_player") or {}).get("player_id")
        return "confirmed", str(player) if player else None, "operator_seed_safe_lineage", [str(item.get("observation_key") or "") for item in seed.get("seed_observations") or []], []
    # Exact observation seeds remain exact; their enclosing tracklet is deliberately not named.
    return "unresolved", None, "operator_seed_observation_only" if exact else None, exact or [], []


def _apply_parallel_conflicts(assignments: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> None:
    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in assignments:
        if row.get("canonical_player_id"):
            by_player[str(row["canonical_player_id"])].append(row)
    conflicted: set[int] = set()
    for rows in by_player.values():
        active: list[dict[str, Any]] = []
        for row in sorted(rows, key=lambda item: (int(item["frame_start"] or 0), int(item["frame_end"] or 0), str(item["tracklet_id"]))):
            start = int(row["frame_start"] or 0)
            active = [item for item in active if int(item["frame_end"] or -1) >= start]
            if active:
                conflicted.add(id(row))
                conflicted.update(id(item) for item in active)
            active.append(row)
    for row in assignments:
        if id(row) in conflicted:
            conflicts.append({"code": "parallel_confirmed_same_player", "tracklet_id": row["tracklet_id"], "player_id": row["canonical_player_id"]})
            row["identity_status"] = "conflicted"; row["canonical_player_id"] = None; row["player_name"] = None; row["eligible_for_player_stats"] = False; row["display_label"] = f"{row['fallback_label']} !"; row["conflicts"].append({"code": "parallel_confirmed_same_player"})


def _fallback_labels(tracklets: dict[str, dict[str, Any]], subject_by_tracklet: dict[str, str]) -> dict[str, str]:
    groups: dict[str, list[str]] = defaultdict(list)
    for tracklet_id, row in tracklets.items(): groups[subject_by_tracklet.get(tracklet_id) or f"tracklet:{tracklet_id}"].append(tracklet_id)
    ordered = sorted(groups, key=lambda key: (str(tracklets[groups[key][0]].get("team_label") or "U"), min(_frame_start(tracklets[item]) for item in groups[key]), key))
    counters: Counter[str] = Counter(); labels: dict[str, str] = {}
    for key in ordered:
        team = str(tracklets[groups[key][0]].get("team_label") or "U"); counters[team] += 1; label = f"{team}{counters[team]:02d}"
        for tracklet_id in groups[key]: labels[tracklet_id] = label
    return labels


def _summary(assignments: list[dict[str, Any]], tracklets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row["identity_status"]) for row in assignments); total_frames = sum(max(0, int(row.get("frame_end") or 0) - int(row.get("frame_start") or 0) + 1) for row in assignments); confirmed_frames = sum(max(0, int(row.get("frame_end") or 0) - int(row.get("frame_start") or 0) + 1) for row in assignments if row["identity_status"] == "confirmed")
    return {"tracklets_total": len(assignments), "confirmed": counts["confirmed"], "probable": counts["probable"], "unresolved": counts["unresolved"], "conflicted": counts["conflicted"], "blocked": counts["blocked"], "confirmed_players": len({row["canonical_player_id"] for row in assignments if row.get("canonical_player_id")}), "confirmed_detected_frame_coverage": round(confirmed_frames / total_frames, 4) if total_frames else None, "unresolved_detected_frame_coverage": round(sum(max(0, int(row.get("frame_end") or 0) - int(row.get("frame_start") or 0) + 1) for row in assignments if row["identity_status"] == "unresolved") / total_frames, 4) if total_frames else None, "conflict_count": sum(bool(row["conflicts"]) for row in assignments), "cross_team_violations": sum(any(value.get("code") == "cross_team_confirmed_assignment" for value in row["conflicts"]) for row in assignments), "invalid_roster_references": sum("invalid_roster_player" in row["hard_blockers"] for row in assignments), "team_a": {"confirmed": sum(row["identity_status"] == "confirmed" and row["team_label"] == "A" for row in assignments), "unresolved": sum(row["identity_status"] == "unresolved" and row["team_label"] == "A" for row in assignments)}, "team_b": {"confirmed": sum(row["identity_status"] == "confirmed" and row["team_label"] == "B" for row in assignments), "unresolved": sum(row["identity_status"] == "unresolved" and row["team_label"] == "B" for row in assignments)}}


def _entities(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = defaultdict(list)
    for row in assignments: values[row["candidate_subject_id"] or row["tracklet_id"]].append(row)
    return [{"entity_key": key, "fallback_label": rows[0]["fallback_label"], "team_label": rows[0]["team_label"], "tracklet_ids": sorted(row["tracklet_id"] for row in rows), "identity_status": "confirmed" if all(row["identity_status"] == "confirmed" for row in rows) else "unresolved"} for key, rows in sorted(values.items())]


def _roster(match_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output = {}
    for index, team in enumerate(match_doc.get("teams") or []):
        label = str(team.get("team_label") or chr(ord("A") + index))
        for player in team.get("players") or []: output[str(player.get("id"))] = {**player, "team_id": str(team.get("id") or ""), "team_label": label}
    return output


def _has_conflicting_review_decisions(decisions: list[dict[str, Any]]) -> bool:
    values = {(str(row.get("decision") or ""), str(row.get("player_id") or "")) for row in decisions}
    return len(values) > 1


def _subject_by_tracklet(doc: dict[str, Any]) -> dict[str, str]: return {str(tracklet): str(subject.get("candidate_subject_id")) for subject in doc.get("subjects") or [] for tracklet in subject.get("tracklet_ids") or []}
def _frame_start(row: dict[str, Any]) -> int: return int(row.get("start_frame") or ((row.get("positions_m") or [{}])[0].get("frame") or 0))
def _frame_end(row: dict[str, Any]) -> int: return int(row.get("end_frame") or ((row.get("positions_m") or [{}])[-1].get("frame") or _frame_start(row)))
def _decisions_digest(doc: dict[str, Any]) -> str | None: return canonical_digest(_semantic_input(doc.get("decisions") or [])) if doc else None
def _semantic_digest(snapshot: dict[str, Any]) -> str: return canonical_digest({key: value for key, value in snapshot.items() if key not in {"generated_at", "semantic_digest"}})
def _semantic_input(value: Any) -> Any:
    if isinstance(value, list): return [_semantic_input(item) for item in value]
    if isinstance(value, dict): return {key: _semantic_input(item) for key, item in value.items() if key not in {"generated_at", "updated_at", "operator_telemetry", "telemetry_state", "created_at"}}
    return value
def _optional(path: Path) -> dict[str, Any]: return _load(path) if path.exists() else {}
def _load(path: Path) -> dict[str, Any]:
    import json
    return json.loads(path.read_text(encoding="utf-8"))
