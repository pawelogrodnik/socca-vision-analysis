from __future__ import annotations

"""Offline, leakage-safe quality audit helpers for person-ReID experiments."""

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

import cv2
import numpy as np


OSNET_SITE_PACKAGES = (
    Path(__file__).resolve().parents[2]
    / ".reid-runtime-lab"
    / "osnet-native"
    / "lib"
    / "python3.11"
    / "site-packages"
)
OSNET_WEIGHTS = (
    Path(__file__).resolve().parents[2]
    / ".reid-runtime-lab"
    / "osnet-native"
    / "weights"
    / "osnet_ain_x1_0_msmt17.pth"
)


@dataclass(frozen=True)
class AuditCrop:
    player_id: str
    player_name: str
    team_label: str
    anchor_crop_id: str
    frame: int
    bbox_xyxy: tuple[float, float, float, float]
    artifact: Path
    selection_score: float
    candidate_subject_id: str
    tracklet_id: str
    capture_domain: str
    source_workspace: str
    source_match: str
    source_frame: int
    audit_source: str
    audit_decision_digest: str
    crop_sha256: str


class OsnetAinEmbedder:
    """Uses an experiment-only worker; no dependency enters the app venv."""

    model_name = "osnet_ain_x1_0_msmt17"
    model_version = "torchreid-0.2.5-msmt17-official-8a07e8da3894"
    embedding_dimension = 512
    runtime_name = "isolated_torchreid_native"

    def __init__(self, *, python: Path) -> None:
        self.python = python
        if not OSNET_SITE_PACKAGES.is_dir() or not OSNET_WEIGHTS.is_file():
            raise FileNotFoundError("OSNet isolated runtime or official weights unavailable")
        self.weights_sha256 = _file_sha256(OSNET_WEIGHTS)
        self.architecture = "osnet_ain_x1_0"
        self.torch_version = "2.4.1"

    def embed_batch(self, crops: list[np.ndarray]) -> np.ndarray:
        if not crops:
            return np.empty((0, self.embedding_dimension), dtype=np.float32)
        with tempfile.TemporaryDirectory(prefix="orlik-osnet-") as directory:
            root = Path(directory)
            inputs = root / "inputs.npz"
            outputs = root / "outputs.npy"
            response = root / "response.json"
            np.savez_compressed(
                inputs,
                images=np.stack(
                    [cv2.resize(crop, (128, 256), interpolation=cv2.INTER_LINEAR) for crop in crops]
                ).astype(np.uint8),
            )
            environment = dict(os.environ)
            previous = environment.get("PYTHONPATH", "")
            environment["PYTHONPATH"] = str(OSNET_SITE_PACKAGES) + (
                os.pathsep + previous if previous else ""
            )
            command = [
                str(self.python),
                str(Path(__file__).resolve().parents[2] / "scripts" / "embed_osnet_reid.py"),
                "--inputs", str(inputs), "--outputs", str(outputs),
                "--response", str(response), "--weights", str(OSNET_WEIGHTS),
            ]
            completed = subprocess.run(
                command, check=False, text=True, capture_output=True,
                env=environment, timeout=300,
            )
            if completed.returncode != 0 or not outputs.is_file():
                raise RuntimeError("OSNet worker failed: " + (completed.stderr or completed.stdout))
            try:
                handshake = json.loads(response.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError("OSNet worker did not return a valid handshake") from error
            expected_sha = _file_sha256(OSNET_WEIGHTS)
            if (
                handshake.get("schema_version") != "1.0.0"
                or handshake.get("status") != "ok"
                or handshake.get("model_name") != self.model_name
                or handshake.get("weights_sha256") != expected_sha
                or int(handshake.get("embedding_dimension") or 0) != self.embedding_dimension
                or int(handshake.get("input_count") or 0) != len(crops)
                or not handshake.get("finite")
                or float(handshake.get("norm_min") or 0) < 0.99
                or float(handshake.get("norm_max") or 0) > 1.01
            ):
                raise RuntimeError("OSNet worker handshake violates the embedding contract")
            values = np.asarray(np.load(outputs, allow_pickle=False), dtype=np.float32)
            if values.shape != (len(crops), self.embedding_dimension):
                raise ValueError("OSNet worker returned unexpected embedding shape")
            return values


def collect_h1_crops(
    h1_root: Path,
    *,
    max_crops_per_player: int | None = None,
) -> list[AuditCrop]:
    gallery = _load(h1_root / "identity_approved_appearance_gallery.json")
    audit_digest = _file_sha256(
        h1_root.parent.parent / "h1_workspace" / "identity_operator_seeds.json"
    )
    crops: list[AuditCrop] = []
    for player in gallery.get("players") or []:
        for domain in player.get("capture_domains") or []:
            for crop in domain.get("crops") or []:
                bbox = crop.get("bbox_xyxy") or []
                if len(bbox) != 4 or not crop.get("artifact"):
                    continue
                artifact = h1_root / str(crop["artifact"])
                if not artifact.is_file():
                    continue
                crops.append(AuditCrop(
                    player_id=str(player.get("player_id")),
                    player_name=str(player.get("player_name") or player.get("player_id")),
                    team_label=str(player.get("team_label") or "U"),
                    anchor_crop_id=str(crop.get("anchor_crop_id") or crop.get("artifact")),
                    frame=int(crop.get("frame") or 0),
                    bbox_xyxy=tuple(float(value) for value in bbox),
                    artifact=artifact,
                    selection_score=float(crop.get("selection_score") or 0.0),
                    candidate_subject_id=str(crop.get("candidate_subject_id") or ""),
                    tracklet_id=str(crop.get("tracklet_id") or ""),
                    capture_domain=str(crop.get("capture_domain") or domain.get("capture_domain") or "H1"),
                    source_workspace="h1_workspace",
                    source_match=str(crop.get("source_match_key") or "benchmark-product-flow-20260730-v4-h1"),
                    source_frame=int(crop.get("frame") or 0),
                    audit_source="h1_operator_seed_and_approved_gallery",
                    audit_decision_digest=audit_digest,
                    crop_sha256=_file_sha256(artifact),
                ))
    by_player: dict[str, list[AuditCrop]] = defaultdict(list)
    for crop in crops:
        by_player[crop.player_id].append(crop)
    selected = []
    for player_crops in by_player.values():
        ordered = sorted(
            player_crops,
            key=lambda row: (-row.selection_score, row.frame, row.anchor_crop_id),
        )
        selected.extend(
            ordered if max_crops_per_player is None else ordered[:max_crops_per_player]
        )
    return sorted(selected, key=lambda row: (row.player_id, row.frame, row.anchor_crop_id))


def crop_variants(crops: list[AuditCrop], *, video_path: Path) -> dict[str, list[np.ndarray]]:
    """Build explicit crop transforms from frozen H1 frame/bbox observations."""
    capture = cv2.VideoCapture(str(video_path))
    variants: dict[str, list[np.ndarray]] = {name: [] for name in (
        "A_original_bbox", "B_padded_bbox", "C_torso", "D_upper_body", "E_background_reduced",
    )}
    try:
        for crop in crops:
            original = cv2.imread(str(crop.artifact))
            if original is None or original.size == 0:
                raise ValueError(f"Missing frozen H1 crop: {crop.artifact}")
            capture.set(cv2.CAP_PROP_POS_FRAMES, crop.frame)
            ok, frame = capture.read()
            if not ok:
                raise ValueError(f"Unable to read H1 video frame {crop.frame}")
            x1, y1, x2, y2 = crop.bbox_xyxy
            width, height = x2 - x1, y2 - y1
            padded = _slice(frame, x1 - .12 * width, y1 - .08 * height, x2 + .12 * width, y2 + .10 * height)
            torso = _slice(original, .15 * original.shape[1], .22 * original.shape[0], .85 * original.shape[1], .86 * original.shape[0])
            upper = _slice(original, .10 * original.shape[1], 0, .90 * original.shape[1], .66 * original.shape[0])
            reduced = _background_reduced(original)
            variants["A_original_bbox"].append(original)
            variants["B_padded_bbox"].append(padded)
            variants["C_torso"].append(torso)
            variants["D_upper_body"].append(upper)
            variants["E_background_reduced"].append(reduced)
    finally:
        capture.release()
    return variants


def embed_variant(
    images: list[np.ndarray], *, embedder: Any, cache_path: Path,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Content-addressed cache, intentionally separate for every model/crop variant."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    namespace = cache_namespace(embedder=embedder, crop_variant=cache_path.stem)
    cache = _load(cache_path) if cache_path.exists() else {"entries": {}, "namespace": namespace}
    if cache.get("namespace") != namespace:
        cache = {"entries": {}, "namespace": namespace}
    entries = dict(cache.get("entries") or {})
    vectors: list[np.ndarray | None] = [None] * len(images)
    pending: list[tuple[int, str, np.ndarray]] = []
    hits = 0
    for index, image in enumerate(images):
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        value = entries.get(digest)
        if isinstance(value, list) and len(value) == int(embedder.embedding_dimension):
            vectors[index] = _normalise(np.asarray(value, dtype=np.float32))
            hits += 1
        else:
            pending.append((index, digest, image))
    if pending:
        if hasattr(embedder, "embed_batch"):
            produced = embedder.embed_batch([row[2] for row in pending])
        else:
            produced = np.stack([embedder.embed(row[2]) for row in pending])
        for (index, digest, _), vector in zip(pending, produced, strict=True):
            normalised = _normalise(np.asarray(vector, dtype=np.float32))
            vectors[index] = normalised
            entries[digest] = [float(value) for value in normalised]
    cache_path.write_text(json.dumps({"entries": entries, "namespace": namespace}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return np.stack([value for value in vectors if value is not None]), {
        "cache_path": str(cache_path), "hits": hits, "misses": len(pending), "entries": len(entries), "namespace": namespace,
    }


def evaluate_crop_loo(
    crops: list[AuditCrop], vectors: np.ndarray, *, prototype: str, ranking: str,
) -> dict[str, Any]:
    if len(crops) != len(vectors):
        raise ValueError("Crop/vector count mismatch")
    by_player: dict[str, list[int]] = defaultdict(list)
    by_team: dict[str, list[str]] = defaultdict(list)
    for index, crop in enumerate(crops):
        by_player[crop.player_id].append(index)
        if crop.player_id not in by_team[crop.team_label]:
            by_team[crop.team_label].append(crop.player_id)
    rows = []
    for index, crop in enumerate(crops):
        candidate_scores: list[tuple[str, float]] = []
        for player_id in by_team[crop.team_label]:
            reference_indices = [i for i in by_player[player_id] if not (player_id == crop.player_id and i == index)]
            references = [vectors[i] for i in reference_indices]
            if not references:
                continue
            candidate_scores.append((player_id, _score(
                vectors[index], references, prototype, ranking,
                quality_weights=[_quality_weight(crops[i]) for i in reference_indices],
            )))
        ordered = sorted(candidate_scores, key=lambda row: (row[1], row[0]))
        rank = next((position for position, (player_id, _) in enumerate(ordered, 1) if player_id == crop.player_id), None)
        rows.append({
            "player_id": crop.player_id, "player_name": crop.player_name,
            "anchor_crop_id": crop.anchor_crop_id, "frame": crop.frame,
            "truth_rank": rank, "top1_correct": rank == 1,
            "top3_correct": bool(rank is not None and rank <= 3),
            "ranked_player_ids": [player_id for player_id, _ in ordered[:3]],
            "ranked_distances": [round(distance, 6) for _, distance in ordered[:3]],
        })
    return _metrics(rows, method="leave_one_crop_out_same_team")


def select_h1_winner(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Select without H2: subject LOO is unavailable with one H1 subject/player."""
    ordered = sorted(candidates, key=lambda row: (
        -float(row["evaluation"]["top1_accuracy"] or 0),
        -float(row["evaluation"]["top3_accuracy"] or 0),
        str(row["model"]["model_name"]), str(row["crop_variant"]),
        str(row["prototype"]), str(row["ranking"]),
    ))
    winner = ordered[0]
    return {
        "status": "selected_h1_only",
        "primary_protocol": "leave_one_confirmed_subject_out",
        "primary_protocol_status": "not_applicable_one_reference_subject_per_player",
        "deterministic_fallback": "leave_one_crop_out_same_team",
        "winner": winner,
        "candidate_count": len(candidates),
        "h2_was_available_to_selector": False,
    }


def image_quality_records(crops: list[AuditCrop], images: list[np.ndarray]) -> list[dict[str, Any]]:
    rows = []
    for crop, image in zip(crops, images, strict=True):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        rows.append({
            "player_id": crop.player_id, "player_name": crop.player_name,
            "anchor_crop_id": crop.anchor_crop_id, "frame": crop.frame,
            "width": int(image.shape[1]), "height": int(image.shape[0]),
            "area": int(image.shape[0] * image.shape[1]),
            "aspect_ratio": round(float(image.shape[1] / image.shape[0]), 4),
            "blur_laplacian_variance": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 4),
            "brightness": round(float(gray.mean()), 4), "contrast": round(float(gray.std()), 4),
            "near_black_fraction": round(float((gray <= 5).mean()), 4),
            "near_white_fraction": round(float((gray >= 250).mean()), 4),
            "crop_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
        })
    return rows


def embedding_health(crops: list[AuditCrop], vectors: np.ndarray) -> dict[str, Any]:
    similarities = np.clip(vectors @ vectors.T, -1.0, 1.0)
    same, same_team_different, different_team = [], [], []
    for first in range(len(crops)):
        for second in range(first + 1, len(crops)):
            if crops[first].player_id == crops[second].player_id:
                target = same
            elif crops[first].team_label == crops[second].team_label:
                target = same_team_different
            else:
                target = different_team
            target.append(float(similarities[first, second]))
    return {
        "embedding_dimension": int(vectors.shape[1]),
        "finite": bool(np.isfinite(vectors).all()),
        "norm_min": round(float(np.linalg.norm(vectors, axis=1).min()), 6),
        "norm_max": round(float(np.linalg.norm(vectors, axis=1).max()), 6),
        "same_person_similarity": _distribution(same),
        "different_player_same_team_similarity": _distribution(same_team_different),
        "different_team_similarity_diagnostic": _distribution(different_team),
        "same_team_roc_auc": round(_auc(same, same_team_different), 6),
        "same_team_equal_error_rate": _eer(same, same_team_different),
        "same_team_distance_overlap": round(_overlap(same, same_team_different), 6),
        "near_duplicate_pairs": int(sum(value >= .9999 for value in same + same_team_different + different_team)),
        "effective_rank": round(float(_effective_rank(vectors)), 4),
    }


def write_montage(crops: list[AuditCrop], images: list[np.ndarray], evaluation: dict[str, Any], output: Path) -> None:
    """Visual H1 montage: query, correct references and model top-1 are inspectable."""
    lookup = {crop.anchor_crop_id: index for index, crop in enumerate(crops)}
    tiles: list[np.ndarray] = []
    for row in evaluation.get("rows") or []:
        index = lookup.get(str(row.get("anchor_crop_id")))
        if index is None:
            continue
        tile = cv2.resize(images[index], (128, 192), interpolation=cv2.INTER_AREA)
        canvas = np.full((230, 128, 3), 20, dtype=np.uint8)
        canvas[:192] = tile
        title = f"{row['player_name']} r{row['truth_rank']}"
        cv2.putText(canvas, title[:20], (4, 211), cv2.FONT_HERSHEY_SIMPLEX, .38, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(canvas)
    if not tiles:
        return
    columns = 7
    padding = np.full((230, 128, 3), 8, dtype=np.uint8)
    rows = []
    for start in range(0, len(tiles), columns):
        current = tiles[start:start + columns]
        current += [padding] * (columns - len(current))
        rows.append(np.hstack(current))
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), np.vstack(rows))


def _score(
    query: np.ndarray,
    references: list[np.ndarray],
    prototype: str,
    ranking: str,
    *,
    quality_weights: list[float] | None = None,
) -> float:
    values = np.asarray([1.0 - float(np.clip(query @ reference, -1.0, 1.0)) for reference in references])
    if ranking == "minimum_cosine":
        return float(values.min())
    if ranking == "median_cosine":
        return float(np.median(values))
    if ranking == "hybrid_min_median":
        return float(.5 * values.min() + .5 * np.median(values))
    vector = _prototype(references, prototype, quality_weights=quality_weights)
    return 1.0 - float(np.clip(query @ vector, -1.0, 1.0))


def _prototype(
    values: list[np.ndarray],
    method: str,
    *,
    quality_weights: list[float] | None = None,
) -> np.ndarray:
    matrix = np.stack(values)
    if method == "medoid":
        distances = 1.0 - np.clip(matrix @ matrix.T, -1.0, 1.0)
        return matrix[int(np.argmin(distances.mean(axis=1)))]
    if method == "trimmed_mean" and len(values) >= 3:
        centre = _normalise(matrix.mean(axis=0))
        keep = np.argsort(1.0 - matrix @ centre)[:max(2, len(values) - 1)]
        return _normalise(matrix[keep].mean(axis=0))
    if method == "quality_weighted_mean":
        weights = np.asarray(quality_weights or [1.0] * len(values), dtype=np.float32)
        weights /= max(float(weights.sum()), 1e-12)
        return _normalise((matrix * weights[:, None]).sum(axis=0))
    return _normalise(matrix.mean(axis=0))


def _metrics(rows: list[dict[str, Any]], *, method: str) -> dict[str, Any]:
    count = len(rows)
    return {
        "method": method, "queries": count,
        "top1_accuracy": round(sum(bool(row["top1_correct"]) for row in rows) / count, 4) if count else None,
        "top3_accuracy": round(sum(bool(row["top3_correct"]) for row in rows) / count, 4) if count else None,
        "rows": rows,
    }


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    return {"count": len(values), "min": round(min(values), 6), "median": round(float(np.median(values)), 6), "max": round(max(values), 6), "mean": round(float(np.mean(values)), 6)}


def _auc(positives: list[float], negatives: list[float]) -> float:
    if not positives or not negatives:
        return float("nan")
    return sum((positive > negative) + .5 * (positive == negative) for positive in positives for negative in negatives) / (len(positives) * len(negatives))


def _overlap(positives: list[float], negatives: list[float]) -> float:
    if not positives or not negatives:
        return float("nan")
    threshold = (float(np.median(positives)) + float(np.median(negatives))) / 2.0
    return (sum(value <= threshold for value in positives) + sum(value >= threshold for value in negatives)) / (len(positives) + len(negatives))


def _eer(positives: list[float], negatives: list[float]) -> dict[str, Any]:
    if not positives or not negatives:
        return {"available": False}
    thresholds = sorted(set(positives + negatives))
    best = min(((abs(sum(value < threshold for value in positives) / len(positives) - sum(value >= threshold for value in negatives) / len(negatives)), threshold) for threshold in thresholds), key=lambda row: row[0])
    threshold = best[1]
    false_negative = sum(value < threshold for value in positives) / len(positives)
    false_positive = sum(value >= threshold for value in negatives) / len(negatives)
    return {"available": True, "threshold_similarity": round(threshold, 6), "eer": round((false_negative + false_positive) / 2, 6)}


def _effective_rank(values: np.ndarray) -> float:
    singular = np.linalg.svd(values - values.mean(axis=0), compute_uv=False)
    weights = singular / max(float(singular.sum()), 1e-12)
    entropy = -float(np.sum(weights[weights > 0] * np.log(weights[weights > 0])))
    return math.exp(entropy)


def _background_reduced(image: np.ndarray) -> np.ndarray:
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    rectangle = (max(1, image.shape[1] // 12), max(1, image.shape[0] // 20), max(2, image.shape[1] * 5 // 6), max(2, image.shape[0] * 9 // 10))
    cv2.grabCut(image, mask, rectangle, np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64), 2, cv2.GC_INIT_WITH_RECT)
    foreground = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1, 0).astype(np.uint8)
    blurred = cv2.GaussianBlur(image, (0, 0), 9)
    return np.where(foreground[:, :, None] == 1, image, blurred)


def _slice(image: np.ndarray, x1: float, y1: float, x2: float, y2: float) -> np.ndarray:
    height, width = image.shape[:2]
    left, top = max(0, int(math.floor(x1))), max(0, int(math.floor(y1)))
    right, bottom = min(width, int(math.ceil(x2))), min(height, int(math.ceil(y2)))
    result = image[top:bottom, left:right]
    if result.size == 0:
        raise ValueError("Crop transform produced an empty image")
    return result


def _normalise(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if not np.all(np.isfinite(vector)) or norm <= 1e-12:
        raise ValueError("Invalid embedding")
    return vector / norm


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quality_weight(crop: AuditCrop) -> float:
    image = cv2.imread(str(crop.artifact))
    if image is None or image.size == 0:
        return 0.01
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = min(float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 80.0, 1.0)
    contrast = min(float(gray.std()) / 50.0, 1.0)
    area = min(float(image.shape[0] * image.shape[1]) / 5000.0, 1.0)
    edge_clip = 1.0 if min(image.shape[:2]) < 16 else 0.0
    # v1: 45% curator score + 20% size + 20% sharpness + 15% contrast - clipping.
    return max(0.01, .45 * min(crop.selection_score / 1.2, 1.0) + .20 * area + .20 * blur + .15 * contrast - .35 * edge_clip)


def cache_namespace(*, embedder: Any, crop_variant: str, checkpoint_run_id: str = "pretrained") -> dict[str, Any]:
    """All model/checkpoint properties are part of the cache contract."""
    weights = getattr(embedder, "weights_sha256", None)
    if not weights and getattr(embedder, "model_name", "") == OsnetAinEmbedder.model_name:
        weights = _file_sha256(OSNET_WEIGHTS)
    return {
        "weights_sha256": weights or "not_applicable",
        "architecture": getattr(embedder, "architecture", getattr(embedder, "model_name", "unknown")),
        "model_version": getattr(embedder, "model_version", "unknown"),
        "runtime": getattr(embedder, "runtime_name", "unknown"),
        "torch_version": getattr(embedder, "torch_version", "not_applicable"),
        "preprocessing_version": "osnet-rgb-imagenet-256x128-v1",
        "crop_variant_version": crop_variant,
        "embedding_dimension": int(getattr(embedder, "embedding_dimension", 0)),
        "checkpoint_run_id": checkpoint_run_id,
    }
