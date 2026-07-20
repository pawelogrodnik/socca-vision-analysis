from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_same_match_reid_shadow"
ALGORITHM_VERSION = "0.1.0"
DEFAULT_MODEL_NAME = "person-reidentification-retail-0288"
DEFAULT_MODEL_VERSION = "open-model-zoo-2021.4-fp16"
EMBEDDING_CACHE_SCHEMA_VERSION = "0.2.0"
EMBEDDING_PREPROCESSING_VERSION = "bgr-raw-256x128-v1"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "max_embeddings_per_subject": 8,
    "prefilter_observations_per_subject": 24,
    "min_embeddings_for_prototype": 3,
    "min_detection_confidence": 0.60,
    "min_bbox_width_px": 12,
    "min_bbox_height_px": 32,
    "min_bbox_area_px": 500,
    "max_overlap_iou": 0.18,
    "max_overlap_containment": 0.25,
    "min_blur_variance": 10.0,
    "min_brightness": 22.0,
    "max_brightness": 235.0,
    "max_prototype_dispersion": 0.35,
}


class PersonReIdEmbedder(Protocol):
    model_name: str
    model_version: str
    embedding_dimension: int

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        """Return one L2-normalized person embedding."""


class JsonEmbeddingCache:
    """Persistent cache keyed by model, preprocessing and exact crop pixels."""

    def __init__(
        self,
        path: Path,
        *,
        model_name: str,
        model_version: str,
        embedding_dimension: int,
        entries: dict[str, list[float]] | None = None,
    ) -> None:
        self.path = path
        self.model_name = model_name
        self.model_version = model_version
        self.embedding_dimension = int(embedding_dimension)
        self.entries = dict(entries or {})
        self.hits = 0
        self.misses = 0
        self.writes = 0

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        model_name: str,
        model_version: str,
        embedding_dimension: int,
    ) -> JsonEmbeddingCache:
        entries: dict[str, list[float]] = {}
        if path.exists():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                compatible = (
                    document.get("schema_version") == EMBEDDING_CACHE_SCHEMA_VERSION
                    and document.get("model_name") == model_name
                    and document.get("model_version") == model_version
                    and document.get("preprocessing_version") == EMBEDDING_PREPROCESSING_VERSION
                    and int(document.get("embedding_dimension") or 0) == int(embedding_dimension)
                )
                if compatible and isinstance(document.get("entries"), dict):
                    entries = document["entries"]
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                entries = {}
        return cls(
            path,
            model_name=model_name,
            model_version=model_version,
            embedding_dimension=embedding_dimension,
            entries=entries,
        )

    def get(self, crop_digest: str) -> np.ndarray | None:
        value = self.entries.get(self._key(crop_digest))
        if not isinstance(value, list) or len(value) != self.embedding_dimension:
            self.misses += 1
            return None
        vector = np.asarray(value, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if not np.all(np.isfinite(vector)) or not math.isfinite(norm) or norm <= 1e-12:
            self.misses += 1
            return None
        self.hits += 1
        return vector

    def put(self, crop_digest: str, embedding: np.ndarray) -> None:
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if vector.size != self.embedding_dimension:
            raise ValueError("Embedding dimension does not match the cache contract")
        if not np.all(np.isfinite(vector)) or float(np.linalg.norm(vector)) <= 1e-12:
            raise ValueError("Cannot cache a zero or invalid embedding")
        self.entries[self._key(crop_digest)] = [float(value) for value in vector]
        self.writes += 1

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": EMBEDDING_CACHE_SCHEMA_VERSION,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "preprocessing_version": EMBEDDING_PREPROCESSING_VERSION,
            "embedding_dimension": self.embedding_dimension,
            "entries": dict(sorted(self.entries.items())),
        }
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "path": str(self.path),
            "entries": len(self.entries),
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
            "preprocessing_version": EMBEDDING_PREPROCESSING_VERSION,
        }

    def _key(self, crop_digest: str) -> str:
        payload = "|".join(
            (
                self.model_name,
                self.model_version,
                EMBEDDING_PREPROCESSING_VERSION,
                crop_digest,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OpenCvPersonReIdEmbedder:
    net: Any
    model_name: str = DEFAULT_MODEL_NAME
    model_version: str = DEFAULT_MODEL_VERSION
    embedding_dimension: int = 256

    @classmethod
    def from_openvino_ir(
        cls,
        xml_path: Path,
        bin_path: Path,
        *,
        model_version: str = DEFAULT_MODEL_VERSION,
    ) -> OpenCvPersonReIdEmbedder:
        if not xml_path.exists() or not bin_path.exists():
            raise FileNotFoundError(f"Missing ReID model files: {xml_path}, {bin_path}")
        if hasattr(cv2.dnn, "readNetFromModelOptimizer"):
            net = cv2.dnn.readNetFromModelOptimizer(str(xml_path), str(bin_path))
        else:
            net = cv2.dnn.readNet(str(bin_path), str(xml_path))
        return cls(net=net, model_version=model_version)

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        if crop_bgr.size == 0:
            raise ValueError("Cannot embed an empty crop")
        blob = cv2.dnn.blobFromImage(
            crop_bgr,
            scalefactor=1.0,
            size=(128, 256),
            mean=(0.0, 0.0, 0.0),
            swapRB=False,
            crop=False,
        )
        self.net.setInput(blob)
        vector = np.asarray(self.net.forward(), dtype=np.float32).reshape(-1)
        if vector.size != self.embedding_dimension:
            raise ValueError(
                f"Unexpected ReID embedding dimension: {vector.size}; "
                f"expected {self.embedding_dimension}"
            )
        return _l2_normalize(vector)


@dataclass(frozen=True)
class OpenVinoRuntimePersonReIdEmbedder:
    compiled_model: Any
    input_layer: Any
    output_layer: Any
    model_name: str = DEFAULT_MODEL_NAME
    model_version: str = DEFAULT_MODEL_VERSION
    embedding_dimension: int = 256

    @classmethod
    def from_openvino_ir(
        cls,
        xml_path: Path,
        bin_path: Path,
        *,
        model_version: str = DEFAULT_MODEL_VERSION,
    ) -> OpenVinoRuntimePersonReIdEmbedder:
        try:
            import openvino as ov
        except ImportError as exc:
            raise RuntimeError(
                "OpenVINO runtime is not installed; install backend/requirements-reid.txt"
            ) from exc
        core = ov.Core()
        model = core.read_model(model=str(xml_path), weights=str(bin_path))
        compiled_model = core.compile_model(model, "CPU")
        return cls(
            compiled_model=compiled_model,
            input_layer=compiled_model.input(0),
            output_layer=compiled_model.output(0),
            model_version=model_version,
        )

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        if crop_bgr.size == 0:
            raise ValueError("Cannot embed an empty crop")
        resized = cv2.resize(crop_bgr, (128, 256), interpolation=cv2.INTER_LINEAR)
        tensor = np.transpose(resized.astype(np.float32), (2, 0, 1))[None, ...]
        output = self.compiled_model({self.input_layer: tensor})[self.output_layer]
        vector = np.asarray(output, dtype=np.float32).reshape(-1)
        if vector.size != self.embedding_dimension:
            raise ValueError(
                f"Unexpected ReID embedding dimension: {vector.size}; "
                f"expected {self.embedding_dimension}"
            )
        return _l2_normalize(vector)


def default_model_paths(models_dir: Path) -> tuple[Path, Path]:
    model_dir = models_dir / DEFAULT_MODEL_NAME / "FP16"
    return (
        model_dir / f"{DEFAULT_MODEL_NAME}.xml",
        model_dir / f"{DEFAULT_MODEL_NAME}.bin",
    )


def load_default_embedder(models_dir: Path) -> tuple[PersonReIdEmbedder | None, dict[str, Any]]:
    xml_path, bin_path = default_model_paths(models_dir)
    model_status = {
        "model_name": DEFAULT_MODEL_NAME,
        "model_version": DEFAULT_MODEL_VERSION,
        "xml_path": str(xml_path),
        "bin_path": str(bin_path),
        "available": xml_path.exists() and bin_path.exists(),
    }
    if not model_status["available"]:
        model_status["reason"] = "model_files_missing"
        return None, model_status
    load_errors: list[dict[str, str]] = []
    try:
        embedder = OpenCvPersonReIdEmbedder.from_openvino_ir(xml_path, bin_path)
        model_status["runtime"] = "opencv_dnn_openvino"
        return embedder, model_status
    except Exception as exc:
        load_errors.append({"runtime": "opencv_dnn_openvino", "error": str(exc)})
    try:
        embedder = OpenVinoRuntimePersonReIdEmbedder.from_openvino_ir(xml_path, bin_path)
        model_status["runtime"] = "openvino_cpu"
        model_status["load_warnings"] = load_errors
        return embedder, model_status
    except Exception as exc:
        load_errors.append({"runtime": "openvino_cpu", "error": str(exc)})
    model_status.update(
        {
            "available": False,
            "reason": "model_load_failed",
            "load_errors": load_errors,
        }
    )
    return None, model_status


def build_same_match_reid_evidence(
    candidate_doc: dict[str, Any],
    resolved_timeline_doc: dict[str, Any],
    consolidation_doc: dict[str, Any],
    *,
    video_path: Path,
    fps: float,
    video_time_offset_sec: float = 0.0,
    embedder: PersonReIdEmbedder | None,
    embedding_cache: JsonEmbeddingCache | None = None,
    model_status: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build advisory same-match ReID evidence without changing any identity artifact."""
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    safe_fps = max(float(fps), 1e-6)
    proposals = list(consolidation_doc.get("proposals") or [])
    referenced_subjects = {
        str(value)
        for row in proposals
        for value in (
            row.get("source_candidate_subject_id"),
            row.get("target_candidate_subject_id"),
        )
        if value
    }
    timeline_by_subject = {
        str(row.get("shadow_subject_id") or row.get("candidate_subject_id") or ""): row
        for row in resolved_timeline_doc.get("subjects") or []
    }
    candidate_by_subject = {
        str(row.get("candidate_subject_id") or ""): row
        for row in candidate_doc.get("subjects") or []
    }
    status = dict(model_status or {})
    if embedder is None:
        status.setdefault("available", False)
        status.setdefault("reason", "embedder_unavailable")
        document = _unavailable_document(
            proposals,
            referenced_subjects=referenced_subjects,
            model_status=status,
            params=params,
            generated_at=generated,
        )
        return {
            "identity_same_match_reid": document,
            "identity_same_match_reid_report": _report(document),
        }

    overlap_by_observation = _overlap_index(resolved_timeline_doc)
    selected: dict[str, list[dict[str, Any]]] = {}
    metadata_rejections_by_subject: dict[str, Counter[str]] = {}
    selection_rejections: Counter[str] = Counter()
    for subject_id in sorted(referenced_subjects):
        subject = timeline_by_subject.get(subject_id) or {}
        rows, rejections = _select_metadata_candidates(
            subject,
            overlap_by_observation=overlap_by_observation,
            parameters=params,
        )
        selected[subject_id] = rows
        metadata_rejections_by_subject[subject_id] = rejections
        selection_rejections.update(rejections)

    crop_results = _embed_selected_observations(
        selected,
        video_path=video_path,
        fps=safe_fps,
        video_time_offset_sec=float(video_time_offset_sec),
        embedder=embedder,
        embedding_cache=embedding_cache,
        parameters=params,
    )
    subject_rows: list[dict[str, Any]] = []
    prototypes: dict[str, np.ndarray] = {}
    for subject_id in sorted(referenced_subjects):
        candidate = candidate_by_subject.get(subject_id) or {}
        result = crop_results.get(subject_id) or {"accepted": [], "rejections": {}}
        combined_rejections = Counter(metadata_rejections_by_subject.get(subject_id) or {})
        combined_rejections.update(result.get("rejections") or {})
        accepted = list(result["accepted"])
        vectors = [np.asarray(row.pop("embedding"), dtype=np.float32) for row in accepted]
        prototype, dispersion, medoid_index = _robust_medoid(vectors)
        enough = len(vectors) >= int(params["min_embeddings_for_prototype"])
        reliable = bool(
            enough
            and prototype is not None
            and dispersion is not None
            and dispersion <= float(params["max_prototype_dispersion"])
        )
        if prototype is not None:
            prototypes[subject_id] = prototype
        subject_rows.append(
            {
                "candidate_subject_id": subject_id,
                "candidate_player_id": candidate.get("candidate_player_id"),
                "team_label": candidate.get("team_label"),
                "role": candidate.get("role"),
                "embedding_model": embedder.model_name,
                "embedding_version": embedder.model_version,
                "embedding_dimension": int(embedder.embedding_dimension),
                "candidate_crops": len(selected.get(subject_id) or []),
                "accepted_embeddings": len(vectors),
                "embedding_quality": _embedding_quality(accepted, dispersion, params),
                "prototype_dispersion": _rounded(dispersion),
                "prototype_medoid_index": medoid_index,
                "appearance_reliable": reliable,
                "crop_quality_gate_applied": True,
                "prototype": [_rounded(float(value), 7) for value in prototype] if prototype is not None else None,
                "observations": accepted,
                "rejection_counts": dict(sorted(combined_rejections.items())),
                "reason_codes": _prototype_reason_codes(len(vectors), dispersion, params),
            }
        )

    pair_rows = [
        _pair_evidence(row, prototypes=prototypes, subjects=subject_rows)
        for row in proposals
    ]
    pair_status_counts = Counter(str(row["status"]) for row in pair_rows)
    reliable_subjects = sum(bool(row["appearance_reliable"]) for row in subject_rows)
    document = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "same_match_reid_shadow_evidence",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION, "parameters": params},
        "model": {
            **status,
            "available": True,
            "model_name": embedder.model_name,
            "model_version": embedder.model_version,
            "embedding_dimension": int(embedder.embedding_dimension),
        },
        "source": {
            "candidate_algorithm": candidate_doc.get("algorithm") or {},
            "timeline_algorithm": resolved_timeline_doc.get("algorithm") or {},
            "consolidation_algorithm": consolidation_doc.get("algorithm") or {},
            "video_path": str(video_path),
            "video_time_offset_sec": round(float(video_time_offset_sec), 3),
        },
        "summary": {
            "referenced_subjects": len(referenced_subjects),
            "subjects_with_prototype": len(prototypes),
            "reliable_subjects": reliable_subjects,
            "proposal_pairs": len(pair_rows),
            "pair_status_counts": dict(sorted(pair_status_counts.items())),
            "metadata_selection_rejections": dict(sorted(selection_rejections.items())),
            "embedding_cache": (
                embedding_cache.summary() if embedding_cache is not None else {"enabled": False}
            ),
        },
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatically_merges_fragments": False,
            "can_override_hard_constraints": False,
            "eligible_for_player_stats": False,
            "crop_quality_gate_required": True,
        },
        "subjects": subject_rows,
        "pairs": pair_rows,
    }
    return {
        "identity_same_match_reid": document,
        "identity_same_match_reid_report": _report(document),
    }


def _unavailable_document(
    proposals: list[dict[str, Any]],
    *,
    referenced_subjects: set[str],
    model_status: dict[str, Any],
    params: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "mode": "same_match_reid_shadow_evidence",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION, "parameters": params},
        "model": model_status,
        "summary": {
            "referenced_subjects": len(referenced_subjects),
            "subjects_with_prototype": 0,
            "reliable_subjects": 0,
            "proposal_pairs": len(proposals),
            "pair_status_counts": {"unavailable": len(proposals)} if proposals else {},
            "metadata_selection_rejections": {},
        },
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatically_merges_fragments": False,
            "can_override_hard_constraints": False,
            "eligible_for_player_stats": False,
            "crop_quality_gate_required": True,
        },
        "subjects": [],
        "pairs": [
            {
                "proposal_key": row.get("proposal_key"),
                "source_candidate_subject_id": row.get("source_candidate_subject_id"),
                "target_candidate_subject_id": row.get("target_candidate_subject_id"),
                "status": "unavailable",
                "prototype_distance": None,
                "appearance_reliable": False,
                "reason_codes": [str(model_status.get("reason") or "embedder_unavailable")],
                "advisory_only": True,
            }
            for row in proposals
        ],
    }


def _select_metadata_candidates(
    subject: dict[str, Any],
    *,
    overlap_by_observation: dict[tuple[str, int], dict[str, float]],
    parameters: dict[str, Any],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    accepted: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    subject_id = str(subject.get("shadow_subject_id") or subject.get("candidate_subject_id") or "")
    for row in subject.get("observations") or []:
        reason = _metadata_rejection_reason(
            row,
            overlap=overlap_by_observation.get((subject_id, int(row.get("frame") or 0))) or {},
            parameters=parameters,
        )
        if reason:
            rejected[reason] += 1
            continue
        accepted.append(dict(row))
    limit = int(parameters["prefilter_observations_per_subject"])
    return _temporally_diverse(accepted, limit), rejected


def _metadata_rejection_reason(
    row: dict[str, Any],
    *,
    overlap: dict[str, float],
    parameters: dict[str, Any],
) -> str | None:
    if str(row.get("status") or "") != "detected":
        return "not_detected"
    if not bool(row.get("appearance_reliable")):
        return "appearance_unreliable"
    if not bool(row.get("footpoint_reliable")):
        return "footpoint_unreliable"
    if str(row.get("play_area_status") or "") != "inside_play":
        return "outside_play_area"
    if str(row.get("quality_class") or "") in {"noise", "duplicate"}:
        return "unsafe_quality_class"
    if float(row.get("confidence") or 0.0) < float(parameters["min_detection_confidence"]):
        return "low_detection_confidence"
    bbox = _bbox(row.get("bbox_xyxy"))
    if bbox is None:
        return "invalid_bbox"
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if width < float(parameters["min_bbox_width_px"]):
        return "bbox_too_narrow"
    if height < float(parameters["min_bbox_height_px"]):
        return "bbox_too_short"
    if width * height < float(parameters["min_bbox_area_px"]):
        return "bbox_too_small"
    if float(overlap.get("max_iou") or 0.0) > float(parameters["max_overlap_iou"]):
        return "strong_bbox_overlap"
    if float(overlap.get("max_containment") or 0.0) > float(parameters["max_overlap_containment"]):
        return "strong_bbox_containment"
    return None


def _overlap_index(timeline_doc: dict[str, Any]) -> dict[tuple[str, int], dict[str, float]]:
    by_frame: dict[int, list[tuple[str, tuple[float, float, float, float]]]] = defaultdict(list)
    for subject in timeline_doc.get("subjects") or []:
        subject_id = str(subject.get("shadow_subject_id") or subject.get("candidate_subject_id") or "")
        for row in subject.get("observations") or []:
            if str(row.get("status") or "") != "detected":
                continue
            bbox = _bbox(row.get("bbox_xyxy"))
            if bbox is not None:
                by_frame[int(row.get("frame") or 0)].append((subject_id, bbox))
    result: dict[tuple[str, int], dict[str, float]] = {}
    for frame, rows in by_frame.items():
        for index, (subject_id, bbox) in enumerate(rows):
            max_iou = 0.0
            max_containment = 0.0
            for other_index, (_, other_bbox) in enumerate(rows):
                if index == other_index:
                    continue
                intersection = _intersection_area(bbox, other_bbox)
                if intersection <= 0:
                    continue
                area = _area(bbox)
                other_area = _area(other_bbox)
                union = area + other_area - intersection
                max_iou = max(max_iou, intersection / max(union, 1e-6))
                max_containment = max(max_containment, intersection / max(min(area, other_area), 1e-6))
            result[(subject_id, frame)] = {
                "max_iou": max_iou,
                "max_containment": max_containment,
            }
    return result


def _embed_selected_observations(
    selected: dict[str, list[dict[str, Any]]],
    *,
    video_path: Path,
    fps: float,
    video_time_offset_sec: float,
    embedder: PersonReIdEmbedder,
    embedding_cache: JsonEmbeddingCache | None,
    parameters: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    requests_by_local_frame: dict[int, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    offset_frames = int(round(video_time_offset_sec * fps))
    results = {
        subject_id: {"accepted": [], "rejections": Counter()}
        for subject_id in selected
    }
    for subject_id, rows in selected.items():
        for row in rows:
            local_frame = int(row.get("frame") or 0) - offset_frames
            if local_frame < 0:
                results[subject_id]["rejections"]["frame_before_video_window"] += 1
                continue
            requests_by_local_frame[local_frame].append((subject_id, row))
    if not requests_by_local_frame:
        return results
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open ReID source video: {video_path}")
    current = -1
    try:
        for local_frame in sorted(requests_by_local_frame):
            if local_frame != current + 1:
                capture.set(cv2.CAP_PROP_POS_FRAMES, local_frame)
            ok, frame = capture.read()
            current = local_frame
            if not ok or frame is None:
                for subject_id, _ in requests_by_local_frame[local_frame]:
                    results[subject_id]["rejections"]["video_frame_unavailable"] += 1
                continue
            for subject_id, row in requests_by_local_frame[local_frame]:
                crop = _crop(frame, row.get("bbox_xyxy"))
                reason, image_metrics = _image_rejection_reason(crop, parameters)
                if reason:
                    results[subject_id]["rejections"][reason] += 1
                    continue
                crop_digest = hashlib.sha256(crop.tobytes()).hexdigest()
                embedding = embedding_cache.get(crop_digest) if embedding_cache is not None else None
                embedding_source = "cache" if embedding is not None else "model"
                if embedding is None:
                    try:
                        embedding = _l2_normalize(embedder.embed(crop))
                        if embedding_cache is not None:
                            embedding_cache.put(crop_digest, embedding)
                    except Exception:
                        results[subject_id]["rejections"]["embedding_failed"] += 1
                        continue
                results[subject_id]["accepted"].append(
                    {
                        "frame": int(row.get("frame") or 0),
                        "time_sec": _rounded(float(row.get("time_sec") or 0.0), 3),
                        "tracklet_id": row.get("tracklet_id"),
                        "bbox_xyxy": [int(round(value)) for value in _bbox(row.get("bbox_xyxy")) or ()],
                        "confidence": _rounded(float(row.get("confidence") or 0.0), 4),
                        "blur_variance": _rounded(image_metrics["blur_variance"], 3),
                        "brightness": _rounded(image_metrics["brightness"], 3),
                        "crop_digest": crop_digest,
                        "embedding_source": embedding_source,
                        "embedding": embedding,
                    }
                )
    finally:
        capture.release()
    max_embeddings = int(parameters["max_embeddings_per_subject"])
    for result in results.values():
        rows = list(result["accepted"])
        if len(rows) > max_embeddings:
            keep = _temporally_diverse(rows, max_embeddings)
            result["rejections"]["excess_clean_crop"] += len(rows) - len(keep)
            result["accepted"] = keep
        result["rejections"] = dict(result["rejections"])
    return results


def _image_rejection_reason(
    crop: np.ndarray,
    parameters: dict[str, Any],
) -> tuple[str | None, dict[str, float]]:
    if crop.size == 0:
        return "empty_crop", {"blur_variance": 0.0, "brightness": 0.0}
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    metrics = {"blur_variance": blur_variance, "brightness": brightness}
    if blur_variance < float(parameters["min_blur_variance"]):
        return "crop_too_blurry", metrics
    if brightness < float(parameters["min_brightness"]):
        return "crop_too_dark", metrics
    if brightness > float(parameters["max_brightness"]):
        return "crop_too_bright", metrics
    return None, metrics


def _robust_medoid(vectors: list[np.ndarray]) -> tuple[np.ndarray | None, float | None, int | None]:
    if not vectors:
        return None, None, None
    matrix = np.stack([_l2_normalize(value) for value in vectors])
    distances = 1.0 - np.clip(matrix @ matrix.T, -1.0, 1.0)
    median_distance = np.median(distances, axis=1)
    medoid_index = int(np.argmin(median_distance))
    prototype = matrix[medoid_index]
    dispersion = float(np.median(1.0 - np.clip(matrix @ prototype, -1.0, 1.0)))
    return prototype, dispersion, medoid_index


def _pair_evidence(
    proposal: dict[str, Any],
    *,
    prototypes: dict[str, np.ndarray],
    subjects: list[dict[str, Any]],
) -> dict[str, Any]:
    subject_index = {str(row["candidate_subject_id"]): row for row in subjects}
    source_id = str(proposal.get("source_candidate_subject_id") or "")
    target_id = str(proposal.get("target_candidate_subject_id") or "")
    source = subject_index.get(source_id) or {}
    target = subject_index.get(target_id) or {}
    reasons: list[str] = []
    if source.get("team_label") != target.get("team_label"):
        reasons.append("team_conflict")
    if source_id not in prototypes:
        reasons.append("source_prototype_unavailable")
    if target_id not in prototypes:
        reasons.append("target_prototype_unavailable")
    if not bool(source.get("appearance_reliable")):
        reasons.append("source_appearance_unreliable")
    if not bool(target.get("appearance_reliable")):
        reasons.append("target_appearance_unreliable")
    reliable = not reasons
    distance = None
    if source_id in prototypes and target_id in prototypes:
        distance = float(1.0 - np.clip(np.dot(prototypes[source_id], prototypes[target_id]), -1.0, 1.0))
    return {
        "proposal_key": proposal.get("proposal_key"),
        "source_candidate_subject_id": source_id,
        "target_candidate_subject_id": target_id,
        "embedding_model": source.get("embedding_model") or target.get("embedding_model"),
        "embedding_version": source.get("embedding_version") or target.get("embedding_version"),
        "status": "available" if reliable else "unavailable",
        "prototype_distance": _rounded(distance, 6),
        "appearance_reliable": reliable,
        "source_embedding_quality": source.get("embedding_quality"),
        "target_embedding_quality": target.get("embedding_quality"),
        "reason_codes": sorted(set(reasons)),
        "advisory_only": True,
    }


def _report(document: dict[str, Any]) -> dict[str, Any]:
    model_available = bool((document.get("model") or {}).get("available"))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": document.get("generated_at"),
        "algorithm": document.get("algorithm") or {},
        "status": "ready" if model_available else "unavailable",
        "summary": document.get("summary") or {},
        "gates": {
            "production_identity_untouched": True,
            "candidate_identity_untouched": True,
            "no_automatic_merges": True,
            "hard_constraints_cannot_be_overridden": True,
            "only_reliable_crops_used": bool(
                (document.get("safety") or {}).get("crop_quality_gate_required")
            )
            and all(
                bool(row.get("crop_quality_gate_applied"))
                for row in document.get("subjects") or []
            ),
        },
        "limitations": [
            "This model was trained for upright retail-camera pedestrians, not aerial football footage.",
            "Same-match ReID distance is advisory and has no calibrated merge threshold in this stage.",
            "Uniform kits, tiny crops, blur and partial visibility can make different players look alike.",
        ],
    }


def _embedding_quality(
    observations: list[dict[str, Any]],
    dispersion: float | None,
    parameters: dict[str, Any],
) -> float:
    if not observations or dispersion is None:
        return 0.0
    count_score = min(1.0, len(observations) / max(1, int(parameters["max_embeddings_per_subject"])))
    confidence_score = float(np.median([float(row.get("confidence") or 0.0) for row in observations]))
    dispersion_score = max(0.0, 1.0 - dispersion / max(float(parameters["max_prototype_dispersion"]), 1e-6))
    return _rounded((0.35 * count_score) + (0.30 * confidence_score) + (0.35 * dispersion_score), 4) or 0.0


def _prototype_reason_codes(
    count: int,
    dispersion: float | None,
    parameters: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if count < int(parameters["min_embeddings_for_prototype"]):
        reasons.append("insufficient_clean_embeddings")
    if dispersion is None:
        reasons.append("prototype_unavailable")
    elif dispersion > float(parameters["max_prototype_dispersion"]):
        reasons.append("prototype_dispersion_too_high")
    return reasons


def _temporally_diverse(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: int(row.get("frame") or 0))
    if limit <= 0 or len(ordered) <= limit:
        return ordered
    indexes = np.linspace(0, len(ordered) - 1, num=limit)
    unique = sorted({int(round(value)) for value in indexes})
    return [ordered[index] for index in unique]


def _crop(frame: np.ndarray, bbox_value: Any) -> np.ndarray:
    bbox = _bbox(bbox_value)
    if bbox is None:
        return np.empty((0, 0, 3), dtype=np.uint8)
    height, width = frame.shape[:2]
    x1 = max(0, min(width, int(math.floor(bbox[0]))))
    y1 = max(0, min(height, int(math.floor(bbox[1]))))
    x2 = max(0, min(width, int(math.ceil(bbox[2]))))
    y2 = max(0, min(height, int(math.ceil(bbox[3]))))
    if x2 <= x1 or y2 <= y1:
        return np.empty((0, 0, 3), dtype=np.uint8)
    return frame[y1:y2, x1:x2]


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in bbox):
        return None
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _intersection_area(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    return width * height


def _area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _l2_normalize(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("ReID model returned a zero or invalid embedding")
    return vector / norm


def _rounded(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)
