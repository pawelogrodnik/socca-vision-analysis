from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch

from app.services.identity_jersey_number_panel_digitnet import PanelDigitNetV1
from app.services.identity_jersey_number_panel_digitnet_training import build_panel_r3_split
from app.services.identity_jersey_number_panel_digitnet_training import build_panel_training_sets
from app.services.identity_jersey_number_panel_digitnet_training import evaluate_panel_digitnet_r3
from app.services.identity_jersey_number_panel_digitnet_training import TRAINING_PROFILES
from app.services.identity_jersey_number_panel_digitnet_training import train_panel_digitnet


def main() -> None:
    parser = argparse.ArgumentParser(description="J8.4 diagnostic PanelDigitNetV1 trainer")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--stage", required=True, choices=("r1", "r2", "r3"))
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--epochs", type=int, default=500)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--profile", choices=tuple(sorted(TRAINING_PROFILES)), default="overfit_baseline")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text())
    if args.stage == "r3":
        split = build_panel_r3_split(dataset)
        result = train_panel_digitnet(split["train"], epochs=args.epochs, device=args.device, profile=args.profile)
        network = PanelDigitNetV1()
        network.load_state_dict(result["checkpoint"]["state_dict"])
        result["report"]["heldout_evaluation"] = evaluate_panel_digitnet_r3(
            network,
            split["holdout"],
            device=result["report"]["device"],
        )
        result["report"]["split"] = {
            "train_sample_count": len(split["train"]),
            "heldout_sample_count": len(split["holdout"]),
            "heldout_episode_ids": split["heldout_episode_ids"],
            "heldout_confirmed_numbers": split["heldout_confirmed_numbers"],
        }
    else:
        sets = build_panel_training_sets(dataset)
        result = train_panel_digitnet(sets[args.stage], epochs=args.epochs, device=args.device, profile=args.profile)
    args.output_root.mkdir(parents=True, exist_ok=True)
    checkpoint_path = args.output_root / f"panel_digitnet_{args.stage}.pt"
    report_path = args.output_root / f"panel_digitnet_{args.stage}_report.json"
    torch.save(result["checkpoint"], checkpoint_path)
    report_path.write_text(json.dumps(result["report"], indent=2) + "\n")
    report = result["report"]
    evaluation = report.get("evaluation") or {}
    summary: dict[str, object] = {
        "stage": args.stage,
        "checkpoint": str(checkpoint_path),
        "report": str(report_path),
        "device": report.get("device"),
        "sample_count": report.get("sample_count"),
        "training_profile": report.get("training_profile"),
        "visual_state_accuracy": evaluation.get("visual_state_accuracy"),
        "readable_recall": evaluation.get("readable_recall"),
        "negative_specificity": evaluation.get("negative_specificity"),
        "exact_sequence_accuracy": evaluation.get("exact_sequence_accuracy"),
    }
    if args.stage == "r3":
        heldout = report.get("heldout_evaluation") or {}
        summary["heldout"] = {
            "sample_count": heldout.get("holdout_sample_count"),
            "episode_count": heldout.get("holdout_episode_count"),
            "crop_exact_sequence_accuracy": heldout.get("crop_exact_sequence_accuracy"),
            "episode_exact_sequence_accuracy": heldout.get("episode_exact_sequence_accuracy"),
            "episode_precision": heldout.get("episode_precision"),
            "episode_recall": heldout.get("episode_recall"),
            "plain_shirt_false_confirmed_reads": heldout.get("plain_shirt_false_confirmed_reads"),
            "plain_shirt_evaluation_status": heldout.get("plain_shirt_evaluation_status"),
            "real10_episode_result": heldout.get("real10_episode_result"),
        }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
