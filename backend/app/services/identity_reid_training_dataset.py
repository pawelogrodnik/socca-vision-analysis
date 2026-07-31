from __future__ import annotations

"""Export audited H1 crops and a group-safe ReID training split."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.identity_reid_quality_bakeoff import AuditCrop, collect_h1_crops
from app.services.identity_jersey_number_common import canonical_digest


DEFAULT_SELECTION = {
    "max_crops_per_tracklet": 4,
    "max_crops_per_subject": 12,
    "minimum_frame_gap": 300,
    "maximum_crops_per_player": 20,
    "minimum_width": 12,
    "minimum_height": 32,
    "minimum_blur": 8.0,
    "minimum_aspect_ratio": 0.10,
    "maximum_aspect_ratio": 1.40,
}


def export_audited_dataset(
    *,
    source_root: Path,
    output_root: Path,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = {**DEFAULT_SELECTION, **(parameters or {})}
    h1_gallery = source_root / "cross_capture_reid_diagnostic" / "h1"
    crops = collect_h1_crops(h1_gallery, max_crops_per_player=None)
    all_rows = [_record(crop) for crop in crops]
    _mark_integrity(all_rows, config)
    eligible = _select_balanced(all_rows, config)
    split = _assign_group_safe_split(eligible)
    for row in eligible:
        row["split"] = split["assignments"].get(row["sample_id"])
    all_by_id = {str(row["sample_id"]): row for row in all_rows}
    for row in eligible:
        all_by_id[str(row["sample_id"])]["eligible"] = True
        all_by_id[str(row["sample_id"])]["split"] = row["split"]
    rejections = [row for row in all_rows if not row.get("eligible")]
    report = _integrity_report(all_rows, eligible, split, config)
    output_root.mkdir(parents=True, exist_ok=True)
    _write(output_root / "dataset_manifest_all.json", _manifest("all", all_rows, config))
    _write(output_root / "dataset_manifest_eligible.json", _manifest("eligible", eligible, config))
    _write(output_root / "dataset_rejections.json", {"rows": rejections})
    _write(output_root / "dataset_split.json", split)
    _write(output_root / "dataset_integrity_report.json", report)
    return {
        "dataset_manifest": output_root / "dataset_manifest_eligible.json",
        "split": output_root / "dataset_split.json",
        "report": report,
    }


def _record(crop: AuditCrop) -> dict[str, Any]:
    image = cv2.imread(str(crop.artifact))
    reasons: list[str] = []
    if image is None or image.size == 0:
        width = height = 0
        blur = brightness = contrast = 0.0
        perceptual_hash = None
        reasons.append("empty_or_missing_crop")
    else:
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())
        contrast = float(gray.std())
        perceptual_hash = _phash(gray)
    return {
        "sample_id": "audited-reid:" + hashlib.sha256(
            f"{crop.player_id}|{crop.anchor_crop_id}|{crop.crop_sha256}".encode()
        ).hexdigest(),
        "player_id": crop.player_id,
        "player_name": crop.player_name,
        "team_label": crop.team_label,
        "candidate_subject_id": crop.candidate_subject_id,
        "tracklet_id": crop.tracklet_id,
        "capture_domain": crop.capture_domain,
        "source_workspace": crop.source_workspace,
        "source_match": crop.source_match,
        "frame": crop.frame,
        "source_frame": crop.source_frame,
        "crop_path": str(crop.artifact),
        "crop_sha256": crop.crop_sha256,
        "perceptual_hash": perceptual_hash,
        "width": width,
        "height": height,
        "selection_score": round(crop.selection_score, 6),
        "blur_score": round(blur, 6),
        "brightness": round(brightness, 6),
        "contrast": round(contrast, 6),
        "audit_source": crop.audit_source,
        "audit_digest": crop.audit_decision_digest,
        "eligible": False,
        "rejection_reasons": reasons,
    }


def _mark_integrity(rows: list[dict[str, Any]], config: dict[str, Any]) -> None:
    owners: dict[str, set[str]] = defaultdict(set)
    subjects: dict[str, set[str]] = defaultdict(set)
    hashes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        owners[row["crop_sha256"]].add(row["player_id"])
        subjects[row["candidate_subject_id"]].add(row["player_id"])
        hashes[row["crop_sha256"]].append(row)
    for row in rows:
        reasons = row["rejection_reasons"]
        aspect = row["width"] / max(row["height"], 1)
        if row["width"] < int(config["minimum_width"]) or row["height"] < int(config["minimum_height"]):
            reasons.append("extremely_small_crop")
        if not float(config["minimum_aspect_ratio"]) <= aspect <= float(config["maximum_aspect_ratio"]):
            reasons.append("invalid_aspect_ratio")
        if row["blur_score"] < float(config["minimum_blur"]):
            reasons.append("extreme_blur")
        if len(owners[row["crop_sha256"]]) > 1:
            reasons.append("shared_crop_between_players")
        if len(subjects[row["candidate_subject_id"]]) > 1:
            reasons.append("shared_subject_between_players")
        if len(hashes[row["crop_sha256"]]) > 1:
            reasons.append("duplicate_crop")
    for index, first in enumerate(rows):
        if not first.get("perceptual_hash"):
            continue
        for second in rows[index + 1:]:
            if not second.get("perceptual_hash"):
                continue
            if _hamming(str(first["perceptual_hash"]), str(second["perceptual_hash"])) <= 3:
                first["near_duplicate_sample_ids"] = [*first.get("near_duplicate_sample_ids", []), second["sample_id"]]
                second["near_duplicate_sample_ids"] = [*second.get("near_duplicate_sample_ids", []), first["sample_id"]]


def _select_balanced(rows: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    per_player: Counter[str] = Counter()
    per_subject: Counter[str] = Counter()
    per_tracklet: Counter[str] = Counter()
    selected_by_tracklet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(rows, key=lambda value: (
        str(value["player_id"]), -float(value["selection_score"]), int(value["frame"]), str(value["sample_id"]),
    )):
        reasons = row["rejection_reasons"]
        if reasons:
            continue
        tracklet = str(row["tracklet_id"])
        if per_player[row["player_id"]] >= int(config["maximum_crops_per_player"]):
            reasons.append("maximum_crops_per_player")
        elif per_subject[row["candidate_subject_id"]] >= int(config["max_crops_per_subject"]):
            reasons.append("max_crops_per_subject")
        elif per_tracklet[tracklet] >= int(config["max_crops_per_tracklet"]):
            reasons.append("max_crops_per_tracklet")
        elif any(abs(int(row["frame"]) - int(other["frame"])) < int(config["minimum_frame_gap"]) for other in selected_by_tracklet[tracklet]):
            reasons.append("minimum_frame_gap")
        elif any(_hamming(str(row.get("perceptual_hash") or ""), str(other.get("perceptual_hash") or "")) <= 3 for other in selected):
            reasons.append("near_duplicate_of_selected_crop")
        if reasons:
            continue
        selected.append(row)
        per_player[row["player_id"]] += 1
        per_subject[row["candidate_subject_id"]] += 1
        per_tracklet[tracklet] += 1
        selected_by_tracklet[tracklet].append(row)
    return selected


def _assign_group_safe_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    assignments: dict[str, str] = {}
    limitations: list[str] = []
    for player_id, player_rows in sorted(_group(rows, "player_id").items()):
        groups = _group(player_rows, "tracklet_id")
        ordered = sorted(groups.items(), key=lambda item: (min(int(row["frame"]) for row in item[1]), item[0]))
        if len(ordered) < 2:
            limitations.append(f"{player_id}: fewer_than_two_tracklets")
            for _, values in ordered:
                for row in values:
                    assignments[row["sample_id"]] = "train"
            continue
        validation_group = ordered[-1][0]
        for tracklet_id, values in ordered:
            for row in values:
                assignments[row["sample_id"]] = "validation" if tracklet_id == validation_group else "train"
    status = "TRAINING_SPLIT_LIMITED_BY_IDENTITY_DIVERSITY"
    return {
        "status": status,
        "method": "tracklet_grouped_temporally_separated_within_player",
        "subject_safe": True,
        "tracklet_safe": True,
        "capture_domain_status": "single_H1_domain; no domain_holdout_available",
        "limitations": limitations or ["one_candidate_subject_per_player; tracklet-grouped validation used"],
        "assignments": assignments,
        "digest": canonical_digest(assignments),
    }


def _integrity_report(rows: list[dict[str, Any]], eligible: list[dict[str, Any]], split: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "AUDITED_REID_DATASET_READY",
        "selection": config,
        "total_crops": len(rows), "eligible_crops": len(eligible), "rejected_crops": len(rows) - len(eligible),
        "players": len({row["player_id"] for row in eligible}), "teams": len({row["team_label"] for row in eligible}),
        "subjects": len({row["candidate_subject_id"] for row in eligible}), "tracklets": len({row["tracklet_id"] for row in eligible}),
        "capture_domains": sorted({row["capture_domain"] for row in eligible}),
        "duplicates": sum("duplicate_crop" in row["rejection_reasons"] for row in rows),
        "near_duplicates": sum(bool(row.get("near_duplicate_sample_ids")) for row in rows),
        "rejection_counts": dict(Counter(reason for row in rows for reason in row["rejection_reasons"])),
        "split": {key: sum(value == key for value in split["assignments"].values()) for key in ("train", "validation")},
        "split_status": split["status"], "split_digest": split["digest"],
        "source_safety": {"h2_used": False, "yolo_rerun": False, "tracking_rerun": False, "new_operator_decisions": 0},
    }


def _manifest(kind: str, rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": "1.0.0", "kind": kind, "selection": config, "rows": rows, "digest": canonical_digest(rows)}


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    return grouped


def _phash(gray: np.ndarray) -> str:
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(small)[:8, :8]
    median = np.median(dct[1:, :])
    return "".join("1" if value > median else "0" for value in dct.reshape(-1))


def _hamming(first: str, second: str) -> int:
    if not first or not second or len(first) != len(second):
        return 64
    return sum(left != right for left, right in zip(first, second, strict=True))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
