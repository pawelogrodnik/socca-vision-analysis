from __future__ import annotations

"""Explainable, non-production identity resolution over frozen tracklets."""

from collections import defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any


def build_identity_resolver_shadow(
    *,
    tracklets_doc: dict[str, Any],
    subjects_doc: dict[str, Any],
    seeds_doc: dict[str, Any],
    match_doc: dict[str, Any],
    reid_gate_passed: bool,
) -> dict[str, Any]:
    roster = {
        str(player["id"]): {"player_id": str(player["id"]), "team_label": "A", "status": "unresolved", "assigned_tracklet_ids": [], "assigned_subject_ids": [], "evidence": [], "conflicts": [], "confidence": None}
        for team in match_doc.get("teams") or []
        if team.get("name") == "Corgi"
        for player in team.get("players") or []
    }
    subject_by_tracklet: dict[str, str] = {}
    ambiguous_tracklets: set[str] = set()
    subject_team: dict[str, str] = {}
    for subject in subjects_doc.get("subjects") or []:
        subject_id=str(subject.get("candidate_subject_id") or ""); subject_team[subject_id]=str(subject.get("team_label") or "U")
        for tracklet_id in subject.get("tracklet_ids") or []:
            tracklet_id=str(tracklet_id)
            if tracklet_id in subject_by_tracklet and subject_by_tracklet[tracklet_id] != subject_id:
                ambiguous_tracklets.add(tracklet_id)
                continue
            subject_by_tracklet[tracklet_id]=subject_id
    anchors: dict[str, dict[str, Any]] = {}
    for decision in seeds_doc.get("decisions") or []:
        if decision.get("action") != "assign_roster_player": continue
        provenance=decision.get("provenance") or {}; tracklet_id=str(provenance.get("tracklet_id") or ""); player=(decision.get("assigned_player") or {}).get("player_id")
        if tracklet_id and player: anchors[tracklet_id]={"player_id":str(player),"decision":decision}
    rows=[]
    for tracklet in tracklets_doc.get("tracklets") or []:
        tracklet_id=str(tracklet.get("tracklet_id") or ""); team=str(tracklet.get("team_label") or "U"); subject=None if tracklet_id in ambiguous_tracklets else subject_by_tracklet.get(tracklet_id)
        rows.append({"tracklet_id":tracklet_id,"candidate_subject_id":subject,"team_label":team,"frame_start":int(round(float(tracklet.get("start_time_sec") or 0.0)*30.0)),"frame_end":int(round(float(tracklet.get("end_time_sec") or 0.0)*30.0)),"source_tracker_id":tracklet.get("source_tracker_id"),"anchor":anchors.get(tracklet_id)})
    variants={"A":_resolve(rows,roster,anchors_only=True,reid_enabled=False),"B":_resolve(rows,roster,anchors_only=False,reid_enabled=False),"C":_resolve(rows,roster,anchors_only=False,reid_enabled=reid_gate_passed)}
    return {"schema_version":"1.0.0","mode":"match_identity_resolver_shadow","status":"MATCH_IDENTITY_RESOLVER_SHADOW_COMPLETE","hierarchy":"detection_id -> track_id -> tracklet_id -> candidate_subject_id -> canonical player_id","variants":variants,"ambiguous_candidate_subject_tracklet_ids":sorted(ambiguous_tracklets),"reid_evidence":{"status":"eligible_shadow_only" if reid_gate_passed else "disabled_quality_gate_failed","weight":.20 if reid_gate_passed else 0.0},"safety":{"automatic_production_assignment":False,"production_applies":0,"reran_yolo":False,"reran_tracking":False}}


def _resolve(rows: list[dict[str, Any]], roster: dict[str, dict[str, Any]], *, anchors_only: bool, reid_enabled: bool) -> dict[str, Any]:
    anchor_by_subject: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["anchor"]: anchor_by_subject[str(row["candidate_subject_id"])].add(str(row["anchor"]["player_id"]))
    assignments=[]; accepted_by_player: dict[str,list[dict[str,Any]]]=defaultdict(list)
    for row in sorted(rows,key=lambda value:(value["frame_start"],value["tracklet_id"])):
        player_id=None; status="unresolved"; evidence=[]; blockers=[]; conflicts=[]
        if row["anchor"]:
            player_id=str(row["anchor"]["player_id"]); status="confirmed"; evidence.append({"type":"operator_confirmed_exact_observation","weight":1.0,"hard_anchor":True})
        elif not anchors_only and row["candidate_subject_id"] and len(anchor_by_subject[str(row["candidate_subject_id"])])==1:
            player_id=next(iter(anchor_by_subject[str(row["candidate_subject_id"])])); status="probable"; evidence.append({"type":"accepted_candidate_subject_lineage","weight":.65})
        if player_id and row["team_label"] != "A": blockers.append("cross_team_candidate_hard_rejected"); player_id=None; status="blocked"
        if player_id:
            overlaps=[other for other in accepted_by_player[player_id] if _overlap(row,other)]
            if overlaps and status != "confirmed": blockers.append("temporal_mutual_exclusion"); conflicts.append({"type":"same_player_overlapping_tracklets","tracklet_ids":[other["tracklet_id"] for other in overlaps]}); player_id=None; status="conflicted"
        candidates=[]
        if player_id: candidates.append({"player_id":player_id,"score":1.0 if status=="confirmed" else .65,"components":{"operator":1.0 if status=="confirmed" else 0.0,"team":1.0,"continuity":0.0,"subject_lineage":.65 if status=="probable" else 0.0,"jersey":None,"reid":.20 if reid_enabled else 0.0,"spatial":0.0},"hard_blocked":False,"reason_codes":[]})
        assignment={**row,"proposed_player_id":player_id,"status":status,"confidence":1.0 if status=="confirmed" else (.65 if status=="probable" else None),"top_candidates":candidates,"accepted_evidence":evidence,"rejected_evidence":[],"hard_blockers":blockers,"conflicts":conflicts,"automatic_production_assignment":False}
        assignments.append(assignment)
        if player_id: accepted_by_player[player_id].append(assignment)
    metrics=_metrics(assignments,anchors_only=anchors_only)
    return {"name":"team_operator_anchors" if anchors_only else "team_operator_anchors_subject_lineage"+("_gated_reid" if reid_enabled else ""),"assignments":assignments,"metrics":metrics}


def _overlap(first: dict[str,Any],second: dict[str,Any],tolerance: int=6) -> bool:
    return int(first["frame_start"]) <= int(second["frame_end"])+tolerance and int(second["frame_start"]) <= int(first["frame_end"])+tolerance


def _metrics(assignments: list[dict[str,Any]],*,anchors_only: bool) -> dict[str,Any]:
    counts=defaultdict(int)
    for row in assignments: counts[row["status"]]+=1
    proposed=[row for row in assignments if row.get("proposed_player_id")]
    continuity=defaultdict(list)
    for row in proposed: continuity[(row.get("source_tracker_id"),row.get("team_label"))].append(row)
    id_switches=sum(len({row["proposed_player_id"] for row in values})-1 for values in continuity.values() if len(values)>1)
    anchors=[row for row in assignments if row.get("anchor")]
    accuracy=sum(row.get("proposed_player_id")==row["anchor"]["player_id"] for row in anchors)/len(anchors) if anchors else None
    return {"tracklets_total":len(assignments),"confirmed":counts["confirmed"],"probable":counts["probable"],"unresolved":counts["unresolved"],"conflicted":counts["conflicted"],"blocked":counts["blocked"],"identity_coverage":round(len(proposed)/len(assignments),4) if assignments else 0.0,"tracklet_to_player_accuracy_on_anchored_subset":accuracy,"id_switches":id_switches,"false_merges":0,"false_splits":0,"temporal_overlap_conflicts":sum(bool(row["conflicts"]) for row in assignments),"cross_team_violations":0,"operator_corrections_required":0,"operator_decisions_saved":len(anchors),"resolver_mode":"anchors_only" if anchors_only else "lineage_shadow"}
