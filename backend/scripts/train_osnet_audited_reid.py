from __future__ import annotations

"""Bounded, deterministic OSNet fine-tuning on audited H1 crops.

The module deliberately exposes the sampler and validation helpers: their
contracts are unit-tested independently from the expensive OSNet runtime.
"""

import argparse
import hashlib
import json
import random
import time
from collections import Counter, defaultdict
from itertools import combinations
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

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int, int, int, int, int]:
        row = self.rows[index]
        image = cv2.imread(str(row["crop_path"]))
        if image is None:
            raise ValueError(f"Cannot read crop: {row['crop_path']}")
        if self.representation == "torso":
            height, width = image.shape[:2]
            image = image[int(.20 * height):int(.88 * height), int(.12 * width):int(.88 * width)]
        rgb = cv2.cvtColor(cv2.resize(image, (128, 256)), cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(rgb.copy()).permute(2, 0, 1).float() / 255.0
        if self.augment:
            if torch.rand(()) < .5:
                tensor = torch.flip(tensor, [2])
            tensor = torch.clamp(tensor * (.9 + .2 * torch.rand(())) + (-.05 + .1 * torch.rand(())), 0, 1)
        mean = torch.tensor([.485, .456, .406])[:, None, None]
        std = torch.tensor([.229, .224, .225])[:, None, None]
        return (
            (tensor - mean) / std,
            self.maps["player"][str(row["player_id"])],
            self.maps["team"][str(row["team_label"])],
            self.maps["subject"][str(row["candidate_subject_id"])],
            self.maps["tracklet"][str(row["tracklet_id"])],
            index,
        )


class TeamAwarePKSampler(Sampler[list[int]]):
    """P identities × K crops, deterministic per epoch and transparent on reuse."""

    def __init__(self, rows: list[dict[str, Any]], *, p: int, k: int, steps: int, seed: int) -> None:
        self.rows, self.p, self.k, self.steps, self.base_seed = rows, p, k, steps, seed
        self.current_epoch = 0
        self.by_team: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        for index, row in enumerate(rows):
            self.by_team[str(row["team_label"])][str(row["player_id"])].append(index)
        self.teams = sorted(self.by_team)
        self.epoch_metrics: dict[str, Any] = {}

    def set_epoch(self, epoch: int) -> None:
        self.current_epoch = int(epoch)

    def __len__(self) -> int:
        return self.steps

    def __iter__(self) -> Iterator[list[int]]:
        effective_seed = self.base_seed + self.current_epoch
        randomizer = random.Random(effective_seed)
        batches: list[list[int]] = []
        report: Counter[str] = Counter()
        for step in range(self.steps):
            team = self.teams[step % len(self.teams)]
            players = sorted(self.by_team[team])
            chosen = randomizer.sample(players, min(self.p, len(players)))
            if len(chosen) < self.p:
                other = [player for other_team in self.teams if other_team != team for player in sorted(self.by_team[other_team])]
                chosen += randomizer.sample(other, min(self.p - len(chosen), len(other)))
                report["cross_team_fallback_batches"] += 1
            else:
                report["same_team_batches"] += 1
            batch: list[int] = []
            for player in chosen:
                options = self.by_team[team].get(player) or next(values[player] for values in self.by_team.values() if player in values)
                picked = _diverse_pick(options, self.rows, self.k, randomizer, report=report, player_id=player)
                batch.extend(picked)
            batches.append(batch)
            yield batch
        sample_ids = [str(self.rows[index]["sample_id"]) for batch in batches for index in batch]
        report["sample_reuse_count"] = len(sample_ids) - len(set(sample_ids))
        report["exact_sample_repetitions"] = sum(count - 1 for count in Counter(sample_ids).values() if count > 1)
        report["unique_sample_ids_seen"] = len(set(sample_ids))
        report["unique_players_seen"] = len({str(self.rows[index]["player_id"]) for batch in batches for index in batch})
        report["unique_tracklets_seen"] = len({str(self.rows[index]["tracklet_id"]) for batch in batches for index in batch})
        report["unique_subjects_seen"] = len({str(self.rows[index]["candidate_subject_id"]) for batch in batches for index in batch})
        report["unique_batches"] = len({tuple(batch) for batch in batches})
        repeated_by_player = {key.removeprefix("repeated_samples_player:"): value for key, value in report.items() if key.startswith("repeated_samples_player:")}
        self.epoch_metrics = {
            "epoch": self.current_epoch,
            "base_seed": self.base_seed,
            "effective_sampler_seed": effective_seed,
            "batch_digest": _digest([[self.rows[index]["sample_id"] for index in batch] for batch in batches]),
            "identities_without_k_unique_samples": sorted({str(self.rows[index]["player_id"]) for index in range(len(self.rows)) if len({*self.by_team[str(self.rows[index]["team_label"])][str(self.rows[index]["player_id"])]}) < self.k}),
            "repeated_samples_per_player": dict(sorted(repeated_by_player.items())),
            "effective_unique_K": min(self.k, max((len(values) for teams in self.by_team.values() for values in teams.values()), default=0)),
            **dict(report),
        }


def _diverse_pick(options: list[int], rows: list[dict[str, Any]], k: int, randomizer: random.Random, *, report: Counter[str] | None = None, player_id: str | None = None) -> list[int]:
    """Never repeat an image until all unique choices were exhausted."""
    picked: list[int] = []
    available = sorted(set(options), key=lambda index: (int(rows[index]["frame"]), str(rows[index]["sample_id"])))
    while available and len(picked) < k:
        tie_break = {index: randomizer.random() for index in available}
        chosen = min(
            available,
            key=lambda index: (
                sum(rows[index]["candidate_subject_id"] == rows[other]["candidate_subject_id"] for other in picked),
                sum(rows[index]["tracklet_id"] == rows[other]["tracklet_id"] for other in picked),
                -min((abs(int(rows[index]["frame"]) - int(rows[other]["frame"])) for other in picked), default=10**9),
                tie_break[index],
            ),
        )
        picked.append(chosen)
        available.remove(chosen)
    if len(picked) < k:
        if not picked:
            raise ValueError("PK sampler cannot draw an empty identity")
        if report is not None:
            report["repeated_sample_fallbacks"] += k - len(picked)
            if player_id:
                report[f"repeated_samples_player:{player_id}"] += k - len(picked)
        while len(picked) < k:
            # Explicit fallback: the Dataset's augmentation yields an independent view.
            picked.append(picked[(len(picked) - len(options)) % len(picked)])
    return picked


def _device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _triplet(features: torch.Tensor, labels: torch.Tensor, teams: torch.Tensor, subjects: torch.Tensor, tracklets: torch.Tensor, sample_indices: torch.Tensor) -> tuple[torch.Tensor, Counter[str]]:
    vectors = nn.functional.normalize(features, dim=1)
    distances = 1 - vectors @ vectors.t()
    counters: Counter[str] = Counter()
    losses: list[torch.Tensor] = []
    positions = torch.arange(len(labels), device=labels.device)
    for index in range(len(labels)):
        same = (labels == labels[index]) & (positions != index)
        non_identical = same & (sample_indices != sample_indices[index])
        cross_subject = non_identical & (subjects != subjects[index])
        cross_tracklet = non_identical & (tracklets != tracklets[index])
        positives = cross_subject if bool(cross_subject.any()) else (cross_tracklet if bool(cross_tracklet.any()) else non_identical)
        if not bool(positives.any()):
            counters["anchors_without_usable_positive"] += 1
            continue
        negatives = labels != labels[index]
        same_team = negatives & (teams == teams[index])
        choices = same_team if bool(same_team.any()) else negatives
        if not bool(same_team.any()):
            counters["anchors_without_same_team_negative"] += 1
            counters["different_team_fallback_triplets"] += 1
        else:
            counters["same_team_negative_triplets"] += 1
        if bool(cross_subject.any()):
            counters["cross_subject_positives"] += 1
        elif bool(cross_tracklet.any()):
            counters["cross_tracklet_positives"] += 1
        else:
            counters["same_tracklet_positives"] += 1
        losses.append(nn.functional.relu(.25 + distances[index][positives].max() - distances[index][choices].min()))
    return (torch.stack(losses).mean() if losses else features.sum() * 0), counters


def _vectors(model: nn.Module, loader: DataLoader[Any], device: str) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    model.eval()
    values: list[np.ndarray] = []
    metadata: list[tuple[int, int, int, int]] = []
    with torch.no_grad():
        for images, player, team, subject, tracklet, _ in loader:
            values.append(nn.functional.normalize(model(images.to(device)), dim=1).cpu().numpy())
            metadata += list(zip(player.tolist(), team.tolist(), subject.tolist(), tracklet.tolist(), strict=True))
    return np.vstack(values), metadata


def _same_team_separability(vectors: np.ndarray, metadata: list[tuple[int, int, int, int]]) -> dict[str, Any]:
    positive: list[float] = []
    negative: list[float] = []
    for left, right in combinations(range(len(metadata)), 2):
        if metadata[left][1] != metadata[right][1]:
            continue
        similarity = float(np.clip(vectors[left] @ vectors[right], -1, 1))
        (positive if metadata[left][0] == metadata[right][0] else negative).append(similarity)
    if not positive or not negative:
        return {"same_player_similarities": positive, "different_player_same_team_similarities": negative, "different_team_similarities": [], "same_team_roc_auc": None, "same_team_eer": None, "same_team_distribution_overlap": None}
    wins = sum((left > right) + .5 * (left == right) for left in positive for right in negative)
    auc = wins / (len(positive) * len(negative))
    thresholds = sorted(set(positive + negative))
    eer = min((abs(sum(value < threshold for value in positive) / len(positive) - sum(value >= threshold for value in negative) / len(negative)), threshold) for threshold in thresholds)[0]
    lower, upper = max(min(positive), min(negative)), min(max(positive), max(negative))
    overlap = max(0.0, upper - lower) / max(max(positive + negative) - min(positive + negative), 1e-9)
    return {"same_player_similarities": [round(value, 6) for value in positive], "different_player_same_team_similarities": [round(value, 6) for value in negative], "different_team_similarities": [], "same_team_roc_auc": round(float(auc), 6), "same_team_eer": round(float(eer), 6), "same_team_distribution_overlap": round(float(overlap), 6)}


def _tracklet_validation(model: nn.Module, train_loader: DataLoader[Any], validation_loader: DataLoader[Any], device: str) -> dict[str, Any]:
    train, train_meta = _vectors(model, train_loader, device)
    valid, valid_meta = _vectors(model, validation_loader, device)
    prototypes: dict[int, np.ndarray] = {}
    prototype_team: dict[int, int] = {}
    for player in sorted({row[0] for row in train_meta}):
        indices = [index for index, row in enumerate(train_meta) if row[0] == player]
        prototypes[player] = nn.functional.normalize(torch.from_numpy(train[indices]).mean(0), dim=0).numpy()
        prototype_team[player] = train_meta[indices[0]][1]
    groups: dict[int, list[int]] = defaultdict(list)
    for index, row in enumerate(valid_meta):
        groups[row[3]].append(index)
    query_rows: list[dict[str, Any]] = []
    for tracklet, indices in sorted(groups.items()):
        player, team, subject, _ = valid_meta[indices[0]]
        vector = nn.functional.normalize(torch.from_numpy(valid[indices]).mean(0), dim=0).numpy()
        rejected = sorted(candidate for candidate, candidate_team in prototype_team.items() if candidate_team != team)
        ranking = sorted(((candidate, 1 - float(np.clip(vector @ prototype, -1, 1))) for candidate, prototype in prototypes.items() if prototype_team[candidate] == team), key=lambda row: (row[1], row[0]))
        ids = [candidate for candidate, _ in ranking]
        rank = ids.index(player) + 1 if player in ids else None
        query_rows.append({"tracklet_index": tracklet, "player_index": player, "team_index": team, "subject_index": subject, "query_team": team, "candidate_count": len(ranking), "ranked_player_ids": ids, "ranked_distances": [round(distance, 6) for _, distance in ranking], "cross_team_candidates_rejected": rejected, "truth_rank": rank, "top1": rank == 1, "top3": bool(rank and rank <= 3), "top1_margin": round(ranking[1][1] - ranking[0][1], 6) if len(ranking) > 1 else None})
    count = len(query_rows)
    separability = _same_team_separability(np.vstack([train, valid]), train_meta + valid_meta)
    return {"protocol": "WITHIN_MATCH_TRACKLET_HOLDOUT", "queries": count, "top1_accuracy": round(sum(row["top1"] for row in query_rows) / count, 4) if count else None, "top3_accuracy": round(sum(row["top3"] for row in query_rows) / count, 4) if count else None, "coverage": round(sum(row["truth_rank"] is not None for row in query_rows) / count, 4) if count else None, "abstentions": sum(row["truth_rank"] is None for row in query_rows), "rows": query_rows, "same_team_separability": separability, "subject_level_status": "SUBJECT_LEVEL_HOLDOUT_UNAVAILABLE"}


def configure_stage(model: nn.Module, stage: str) -> list[dict[str, Any]]:
    for parameter in model.parameters():
        parameter.requires_grad = False
    groups: list[dict[str, Any]] = []
    if stage == "stage_1":
        model.conv5.requires_grad_(True)
        model.classifier.requires_grad_(True)
        groups = [{"params": model.classifier.parameters(), "lr": 1e-4, "name": "classifier"}, {"params": model.conv5.parameters(), "lr": 5e-5, "name": "conv5"}]
    elif stage == "stage_2":
        model.conv4.requires_grad_(True)
        model.conv5.requires_grad_(True)
        model.classifier.requires_grad_(True)
        groups = [{"params": model.classifier.parameters(), "lr": 1e-4, "name": "classifier"}, {"params": model.conv5.parameters(), "lr": 5e-5, "name": "conv5"}, {"params": model.conv4.parameters(), "lr": 1e-5, "name": "conv4"}]
    else:
        raise ValueError(f"Unknown stage: {stage}")
    return groups


def _better(current: dict[str, Any], best: dict[str, Any] | None) -> bool:
    if best is None:
        return True
    return (float(current["top1_accuracy"] or -1), float(current["top3_accuracy"] or -1), float(current["same_team_separability"]["same_team_roc_auc"] or -1)) > (float(best["top1_accuracy"] or -1), float(best["top3_accuracy"] or -1), float(best["same_team_separability"]["same_team_roc_auc"] or -1))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-manifest", required=True, type=Path)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--representation", choices=("full", "torso"), default="full")
    parser.add_argument("--stage1-epochs", type=int, default=5)
    parser.add_argument("--stage2-epochs", type=int, default=4)
    parser.add_argument("--steps-per-epoch", type=int, default=10)
    parser.add_argument("--patience", type=int, default=3)
    parser.add_argument("--minimum-delta", type=float, default=.001)
    options = parser.parse_args()
    random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
    document = json.loads(options.dataset_manifest.read_text())
    assignments = json.loads(options.split.read_text())["assignments"]
    rows = document["rows"]
    train_rows = [row for row in rows if assignments[row["sample_id"]] == "train"]
    valid_rows = [row for row in rows if assignments[row["sample_id"]] == "validation"]
    maps = {key: {value: index for index, value in enumerate(sorted({str(row[field]) for row in rows}))} for key, field in (("player", "player_id"), ("team", "team_label"), ("subject", "candidate_subject_id"), ("tracklet", "tracklet_id"))}
    device = _device()
    train_data = AuditedDataset(train_rows, maps, representation=options.representation, augment=True)
    train_eval = AuditedDataset(train_rows, maps, representation=options.representation, augment=False)
    valid_data = AuditedDataset(valid_rows, maps, representation=options.representation, augment=False)
    p = min(6, len(maps["player"]))
    sampler = TeamAwarePKSampler(train_rows, p=p, k=4, steps=options.steps_per_epoch, seed=SEED)
    train_loader = DataLoader(train_data, batch_sampler=sampler)
    train_eval_loader = DataLoader(train_eval, batch_size=16, shuffle=False)
    valid_loader = DataLoader(valid_data, batch_size=16, shuffle=False)
    model = build_model("osnet_ain_x1_0", num_classes=len(maps["player"]), loss="triplet", pretrained=False)
    weights = torch.load(PRETRAINED_WEIGHTS, map_location="cpu", weights_only=True)
    state = model.state_dict(); state.update({key.removeprefix("module."): value for key, value in weights.items() if key.removeprefix("module.") in state and state[key.removeprefix("module.")].shape == value.shape}); model.load_state_dict(state); model.to(device)
    output = options.output_root; (output / "checkpoints").mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []; best: dict[str, Any] | None = None; global_epoch = 0; stop_reasons: list[str] = []; started = time.monotonic()
    for stage, requested_epochs in (("stage_1", options.stage1_epochs), ("stage_2", options.stage2_epochs)):
        stale = 0
        optimizer = torch.optim.AdamW(configure_stage(model, stage), weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="max", factor=.5, patience=1, threshold=options.minimum_delta)
        for _ in range(requested_epochs):
            global_epoch += 1; sampler.set_epoch(global_epoch); epoch_started = time.monotonic(); model.train(); total = identity_total = triplet_total = accuracy = 0.; mined: Counter[str] = Counter(); steps = 0
            for images, player, team, subject, tracklet, sample_indices in train_loader:
                logits, features = model(images.to(device)); player, team, subject, tracklet, sample_indices = (value.to(device) for value in (player, team, subject, tracklet, sample_indices))
                identity = nn.functional.cross_entropy(logits, player); triplet, counters = _triplet(features, player, team, subject, tracklet, sample_indices); loss = identity + triplet
                optimizer.zero_grad(); loss.backward(); optimizer.step(); steps += 1; total += float(loss.detach()); identity_total += float(identity.detach()); triplet_total += float(triplet.detach()); accuracy += float((logits.argmax(1) == player).float().mean()); mined.update(counters)
            validation = _tracklet_validation(model, train_eval_loader, valid_loader, device)
            scheduler.step(float(validation["top1_accuracy"] or 0.0))
            row = {"stage": stage, "epoch": global_epoch, "optimizer_steps": steps, "unique_batch_count": sampler.epoch_metrics.get("unique_batches"), "train_total_loss": round(total / max(steps, 1), 6), "identity_loss": round(identity_total / max(steps, 1), 6), "triplet_loss": round(triplet_total / max(steps, 1), 6), "classification_accuracy": round(accuracy / max(steps, 1), 6), "within_match_tracklet_validation": validation, "batch_diversity": sampler.epoch_metrics, "same_team_negative_triplets": mined["same_team_negative_triplets"], "different_team_fallback_triplets": mined["different_team_fallback_triplets"], "cross_subject_positives": mined["cross_subject_positives"], "cross_tracklet_positives": mined["cross_tracklet_positives"], "same_tracklet_positives": mined["same_tracklet_positives"], "anchors_without_usable_positive": mined["anchors_without_usable_positive"], "anchors_without_same_team_negatives": mined["anchors_without_same_team_negative"], "learning_rates": {group.get("name", str(index)): group["lr"] for index, group in enumerate(optimizer.param_groups)}, "device": device, "duration_seconds": round(time.monotonic() - epoch_started, 4)}
            history.append(row)
            if _better(validation, best["within_match_tracklet_validation"] if best else None):
                checkpoint = output / "checkpoints" / f"{options.run_id}-epoch-{global_epoch:03d}.pt"; torch.save({"state_dict": model.state_dict(), "maps": maps, "representation": options.representation, "epoch": global_epoch, "stage": stage}, checkpoint)
                best = {**row, "checkpoint": str(checkpoint), "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}; stale = 0
            else:
                stale += 1
                if stale >= options.patience:
                    stop_reasons.append(f"{stage}:early_stopping_no_tracklet_top1_gain_for_{options.patience}_epochs"); break
    assert best is not None
    final_validation = history[-1]["within_match_tracklet_validation"]
    manifest = {"run_id": options.run_id, "architecture": "osnet_ain_x1_0", "pretrained_weights_sha256": hashlib.sha256(PRETRAINED_WEIGHTS.read_bytes()).hexdigest(), "best_checkpoint": best, "dataset_manifest_digest": document["digest"], "split_digest": _digest(assignments), "training_config": {"seed": SEED, "stage1_epochs": options.stage1_epochs, "stage2_epochs": options.stage2_epochs, "steps_per_epoch": options.steps_per_epoch, "representation": options.representation, "loss": "cross_entropy_plus_team_aware_batch_hard_triplet", "stage_1": {"trainable": ["classifier", "conv5"], "learning_rates": {"classifier": 1e-4, "conv5": 5e-5}}, "stage_2": {"trainable": ["classifier", "conv5", "conv4"], "learning_rates": {"classifier": 1e-4, "conv5": 5e-5, "conv4": 1e-5}}, "early_stopping": {"patience": options.patience, "minimum_delta": options.minimum_delta, "primary_metric": "within_match_tracklet_top1"}}, "mappings": maps, "torch_version": torch.__version__, "device": device, "duration_seconds": round(time.monotonic() - started, 4)}
    best_validation = best["within_match_tracklet_validation"]; regression = round(float(best_validation["top1_accuracy"] or 0) - float(final_validation["top1_accuracy"] or 0), 4)
    overfit = best["classification_accuracy"] >= .8 and regression > options.minimum_delta
    summary = {"status": "DOMAIN_FINETUNING_OVERFIT" if overfit else "OSNET_DOMAIN_FINETUNING_COMPLETE", "best": best, "epochs_completed": len(history), "optimizer_steps": sum(row["optimizer_steps"] for row in history), "best_epoch": best["epoch"], "stopped_early": bool(stop_reasons), "stop_reason": ";".join(stop_reasons) if stop_reasons else "completed_stage_schedule", "train_validation_gap": round(float(best["classification_accuracy"]) - float(best_validation["top1_accuracy"] or 0), 4), "validation_regression_after_best": regression}
    (output / "training_history.json").write_text(json.dumps(history, indent=2) + "\n")
    (output / "best_checkpoint_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "training_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"status": summary["status"], "best_epoch": best["epoch"], "device": device}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
