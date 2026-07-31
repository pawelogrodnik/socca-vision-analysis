from __future__ import annotations

"""Bounded audited OSNet fine-tuning with same-team batch-hard triplets."""

import argparse
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchreid.reid.models import build_model


PRETRAINED_WEIGHTS = Path("backend/.reid-runtime-lab/osnet-native/weights/osnet_ain_x1_0_msmt17.pth")
PREPROCESSING_VERSION = "osnet-rgb-imagenet-256x128-augmentation-v1"


class AuditedDataset(Dataset[tuple[torch.Tensor, int, int]]):
    def __init__(self, rows: list[dict[str, Any]], labels: dict[str, int], *, representation: str, augment: bool) -> None:
        self.rows, self.labels, self.representation, self.augment = rows, labels, representation, augment

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int, int]:
        row = self.rows[index]
        image = cv2.imread(str(row["crop_path"]))
        if image is None:
            raise ValueError(f"Cannot read audited crop: {row['crop_path']}")
        if self.representation == "torso":
            height, width = image.shape[:2]
            image = image[int(.20 * height):int(.88 * height), int(.12 * width):int(.88 * width)]
        image = cv2.cvtColor(cv2.resize(image, (128, 256), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(image.copy()).permute(2, 0, 1).float() / 255.0
        if self.augment:
            if torch.rand(()) < .5:
                tensor = torch.flip(tensor, dims=[2])
            tensor = torch.clamp(tensor * (0.90 + .20 * torch.rand(())) + (-.05 + .10 * torch.rand(())), 0, 1)
            if torch.rand(()) < .15:
                tensor[:, 96:128, 48:80] = tensor.mean(dim=(1, 2), keepdim=True)
        mean = torch.tensor([.485, .456, .406])[:, None, None]
        std = torch.tensor([.229, .224, .225])[:, None, None]
        return (tensor - mean) / std, self.labels[str(row["player_id"])], index


def _device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _batch_hard_triplet(features: torch.Tensor, labels: torch.Tensor, teams: torch.Tensor) -> tuple[torch.Tensor, dict[str, int]]:
    normalized = nn.functional.normalize(features, dim=1)
    distances = 1.0 - normalized @ normalized.t()
    losses, same_team, different_team, cross_subject, same_subject = [], 0, 0, 0, 0
    for index in range(len(labels)):
        positives = (labels == labels[index]) & (torch.arange(len(labels), device=labels.device) != index)
        negatives = labels != labels[index]
        team_negatives = negatives & (teams == teams[index])
        candidates = team_negatives if bool(team_negatives.any()) else negatives
        if not bool(positives.any()) or not bool(candidates.any()):
            continue
        hardest_positive = distances[index][positives].max()
        hardest_negative = distances[index][candidates].min()
        losses.append(nn.functional.relu(.25 + hardest_positive - hardest_negative))
        same_team += int(bool(team_negatives.any()))
    loss = torch.stack(losses).mean() if losses else features.sum() * 0.0
    return loss, {"same_team_triplets": same_team, "different_team_triplets": different_team, "positive_cross_subject": cross_subject, "positive_same_subject": same_subject}


def _embeddings(model: nn.Module, loader: DataLoader[Any], device: str) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    values, labels = [], []
    with torch.no_grad():
        for images, target, _ in loader:
            result = model(images.to(device))
            values.append(nn.functional.normalize(result, dim=1).cpu().numpy())
            labels.append(target.numpy())
    return np.vstack(values), np.concatenate(labels)


def _validation(model: nn.Module, train_loader: DataLoader[Any], validation_loader: DataLoader[Any], device: str) -> dict[str, Any]:
    train_vectors, train_labels = _embeddings(model, train_loader, device)
    validation_vectors, validation_labels = _embeddings(model, validation_loader, device)
    prototypes = {label: nn.functional.normalize(torch.from_numpy(train_vectors[train_labels == label]).mean(dim=0), dim=0).numpy() for label in sorted(set(train_labels.tolist()))}
    rows = []
    for vector, label in zip(validation_vectors, validation_labels, strict=True):
        ranked = sorted(((candidate, 1.0 - float(np.clip(vector @ prototype, -1.0, 1.0))) for candidate, prototype in prototypes.items()), key=lambda row: row[1])
        rank = next(index for index, (candidate, _) in enumerate(ranked, 1) if candidate == label)
        rows.append({"label": int(label), "truth_rank": int(rank), "top1": rank == 1, "top3": rank <= 3})
    count = len(rows)
    return {"queries": count, "top1_accuracy": round(sum(row["top1"] for row in rows) / count, 4), "top3_accuracy": round(sum(row["top3"] for row in rows) / count, 4), "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-manifest", required=True, type=Path)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--representation", choices=("full", "torso"), default="full")
    parser.add_argument("--epochs", type=int, default=12)
    options = parser.parse_args()
    document = json.loads(options.dataset_manifest.read_text(encoding="utf-8"))
    assignments = json.loads(options.split.read_text(encoding="utf-8"))["assignments"]
    rows = [row for row in document["rows"] if row.get("split") in ("train", "validation")]
    labels = {player_id: index for index, player_id in enumerate(sorted({str(row["player_id"]) for row in rows}))}
    train_rows = [row for row in rows if assignments[row["sample_id"]] == "train"]
    validation_rows = [row for row in rows if assignments[row["sample_id"]] == "validation"]
    device = _device()
    train_data = AuditedDataset(train_rows, labels, representation=options.representation, augment=True)
    train_eval = AuditedDataset(train_rows, labels, representation=options.representation, augment=False)
    validation_data = AuditedDataset(validation_rows, labels, representation=options.representation, augment=False)
    train_loader = DataLoader(train_data, batch_size=len(train_data), shuffle=True)
    train_eval_loader = DataLoader(train_eval, batch_size=len(train_eval), shuffle=False)
    validation_loader = DataLoader(validation_data, batch_size=len(validation_data), shuffle=False)
    model = build_model("osnet_ain_x1_0", num_classes=len(labels), loss="triplet", pretrained=False)
    source = torch.load(PRETRAINED_WEIGHTS, map_location="cpu", weights_only=True)
    state = model.state_dict()
    state.update({key.removeprefix("module."): value for key, value in source.items() if key.removeprefix("module.") in state and state[key.removeprefix("module.")].shape == value.shape})
    model.load_state_dict(state)
    for parameter in model.parameters():
        parameter.requires_grad = False
    for parameter in list(model.conv5.parameters()) + list(model.classifier.parameters()):
        parameter.requires_grad = True
    model.to(device)
    optimizer = torch.optim.AdamW((parameter for parameter in model.parameters() if parameter.requires_grad), lr=2e-4, weight_decay=1e-4)
    output = options.output_root
    checkpoints = output / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    history, best, started = [], None, time.monotonic()
    teams = torch.zeros(len(train_rows), dtype=torch.long, device=device)
    for epoch in range(1, options.epochs + 1):
        epoch_started = time.monotonic()
        model.train(); total = identity = triplet = 0.0; accuracy = 0.0; mining = defaultdict(int)
        for images, target, _ in train_loader:
            images, target = images.to(device), target.to(device)
            logits, features = model(images)
            identity_loss = nn.functional.cross_entropy(logits, target)
            triplet_loss, counts = _batch_hard_triplet(features, target, teams)
            loss = identity_loss + triplet_loss
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            total += float(loss.detach()); identity += float(identity_loss.detach()); triplet += float(triplet_loss.detach())
            accuracy += float((logits.argmax(dim=1) == target).float().mean())
            for key, value in counts.items(): mining[key] += value
        metrics = _validation(model, train_eval_loader, validation_loader, device)
        row = {"epoch": epoch, "train_total_loss": round(total, 6), "identity_loss": round(identity, 6), "triplet_loss": round(triplet, 6), "classification_accuracy": round(accuracy, 6), "validation": metrics, "same_team_triplets": mining["same_team_triplets"], "different_team_triplets": mining["different_team_triplets"], "positive_cross_subject": mining["positive_cross_subject"], "positive_same_subject": mining["positive_same_subject"], "learning_rate": optimizer.param_groups[0]["lr"], "device": device, "epoch_seconds": round(time.monotonic() - epoch_started, 4)}
        history.append(row)
        if best is None or (metrics["top1_accuracy"], metrics["top3_accuracy"]) > (best["validation"]["top1_accuracy"], best["validation"]["top3_accuracy"]):
            checkpoint = checkpoints / f"{options.run_id}-epoch-{epoch:03d}.pt"
            torch.save({"state_dict": model.state_dict(), "labels": labels, "representation": options.representation, "epoch": epoch}, checkpoint)
            best = {**row, "checkpoint": str(checkpoint), "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
    manifest = {"run_id": options.run_id, "architecture": "osnet_ain_x1_0", "pretrained_weights": str(PRETRAINED_WEIGHTS), "pretrained_weights_sha256": hashlib.sha256(PRETRAINED_WEIGHTS.read_bytes()).hexdigest(), "best_checkpoint": best, "dataset_manifest_digest": document["digest"], "train_split_digest": hashlib.sha256(json.dumps(sorted(row["sample_id"] for row in train_rows)).encode()).hexdigest(), "validation_split_digest": hashlib.sha256(json.dumps(sorted(row["sample_id"] for row in validation_rows)).encode()).hexdigest(), "training_config": {"epochs": options.epochs, "representation": options.representation, "loss": "cross_entropy_plus_same_team_batch_hard_triplet", "preprocessing": PREPROCESSING_VERSION}, "torch_version": torch.__version__, "device": device, "duration_seconds": round(time.monotonic() - started, 4)}
    output.mkdir(parents=True, exist_ok=True)
    (output / "training_history.json").write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    (output / "best_checkpoint_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / "training_summary.json").write_text(json.dumps({"status": "OSNET_DOMAIN_FINETUNING_COMPLETE", "best": best, "epochs": len(history), "device": device, "duration_seconds": manifest["duration_seconds"]}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "OSNET_DOMAIN_FINETUNING_COMPLETE", "best": best, "device": device}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
