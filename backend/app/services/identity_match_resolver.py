from __future__ import annotations

"""Generic, deterministic, non-production player-ID resolver shadow."""

from collections import Counter, defaultdict
import hashlib
import json
from typing import Any


def build_identity_resolver_shadow(*, tracklets_doc: dict[str, Any], subjects_doc: dict[str, Any], seeds_doc: dict[str, Any], match_doc: dict[str, Any] | list[dict[str, Any]], reid_evidence_doc: dict[str, Any] | None = None, reid_gate_passed: bool = False, fps_value: float | None = None, fps_source: str = "unavailable") -> dict[str, Any]:
    tracklets = list(tracklets_doc.get("tracklets") or [])
    roster, team_mapping = _build_roster(match_doc, tracklets)
    subject_by_tracklet, ambiguous_tracklets = _subject_index(subjects_doc)
    anchors = _anchors(seeds_doc)
    rows = [_row(tracklet, subject_by_tracklet, ambiguous_tracklets, anchors, fps_value) for tracklet in tracklets]
    continuity = build_continuity_edges(rows)
    evidence = {str(value.get("tracklet_id")): value for value in (reid_evidence_doc or {}).get("tracklets", [])}
    conflicts = _operator_anchor_conflicts(rows, roster)
    variants = {
        "A": _resolve(rows, roster, continuity, evidence, anchors_only=True, reid_enabled=False, anchor_conflicts=conflicts),
        "B": _resolve(rows, roster, continuity, evidence, anchors_only=False, reid_enabled=False, anchor_conflicts=conflicts),
        "C": _resolve(rows, roster, continuity, evidence, anchors_only=False, reid_enabled=bool(reid_gate_passed), anchor_conflicts=conflicts),
    }
    return {"schema_version": "2.0.0", "mode": "match_identity_resolver_shadow", "status": "MATCH_IDENTITY_RESOLVER_SHADOW_COMPLETE", "hierarchy": "detection_id -> track_id -> tracklet_id -> candidate_subject_id -> canonical player_id", "roster": {"players": roster, "team_mapping": team_mapping}, "fps": {"fps_source": fps_source, "fps_value": fps_value}, "variants": variants, "ambiguous_candidate_subject_tracklet_ids": sorted(ambiguous_tracklets), "operator_anchor_conflicts": conflicts, "reid_evidence": {"status": "eligible_shadow_only" if reid_gate_passed else "disabled_quality_gate_failed", "weight": .20 if reid_gate_passed else 0.0}, "continuity_edges": continuity, "safety": {"automatic_production_assignment": False, "production_applies": 0, "reran_yolo": False, "reran_tracking": False}}


def _build_roster(match_doc: dict[str, Any] | list[dict[str, Any]], tracklets: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    teams = list(match_doc.get("teams") or []) if isinstance(match_doc, dict) else list(match_doc)
    labels_by_team_id: dict[str, set[str]] = defaultdict(set)
    for tracklet in tracklets:
        if tracklet.get("team_id") and tracklet.get("team_label"):
            labels_by_team_id[str(tracklet["team_id"])].add(str(tracklet["team_label"]))
    fallback_labels = {str(team.get("id")): chr(ord("A") + index) for index, team in enumerate(sorted(teams, key=lambda item: str(item.get("id") or item.get("name"))))}
    roster: dict[str, dict[str, Any]] = {}; mapping: dict[str, Any] = {}
    for team in sorted(teams, key=lambda item: str(item.get("id") or item.get("name"))):
        team_id = str(team.get("id") or "")
        labels = sorted(labels_by_team_id.get(team_id) or {str(team.get("team_label") or fallback_labels.get(team_id, "U"))})
        label = labels[0]
        mapping[team_id] = {"team_id": team_id, "team_name": str(team.get("name") or ""), "team_label": label, "label_source": "tracklet_team_id_mapping" if labels_by_team_id.get(team_id) else "deterministic_match_team_order"}
        for player in team.get("players") or []:
            player_id = str(player.get("id") or "")
            if player_id:
                roster[player_id] = {"player_id": player_id, "team_id": team_id, "team_name": str(team.get("name") or ""), "team_label": label}
    return roster, mapping


def _subject_index(doc: dict[str, Any]) -> tuple[dict[str, str], set[str]]:
    values: dict[str, str] = {}; ambiguous: set[str] = set()
    for subject in doc.get("subjects") or []:
        subject_id = str(subject.get("candidate_subject_id") or "")
        for tracklet_id in subject.get("tracklet_ids") or []:
            tracklet_id = str(tracklet_id)
            if tracklet_id in values and values[tracklet_id] != subject_id:
                ambiguous.add(tracklet_id)
            else:
                values[tracklet_id] = subject_id
    return values, ambiguous


def _anchors(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for decision in doc.get("decisions") or []:
        if decision.get("action") != "assign_roster_player":
            continue
        tracklet_id = str((decision.get("provenance") or {}).get("tracklet_id") or "")
        player_id = str((decision.get("assigned_player") or {}).get("player_id") or "")
        if tracklet_id and player_id:
            values[tracklet_id] = {"player_id": player_id, "decision": decision}
    return values


def _row(tracklet: dict[str, Any], subject_by_tracklet: dict[str, str], ambiguous: set[str], anchors: dict[str, dict[str, Any]], fps: float | None) -> dict[str, Any]:
    tracklet_id = str(tracklet.get("tracklet_id") or "")
    positions = tracklet.get("positions_m") or []
    first_frame = tracklet.get("start_frame", positions[0].get("frame") if positions else None)
    last_frame = tracklet.get("end_frame", positions[-1].get("frame") if positions else None)
    if first_frame is None and fps:
        first_frame = round(float(tracklet.get("start_time_sec") or 0) * fps)
    if last_frame is None and fps:
        last_frame = round(float(tracklet.get("end_time_sec") or 0) * fps)
    return {"tracklet_id": tracklet_id, "candidate_subject_id": None if tracklet_id in ambiguous else subject_by_tracklet.get(tracklet_id), "team_label": str(tracklet.get("team_label") or "U"), "team_id": str(tracklet.get("team_id") or ""), "frame_start": first_frame, "frame_end": last_frame, "start_time_sec": float(tracklet.get("start_time_sec") or 0), "end_time_sec": float(tracklet.get("end_time_sec") or 0), "source_tracker_id": tracklet.get("source_tracker_id"), "first_pitch_m": tracklet.get("first_pitch_m"), "last_pitch_m": tracklet.get("last_pitch_m"), "first_bbox_xyxy": tracklet.get("first_bbox_xyxy"), "last_bbox_xyxy": tracklet.get("last_bbox_xyxy"), "anchor": anchors.get(tracklet_id)}


def build_continuity_edges(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for team, values in sorted(_group(rows, "team_label").items()):
        ordered = sorted(values, key=lambda row: (row["start_time_sec"], row["tracklet_id"]))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:index + 5]:
                gap = right["start_time_sec"] - left["end_time_sec"]
                blockers: list[str] = []
                if gap < 0: blockers.append("temporal_overlap")
                if left["team_label"] != right["team_label"]: blockers.append("cross_team")
                distance = _distance(left.get("last_pitch_m"), right.get("first_pitch_m"))
                speed = distance / max(gap, .001) if distance is not None else None
                if speed is not None and speed > 12: blockers.append("impossible_movement")
                temporal = max(0.0, 1 - max(gap, 0) / 10)
                spatial = max(0.0, 1 - (distance or 10) / 15) if distance is not None else .25
                scale = _scale_similarity(left.get("last_bbox_xyxy"), right.get("first_bbox_xyxy"))
                lineage = .5 if left.get("source_tracker_id") == right.get("source_tracker_id") else 0.0
                score = round((temporal + spatial + scale + lineage + 1.0) / 5, 6)
                edges.append({"from_tracklet_id": left["tracklet_id"], "to_tracklet_id": right["tracklet_id"], "eligible": not blockers, "score": 0.0 if blockers else score, "components": {"temporal_gap": round(temporal, 6), "spatial_consistency": round(spatial, 6), "scale_consistency": round(scale, 6), "tracker_lineage": lineage, "team_constraint": 1.0}, "hard_blockers": blockers, "temporal_gap_seconds": round(gap, 6), "estimated_speed_mps": None if speed is None else round(speed, 6)})
    return edges


def _operator_anchor_conflicts(rows: list[dict[str, Any]], roster: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("anchor") and row["anchor"]["player_id"] in roster:
            by_player[row["anchor"]["player_id"]].append(row)
    conflicts = []
    for player_id, anchored in sorted(by_player.items()):
        for index, left in enumerate(sorted(anchored, key=lambda row: row["tracklet_id"])):
            for right in anchored[index + 1:]:
                if _overlap(left, right): conflicts.append({"code": "HARD_OPERATOR_ANCHOR_CONFLICT", "player_id": player_id, "tracklet_ids": [left["tracklet_id"], right["tracklet_id"]], "operator_anchor_retained": True, "production_apply": False, "manual_review_required": True})
    return conflicts


def _resolve(rows: list[dict[str, Any]], roster: dict[str, dict[str, Any]], continuity: list[dict[str, Any]], evidence: dict[str, dict[str, Any]], *, anchors_only: bool, reid_enabled: bool, anchor_conflicts: list[dict[str, Any]]) -> dict[str, Any]:
    conflict_tracklets = {tracklet for conflict in anchor_conflicts for tracklet in conflict["tracklet_ids"]}
    anchored_by_subject: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("anchor") and row["anchor"]["player_id"] in roster:
            anchored_by_subject[str(row.get("candidate_subject_id") or "")].add(row["anchor"]["player_id"])
    continuity_by_tracklet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in continuity:
        if edge["eligible"]:
            continuity_by_tracklet[edge["from_tracklet_id"]].append(edge); continuity_by_tracklet[edge["to_tracklet_id"]].append(edge)
    candidates: dict[str, list[dict[str, Any]]] = {}; assignment_seed: dict[str, dict[str, Any]] = {}
    for row in rows:
        tracklet_id = row["tracklet_id"]; options: list[dict[str, Any]] = []
        anchor = row.get("anchor")
        if anchor:
            player_id = anchor["player_id"]
            if player_id not in roster:
                assignment_seed[tracklet_id] = _assignment(row, None, "blocked", [], ["invalid_roster_player"], [])
                continue
            if roster[player_id]["team_label"] != row["team_label"]:
                assignment_seed[tracklet_id] = _assignment(row, None, "blocked", [], ["cross_team_candidate_hard_rejected"], [])
                continue
            status = "confirmed_but_conflicted" if tracklet_id in conflict_tracklets else "confirmed"
            assignment_seed[tracklet_id] = _assignment(row, _edge(row, player_id, 1.0, operator=1.0), status, ["operator_confirmed_exact_observation"], ["HARD_OPERATOR_ANCHOR_CONFLICT"] if tracklet_id in conflict_tracklets else [], [])
            continue
        if not anchors_only:
            subject_players = anchored_by_subject.get(str(row.get("candidate_subject_id") or ""), set())
            if len(subject_players) == 1:
                player_id = next(iter(subject_players))
                if player_id in roster and roster[player_id]["team_label"] == row["team_label"]:
                    continuity_score = max((float(edge["score"]) for edge in continuity_by_tracklet.get(tracklet_id, [])), default=0.0)
                    options.append(_edge(row, player_id, .65 + .15 * continuity_score, lineage=.65, continuity=.15 * continuity_score))
            if reid_enabled:
                reid = evidence.get(tracklet_id) or {}
                if reid.get("eligible") and reid.get("top1_player_id") in roster:
                    player_id = str(reid["top1_player_id"])
                    if roster[player_id]["team_label"] == row["team_label"]:
                        value = max(0.0, 1 - float(reid.get("top1_distance") or 1)) * min(1.0, float(reid.get("margin") or 0) / .2)
                        options.append(_edge(row, player_id, .20 * value, reid=.20 * value, reason="gated_reid_ranking"))
        candidates[tracklet_id] = _dedupe_edges(options)
    accepted: dict[str, list[dict[str, Any]]] = defaultdict(list)
    assignments: dict[str, dict[str, Any]] = {}
    for value in assignment_seed.values():
        assignments[value["tracklet_id"]] = value
        if value.get("proposed_player_id") and value["status"] == "confirmed": accepted[value["proposed_player_id"]].append(value)
    decisions = sorted((edge for values in candidates.values() for edge in values), key=lambda edge: (-edge["score"], edge["tracklet_id"], edge["player_id"]))
    used: set[str] = set()
    for edge in decisions:
        if edge["tracklet_id"] in used or edge["tracklet_id"] in assignments:
            continue
        row = next(row for row in rows if row["tracklet_id"] == edge["tracklet_id"])
        overlaps = [other for other in accepted[edge["player_id"]] if _overlap(row, other)]
        if overlaps:
            assignments[edge["tracklet_id"]] = _assignment(row, None, "conflicted", [], ["temporal_mutual_exclusion"], [{"type": "same_player_overlapping_tracklets", "tracklet_ids": sorted(other["tracklet_id"] for other in overlaps)}])
        else:
            assignments[edge["tracklet_id"]] = _assignment(row, edge, "probable", edge["reason_codes"], [], [])
            accepted[edge["player_id"]].append(assignments[edge["tracklet_id"]])
        used.add(edge["tracklet_id"])
    for row in rows:
        if row["tracklet_id"] not in assignments:
            assignments[row["tracklet_id"]] = _assignment(row, None, "unresolved", [], [], [])
    ordered = [assignments[key] for key in sorted(assignments)]
    return {"name": "team_operator_anchors" if anchors_only else "team_operator_anchors_subject_lineage" + ("_gated_reid" if reid_enabled else ""), "assignments": ordered, "edge_scores": sorted((edge for values in candidates.values() for edge in values), key=lambda edge: (edge["tracklet_id"], edge["player_id"])), "metrics": evaluate_stability_metrics(ordered, roster)}


def _edge(row: dict[str, Any], player_id: str, score: float, *, operator: float = 0.0, lineage: float = 0.0, continuity: float = 0.0, reid: float = 0.0, reason: str | None = None) -> dict[str, Any]:
    return {"tracklet_id": row["tracklet_id"], "player_id": player_id, "team_label": row["team_label"], "score": round(score, 6), "components": {"operator_anchor": operator, "team_constraint": 1.0, "subject_lineage": lineage, "continuity": round(continuity, 6), "reid": round(reid, 6), "jersey": None}, "hard_blocked": False, "hard_blockers": [], "reason_codes": [reason] if reason else (["accepted_candidate_subject_lineage"] if lineage else [])}


def _dedupe_edges(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[str, dict[str, Any]] = {}
    for value in values:
        current = chosen.get(value["player_id"])
        if current is None or value["score"] > current["score"]:
            chosen[value["player_id"]] = value
        elif current is not None:
            current["score"] = round(current["score"] + value["score"], 6); current["components"]["reid"] = round(current["components"]["reid"] + value["components"]["reid"], 6); current["components"]["subject_lineage"] = max(current["components"]["subject_lineage"], value["components"]["subject_lineage"]); current["reason_codes"] = sorted(set(current["reason_codes"] + value["reason_codes"]))
    return list(chosen.values())


def _assignment(row: dict[str, Any], edge: dict[str, Any] | None, status: str, evidence: list[str], blockers: list[str], conflicts: list[dict[str, Any]]) -> dict[str, Any]:
    anchor = row.get("anchor") or {}
    return {**row, "proposed_player_id": edge["player_id"] if edge else None, "status": status, "confidence": edge["score"] if edge else None, "top_candidates": [edge] if edge else [], "accepted_evidence": evidence, "rejected_evidence": [], "hard_blockers": blockers, "conflicts": conflicts, "automatic_production_assignment": False, "ground_truth_player_id": anchor.get("player_id")}


def evaluate_stability_metrics(assignments: list[dict[str, Any]], roster: dict[str, dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row["status"]) for row in assignments); proposed = [row for row in assignments if row.get("proposed_player_id")]; audited = [row for row in assignments if row.get("ground_truth_player_id")]
    def accuracy(statuses: set[str] | None = None) -> float | None:
        rows = [row for row in audited if row.get("proposed_player_id") and (statuses is None or row["status"] in statuses)]
        return round(sum(row["proposed_player_id"] == row["ground_truth_player_id"] for row in rows) / len(rows), 4) if rows else None
    assigned_audited = [row for row in audited if row.get("proposed_player_id")]
    merge_pairs = {(row["ground_truth_player_id"], row["proposed_player_id"]) for row in assigned_audited}
    false_merges = sum(1 for player in {row["proposed_player_id"] for row in assigned_audited} if len({row["ground_truth_player_id"] for row in assigned_audited if row["proposed_player_id"] == player}) > 1)
    false_splits = sum(1 for truth in {row["ground_truth_player_id"] for row in assigned_audited} if len({row["proposed_player_id"] for row in assigned_audited if row["ground_truth_player_id"] == truth}) > 1)
    switches = 0
    for truth, rows in _group(audited, "ground_truth_player_id").items():
        sequence = [row["proposed_player_id"] for row in sorted(rows, key=lambda row: (row.get("frame_start") if row.get("frame_start") is not None else float("inf"), row["start_time_sec"], row["tracklet_id"])) if row.get("proposed_player_id")]
        switches += sum(left != right for left, right in zip(sequence, sequence[1:]))
    cross_team = sum(1 for row in proposed if row["proposed_player_id"] not in roster or roster[row["proposed_player_id"]]["team_label"] != row["team_label"])
    corrections = sum(bool(row["hard_blockers"] or row["conflicts"] or (row.get("ground_truth_player_id") and row.get("proposed_player_id") != row["ground_truth_player_id"])) for row in assignments)
    return {"tracklets_total": len(assignments), "confirmed": counts["confirmed"] + counts["confirmed_but_conflicted"], "probable": counts["probable"], "unresolved": counts["unresolved"], "conflicted": counts["conflicted"] + counts["confirmed_but_conflicted"], "blocked": counts["blocked"], "identity_coverage": round(len(proposed) / len(assignments), 4) if assignments else None, "assigned_accuracy": accuracy(), "confirmed_accuracy": accuracy({"confirmed", "confirmed_but_conflicted"}), "probable_accuracy": accuracy({"probable"}), "evaluated_tracklets": len(audited), "id_switches": switches if audited else "NOT_EVALUABLE", "false_merges": false_merges if len(merge_pairs) > 1 else "NOT_EVALUABLE", "false_splits": false_splits if len(merge_pairs) > 1 else "NOT_EVALUABLE", "cross_team_violations": cross_team, "operator_corrections_required": corrections, "operator_decisions_saved": len(audited), "temporal_overlap_conflicts": sum(bool(row["conflicts"]) for row in assignments)}


def _overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return float(first["start_time_sec"]) < float(second["end_time_sec"]) and float(second["start_time_sec"]) < float(first["end_time_sec"])


def _distance(first: Any, second: Any) -> float | None:
    if not isinstance(first, list) or not isinstance(second, list) or len(first) < 2 or len(second) < 2:
        return None
    return ((float(first[0]) - float(second[0])) ** 2 + (float(first[1]) - float(second[1])) ** 2) ** .5


def _scale_similarity(first: Any, second: Any) -> float:
    if not isinstance(first, list) or not isinstance(second, list) or len(first) < 4 or len(second) < 4:
        return .25
    first_area = max((float(first[2]) - float(first[0])) * (float(first[3]) - float(first[1])), 1.0); second_area = max((float(second[2]) - float(second[0])) * (float(second[3]) - float(second[1])), 1.0)
    return max(0.0, 1 - abs(first_area - second_area) / max(first_area, second_area))


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        output[str(row.get(key) or "")].append(row)
    return output
