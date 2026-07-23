from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import cv2
import torch

from app.services.identity_jersey_number_sequence import (
    load_sequence_checkpoint,
    predict_jersey_number_sequence,
)
from app.services.identity_jersey_number_sequence_contract import (
    build_sequence_training_eligibility_report,
)
from app.services.identity_jersey_number_visibility_episodes import (
    attach_jersey_visibility_episode_ids,
    partition_jersey_visibility_episodes,
)


EVALUATION_SPLITS = frozenset({"validation", "heldout"})


def evaluate_jersey_number_sequence_shadow(
    dataset_manifest: dict[str, Any],
    checkpoint_artifact: dict[str, Any] | str | Path,
    *,
    device: str | torch.device = "cpu",
) -> dict[str, Any]:
    build_sequence_training_eligibility_report(dataset_manifest)
    checkpoint = _load_checkpoint(checkpoint_artifact)
    model = load_sequence_checkpoint(checkpoint, device=device)
    crops = [
        _predict_crop(model, row)
        for row in dataset_manifest.get("samples") or []
        if isinstance(row, dict) and row.get("split") in EVALUATION_SPLITS
    ]
    attached = attach_jersey_visibility_episode_ids(crops)
    episodes = [_fuse_episode(rows) for rows in partition_jersey_visibility_episodes(attached)]
    return {
        "mode": "shadow_only_raw_sequence_evaluation",
        "checkpoint_digest": checkpoint.get("checkpoint_digest"),
        "gates": {
            "production_eligible": False,
            "candidate_eligible": False,
            "activation_eligible": False,
            "reason_codes": ["single_match_diagnostic_only", "uncalibrated_raw_sequence"],
        },
        "crop_metrics": _metrics(crops, "crop"),
        "episode_metrics": _metrics(episodes, "visibility_episode"),
        "crops": attached,
        "episodes": episodes,
    }


def _load_checkpoint(artifact: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(artifact, dict):
        return artifact
    checkpoint = torch.load(Path(artifact), map_location="cpu", weights_only=True)
    if not isinstance(checkpoint, dict):
        raise ValueError("sequence checkpoint must be an object")
    return checkpoint


def _predict_crop(model: Any, sample: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(sample.get("artifact_root") or "")) / str(sample.get("artifact") or "")
    image = cv2.imread(str(path)) if path.is_file() else None
    prediction = predict_jersey_number_sequence(
        model,
        image,
        artifact_kind=str(sample.get("artifact_kind") or "torso_crop"),
        bbox_xyxy=sample.get("bbox_xyxy"),
    )
    expected_number = str(sample.get("number")) if sample.get("number") is not None else None
    return {
        "sample_key": sample.get("sample_key"),
        "source_match_key": sample.get("source_match_key"),
        "source_video_key": sample.get("source_video_key"),
        "candidate_subject_id": sample.get("candidate_subject_id"),
        "tracklet_id": sample.get("tracklet_id"),
        "team_id": sample.get("team_id"),
        "team_label": sample.get("team_label"),
        "visibility_episode_id": sample.get("visibility_episode_id"),
        "frame": sample.get("frame"),
        "split": sample.get("split"),
        "expected_state": sample.get("label_state"),
        "expected_number": expected_number,
        "raw_prediction": prediction,
        "raw_digit_string": prediction["raw_digit_string"],
        "raw_sequence_confidence": prediction["raw_sequence_confidence"],
        "accepted": False,
        "accepted_identity_evidence": None,
        "reason_codes": prediction["reason_codes"],
    }


def _fuse_episode(rows: list[dict[str, Any]]) -> dict[str, Any]:
    votes: dict[str, float] = defaultdict(float)
    for row in rows:
        number = row.get("raw_digit_string")
        if number is not None:
            votes[str(number)] += float(row.get("raw_sequence_confidence") or 0.0)
    ranked = sorted(votes, key=lambda value: (votes[value], value), reverse=True)
    raw_digit_string = ranked[0] if ranked else None
    expected = Counter(
        str(row["expected_number"])
        for row in rows
        if row.get("expected_state") == "number_confirmed" and row.get("expected_number") is not None
    )
    return {
        "visibility_episode_id": rows[0]["visibility_episode_id"],
        "source_match_key": rows[0].get("source_match_key"),
        "source_video_key": rows[0].get("source_video_key"),
        "candidate_subject_id": rows[0].get("candidate_subject_id"),
        "tracklet_id": rows[0].get("tracklet_id"),
        "team_id": rows[0].get("team_id"),
        "team_label": rows[0].get("team_label"),
        "start_frame": min(int(row["frame"]) for row in rows),
        "end_frame": max(int(row["frame"]) for row in rows),
        "observations": len(rows),
        "expected_number": expected.most_common(1)[0][0] if expected else None,
        "raw_digit_string": raw_digit_string,
        "accepted": False,
        "accepted_identity_evidence": None,
        "reason_codes": ["diagnostic_single_match_uncalibrated"],
    }


def _metrics(rows: list[dict[str, Any]], unit: str) -> dict[str, Any]:
    expected = sum(row.get("expected_number") is not None for row in rows)
    raw_reads = sum(row.get("raw_digit_string") is not None for row in rows)
    correct = sum(
        row.get("raw_digit_string") == row.get("expected_number") and row.get("expected_number") is not None
        for row in rows
    )
    return {
        "unit": unit,
        "reviewed": len(rows),
        "expected_readable": expected,
        "raw_reads": raw_reads,
        "raw_correct": correct,
        "accepted_reads": 0,
    }
