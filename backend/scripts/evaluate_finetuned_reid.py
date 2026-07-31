from __future__ import annotations

"""H1-only ReID selection, then one explicitly separate frozen H2 replay."""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from torch import nn
from torchreid.reid.models import build_model

from app.services.identity_cross_capture_reid_validation import build_operator_name_display_gate
from app.services.identity_jersey_number_common import canonical_digest


PRETRAINED = Path("backend/.reid-runtime-lab/osnet-native/weights/osnet_ain_x1_0_msmt17.pth")


def _device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _tensor(path: str, representation: str) -> torch.Tensor:
    image = cv2.imread(path)
    if image is None:
        raise ValueError(f"Missing crop: {path}")
    if representation == "torso":
        height, width = image.shape[:2]
        image = image[int(.2 * height):int(.88 * height), int(.12 * width):int(.88 * width)]
    rgb = cv2.cvtColor(cv2.resize(image, (128, 256)), cv2.COLOR_BGR2RGB)
    value = torch.from_numpy(rgb.copy()).permute(2, 0, 1).float() / 255.0
    return (value - torch.tensor([.485, .456, .406])[:, None, None]) / torch.tensor([.229, .224, .225])[:, None, None]


def _model(labels: dict[str, int], checkpoint: Path | None, device: str) -> nn.Module:
    model = build_model("osnet_ain_x1_0", num_classes=len(labels), loss="triplet", pretrained=False)
    source = torch.load(PRETRAINED, map_location="cpu", weights_only=True)
    state = model.state_dict()
    state.update({key.removeprefix("module."): value for key, value in source.items() if key.removeprefix("module.") in state and state[key.removeprefix("module.")].shape == value.shape})
    model.load_state_dict(state)
    if checkpoint:
        model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True)["state_dict"])
    return model.to(device).eval()


def _embeddings(model: nn.Module, rows: list[dict[str, Any]], representation: str, device: str) -> np.ndarray:
    vectors: list[np.ndarray] = []
    with torch.no_grad():
        for start in range(0, len(rows), 16):
            batch = torch.stack([_tensor(str(row["crop_path"]), representation) for row in rows[start:start + 16]]).to(device)
            vectors.append(nn.functional.normalize(model(batch), dim=1).cpu().numpy())
    return np.vstack(vectors) if vectors else np.empty((0, 512), dtype=np.float32)


def _prototypes(rows: list[dict[str, Any]], vectors: np.ndarray) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    grouped: dict[str, list[np.ndarray]] = defaultdict(list); teams: dict[str, str] = {}
    for row, vector in zip(rows, vectors, strict=True):
        player = str(row["player_id"]); grouped[player].append(vector); teams[player] = str(row["team_label"])
    return {player: nn.functional.normalize(torch.from_numpy(np.stack(values)).mean(0), dim=0).numpy() for player, values in grouped.items()}, teams


def rank_tracklets(gallery_rows: list[dict[str, Any]], gallery_vectors: np.ndarray, query_rows: list[dict[str, Any]], query_vectors: np.ndarray, *, include_truth: bool = True) -> dict[str, Any]:
    prototypes, player_teams = _prototypes(gallery_rows, gallery_vectors)
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(query_rows):
        groups[str(row["tracklet_id"])].append(index)
    results: list[dict[str, Any]] = []
    for tracklet_id, indices in sorted(groups.items()):
        query = query_rows[indices[0]]; team = str(query["team_label"])
        valid = query_vectors[indices]
        prototype = nn.functional.normalize(torch.from_numpy(valid).mean(0), dim=0).numpy()
        rejected = sorted(player for player, player_team in player_teams.items() if player_team != team)
        ranking = sorted(((player, 1 - float(np.clip(prototype @ vector, -1, 1))) for player, vector in prototypes.items() if player_teams[player] == team), key=lambda item: (item[1], item[0]))
        ids = [player for player, _ in ranking]; distances = [round(distance, 6) for _, distance in ranking]
        truth = str(query.get("player_id") or "") if include_truth else None
        truth_rank = ids.index(truth) + 1 if truth in ids else None
        results.append({"tracklet_id": tracklet_id, "candidate_subject_id": str(query.get("candidate_subject_id") or ""), "team_label": team, "embedding": {"crop_count": len(indices), "valid_crop_count": len(indices), "prototype_method": "normalized_mean"}, "query_team": team, "candidate_count": len(ids), "ranked_player_ids": ids, "ranked_distances": distances, "cross_team_candidates_rejected": rejected, "truth_rank": truth_rank, "top1_correct": truth_rank == 1 if include_truth else None, "top3_correct": bool(truth_rank and truth_rank <= 3) if include_truth else None, "top1_margin": round(distances[1] - distances[0], 6) if len(distances) > 1 else None})
    known = [row for row in results if row["truth_rank"] is not None]
    count = len(results)
    cross_team_violations = sum(any(player_teams.get(player) != row["team_label"] for player in row["ranked_player_ids"]) for row in results)
    invalid_ranked_players = sum(any(player not in player_teams for player in row["ranked_player_ids"]) for row in results)
    return {"tracklets_total": count, "tracklets_evaluated": count, "tracklets_without_valid_embeddings": 0, "top1_accuracy": round(sum(bool(row["top1_correct"]) for row in results) / len(known), 4) if known else None, "top3_accuracy": round(sum(bool(row["top3_correct"]) for row in results) / len(known), 4) if known else None, "truth_ranks": [row["truth_rank"] for row in results], "mean_truth_rank": round(float(np.mean([row["truth_rank"] for row in known])), 4) if known else None, "median_truth_rank": round(float(np.median([row["truth_rank"] for row in known])), 4) if known else None, "top1_margins": [row["top1_margin"] for row in results], "coverage": round(len(known) / count, 4) if count else None, "abstentions": count - len(known), "cross_team_violations": cross_team_violations, "invalid_ranked_players": invalid_ranked_players, "rows": results}


def _h2(source_root: Path, session_root: Path) -> list[dict[str, Any]]:
    decisions = json.loads((session_root / "operator_decisions.json").read_text())["decisions"]
    rows = [{"frame": int(row["frame"]), "bbox_xyxy": row["bbox_xyxy"], "player_id": row["player_id"], "team_label": row["team_label"], "tracklet_id": f"h2-v5-{index}", "candidate_subject_id": "", "source": "v5"} for index, row in enumerate(decisions) if row.get("action") == "player"]
    validation = json.loads((source_root / "cross_capture_reid_diagnostic" / "cross_capture_reid_validation.json").read_text())["preferred_cross_capture_evaluation"]["rows"][0]
    provenance = validation["observation_provenance"]
    rows.append({"frame": int(provenance["frame_number"]), "bbox_xyxy": provenance["bbox_xyxy"], "player_id": validation["ground_truth_player_id"], "team_label": validation["ground_truth_team"], "tracklet_id": "h2-v4-0", "candidate_subject_id": "", "source": "v4"})
    if len(rows) != 6:
        raise ValueError("Frozen H2 must have exactly six queries")
    return rows


def _h2_rows(source_root: Path, queries: list[dict[str, Any]], representation: str, temp_root: Path) -> list[dict[str, Any]]:
    capture = cv2.VideoCapture(str(source_root / "h2_workspace" / "video.mp4")); output: list[dict[str, Any]] = []
    try:
        for index, query in enumerate(queries):
            capture.set(cv2.CAP_PROP_POS_FRAMES, query["frame"]); ok, frame = capture.read()
            if not ok:
                raise ValueError("Missing frozen H2 frame")
            x1, y1, x2, y2 = (int(value) for value in query["bbox_xyxy"]); crop = frame[max(0, y1):y2, max(0, x1):x2]
            path = temp_root / f"h2-{representation}-{index}.jpg"; cv2.imwrite(str(path), crop); output.append({**query, "crop_path": str(path)})
    finally:
        capture.release()
    return output


def _runs(root: Path) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = [{"run_id": "run-a-pretrained", "checkpoint": None, "checkpoint_sha256": hashlib.sha256(PRETRAINED.read_bytes()).hexdigest(), "representation": "full", "latency_rank": 0}]
    for run_id in ("run-b-full", "run-c-torso"):
        manifest = json.loads((root / "training" / run_id / "best_checkpoint_manifest.json").read_text()); best = manifest["best_checkpoint"]
        runs.append({"run_id": run_id, "checkpoint": best["checkpoint"], "checkpoint_sha256": best["checkpoint_sha256"], "representation": manifest["training_config"]["representation"], "latency_rank": 1})
    return runs


def _select(root: Path) -> dict[str, Any]:
    dataset = json.loads((root / "dataset" / "dataset_manifest_eligible.json").read_text()); assignments = json.loads((root / "dataset" / "dataset_split.json").read_text())["assignments"]
    rows = dataset["rows"]; train = [row for row in rows if assignments[row["sample_id"]] == "train"]; valid = [row for row in rows if assignments[row["sample_id"]] == "validation"]; labels = {player: index for index, player in enumerate(sorted({str(row["player_id"]) for row in rows}))}; device = _device(); evaluations: list[dict[str, Any]] = []
    for run in _runs(root):
        model = _model(labels, Path(run["checkpoint"]) if run["checkpoint"] else None, device); evaluation = rank_tracklets(train, _embeddings(model, train, run["representation"], device), valid, _embeddings(model, valid, run["representation"], device))
        history_path = root / "training" / run["run_id"] / "training_history.json"
        auc = None if not history_path.exists() else json.loads(history_path.read_text())[-1]["within_match_tracklet_validation"]["same_team_separability"]["same_team_roc_auc"]
        evaluations.append({**run, "h1_validation": evaluation, "same_team_roc_auc": auc})
    ordered = sorted(evaluations, key=lambda row: (-float(row["h1_validation"]["top1_accuracy"] or -1), -float(row["h1_validation"]["top3_accuracy"] or -1), -float(row["same_team_roc_auc"] or -1), -float(np.median([value for value in row["h1_validation"]["top1_margins"] if value is not None]) if any(value is not None for value in row["h1_validation"]["top1_margins"]) else -1), -float(row["h1_validation"]["coverage"] or -1), row["latency_rank"], row["run_id"]))
    winner = ordered[0]
    comparison = {"status": "REID_TRAINING_PROTOCOL_FIXED", "candidates": evaluations, "dataset_digest": dataset["digest"], "split_digest": canonical_digest(assignments), "selection_policy": "H1 tracklet top1, top3, same-team AUC, median margin, coverage, latency, run_id", "h2_used_for_training": False, "h2_used_for_model_selection": False}
    selection = {"status": "FINETUNED_REID_WINNER_FROZEN_ON_H1", "winner": winner, "winner_checkpoint_sha256": winner["checkpoint_sha256"], "dataset_digest": dataset["digest"], "split_digest": canonical_digest(assignments), "training_config_digest": canonical_digest({row["run_id"]: row.get("checkpoint_sha256") for row in evaluations}), "h2_available_to_selection": False, "h2_used_for_training": False, "h2_used_for_model_selection": False}
    selection["selection_digest"] = canonical_digest(selection)
    (root / "h1_reid_run_comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")
    (root / "h1_reid_winner_selection.json").write_text(json.dumps(selection, indent=2) + "\n")
    return {"dataset": dataset, "assignments": assignments, "train": train, "valid": valid, "labels": labels, "evaluations": evaluations, "winner": winner, "selection": selection}


def _h2_replay(root: Path, source_root: Path, session_root: Path) -> dict[str, Any]:
    selection = json.loads((root / "h1_reid_winner_selection.json").read_text())
    if selection.get("h2_used_for_model_selection") or not selection.get("selection_digest"):
        raise ValueError("H1 selection was not safely frozen before H2")
    dataset = json.loads((root / "dataset" / "dataset_manifest_eligible.json").read_text()); assignments = json.loads((root / "dataset" / "dataset_split.json").read_text())["assignments"]
    rows = dataset["rows"]; train = [row for row in rows if assignments[row["sample_id"]] == "train"]; labels = {player: index for index, player in enumerate(sorted({str(row["player_id"]) for row in rows}))}; winner = selection["winner"]; device = _device(); model = _model(labels, Path(winner["checkpoint"]) if winner.get("checkpoint") else None, device)
    h2_root = root / "final_h2_crops"; h2_root.mkdir(parents=True, exist_ok=True); queries = _h2(source_root, session_root); h2_rows = _h2_rows(source_root, queries, winner["representation"], h2_root); evaluation = rank_tracklets(train, _embeddings(model, train, winner["representation"], device), h2_rows, _embeddings(model, h2_rows, winner["representation"], device))
    gate = build_operator_name_display_gate(model_status={"quality_tier": "preferred_reid_model", "selected_runtime": "isolated_osnet_training"}, internal_calibration=winner["h1_validation"], cross_capture_evaluation=evaluation)
    status = "FINETUNED_REID_QUALITY_GATE_PASSED" if gate["display_eligible"] else "FINETUNED_REID_IMPROVED_BUT_GATE_FAILED"
    report = {"status": status, "operator_name_status": "OPERATOR_NAMES_ELIGIBLE_FOR_FUTURE_FLOW" if gate["display_eligible"] else "OPERATOR_NAMES_REMAIN_HIDDEN", "winner": winner, "selection_digest": selection["selection_digest"], "final_h2": evaluation, "canonical_gate": gate, "small_sample_warning": True, "safety": {"automatic_identity_assignments": 0, "production_applies": 0, "h2_used_for_training": False, "h2_used_for_model_selection": False, "yolo_reruns": 0, "tracking_reruns": 0}}
    (root / "final_h2_finetuned_holdout.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def _evidence(root: Path, source_root: Path | None = None) -> dict[str, Any]:
    selection = json.loads((root / "h1_reid_winner_selection.json").read_text()); final = json.loads((root / "final_h2_finetuned_holdout.json").read_text()); dataset = json.loads((root / "dataset" / "dataset_manifest_eligible.json").read_text()); assignments = json.loads((root / "dataset" / "dataset_split.json").read_text())["assignments"]
    rows = dataset["rows"]; train = [row for row in rows if assignments[row["sample_id"]] == "train"]; labels = {player: index for index, player in enumerate(sorted({str(row["player_id"]) for row in rows}))}; winner = selection["winner"]; device = _device(); model = _model(labels, Path(winner["checkpoint"]) if winner.get("checkpoint") else None, device)
    ranked = rank_tracklets(train, _embeddings(model, train, winner["representation"], device), rows, _embeddings(model, rows, winner["representation"], device))
    h1_margins = [value for value in winner["h1_validation"]["top1_margins"] if value is not None]; h1_distances = [row["ranked_distances"][0] for row in winner["h1_validation"]["rows"] if row["ranked_distances"]]
    policy = {"source": "H1_validation_only", "minimum_margin": round(float(np.percentile(h1_margins, 10)), 6) if h1_margins else None, "maximum_top1_distance": round(float(np.percentile(h1_distances, 90)), 6) if h1_distances else None, "quality_gate_passed": bool((final.get("canonical_gate") or {}).get("display_eligible")), "selection_digest": selection["selection_digest"]}
    roster = {str(row["player_id"]) for row in rows}
    evidence_rows = []
    for row in ranked["rows"]:
        top = row["ranked_distances"][0] if row["ranked_distances"] else None; margin = row["top1_margin"]
        reasons = []
        if not row["ranked_player_ids"]: reasons.append("no_valid_embeddings")
        if policy["minimum_margin"] is not None and (margin is None or margin < policy["minimum_margin"]): reasons.append("top1_margin_below_h1_policy")
        if policy["maximum_top1_distance"] is not None and (top is None or top > policy["maximum_top1_distance"]): reasons.append("top1_distance_above_h1_policy")
        if not policy["quality_gate_passed"]: reasons.append("model_gate_failed")
        if row["ranked_player_ids"] and row["ranked_player_ids"][0] not in roster: reasons.append("player_not_in_roster")
        evidence_rows.append({"tracklet_id": row["tracklet_id"], "candidate_subject_id": row["candidate_subject_id"], "team_label": row["team_label"], "model": {"run_id": winner["run_id"], "checkpoint_sha256": selection["winner_checkpoint_sha256"], "selection_digest": selection["selection_digest"]}, "embedding": row["embedding"], "rankings": [{"player_id": player, "distance": distance, "similarity": round(1 - distance, 6), "rank": index + 1} for index, (player, distance) in enumerate(zip(row["ranked_player_ids"], row["ranked_distances"], strict=True))], "top1_player_id": row["ranked_player_ids"][0] if row["ranked_player_ids"] else None, "top1_distance": top, "top2_distance": row["ranked_distances"][1] if len(row["ranked_distances"]) > 1 else None, "margin": margin, "eligible": not reasons, "abstention_reason": ";".join(reasons) if reasons else None})
    if source_root:
        workspace = source_root / "h1_workspace"
        subjects = json.loads((workspace / "identity_candidate_shadow.json").read_text())
        subject_by_tracklet = {str(tracklet_id): str(subject.get("candidate_subject_id") or "") for subject in subjects.get("subjects") or [] for tracklet_id in subject.get("tracklet_ids") or []}
        existing = {row["tracklet_id"] for row in evidence_rows}
        for tracklet in json.loads((workspace / "tracklets.json").read_text()).get("tracklets") or []:
            tracklet_id = str(tracklet.get("tracklet_id") or "")
            if tracklet_id and tracklet_id not in existing:
                evidence_rows.append({"tracklet_id": tracklet_id, "candidate_subject_id": subject_by_tracklet.get(tracklet_id), "team_label": str(tracklet.get("team_label") or "U"), "model": {"run_id": winner["run_id"], "checkpoint_sha256": selection["winner_checkpoint_sha256"], "selection_digest": selection["selection_digest"]}, "embedding": {"crop_count": 0, "valid_crop_count": 0, "prototype_method": "normalized_mean"}, "rankings": [], "top1_player_id": None, "top1_distance": None, "top2_distance": None, "margin": None, "eligible": False, "abstention_reason": "no_h1_eligible_crops_for_tracklet"})
    result = {"schema_version": "1.0.0", "confidence_policy_digest": canonical_digest(policy), "policy": policy, "tracklets": sorted(evidence_rows, key=lambda row: row["tracklet_id"])}
    (root / "reid_confidence_policy.json").write_text(json.dumps(policy, indent=2) + "\n")
    (root / "tracklet_reid_evidence.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--experiment-root", required=True, type=Path); parser.add_argument("--mode", choices=("h1", "h2", "evidence"), required=True); parser.add_argument("--source-root", type=Path); parser.add_argument("--session-root", type=Path); options = parser.parse_args()
    if options.mode == "h1":
        result = _select(options.experiment_root)
        print(json.dumps({"status": "FINETUNED_REID_WINNER_FROZEN_ON_H1", "winner": result["winner"]["run_id"]}))
    elif options.mode == "h2":
        if not options.source_root or not options.session_root: raise ValueError("H2 requires frozen source and session roots")
        print(json.dumps(_h2_replay(options.experiment_root, options.source_root, options.session_root)))
    else:
        print(json.dumps({"tracklets": len(_evidence(options.experiment_root, options.source_root)["tracklets"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
