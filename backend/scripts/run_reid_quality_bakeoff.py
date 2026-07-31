from __future__ import annotations

"""Run the H1-only ReID bake-off, then consume H2 exactly once."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.identity_approved_appearance_reid import (
    PortableAppearanceEmbedder,
    _embed_candidate_subjects,
    build_appearance_ranking_calibration,
)
from app.services.identity_cross_analysis_appearance_reid import _reference_embedding_inputs
from app.services.identity_reid_quality_bakeoff import (
    AuditCrop,
    OsnetAinEmbedder,
    collect_h1_crops,
    crop_variants,
    embed_variant,
    embedding_health,
    evaluate_crop_loo,
    image_quality_records,
    select_h1_winner,
    write_montage,
)
from app.services.identity_rosetta_openvino_reid import (
    activate_rosetta_openvino_runtime,
    discover_rosetta_openvino_runtime,
)
from app.services.identity_same_match_reid import JsonEmbeddingCache
from app.services.identity_jersey_number_common import canonical_digest


EXPECTED_H1 = {"queries": 21, "top1_accuracy": 0.0476, "top3_accuracy": 0.1429}
EXPECTED_H2 = {"queries": 6, "top1_accuracy": 0.3333, "top3_accuracy": 0.6667}


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--session-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--models-dir", default=Path("backend/models"), type=Path)
    return parser.parse_args()


def main() -> int:
    options = args()
    output = options.output_root
    output.mkdir(parents=True, exist_ok=True)
    h1 = options.source_root / "h1_workspace"
    baseline = reproduce_frozen_baseline(options.source_root, h1)
    _write(output / "baseline_reproduction.json", baseline)
    if not baseline["passed"]:
        _write(output / "reid_quality_bakeoff_report.json", {
            "status": "BASELINE_REPRODUCTION_FAILED", "baseline": baseline,
            "safety": {"h2_used_for_selection": False, "h2_final_evaluation_run": False},
        })
        return 2

    crops = collect_h1_crops(
        options.source_root / "cross_capture_reid_diagnostic" / "h1",
        max_crops_per_player=3,
    )
    variants = crop_variants(crops, video_path=h1 / "video.mp4")
    quality = image_quality_records(crops, variants["A_original_bbox"])
    _write(output / "h1_crop_quality_audit.json", _crop_audit(crops, quality))

    embedders = _available_embedders(options.models_dir, variants["A_original_bbox"][0], output)
    candidates, health = [], {}
    for embedder, model in embedders:
        for variant_name, images in variants.items():
            vectors, cache = embed_variant(
                images, embedder=embedder,
                cache_path=output / "embedding_cache" / f"{model['model_name']}-{variant_name}.json",
            )
            health[f"{model['model_name']}:{variant_name}"] = {
                "model": model, "crop_variant": variant_name,
                "cache": cache, "health": embedding_health(crops, vectors),
            }
            for prototype in ("medoid", "normalised_mean", "quality_weighted_mean", "trimmed_mean"):
                for ranking in ("prototype_cosine", "minimum_cosine", "median_cosine", "hybrid_min_median"):
                    candidates.append({
                        "model": model, "crop_variant": variant_name,
                        "prototype": prototype, "ranking": ranking,
                        "evaluation": evaluate_crop_loo(crops, vectors, prototype=prototype, ranking=ranking),
                    })
    selection = select_h1_winner(candidates)
    selection["selection_config_digest"] = canonical_digest(selection["winner"])
    selection["frozen_after_h1"] = True
    _write(output / "h1_model_selection.json", {
        "status": "completed_h1_only", "baseline": baseline,
        "candidates": candidates, "selection": selection,
        "h2_was_available_to_selector": False,
        "h2_information_used_for_selection": False,
    })
    _write(output / "embedding_health.json", health)
    write_montage(crops, variants["A_original_bbox"], selection["winner"]["evaluation"], output / "visual_audit" / "h1_query_montage.jpg")

    winner = selection["winner"]
    chosen_vectors = _vectors_for_winner(crops, variants, embedders, winner, output)
    final_h2_path = output / "final_h2_holdout_evaluation.json"
    if final_h2_path.is_file():
        h2 = _load(final_h2_path)
        if h2.get("selection_config_digest") != canonical_digest(winner):
            raise ValueError("Frozen final H2 result belongs to a different H1-selected configuration")
        h2["replayed_from_frozen_final_artifact"] = True
    else:
        h2 = evaluate_h2_once(
            source_root=options.source_root, session_root=options.session_root,
            crops=crops, h1_vectors=chosen_vectors, winner=winner,
            embedder=next(embedder for embedder, model in embedders if model == winner["model"]),
        )
        _write(final_h2_path, h2)
    report = {
        "status": "REID_QUALITY_BAKEOFF_COMPLETED",
        "baseline": baseline,
        "selection": selection,
        "final_h2": h2,
        "gate": {
            "thresholds": {"top1_accuracy": 0.75, "top3_accuracy": 0.90},
            "passed": bool(
                (h2["top1_accuracy"] or 0) >= .75 and (h2["top3_accuracy"] or 0) >= .90
            ),
        },
        "safety": {
            "selection_used_h1_only": True,
            "h2_consumed_once_after_selection": True,
            "h2_information_used_for_selection": False,
            "automatic_identity_assignments": 0,
            "production_identity_mutations": 0,
            "reran_yolo": False,
            "reran_tracking": False,
        },
    }
    _write(output / "reid_quality_bakeoff_report.json", report)
    return 0


def reproduce_frozen_baseline(source_root: Path, h1: Path) -> dict[str, Any]:
    diagnostic = source_root / "cross_capture_reid_diagnostic" / "h1"
    gallery = _load(diagnostic / "identity_approved_appearance_gallery.json")
    anchors = _load(diagnostic / "identity_roster_anchor_crops_shadow.json")
    cache_path = diagnostic / "preferred_h1_embeddings_cache.json"
    cache_doc = _load(cache_path)
    cache = JsonEmbeddingCache.load(
        cache_path, model_name=str(cache_doc["model_name"]), model_version=str(cache_doc["model_version"]),
        embedding_dimension=int(cache_doc["embedding_dimension"]), cache_namespace=cache_doc["cache_namespace"],
    )
    reference_gallery, reference_anchors = _reference_embedding_inputs(gallery)
    class CacheOnly:
        model_name = str(cache_doc["model_name"])
        model_version = str(cache_doc["model_version"])
        embedding_dimension = int(cache_doc["embedding_dimension"])
        def embed(self, crop: np.ndarray) -> np.ndarray:  # pragma: no cover - cache hit invariant
            raise AssertionError("Frozen baseline unexpectedly required inference")
    vectors, _, rejected = _embed_candidate_subjects(
        reference_anchors, match_path=diagnostic, embedder=CacheOnly(), embedding_cache=cache,
        parameters={"max_crops_per_candidate_subject": 3},
    )
    calibration = build_appearance_ranking_calibration(
        reference_gallery, subject_vectors=vectors,
        model_status={"quality_tier": "preferred_reid_model"},
        parameters={"minimum_calibration_queries": 8, "minimum_calibration_top1_accuracy": .75},
    )
    observed = {key: calibration.get(key) for key in EXPECTED_H1}
    h2_frozen = _load(source_root.parent / "product-flow-20260730-v5-reid-followup" / "bounded_h2_evaluation.json")
    h2_observed = {
        key: (h2_frozen.get("cross_capture_evaluation") or {}).get(key)
        for key in EXPECTED_H2
    }
    return {
        "status": "passed" if observed == EXPECTED_H1 and h2_observed == EXPECTED_H2 and not rejected and cache.misses == 0 else "failed",
        "passed": observed == EXPECTED_H1 and h2_observed == EXPECTED_H2 and not rejected and cache.misses == 0,
        "h1_expected": EXPECTED_H1, "h1_observed": observed,
        "h2_expected": EXPECTED_H2, "h2_observed": h2_observed,
        "cache": cache.summary(), "rejected": rejected,
        "source_artifact_digests": {
            "h1_gallery": canonical_digest(gallery), "h1_anchor_crops": canonical_digest(anchors),
            "frozen_h2_evaluation": canonical_digest(h2_frozen),
        },
    }


def _available_embedders(models_dir: Path, sample: np.ndarray, output: Path) -> list[tuple[Any, dict[str, Any]]]:
    items: list[tuple[Any, dict[str, Any]]] = []
    candidate = discover_rosetta_openvino_runtime(models_dir)
    rosetta, status = activate_rosetta_openvino_runtime(candidate, real_crop_bgr=sample, diagnostics_directory=output / "runtime")
    _write(output / "runtime" / "rosetta_runtime_probe.json", status)
    if rosetta is not None:
        items.append((rosetta, {"model_name": rosetta.model_name, "model_version": rosetta.model_version, "runtime": rosetta.runtime_name, "embedding_dimension": rosetta.embedding_dimension}))
    portable = PortableAppearanceEmbedder()
    items.append((portable, {"model_name": portable.model_name, "model_version": portable.model_version, "runtime": "portable_opencv_descriptor", "embedding_dimension": portable.embedding_dimension}))
    osnet = OsnetAinEmbedder(python=Path("backend/.venv-mps/bin/python"))
    items.append((osnet, {"model_name": osnet.model_name, "model_version": osnet.model_version, "runtime": osnet.runtime_name, "embedding_dimension": osnet.embedding_dimension, "weight_sha256": _sha256(Path("backend/.reid-runtime-lab/osnet-native/weights/osnet_ain_x1_0_msmt17.pth"))}))
    return items


def _vectors_for_winner(crops: list[AuditCrop], variants: dict[str, list[np.ndarray]], embedders: list[tuple[Any, dict[str, Any]]], winner: dict[str, Any], output: Path) -> np.ndarray:
    embedder = next(embedder for embedder, model in embedders if model == winner["model"])
    vectors, _ = embed_variant(variants[winner["crop_variant"]], embedder=embedder, cache_path=output / "embedding_cache" / f"{winner['model']['model_name']}-{winner['crop_variant']}.json")
    return vectors


def evaluate_h2_once(*, source_root: Path, session_root: Path, crops: list[AuditCrop], h1_vectors: np.ndarray, winner: dict[str, Any], embedder: Any) -> dict[str, Any]:
    queries = _h2_queries(source_root, session_root)
    video = source_root / "h2_workspace" / "video.mp4"
    capture = cv2.VideoCapture(str(video))
    images = []
    try:
        for query in queries:
            capture.set(cv2.CAP_PROP_POS_FRAMES, query["frame"])
            ok, frame = capture.read()
            if not ok:
                raise ValueError(f"Cannot read frozen H2 frame {query['frame']}")
            images.append(_variant_from_observation(frame, query["bbox_xyxy"], winner["crop_variant"]))
    finally:
        capture.release()
    vectors, _ = embed_variant(images, embedder=embedder, cache_path=session_root.parent / "reid-quality-bakeoff-20260731-v1" / "final_h2_embedding_cache.json")
    by_player: dict[str, list[np.ndarray]] = {}
    for crop, vector in zip(crops, h1_vectors, strict=True):
        by_player.setdefault(crop.player_id, []).append(vector)
    rows = []
    for query, vector in zip(queries, vectors, strict=True):
        ranking = sorted(((player_id, _score_h2(vector, values, winner)) for player_id, values in by_player.items()), key=lambda row: (row[1], row[0]))
        ids = [player_id for player_id, _ in ranking]
        rank = ids.index(query["player_id"]) + 1 if query["player_id"] in ids else None
        rows.append({**query, "truth_rank": rank, "top1_correct": rank == 1, "top3_correct": bool(rank and rank <= 3), "ranked_player_ids": ids[:3], "ranked_distances": [round(distance, 6) for _, distance in ranking[:3]]})
    document = {**_metrics(rows, method="single_final_h2_holdout_after_h1_selection"), "selection_config_digest": canonical_digest(winner), "h2_used_for_model_selection": False, "expected_historical_baseline": EXPECTED_H2}
    return document


def _h2_queries(source_root: Path, session_root: Path) -> list[dict[str, Any]]:
    decisions = _load(session_root / "operator_decisions.json")
    rows = [{"frame": int(row["frame"]), "bbox_xyxy": row["bbox_xyxy"], "player_id": row["player_id"], "team_label": row["team_label"], "source": "v5_operator_decision"} for row in decisions["decisions"] if row.get("action") == "player"]
    validation = _load(source_root / "cross_capture_reid_diagnostic" / "cross_capture_reid_validation.json")
    old = (validation["preferred_cross_capture_evaluation"]["rows"])[0]
    provenance = old["observation_provenance"]
    rows.append({"frame": int(provenance["frame_number"]), "bbox_xyxy": provenance["bbox_xyxy"], "player_id": old["ground_truth_player_id"], "team_label": old["ground_truth_team"], "source": "v4_operator_decision"})
    if len(rows) != 6:
        raise ValueError("The frozen H2 holdout must contain exactly six operator decisions")
    return rows


def _variant_from_observation(frame: np.ndarray, bbox: list[float], variant: str) -> np.ndarray:
    x1, y1, x2, y2 = (float(value) for value in bbox)
    original = _slice(frame, x1, y1, x2, y2)
    if variant == "A_original_bbox": return original
    if variant == "B_padded_bbox": return _slice(frame, x1 - .12 * (x2-x1), y1 - .08 * (y2-y1), x2 + .12 * (x2-x1), y2 + .1 * (y2-y1))
    if variant == "C_torso": return _slice(original, .15*original.shape[1], .22*original.shape[0], .85*original.shape[1], .86*original.shape[0])
    if variant == "D_upper_body": return _slice(original, .1*original.shape[1], 0, .9*original.shape[1], .66*original.shape[0])
    return _background_reduced(original)


def _score_h2(query: np.ndarray, references: list[np.ndarray], winner: dict[str, Any]) -> float:
    ranking = winner["ranking"]
    distances = np.asarray([1.0 - float(np.clip(query @ value, -1.0, 1.0)) for value in references])
    if ranking == "minimum_cosine": return float(distances.min())
    if ranking == "median_cosine": return float(np.median(distances))
    if ranking == "hybrid_min_median": return float(.5*distances.min() + .5*np.median(distances))
    return 1.0 - float(np.clip(query @ _prototype(references, winner["prototype"]), -1.0, 1.0))


def _metrics(rows: list[dict[str, Any]], *, method: str) -> dict[str, Any]:
    count = len(rows)
    return {
        "method": method,
        "queries": count,
        "top1_accuracy": round(sum(bool(row["top1_correct"]) for row in rows) / count, 4) if count else None,
        "top3_accuracy": round(sum(bool(row["top3_correct"]) for row in rows) / count, 4) if count else None,
        "rows": rows,
    }


def _crop_audit(crops: list[AuditCrop], quality: list[dict[str, Any]]) -> dict[str, Any]:
    duplicate = len(quality) - len({row["crop_sha256"] for row in quality})
    return {"status": "completed", "crops": quality, "summary": {"count": len(crops), "players": len({crop.player_id for crop in crops}), "duplicate_pixel_crops": duplicate, "missing_artifacts": sum(not crop.artifact.is_file() for crop in crops), "single_subject_per_player": True, "data_integrity": "limited_h1_gallery; subject_LOO_not_applicable"}}


def _background_reduced(image: np.ndarray) -> np.ndarray:
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    rect = (max(1, image.shape[1]//12), max(1, image.shape[0]//20), max(2, image.shape[1]*5//6), max(2, image.shape[0]*9//10))
    cv2.grabCut(image, mask, rect, np.zeros((1,65),np.float64), np.zeros((1,65),np.float64), 2, cv2.GC_INIT_WITH_RECT)
    foreground = ((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD))[:, :, None]
    return np.where(foreground, image, cv2.GaussianBlur(image, (0,0), 9))


def _slice(image: np.ndarray, x1: float, y1: float, x2: float, y2: float) -> np.ndarray:
    h,w=image.shape[:2]; result=image[max(0,int(y1)):min(h,int(np.ceil(y2))), max(0,int(x1)):min(w,int(np.ceil(x2)))]
    if result.size == 0: raise ValueError("Empty bbox crop")
    return result


def _prototype(values: list[np.ndarray], method: str) -> np.ndarray:
    matrix=np.stack(values)
    if method == "medoid": return matrix[int(np.argmin((1-np.clip(matrix@matrix.T,-1,1)).mean(axis=1)))]
    if method == "trimmed_mean" and len(values) >= 3:
        centre=_normalise(matrix.mean(axis=0)); matrix=matrix[np.argsort(1-matrix@centre)[:max(2,len(values)-1)]]
    if method == "quality_weighted_mean": return _normalise((matrix*np.linspace(1,.7,len(matrix))[:,None]).sum(axis=0))
    return _normalise(matrix.mean(axis=0))


def _normalise(vector: np.ndarray) -> np.ndarray:
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def _load(path: Path) -> dict[str, Any]: return json.loads(path.read_text(encoding="utf-8"))
def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
