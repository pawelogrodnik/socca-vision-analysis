from __future__ import annotations

"""Freeze an audited OSNet winner on H1, then replay frozen H2 once."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader
from torchreid.reid.models import build_model

from app.services.identity_cross_capture_reid_validation import build_operator_name_display_gate
from app.services.identity_jersey_number_common import canonical_digest

PRETRAINED = Path("backend/.reid-runtime-lab/osnet-native/weights/osnet_ain_x1_0_msmt17.pth")


def _device() -> str: return "mps" if torch.backends.mps.is_available() else "cpu"


def _tensor(path: str, representation: str) -> torch.Tensor:
    image = cv2.imread(path)
    if image is None: raise ValueError(f"Missing crop: {path}")
    if representation == "torso":
        height, width = image.shape[:2]; image = image[int(.2*height):int(.88*height), int(.12*width):int(.88*width)]
    rgb = cv2.cvtColor(cv2.resize(image, (128,256)), cv2.COLOR_BGR2RGB)
    value = torch.from_numpy(rgb.copy()).permute(2,0,1).float() / 255.0
    return (value - torch.tensor([.485,.456,.406])[:,None,None]) / torch.tensor([.229,.224,.225])[:,None,None]


def _model(labels: dict[str, int], checkpoint: Path | None, device: str) -> nn.Module:
    model = build_model("osnet_ain_x1_0", num_classes=len(labels), loss="triplet", pretrained=False)
    source = torch.load(PRETRAINED, map_location="cpu", weights_only=True)
    state = model.state_dict()
    state.update({key.removeprefix("module."): value for key, value in source.items() if key.removeprefix("module.") in state and state[key.removeprefix("module.")].shape == value.shape})
    model.load_state_dict(state)
    if checkpoint is not None:
        trained = torch.load(checkpoint, map_location="cpu", weights_only=True)["state_dict"]
        model.load_state_dict(trained)
    return model.to(device).eval()


def _embeddings(model: nn.Module, rows: list[dict[str, Any]], representation: str, device: str) -> np.ndarray:
    with torch.no_grad():
        values = [_tensor(str(row["crop_path"]), representation).to(device) for row in rows]
        return nn.functional.normalize(model(torch.stack(values)), dim=1).cpu().numpy()


def _rank(train_rows: list[dict[str, Any]], train_vectors: np.ndarray, queries: list[dict[str, Any]], query_vectors: np.ndarray) -> dict[str, Any]:
    by_player: dict[str, list[np.ndarray]] = {}
    team_by_player: dict[str, str] = {}
    for row, vector in zip(train_rows, train_vectors, strict=True):
        by_player.setdefault(str(row["player_id"]), []).append(vector); team_by_player[str(row["player_id"])] = str(row["team_label"])
    rows = []; cross_team = invalid = duplicate = missing = 0
    for query, vector in zip(queries, query_vectors, strict=True):
        candidates = [(player, values) for player, values in by_player.items() if team_by_player[player] == str(query["team_label"])]
        ranking = sorted(((player, 1-float(np.clip(vector @ nn.functional.normalize(torch.from_numpy(np.stack(values)).mean(0),dim=0).numpy(),-1,1))) for player, values in candidates), key=lambda item:(item[1],item[0]))
        ids = [player for player,_ in ranking]; truth = str(query["player_id"]); rank = ids.index(truth)+1 if truth in ids else None
        duplicate += len(ids) - len(set(ids)); missing += int(truth not in by_player); invalid += int(any(player not in by_player for player in ids)); cross_team += int(any(team_by_player[player] != query["team_label"] for player in ids))
        rows.append({**query,"truth_rank":rank,"top1_correct":rank==1,"top3_correct":bool(rank and rank<=3),"ranked_player_ids":ids[:3],"ranked_distances":[round(distance,6) for _,distance in ranking[:3]]})
    count=len(rows)
    truth_ranks = [row["truth_rank"] for row in rows if row["truth_rank"]]
    return {
        "queries": count,
        "top1_accuracy": round(sum(bool(row["top1_correct"]) for row in rows) / count, 4) if count else None,
        "top3_accuracy": round(sum(bool(row["top3_correct"]) for row in rows) / count, 4) if count else None,
        "truth_ranks": [row["truth_rank"] for row in rows],
        "mean_truth_rank": round(float(np.mean(truth_ranks)), 4) if truth_ranks else None,
        "median_truth_rank": round(float(np.median(truth_ranks)), 4) if truth_ranks else None,
        "abstentions": sum(row["truth_rank"] is None for row in rows),
        "cross_team_violations": cross_team,
        "invalid_ranked_players": invalid,
        "duplicate_ranked_players": duplicate,
        "missing_roster_players": missing,
        "rows": rows,
    }


def _h2(source_root: Path, session_root: Path) -> list[dict[str, Any]]:
    decisions=json.loads((session_root/"operator_decisions.json").read_text())["decisions"]
    rows=[{"frame":int(row["frame"]),"bbox_xyxy":row["bbox_xyxy"],"player_id":row["player_id"],"team_label":row["team_label"],"source":"v5"} for row in decisions if row.get("action")=="player"]
    validation=json.loads((source_root/"cross_capture_reid_diagnostic"/"cross_capture_reid_validation.json").read_text())["preferred_cross_capture_evaluation"]["rows"][0]
    provenance=validation["observation_provenance"]; rows.append({"frame":int(provenance["frame_number"]),"bbox_xyxy":provenance["bbox_xyxy"],"player_id":validation["ground_truth_player_id"],"team_label":validation["ground_truth_team"],"source":"v4"})
    if len(rows)!=6: raise ValueError("Frozen H2 must have exactly six queries")
    return rows


def _h2_rows(source_root: Path, queries: list[dict[str, Any]], representation: str, temp_root: Path) -> list[dict[str, Any]]:
    capture=cv2.VideoCapture(str(source_root/"h2_workspace"/"video.mp4")); output=[]
    try:
        for index, query in enumerate(queries):
            capture.set(cv2.CAP_PROP_POS_FRAMES,query["frame"]); ok,frame=capture.read()
            if not ok: raise ValueError("Missing frozen H2 frame")
            x1,y1,x2,y2=(int(value) for value in query["bbox_xyxy"]); crop=frame[max(0,y1):y2,max(0,x1):x2]
            path=temp_root/f"h2-{representation}-{index}.jpg"; cv2.imwrite(str(path),crop)
            output.append({**query,"crop_path":str(path)})
    finally: capture.release()
    return output


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--experiment-root",required=True,type=Path); parser.add_argument("--source-root",required=True,type=Path); parser.add_argument("--session-root",required=True,type=Path); options=parser.parse_args()
    dataset=json.loads((options.experiment_root/"dataset"/"dataset_manifest_eligible.json").read_text()); split=json.loads((options.experiment_root/"dataset"/"dataset_split.json").read_text())["assignments"]
    rows=dataset["rows"]; train=[row for row in rows if split[row["sample_id"]]=="train"]; validation=[row for row in rows if split[row["sample_id"]]=="validation"]; labels={player:index for index,player in enumerate(sorted({str(row["player_id"]) for row in rows}))}; device=_device()
    runs=[{"run_id":"run-a-pretrained","checkpoint":None,"representation":"full"}]
    for run_id in ("run-b-full","run-c-torso"):
        manifest=json.loads((options.experiment_root/"training"/run_id/"best_checkpoint_manifest.json").read_text()); best=manifest["best_checkpoint"]
        runs.append({"run_id":run_id,"checkpoint":str(best["checkpoint"]),"checkpoint_sha256":best["checkpoint_sha256"],"representation":manifest["training_config"]["representation"]})
    evaluations=[]
    for run in runs:
        checkpoint = Path(run["checkpoint"]) if run["checkpoint"] else None
        model=_model(labels,checkpoint,device); evaluation=_rank(train,_embeddings(model,train,run["representation"],device),validation,_embeddings(model,validation,run["representation"],device)); evaluations.append({**run,"h1_validation":evaluation})
    ordered=sorted(evaluations,key=lambda row:(-float(row["h1_validation"]["top1_accuracy"] or 0),-float(row["h1_validation"]["top3_accuracy"] or 0),row["run_id"])); winner=ordered[0]
    selection={"status":"FINETUNED_REID_WINNER_FROZEN_ON_H1","winner":winner,"candidates":evaluations,"dataset_digest":dataset["digest"],"split_digest":canonical_digest(split),"selection_policy":"H1 tracklet-grouped top1, then top3, then run_id; H2 unavailable","h2_used_for_training":False,"h2_used_for_model_selection":False}; selection["selection_config_digest"]=canonical_digest(selection)
    (options.experiment_root/"h1_finetuned_selection.json").write_text(json.dumps(selection,indent=2)+"\n")
    # The first H2 read happens only after the immutable H1 selection has been persisted.
    h2_queries=_h2(options.source_root,options.session_root); h2_root=options.experiment_root/"final_h2_crops"; h2_root.mkdir(parents=True,exist_ok=True); comparisons=[]
    for run in evaluations:
        checkpoint = Path(run["checkpoint"]) if run["checkpoint"] else None
        model=_model(labels,checkpoint,device); h2_rows=_h2_rows(options.source_root,h2_queries,run["representation"],h2_root); comparisons.append({"run_id":run["run_id"],"evaluation":_rank(train,_embeddings(model,train,run["representation"],device),h2_rows,_embeddings(model,h2_rows,run["representation"],device))})
    final=next(row["evaluation"] for row in comparisons if row["run_id"]==winner["run_id"])
    gate=build_operator_name_display_gate(model_status={"quality_tier":"preferred_reid_model","selected_runtime":"isolated_osnet_training"},internal_calibration=winner["h1_validation"],cross_capture_evaluation=final)
    status="FINETUNED_REID_QUALITY_GATE_PASSED" if gate["display_eligible"] else ("FINETUNED_REID_IMPROVED_BUT_GATE_FAILED" if winner["h1_validation"]["top1_accuracy"] > evaluations[0]["h1_validation"]["top1_accuracy"] else "FINETUNED_REID_NO_GENERALIZATION_GAIN")
    report={"status":status,"operator_name_status":"OPERATOR_NAMES_ELIGIBLE_FOR_FUTURE_FLOW" if gate["display_eligible"] else "OPERATOR_NAMES_REMAIN_HIDDEN","winner":winner,"selection_digest":selection["selection_config_digest"],"final_h2":final,"comparisons":comparisons,"canonical_gate":gate,"small_sample_warning":True,"safety":{"automatic_identity_assignments":0,"production_applies":0,"h2_used_for_training":False,"h2_used_for_model_selection":False,"yolo_reruns":0,"tracking_reruns":0}}
    (options.experiment_root/"pretrained_bakeoff.json").write_text(json.dumps({"run_a":evaluations[0],"h2_unused":True},indent=2)+"\n")
    (options.experiment_root/"final_h2_finetuned_holdout.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"status":status,"winner":winner["run_id"],"h2":final,"gate":gate}))
    return 0


if __name__=="__main__": raise SystemExit(main())
