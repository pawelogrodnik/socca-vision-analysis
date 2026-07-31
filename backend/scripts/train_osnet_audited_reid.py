from __future__ import annotations

"""Bounded OSNet tuning with deterministic team-aware PK batches."""

import argparse
import hashlib
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

import cv2
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset, Sampler
from torchreid.reid.models import build_model


PRETRAINED_WEIGHTS = Path("backend/.reid-runtime-lab/osnet-native/weights/osnet_ain_x1_0_msmt17.pth")
SEED = 1337


class AuditedDataset(Dataset[tuple[torch.Tensor, int, int, int, int, int]]):
    def __init__(self, rows: list[dict[str, Any]], maps: dict[str, dict[str, int]], *, representation: str, augment: bool) -> None:
        self.rows, self.maps, self.representation, self.augment = rows, maps, representation, augment

    def __len__(self) -> int: return len(self.rows)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int, int, int, int, int]:
        row = self.rows[index]; image = cv2.imread(str(row["crop_path"]))
        if image is None: raise ValueError(f"Cannot read crop: {row['crop_path']}")
        if self.representation == "torso":
            height,width=image.shape[:2]; image=image[int(.20*height):int(.88*height),int(.12*width):int(.88*width)]
        rgb=cv2.cvtColor(cv2.resize(image,(128,256)),cv2.COLOR_BGR2RGB)
        tensor=torch.from_numpy(rgb.copy()).permute(2,0,1).float()/255.0
        if self.augment:
            if torch.rand(()) < .5: tensor=torch.flip(tensor,[2])
            tensor=torch.clamp(tensor*(.9+.2*torch.rand(()))+(-.05+.1*torch.rand(())),0,1)
        mean=torch.tensor([.485,.456,.406])[:,None,None]; std=torch.tensor([.229,.224,.225])[:,None,None]
        return (tensor-mean)/std, self.maps["player"][str(row["player_id"])], self.maps["team"][str(row["team_label"])], self.maps["subject"][str(row["candidate_subject_id"])], self.maps["tracklet"][str(row["tracklet_id"])], index


class TeamAwarePKSampler(Sampler[list[int]]):
    """Draw P identities from one team and K diverse samples per identity."""
    def __init__(self, rows: list[dict[str, Any]], *, p: int, k: int, steps: int, seed: int) -> None:
        self.rows,self.p,self.k,self.steps,self.seed=rows,p,k,steps,seed
        self.by_team: dict[str,dict[str,list[int]]]=defaultdict(lambda:defaultdict(list))
        for index,row in enumerate(rows): self.by_team[str(row["team_label"])][str(row["player_id"])].append(index)
        self.teams=sorted(self.by_team); self.fallback_batches=0

    def __len__(self) -> int: return self.steps

    def __iter__(self) -> Iterator[list[int]]:
        randomizer=random.Random(self.seed)
        for step in range(self.steps):
            team=self.teams[step%len(self.teams)]; players=sorted(self.by_team[team])
            chosen=randomizer.sample(players,min(self.p,len(players)))
            if len(chosen)<self.p:
                other=[player for other_team in self.teams if other_team!=team for player in self.by_team[other_team]]
                chosen += randomizer.sample(other,min(self.p-len(chosen),len(other))); self.fallback_batches += 1
            batch=[]
            for player in chosen:
                options=self.by_team[team].get(player) or next(values[player] for values in self.by_team.values() if player in values)
                batch.extend(_diverse_pick(options,self.rows,self.k,randomizer))
            yield batch


def _diverse_pick(options: list[int], rows: list[dict[str,Any]], k: int, randomizer: random.Random) -> list[int]:
    picked=[]; available=sorted(options,key=lambda index:(int(rows[index]["frame"]),str(rows[index]["sample_id"])))
    while len(picked)<k:
        candidates=sorted(
            available,
            key=lambda index: (
                sum(
                    int(any(rows[index][field] == rows[other][field] for other in picked))
                    for field in ("candidate_subject_id", "tracklet_id")
                ),
                -min(
                    (abs(int(rows[index]["frame"]) - int(rows[other]["frame"])) for other in picked),
                    default=10**9,
                ),
                randomizer.random(),
            ),
        )
        chosen=candidates[0]; picked.append(chosen)
        if len(available)>1: available.remove(chosen)
    return picked


def _device() -> str: return "mps" if torch.backends.mps.is_available() else "cpu"


def _triplet(features: torch.Tensor, labels: torch.Tensor, teams: torch.Tensor, subjects: torch.Tensor, tracklets: torch.Tensor) -> tuple[torch.Tensor, Counter[str]]:
    vectors=nn.functional.normalize(features,dim=1); distances=1-vectors@vectors.t(); counters:Counter[str]=Counter(); losses=[]
    positions=torch.arange(len(labels),device=labels.device)
    for index in range(len(labels)):
        same=(labels==labels[index])&(positions!=index)
        cross_subject=same&(subjects!=subjects[index]); cross_tracklet=same&(tracklets!=tracklets[index])
        positives=cross_subject if bool(cross_subject.any()) else (cross_tracklet if bool(cross_tracklet.any()) else same)
        negatives=labels!=labels[index]; same_team=negatives&(teams==teams[index]); choices=same_team if bool(same_team.any()) else negatives
        if not bool(positives.any()): counters["anchors_without_positive"]+=1; continue
        if not bool(same_team.any()): counters["anchors_without_same_team_negative"]+=1; counters["different_team_fallback_triplets"]+=1
        else: counters["same_team_negative_triplets"]+=1
        if bool(cross_subject.any()): counters["positive_cross_subject"]+=1
        elif bool(cross_tracklet.any()): counters["positive_cross_tracklet"]+=1
        else: counters["positive_same_tracklet"]+=1
        losses.append(nn.functional.relu(.25+distances[index][positives].max()-distances[index][choices].min()))
    return (torch.stack(losses).mean() if losses else features.sum()*0),counters


def _vectors(model: nn.Module, loader: DataLoader[Any], device: str) -> tuple[np.ndarray,list[tuple[int,int,int,int]]]:
    model.eval(); values=[]; metadata=[]
    with torch.no_grad():
        for images,player,team,subject,tracklet,_ in loader:
            values.append(nn.functional.normalize(model(images.to(device)),dim=1).cpu().numpy())
            metadata += list(zip(player.tolist(),team.tolist(),subject.tolist(),tracklet.tolist(),strict=True))
    return np.vstack(values),metadata


def _tracklet_validation(model: nn.Module, train_loader: DataLoader[Any], validation_loader: DataLoader[Any], device: str) -> dict[str,Any]:
    train,train_meta=_vectors(model,train_loader,device); valid,valid_meta=_vectors(model,validation_loader,device)
    prototypes={player:nn.functional.normalize(torch.from_numpy(train[[index for index,row in enumerate(train_meta) if row[0]==player]]).mean(0),dim=0).numpy() for player in {row[0] for row in train_meta}}
    groups:dict[int,list[int]]=defaultdict(list)
    for index,row in enumerate(valid_meta): groups[row[3]].append(index)
    rows=[]
    for tracklet,indices in sorted(groups.items()):
        player,team,subject,_=valid_meta[indices[0]]; vector=nn.functional.normalize(torch.from_numpy(valid[indices]).mean(0),dim=0).numpy()
        ranking=sorted(((candidate,1-float(np.clip(vector@prototype,-1,1))) for candidate,prototype in prototypes.items()),key=lambda row:row[1]); rank=next(position for position,(candidate,_) in enumerate(ranking,1) if candidate==player)
        rows.append({"tracklet_index":tracklet,"player_index":player,"team_index":team,"subject_index":subject,"truth_rank":rank,"top1":rank==1,"top3":rank<=3,"top1_margin":round(ranking[1][1]-ranking[0][1],6)})
    count=len(rows)
    return {"protocol":"WITHIN_MATCH_TRACKLET_HOLDOUT","queries":count,"top1_accuracy":round(sum(row["top1"] for row in rows)/count,4),"top3_accuracy":round(sum(row["top3"] for row in rows)/count,4),"coverage":1.0,"abstentions":0,"rows":rows,"subject_level_status":"SUBJECT_LEVEL_HOLDOUT_UNAVAILABLE"}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--dataset-manifest",required=True,type=Path); parser.add_argument("--split",required=True,type=Path); parser.add_argument("--output-root",required=True,type=Path); parser.add_argument("--run-id",required=True); parser.add_argument("--representation",choices=("full","torso"),default="full"); parser.add_argument("--epochs",type=int,default=12); parser.add_argument("--steps-per-epoch",type=int,default=12); options=parser.parse_args()
    random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
    document=json.loads(options.dataset_manifest.read_text()); assignments=json.loads(options.split.read_text())["assignments"]; rows=document["rows"]; train_rows=[row for row in rows if assignments[row["sample_id"]]=="train"]; valid_rows=[row for row in rows if assignments[row["sample_id"]]=="validation"]
    maps={key:{value:index for index,value in enumerate(sorted({str(row[field]) for row in rows}))} for key,field in (("player","player_id"),("team","team_label"),("subject","candidate_subject_id"),("tracklet","tracklet_id"))}
    device=_device(); train_data=AuditedDataset(train_rows,maps,representation=options.representation,augment=True); train_eval=AuditedDataset(train_rows,maps,representation=options.representation,augment=False); valid_data=AuditedDataset(valid_rows,maps,representation=options.representation,augment=False)
    p=min(6,len(maps["player"])); sampler=TeamAwarePKSampler(train_rows,p=p,k=4,steps=options.steps_per_epoch,seed=SEED); train_loader=DataLoader(train_data,batch_sampler=sampler); train_eval_loader=DataLoader(train_eval,batch_size=16,shuffle=False); valid_loader=DataLoader(valid_data,batch_size=16,shuffle=False)
    model=build_model("osnet_ain_x1_0",num_classes=len(maps["player"]),loss="triplet",pretrained=False); weights=torch.load(PRETRAINED_WEIGHTS,map_location="cpu",weights_only=True); state=model.state_dict(); state.update({key.removeprefix("module."):value for key,value in weights.items() if key.removeprefix("module.") in state and state[key.removeprefix("module.")].shape==value.shape}); model.load_state_dict(state)
    for parameter in model.parameters(): parameter.requires_grad=False
    for parameter in list(model.conv5.parameters())+list(model.classifier.parameters()): parameter.requires_grad=True
    model.to(device); optimizer=torch.optim.AdamW((value for value in model.parameters() if value.requires_grad),lr=2e-4,weight_decay=1e-4); output=options.output_root; (output/"checkpoints").mkdir(parents=True,exist_ok=True); history=[]; best=None; started=time.monotonic()
    for epoch in range(1,options.epochs+1):
        model.train(); loss_total=loss_id=loss_tri=accuracy=0.; counts:Counter[str]=Counter(); steps=0; epoch_started=time.monotonic()
        for images,player,team,subject,tracklet,_ in train_loader:
            logits,features=model(images.to(device)); player,team,subject,tracklet=(value.to(device) for value in (player,team,subject,tracklet)); identity=nn.functional.cross_entropy(logits,player); triplet,mined=_triplet(features,player,team,subject,tracklet); loss=identity+triplet; optimizer.zero_grad(); loss.backward(); optimizer.step(); steps+=1; loss_total+=float(loss.detach()); loss_id+=float(identity.detach()); loss_tri+=float(triplet.detach()); accuracy+=float((logits.argmax(1)==player).float().mean()); counts.update(mined)
        validation=_tracklet_validation(model,train_eval_loader,valid_loader,device); row={"epoch":epoch,"train_total_loss":round(loss_total/steps,6),"identity_loss":round(loss_id/steps,6),"triplet_loss":round(loss_tri/steps,6),"classification_accuracy":round(accuracy/steps,6),"within_match_tracklet_validation":validation,"optimizer_steps":steps,"batch": {"P":p,"K":4,"batch_size":p*4,"same_team_fallback_batches":sampler.fallback_batches},**dict(counts),"device":device,"epoch_seconds":round(time.monotonic()-epoch_started,4)}; history.append(row)
        metrics=validation
        if best is None or (metrics["top1_accuracy"],metrics["top3_accuracy"])>(best["within_match_tracklet_validation"]["top1_accuracy"],best["within_match_tracklet_validation"]["top3_accuracy"]):
            checkpoint=output/"checkpoints"/f"{options.run_id}-epoch-{epoch:03d}.pt"; torch.save({"state_dict":model.state_dict(),"maps":maps,"representation":options.representation,"epoch":epoch},checkpoint); best={**row,"checkpoint":str(checkpoint),"checkpoint_sha256":hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
    manifest={"run_id":options.run_id,"architecture":"osnet_ain_x1_0","pretrained_weights_sha256":hashlib.sha256(PRETRAINED_WEIGHTS.read_bytes()).hexdigest(),"best_checkpoint":best,"dataset_manifest_digest":document["digest"],"split_digest":hashlib.sha256(json.dumps(assignments,sort_keys=True).encode()).hexdigest(),"training_config":{"seed":SEED,"epochs":options.epochs,"steps_per_epoch":options.steps_per_epoch,"representation":options.representation,"loss":"cross_entropy_plus_team_aware_batch_hard_triplet"},"mappings":maps,"torch_version":torch.__version__,"device":device,"duration_seconds":round(time.monotonic()-started,4)}
    (output/"training_history.json").write_text(json.dumps(history,indent=2)+"\n"); (output/"best_checkpoint_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n"); (output/"training_summary.json").write_text(json.dumps({"status":"OSNET_DOMAIN_FINETUNING_COMPLETE","best":best,"epochs":options.epochs,"optimizer_steps":options.epochs*options.steps_per_epoch},indent=2)+"\n"); print(json.dumps({"status":"OSNET_DOMAIN_FINETUNING_COMPLETE","best":best,"device":device})); return 0


if __name__=="__main__": raise SystemExit(main())
