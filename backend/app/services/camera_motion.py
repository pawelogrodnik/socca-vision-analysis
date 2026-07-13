from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np


DEFAULT_CAMERA_MOTION_COMPENSATION = True
DEFAULT_CAMERA_MOTION_INTERVAL_SEC = 0.5
DEFAULT_CAMERA_MOTION_MIN_INLIER_RATIO = 0.6
DEFAULT_CAMERA_MOTION_MIN_INLIERS = 24
DEFAULT_CAMERA_MOTION_MAX_FEATURES = 3500
DEFAULT_CAMERA_MOTION_MAX_TRANSLATION_PX = 35.0
DEFAULT_CAMERA_MOTION_MAX_ROTATION_DEG = 1.0
DEFAULT_CAMERA_MOTION_MAX_SCALE_DELTA = 0.02
DEFAULT_CAMERA_MOTION_MAX_FALLBACK_HOLD_SEC = 3.0
DEFAULT_CAMERA_MOTION_MAX_SAMPLE_STEP_PX = 80.0
DEFAULT_CAMERA_MOTION_RELAXED_MIN_INLIER_RATIO = 0.42
DEFAULT_CAMERA_MOTION_RELAXED_MIN_INLIERS = 72
DEFAULT_CAMERA_MOTION_ECC_MIN_CORRELATION = 0.86
DEFAULT_CAMERA_MOTION_ECC_DOWNSCALE = 0.35
DEFAULT_CAMERA_MOTION_MAX_RAPID_STEP_PX = 180.0
DEFAULT_CAMERA_MOTION_MAX_RAPID_ROTATION_DEG = 3.0
DEFAULT_CAMERA_MOTION_PITCH_MASK_DILATION_PX = 160
DEFAULT_CAMERA_MOTION_HOMOGRAPHY_MIN_INLIERS = 36
DEFAULT_CAMERA_MOTION_HOMOGRAPHY_MIN_INLIER_RATIO = 0.52
DEFAULT_CAMERA_MOTION_MAX_PERSPECTIVE_DELTA = 0.0035
DEFAULT_CAMERA_MOTION_DRIFT_WINDOW_SEC = 3.0
DEFAULT_CAMERA_MOTION_MAX_WINDOW_STEP_PX = 130.0
DEFAULT_CAMERA_MOTION_MIN_POLYGON_AREA_RATIO = 0.4
DEFAULT_CAMERA_MOTION_MAX_POLYGON_AREA_RATIO = 2.4
DEFAULT_CAMERA_MOTION_MAX_POLYGON_CORNER_STEP_PX = 220.0
CAMERA_MOTION_TRUSTED_STATUSES = {"ok", "identity", "chained", "fallback"}
CAMERA_MOTION_INTERPOLATED_STATUSES = {"ok", "identity", "chained"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CameraMotionSample:
    frame: int
    time_sec: float
    status: str
    matrix_current_to_reference: list[list[float]]
    matrix_reference_to_current: list[list[float]]
    inlier_ratio: float | None = None
    inliers: int = 0
    matches: int = 0
    dx_px: float = 0.0
    dy_px: float = 0.0
    rotation_deg: float = 0.0
    scale: float = 1.0
    reason: str | None = None
    estimator: str | None = None


@dataclass(frozen=True)
class _MotionEstimate:
    matrix: np.ndarray
    inlier_ratio: float
    inliers: int
    matches: int
    estimator: str


class CameraMotionModel:
    def __init__(
        self,
        *,
        enabled: bool,
        reference_frame: int,
        reference_time_sec: float,
        frame_count: int,
        fps: float,
        interval_sec: float,
        min_inlier_ratio: float,
        samples: list[CameraMotionSample],
    ) -> None:
        self.enabled = bool(enabled)
        self.reference_frame = int(reference_frame)
        self.reference_time_sec = float(reference_time_sec)
        self.frame_count = int(frame_count)
        self.fps = float(fps)
        self.interval_sec = float(interval_sec)
        self.min_inlier_ratio = float(min_inlier_ratio)
        self.samples = sorted(samples, key=lambda item: item.frame)
        self._frames = [sample.frame for sample in self.samples]

    @classmethod
    def disabled(cls, *, fps: float = 0.0, frame_count: int = 0) -> "CameraMotionModel":
        sample = CameraMotionSample(
            frame=0,
            time_sec=0.0,
            status="disabled",
            matrix_current_to_reference=_identity_matrix().tolist(),
            matrix_reference_to_current=_identity_matrix().tolist(),
            reason="camera motion compensation disabled",
        )
        return cls(
            enabled=False,
            reference_frame=0,
            reference_time_sec=0.0,
            frame_count=frame_count,
            fps=fps,
            interval_sec=0.0,
            min_inlier_ratio=0.0,
            samples=[sample],
        )

    def sample_for_frame(self, frame_idx: int) -> CameraMotionSample:
        if not self.samples:
            return CameraMotionModel.disabled(fps=self.fps, frame_count=self.frame_count).samples[0]
        frame = int(frame_idx)
        if frame <= self._frames[0]:
            return self.samples[0]
        if frame >= self._frames[-1]:
            return self.samples[-1]
        insert_at = int(np.searchsorted(self._frames, frame))
        before = self.samples[max(0, insert_at - 1)]
        after = self.samples[min(len(self.samples) - 1, insert_at)]
        if before.frame == after.frame:
            return before
        ratio = (frame - before.frame) / max(1, after.frame - before.frame)
        matrix = _interpolate_motion_matrix(
            np.asarray(before.matrix_current_to_reference, dtype=np.float32),
            np.asarray(after.matrix_current_to_reference, dtype=np.float32),
            float(ratio),
        )
        status = (
            "interpolated"
            if before.status in CAMERA_MOTION_INTERPOLATED_STATUSES and after.status in CAMERA_MOTION_INTERPOLATED_STATUSES
            else "fallback"
        )
        reason = None if status == "interpolated" else f"interpolated_{before.reason or before.status}_to_{after.reason or after.status}"
        inlier_values = [value for value in (before.inlier_ratio, after.inlier_ratio) if value is not None]
        inlier_ratio = sum(inlier_values) / len(inlier_values) if inlier_values else None
        estimator = before.estimator if before.estimator == after.estimator else "interpolated_mixed"
        return _sample_from_matrix(
            frame,
            self.fps,
            matrix,
            status=status,
            inlier_ratio=inlier_ratio,
            inliers=min(before.inliers, after.inliers),
            matches=min(before.matches, after.matches),
            reason=reason,
            estimator=estimator,
        )

    def transform_point(self, frame_idx: int, point: tuple[float, float] | list[float]) -> list[float]:
        sample = self.sample_for_frame(frame_idx)
        matrix = np.asarray(sample.matrix_current_to_reference, dtype=np.float32)
        mapped = _transform_points(np.asarray([[float(point[0]), float(point[1])]], dtype=np.float32), matrix)
        return [round(float(mapped[0][0]), 2), round(float(mapped[0][1]), 2)]

    def polygon_for_frame(self, frame_idx: int, reference_polygon: Any) -> np.ndarray:
        sample = self.sample_for_frame(frame_idx)
        matrix = np.asarray(sample.matrix_reference_to_current, dtype=np.float32)
        polygon = np.asarray(reference_polygon, dtype=np.float32)
        return _transform_points(polygon, matrix).astype(np.float32)

    def metadata_for_frame(self, frame_idx: int) -> dict[str, Any]:
        sample = self.sample_for_frame(frame_idx)
        return {
            "camera_motion_sample_frame": sample.frame,
            "camera_motion_status": sample.status,
            "camera_motion_inlier_ratio": sample.inlier_ratio,
            "camera_motion_fallback": sample.status == "fallback",
        }

    def report(self) -> dict[str, Any]:
        ok_samples = [sample for sample in self.samples if sample.status in CAMERA_MOTION_INTERPOLATED_STATUSES]
        fallback_samples = [sample for sample in self.samples if sample.status == "fallback"]
        failed_samples = [sample for sample in self.samples if sample.status == "failed"]
        chained_samples = [sample for sample in self.samples if sample.status == "chained"]
        inlier_ratios = [float(sample.inlier_ratio) for sample in ok_samples if sample.inlier_ratio is not None]
        estimator_counts = {
            estimator: sum(1 for sample in self.samples if sample.estimator == estimator)
            for estimator in sorted({sample.estimator for sample in self.samples if sample.estimator})
        }
        return {
            "schema_version": "0.1.0",
            "generated_at": now_iso(),
            "status": "enabled" if self.enabled else "disabled",
            "reference_frame": self.reference_frame,
            "reference_time_sec": round(self.reference_time_sec, 3),
            "parameters": {
                "interval_sec": self.interval_sec,
                "min_inlier_ratio": self.min_inlier_ratio,
                "min_inliers": DEFAULT_CAMERA_MOTION_MIN_INLIERS,
                "max_features": DEFAULT_CAMERA_MOTION_MAX_FEATURES,
                "max_translation_px": DEFAULT_CAMERA_MOTION_MAX_TRANSLATION_PX,
                "max_rotation_deg": DEFAULT_CAMERA_MOTION_MAX_ROTATION_DEG,
                "max_scale_delta": DEFAULT_CAMERA_MOTION_MAX_SCALE_DELTA,
                "max_fallback_hold_sec": DEFAULT_CAMERA_MOTION_MAX_FALLBACK_HOLD_SEC,
                "max_sample_step_px": DEFAULT_CAMERA_MOTION_MAX_SAMPLE_STEP_PX,
                "relaxed_min_inlier_ratio": DEFAULT_CAMERA_MOTION_RELAXED_MIN_INLIER_RATIO,
                "relaxed_min_inliers": DEFAULT_CAMERA_MOTION_RELAXED_MIN_INLIERS,
                "ecc_min_correlation": DEFAULT_CAMERA_MOTION_ECC_MIN_CORRELATION,
                "ecc_downscale": DEFAULT_CAMERA_MOTION_ECC_DOWNSCALE,
                "max_rapid_step_px": DEFAULT_CAMERA_MOTION_MAX_RAPID_STEP_PX,
                "max_rapid_rotation_deg": DEFAULT_CAMERA_MOTION_MAX_RAPID_ROTATION_DEG,
                "pitch_mask_dilation_px": DEFAULT_CAMERA_MOTION_PITCH_MASK_DILATION_PX,
                "homography_min_inliers": DEFAULT_CAMERA_MOTION_HOMOGRAPHY_MIN_INLIERS,
                "homography_min_inlier_ratio": DEFAULT_CAMERA_MOTION_HOMOGRAPHY_MIN_INLIER_RATIO,
                "max_perspective_delta": DEFAULT_CAMERA_MOTION_MAX_PERSPECTIVE_DELTA,
                "drift_window_sec": DEFAULT_CAMERA_MOTION_DRIFT_WINDOW_SEC,
                "max_window_step_px": DEFAULT_CAMERA_MOTION_MAX_WINDOW_STEP_PX,
                "estimator": "pitch_masked_orb_homography_then_affine_then_ecc",
                "motion_strategy": "pitch_masked_direct_reference_then_chained_incremental_then_ecc",
            },
            "summary": {
                "sample_count": len(self.samples),
                "ok_samples": len(ok_samples),
                "chained_samples": len(chained_samples),
                "fallback_samples": len(fallback_samples),
                "failed_samples": len(failed_samples),
                "mean_inlier_ratio": round(sum(inlier_ratios) / len(inlier_ratios), 4) if inlier_ratios else None,
                "max_abs_dx_px": round(max((abs(sample.dx_px) for sample in self.samples), default=0.0), 2),
                "max_abs_dy_px": round(max((abs(sample.dy_px) for sample in self.samples), default=0.0), 2),
                "max_abs_rotation_deg": round(max((abs(sample.rotation_deg) for sample in self.samples), default=0.0), 4),
                "max_abs_scale_delta": round(max((abs(sample.scale - 1.0) for sample in self.samples), default=0.0), 5),
                "max_sample_step_px": round(_max_sample_step_px(self.samples), 2),
                "estimator_counts": estimator_counts,
                "stale_hold_samples": sum(1 for sample in self.samples if sample.reason and "stale_hold" in sample.reason),
                "identity_fallback_samples": sum(
                    1
                    for sample in fallback_samples
                    if np.allclose(np.asarray(sample.matrix_current_to_reference, dtype=np.float32), _identity_matrix())
                ),
            },
            "samples": [
                {
                    "frame": sample.frame,
                    "time_sec": round(sample.time_sec, 3),
                    "status": sample.status,
                    "inlier_ratio": sample.inlier_ratio,
                    "inliers": sample.inliers,
                    "matches": sample.matches,
                    "dx_px": round(sample.dx_px, 2),
                    "dy_px": round(sample.dy_px, 2),
                    "rotation_deg": round(sample.rotation_deg, 4),
                    "scale": round(sample.scale, 5),
                    "reason": sample.reason,
                    "estimator": sample.estimator,
                }
                for sample in self.samples
            ],
        }


def build_camera_motion_model(
    video_path: Path,
    metadata: dict[str, Any],
    *,
    calibration_frame_time_sec: float = 0.0,
    start_time_sec: float = 0.0,
    end_time_sec: float | None = None,
    interval_sec: float = DEFAULT_CAMERA_MOTION_INTERVAL_SEC,
    min_inlier_ratio: float = DEFAULT_CAMERA_MOTION_MIN_INLIER_RATIO,
    enabled: bool = DEFAULT_CAMERA_MOTION_COMPENSATION,
    reference_pitch_polygon: Any | None = None,
) -> CameraMotionModel:
    fps = float(metadata.get("fps") or 0.0)
    frame_count = int(metadata.get("frame_count") or 0)
    if not enabled or fps <= 0 or frame_count <= 0:
        return CameraMotionModel.disabled(fps=fps, frame_count=frame_count)

    start_frame = max(0, int(round(max(0.0, start_time_sec) * fps)))
    if end_time_sec is None or end_time_sec <= 0:
        end_frame = frame_count - 1
    else:
        end_frame = min(frame_count - 1, max(start_frame, int(round(float(end_time_sec) * fps))))
    reference_frame = min(frame_count - 1, max(0, int(round(max(0.0, calibration_frame_time_sec) * fps))))
    interval_frames = max(1, int(round(max(0.05, float(interval_sec)) * fps)))

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video for camera motion estimation: {video_path}")
    try:
        reference_frame_img = _read_frame(cap, reference_frame)
        if reference_frame_img is None:
            return CameraMotionModel.disabled(fps=fps, frame_count=frame_count)
        reference_gray = _preprocess_frame(reference_frame_img)
        pitch_polygon = _normalize_pitch_polygon(reference_pitch_polygon)
        reference_pitch_mask = _pitch_mask_for_polygon(reference_gray.shape, pitch_polygon)
        samples: list[CameraMotionSample] = []
        last_good = _identity_matrix()
        last_good_frame = reference_frame
        last_good_gray = reference_gray
        sample_frames = sorted(set([reference_frame, *range(start_frame, end_frame + 1, interval_frames), end_frame]))
        for frame_idx in sample_frames:
            if frame_idx == reference_frame:
                sample = _sample_from_matrix(
                    frame_idx,
                    fps,
                    _identity_matrix(),
                    status="identity",
                    inlier_ratio=1.0,
                    inliers=0,
                    matches=0,
                    estimator="identity",
                )
                samples.append(sample)
                last_good = _identity_matrix()
                last_good_frame = frame_idx
                last_good_gray = reference_gray
                continue
            frame = _read_frame(cap, frame_idx)
            if frame is None:
                samples.append(
                    _fallback_sample_from_last_good(
                        frame_idx,
                        fps,
                        last_good,
                        last_good_frame,
                        reason="frame_read_failed",
                    )
                )
                continue
            current_gray = _preprocess_frame(frame)
            current_pitch_mask = _predicted_pitch_mask(current_gray.shape, pitch_polygon, last_good)
            estimate = _estimate_current_to_reference(
                current_gray,
                reference_gray,
                current_mask=current_pitch_mask,
                reference_mask=reference_pitch_mask,
            )
            if estimate is None:
                chained = _estimate_chained_current_to_reference(
                    current_gray,
                    last_good_gray,
                    last_good,
                    min_inlier_ratio=min_inlier_ratio,
                    reference_pitch_polygon=pitch_polygon,
                )
                if chained is None:
                    samples.append(
                        _fallback_sample_from_last_good(
                            frame_idx,
                            fps,
                            last_good,
                            last_good_frame,
                            reason="estimate_failed",
                        )
                    )
                    continue
                matrix, inlier_ratio, inliers, matches, chain_reason, estimator = chained
                status = "chained"
                reason = chain_reason
            else:
                matrix = estimate.matrix
                inlier_ratio = estimate.inlier_ratio
                inliers = estimate.inliers
                matches = estimate.matches
                estimator = estimate.estimator
                status = "ok"
                reason = None
                if inliers < DEFAULT_CAMERA_MOTION_MIN_INLIERS or inlier_ratio < min_inlier_ratio:
                    relaxed_reason = _camera_motion_sanity_rejection_reason(matrix, previous_matrix=last_good)
                    if relaxed_reason is None and _camera_motion_relaxed_confidence_ok(inlier_ratio, inliers):
                        reason = "relaxed_temporal_direct"
                    else:
                        chained = _estimate_chained_current_to_reference(
                            current_gray,
                            last_good_gray,
                            last_good,
                            min_inlier_ratio=min_inlier_ratio,
                            direct_rejection_reason="direct_low_confidence",
                            reference_pitch_polygon=pitch_polygon,
                        )
                        if chained is None:
                            samples.append(
                                _fallback_sample_from_last_good(
                                    frame_idx,
                                    fps,
                                    last_good,
                                    last_good_frame,
                                    inlier_ratio=inlier_ratio,
                                    inliers=inliers,
                                    matches=matches,
                                    reason=relaxed_reason or "low_confidence",
                                )
                            )
                            continue
                        matrix, inlier_ratio, inliers, matches, reason, estimator = chained
                        status = "chained"
                else:
                    rejection_reason = _camera_motion_sanity_rejection_reason(matrix, previous_matrix=last_good)
                    if rejection_reason is not None:
                        chained = _estimate_chained_current_to_reference(
                            current_gray,
                            last_good_gray,
                            last_good,
                            min_inlier_ratio=min_inlier_ratio,
                            direct_rejection_reason=rejection_reason,
                            reference_pitch_polygon=pitch_polygon,
                        )
                        if chained is None:
                            samples.append(
                                _fallback_sample_from_last_good(
                                    frame_idx,
                                    fps,
                                    last_good,
                                    last_good_frame,
                                    inlier_ratio=inlier_ratio,
                                    inliers=inliers,
                                    matches=matches,
                                    reason=rejection_reason,
                                )
                            )
                            continue
                        matrix, inlier_ratio, inliers, matches, reason, estimator = chained
                        status = "chained"
            last_good = matrix
            last_good_frame = frame_idx
            last_good_gray = current_gray
            samples.append(
                _sample_from_matrix(
                    frame_idx,
                    fps,
                    matrix,
                    status=status,
                    inlier_ratio=inlier_ratio,
                    inliers=inliers,
                    matches=matches,
                    reason=reason,
                    estimator=estimator,
                )
            )
    finally:
        cap.release()

    samples = _smooth_successful_samples(samples, fps, reference_pitch_polygon=_normalize_pitch_polygon(reference_pitch_polygon))
    return CameraMotionModel(
        enabled=True,
        reference_frame=reference_frame,
        reference_time_sec=reference_frame / max(fps, 0.001),
        frame_count=frame_count,
        fps=fps,
        interval_sec=interval_sec,
        min_inlier_ratio=min_inlier_ratio,
        samples=samples,
    )


def write_camera_motion_report(match_dir: Path, model: CameraMotionModel) -> Path:
    path = match_dir / "camera_motion_report.json"
    path.write_text(json.dumps(model.report(), indent=2), encoding="utf-8")
    return path


def write_camera_motion_overlay(
    video_path: Path,
    match_dir: Path,
    model: CameraMotionModel,
    reference_pitch_polygon: Any,
    metadata: dict[str, Any],
    *,
    frame_stride: int,
    max_seconds: float,
    output_name: str = "camera_motion_overlay.mp4",
) -> Path:
    fps = float(metadata.get("fps") or 0.0)
    width = int(metadata.get("width") or 0)
    height = int(metadata.get("height") or 0)
    frame_count = int(metadata.get("frame_count") or 0)
    if fps <= 0 or width <= 0 or height <= 0:
        raise ValueError("Video metadata is missing fps/width/height for camera motion overlay.")
    max_frame = int(max_seconds * fps) if max_seconds > 0 else frame_count - 1
    max_frame = min(max_frame, frame_count - 1) if frame_count > 0 else max_frame
    stride = max(1, int(frame_stride))
    reference_polygon = np.asarray(reference_pitch_polygon, dtype=np.float32)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video for camera motion overlay: {video_path}")
    temp_path = match_dir / f"{output_name}.raw.avi"
    final_path = match_dir / output_name
    writer = cv2.VideoWriter(str(temp_path), cv2.VideoWriter_fourcc(*"MJPG"), max(1.0, fps / stride), (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"Could not open OpenCV VideoWriter for {temp_path.name}.")
    frame_idx = 0
    frames_written = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame_idx > max_frame:
                break
            if frame_idx % stride != 0:
                frame_idx += 1
                continue
            overlay = frame.copy()
            dynamic_polygon = model.polygon_for_frame(frame_idx, reference_polygon)
            cv2.polylines(overlay, [reference_polygon.astype(np.int32)], True, (80, 80, 255), 2)
            cv2.polylines(overlay, [dynamic_polygon.astype(np.int32)], True, (0, 255, 255), 2)
            sample = model.sample_for_frame(frame_idx)
            label = (
                f"camera motion {sample.status} ref={model.reference_frame} "
                f"sample={sample.frame} inliers={sample.inliers} ir={sample.inlier_ratio if sample.inlier_ratio is not None else 0:.2f}"
            )
            cv2.rectangle(overlay, (8, 8), (min(width - 8, 920), 50), (0, 0, 0), -1)
            cv2.putText(overlay, label, (18, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
            writer.write(overlay)
            frames_written += 1
            frame_idx += 1
    finally:
        cap.release()
        writer.release()
    if frames_written == 0:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError("Camera motion overlay was not generated because zero frames were processed.")
    if final_path.exists():
        final_path.unlink()
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is not available, so camera motion overlay could not be converted to MP4.")
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(temp_path),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(final_path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    temp_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg failed while converting camera motion overlay: {completed.stderr.strip()}")
    return final_path


def _read_frame(cap: Any, frame_idx: int) -> Any | None:
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
    ok, frame = cap.read()
    return frame if ok else None


def _preprocess_frame(frame: Any) -> Any:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.equalizeHist(gray)


def _normalize_pitch_polygon(reference_pitch_polygon: Any | None) -> np.ndarray | None:
    if reference_pitch_polygon is None:
        return None
    try:
        polygon = np.asarray(reference_pitch_polygon, dtype=np.float32).reshape(-1, 2)
    except (TypeError, ValueError):
        return None
    if len(polygon) < 4 or not np.isfinite(polygon).all():
        return None
    return polygon.astype(np.float32)


def _pitch_mask_for_polygon(image_shape: Any, polygon: np.ndarray | None) -> np.ndarray | None:
    if polygon is None:
        return None
    height = int(image_shape[0])
    width = int(image_shape[1])
    if height <= 0 or width <= 0:
        return None
    mask = np.zeros((height, width), dtype=np.uint8)
    points = np.asarray(polygon, dtype=np.float32).copy()
    points[:, 0] = np.clip(points[:, 0], -width, width * 2)
    points[:, 1] = np.clip(points[:, 1], -height, height * 2)
    cv2.fillPoly(mask, [points.astype(np.int32)], 255)
    dilation = int(DEFAULT_CAMERA_MOTION_PITCH_MASK_DILATION_PX)
    if dilation > 0:
        x_min = max(0, int(math.floor(float(np.min(points[:, 0])))) - dilation)
        y_min = max(0, int(math.floor(float(np.min(points[:, 1])))) - dilation)
        x_max = min(width - 1, int(math.ceil(float(np.max(points[:, 0])))) + dilation)
        y_max = min(height - 1, int(math.ceil(float(np.max(points[:, 1])))) + dilation)
        if x_max > x_min and y_max > y_min:
            mask[y_min : y_max + 1, x_min : x_max + 1] = 255
    return mask


def _predicted_pitch_polygon(reference_pitch_polygon: np.ndarray | None, current_to_reference: np.ndarray) -> np.ndarray | None:
    if reference_pitch_polygon is None:
        return None
    return _transform_points(reference_pitch_polygon, _safe_inverse(np.asarray(current_to_reference, dtype=np.float32)))


def _predicted_pitch_mask(
    image_shape: Any,
    reference_pitch_polygon: np.ndarray | None,
    current_to_reference: np.ndarray,
) -> np.ndarray | None:
    return _pitch_mask_for_polygon(image_shape, _predicted_pitch_polygon(reference_pitch_polygon, current_to_reference))


def _estimate_current_to_reference(
    current_gray: Any,
    reference_gray: Any,
    *,
    current_mask: Any | None = None,
    reference_mask: Any | None = None,
) -> _MotionEstimate | None:
    orb = cv2.ORB_create(nfeatures=DEFAULT_CAMERA_MOTION_MAX_FEATURES)
    kp_current, des_current = orb.detectAndCompute(current_gray, current_mask)
    kp_ref, des_ref = orb.detectAndCompute(reference_gray, reference_mask)
    if des_current is None or des_ref is None or len(kp_current) < 20 or len(kp_ref) < 20:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    matches = matcher.knnMatch(des_current, des_ref, k=2)
    good = []
    for pair in matches:
        if len(pair) < 2:
            continue
        first, second = pair
        if first.distance < 0.72 * second.distance:
            good.append(first)
    if len(good) < 12:
        return None
    src = np.float32([kp_current[match.queryIdx].pt for match in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_ref[match.trainIdx].pt for match in good]).reshape(-1, 1, 2)
    homography, homography_mask = cv2.findHomography(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
    homography = _normalize_homography(homography)
    if homography is not None and homography_mask is not None:
        inliers = int(homography_mask.ravel().sum())
        inlier_ratio = float(inliers / max(1, len(good)))
        perspective_rejection = _camera_motion_perspective_rejection_reason(homography)
        if (
            inliers >= DEFAULT_CAMERA_MOTION_HOMOGRAPHY_MIN_INLIERS
            and inlier_ratio >= DEFAULT_CAMERA_MOTION_HOMOGRAPHY_MIN_INLIER_RATIO
            and perspective_rejection is None
        ):
            return _MotionEstimate(homography.astype(np.float32), inlier_ratio, inliers, len(good), "orb_homography")
    affine, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
    if affine is None or mask is None:
        return None
    inliers = int(mask.ravel().sum())
    inlier_ratio = float(inliers / max(1, len(good)))
    matrix = np.eye(3, dtype=np.float32)
    matrix[:2, :] = affine.astype(np.float32)
    return _MotionEstimate(matrix, inlier_ratio, inliers, len(good), "orb_affine")


def _estimate_chained_current_to_reference(
    current_gray: Any,
    previous_gray: Any,
    previous_current_to_reference: np.ndarray,
    *,
    min_inlier_ratio: float,
    direct_rejection_reason: str = "direct_estimate_failed",
    reference_pitch_polygon: np.ndarray | None = None,
) -> tuple[np.ndarray, float, int, int, str, str] | None:
    previous_pitch_polygon = _predicted_pitch_polygon(reference_pitch_polygon, previous_current_to_reference)
    local_mask = _pitch_mask_for_polygon(current_gray.shape, previous_pitch_polygon)
    previous_mask = _pitch_mask_for_polygon(previous_gray.shape, previous_pitch_polygon)
    local_estimate = _estimate_current_to_reference(
        current_gray,
        previous_gray,
        current_mask=local_mask,
        reference_mask=previous_mask,
    )
    if local_estimate is not None:
        current_to_previous = local_estimate.matrix
        inlier_ratio = local_estimate.inlier_ratio
        inliers = local_estimate.inliers
        matches = local_estimate.matches
        local_rejection = _camera_motion_local_rejection_reason(current_to_previous)
        if inliers >= DEFAULT_CAMERA_MOTION_MIN_INLIERS and inlier_ratio >= min_inlier_ratio and local_rejection is None:
            matrix = np.asarray(previous_current_to_reference, dtype=np.float32) @ current_to_previous
            matrix = _normalize_homography(matrix)
            if matrix is not None:
                return (
                    matrix.astype(np.float32),
                    inlier_ratio,
                    inliers,
                    matches,
                    f"chained_after_{direct_rejection_reason}",
                    f"local_{local_estimate.estimator}",
                )
    ecc_estimate = _estimate_ecc_current_to_previous(current_gray, previous_gray)
    if ecc_estimate is None:
        return None
    current_to_previous, correlation = ecc_estimate
    rapid_rejection = _camera_motion_rapid_local_rejection_reason(current_to_previous)
    if rapid_rejection is not None:
        return None
    matrix = np.asarray(previous_current_to_reference, dtype=np.float32) @ current_to_previous
    matrix = _normalize_homography(matrix)
    if matrix is None:
        return None
    return matrix.astype(np.float32), correlation, 0, 0, f"ecc_after_{direct_rejection_reason}", "ecc_euclidean"


def _fallback_sample_from_last_good(
    frame_idx: int,
    fps: float,
    last_good: np.ndarray,
    last_good_frame: int,
    *,
    inlier_ratio: float | None = None,
    inliers: int = 0,
    matches: int = 0,
    reason: str,
) -> CameraMotionSample:
    matrix, fallback_reason = _fallback_matrix_and_reason(frame_idx, fps, last_good, last_good_frame, reason)
    return _sample_from_matrix(
        frame_idx,
        fps,
        matrix,
        status="fallback",
        inlier_ratio=inlier_ratio,
        inliers=inliers,
        matches=matches,
        reason=fallback_reason,
    )


def _fallback_matrix_and_reason(
    frame_idx: int,
    fps: float,
    last_good: np.ndarray,
    last_good_frame: int,
    reason: str,
) -> tuple[np.ndarray, str]:
    hold_frames = int(round(DEFAULT_CAMERA_MOTION_MAX_FALLBACK_HOLD_SEC * max(fps, 0.0)))
    if hold_frames > 0 and int(frame_idx) - int(last_good_frame) > hold_frames:
        return last_good, f"{reason}_stale_hold"
    return last_good, reason


def _camera_motion_sanity_rejection_reason(matrix: np.ndarray, *, previous_matrix: np.ndarray | None = None) -> str | None:
    perspective_rejection = _camera_motion_perspective_rejection_reason(matrix)
    if perspective_rejection is not None:
        return perspective_rejection
    if previous_matrix is not None:
        relative = _relative_motion_matrix(matrix, previous_matrix)
        local_rejection = _camera_motion_local_rejection_reason(relative)
        if local_rejection is not None:
            return f"direct_{local_rejection}"
        dx, dy, rotation, scale = _camera_motion_components(matrix)
        if abs(rotation) > DEFAULT_CAMERA_MOTION_MAX_ROTATION_DEG * 4.0:
            return "motion_rotation_out_of_range"
        if abs(scale - 1.0) > DEFAULT_CAMERA_MOTION_MAX_SCALE_DELTA * 2.5:
            return "motion_scale_out_of_range"
        return None
    dx, dy, rotation, scale = _camera_motion_components(matrix)
    if max(abs(dx), abs(dy)) > DEFAULT_CAMERA_MOTION_MAX_TRANSLATION_PX:
        return "motion_translation_out_of_range"
    if abs(rotation) > DEFAULT_CAMERA_MOTION_MAX_ROTATION_DEG:
        return "motion_rotation_out_of_range"
    if abs(scale - 1.0) > DEFAULT_CAMERA_MOTION_MAX_SCALE_DELTA:
        return "motion_scale_out_of_range"
    return None


def _camera_motion_local_rejection_reason(matrix: np.ndarray) -> str | None:
    perspective_rejection = _camera_motion_perspective_rejection_reason(matrix)
    if perspective_rejection is not None:
        return perspective_rejection.replace("motion_", "local_motion_", 1)
    dx, dy, rotation, scale = _camera_motion_components(matrix)
    if math.hypot(dx, dy) > DEFAULT_CAMERA_MOTION_MAX_SAMPLE_STEP_PX * 1.4:
        return "local_motion_step_out_of_range"
    if abs(rotation) > DEFAULT_CAMERA_MOTION_MAX_ROTATION_DEG:
        return "local_motion_rotation_out_of_range"
    if abs(scale - 1.0) > DEFAULT_CAMERA_MOTION_MAX_SCALE_DELTA:
        return "local_motion_scale_out_of_range"
    return None


def _camera_motion_rapid_local_rejection_reason(matrix: np.ndarray) -> str | None:
    perspective_rejection = _camera_motion_perspective_rejection_reason(matrix, multiplier=1.8)
    if perspective_rejection is not None:
        return perspective_rejection.replace("motion_", "rapid_motion_", 1)
    dx, dy, rotation, scale = _camera_motion_components(matrix)
    if math.hypot(dx, dy) > DEFAULT_CAMERA_MOTION_MAX_RAPID_STEP_PX:
        return "rapid_motion_step_out_of_range"
    if abs(rotation) > DEFAULT_CAMERA_MOTION_MAX_RAPID_ROTATION_DEG:
        return "rapid_motion_rotation_out_of_range"
    if abs(scale - 1.0) > DEFAULT_CAMERA_MOTION_MAX_SCALE_DELTA * 3.0:
        return "rapid_motion_scale_out_of_range"
    return None


def _camera_motion_perspective_rejection_reason(matrix: np.ndarray, *, multiplier: float = 1.0) -> str | None:
    normalized = _normalize_homography(matrix)
    if normalized is None:
        return "motion_invalid_matrix"
    perspective = math.hypot(float(normalized[2, 0]), float(normalized[2, 1]))
    if perspective > DEFAULT_CAMERA_MOTION_MAX_PERSPECTIVE_DELTA * float(multiplier):
        return "motion_perspective_out_of_range"
    return None


def _estimate_ecc_current_to_previous(current_gray: Any, previous_gray: Any) -> tuple[np.ndarray, float] | None:
    scale = float(DEFAULT_CAMERA_MOTION_ECC_DOWNSCALE)
    if scale <= 0 or scale > 1:
        scale = 1.0
    previous_small = cv2.resize(previous_gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    current_small = cv2.resize(current_gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    previous_float = previous_small.astype(np.float32) / 255.0
    current_float = current_small.astype(np.float32) / 255.0
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5)
    try:
        correlation, affine = cv2.findTransformECC(
            previous_float,
            current_float,
            warp,
            cv2.MOTION_EUCLIDEAN,
            criteria,
            None,
            5,
        )
    except cv2.error:
        return None
    if not math.isfinite(float(correlation)) or float(correlation) < DEFAULT_CAMERA_MOTION_ECC_MIN_CORRELATION:
        return None
    previous_to_current = np.eye(3, dtype=np.float32)
    previous_to_current[:2, :] = affine.astype(np.float32)
    previous_to_current[0, 2] /= scale
    previous_to_current[1, 2] /= scale
    try:
        current_to_previous = np.linalg.inv(previous_to_current).astype(np.float32)
    except np.linalg.LinAlgError:
        return None
    current_to_previous[2] = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    return current_to_previous, float(correlation)


def _camera_motion_relaxed_confidence_ok(inlier_ratio: float, inliers: int) -> bool:
    return inliers >= DEFAULT_CAMERA_MOTION_RELAXED_MIN_INLIERS and inlier_ratio >= DEFAULT_CAMERA_MOTION_RELAXED_MIN_INLIER_RATIO


def _normalize_homography(matrix: Any | None) -> np.ndarray | None:
    if matrix is None:
        return None
    normalized = np.asarray(matrix, dtype=np.float32)
    if normalized.shape == (2, 3):
        full = np.eye(3, dtype=np.float32)
        full[:2, :] = normalized
        normalized = full
    if normalized.shape != (3, 3) or not np.isfinite(normalized).all():
        return None
    denominator = float(normalized[2, 2])
    if abs(denominator) < 1e-6:
        return None
    normalized = normalized / denominator
    if not np.isfinite(normalized).all():
        return None
    return normalized.astype(np.float32)


def _is_perspective_matrix(matrix: np.ndarray) -> bool:
    normalized = _normalize_homography(matrix)
    if normalized is None:
        return False
    return math.hypot(float(normalized[2, 0]), float(normalized[2, 1])) > 1e-7


def _relative_motion_matrix(matrix: np.ndarray, previous_matrix: np.ndarray) -> np.ndarray:
    try:
        previous_to_current = np.linalg.inv(np.asarray(previous_matrix, dtype=np.float32))
    except np.linalg.LinAlgError:
        previous_to_current = _identity_matrix()
    relative = previous_to_current @ np.asarray(matrix, dtype=np.float32)
    normalized = _normalize_homography(relative)
    return normalized if normalized is not None else _identity_matrix()


def _interpolate_motion_matrix(a: np.ndarray, b: np.ndarray, ratio: float) -> np.ndarray:
    if _is_perspective_matrix(a) or _is_perspective_matrix(b):
        return _blend_motion_matrices([a, b], [1.0 - float(ratio), float(ratio)])
    a_dx, a_dy, a_rotation, a_scale = _camera_motion_components(a)
    b_dx, b_dy, b_rotation, b_scale = _camera_motion_components(b)
    rotation_delta = ((b_rotation - a_rotation + 180.0) % 360.0) - 180.0
    return _matrix_from_components(
        dx=_lerp(a_dx, b_dx, ratio),
        dy=_lerp(a_dy, b_dy, ratio),
        rotation_deg=a_rotation + rotation_delta * ratio,
        scale=_lerp(a_scale, b_scale, ratio),
    )


def _blend_motion_matrices(matrices: list[np.ndarray], weights: list[float]) -> np.ndarray:
    normalized_pairs = [
        (normalized, float(weight))
        for matrix, weight in zip(matrices, weights)
        if (normalized := _normalize_homography(matrix)) is not None and float(weight) > 0
    ]
    if not normalized_pairs:
        return _identity_matrix()
    total_weight = sum(weight for _, weight in normalized_pairs)
    if total_weight <= 0:
        return normalized_pairs[0][0]
    blended = sum(matrix * (weight / total_weight) for matrix, weight in normalized_pairs)
    normalized = _normalize_homography(blended)
    return normalized if normalized is not None else normalized_pairs[0][0]


def _smooth_successful_samples(
    samples: list[CameraMotionSample],
    fps: float,
    *,
    reference_pitch_polygon: np.ndarray | None = None,
) -> list[CameraMotionSample]:
    if len(samples) < 3:
        return samples
    smoothed: list[CameraMotionSample] = []
    matrices = [np.asarray(sample.matrix_current_to_reference, dtype=np.float32) for sample in samples]
    for index, sample in enumerate(samples):
        if sample.status not in {"ok", "chained"}:
            smoothed.append(sample)
            continue
        candidates = []
        weights = []
        for neighbor_index, weight in [(index - 1, 0.25), (index, 0.5), (index + 1, 0.25)]:
            if neighbor_index < 0 or neighbor_index >= len(samples):
                continue
            neighbor = samples[neighbor_index]
            if neighbor.status not in CAMERA_MOTION_INTERPOLATED_STATUSES:
                continue
            candidates.append(matrices[neighbor_index])
            weights.append(weight)
        if len(candidates) < 2:
            smoothed.append(sample)
            continue
        total_weight = float(sum(weights))
        if any(_is_perspective_matrix(candidate) for candidate in candidates):
            matrix = _blend_motion_matrices(candidates, weights)
        else:
            components = [_camera_motion_components(candidate) for candidate in candidates]
            matrix = _matrix_from_components(
                dx=sum(component[0] * (weight / total_weight) for component, weight in zip(components, weights)),
                dy=sum(component[1] * (weight / total_weight) for component, weight in zip(components, weights)),
                rotation_deg=sum(component[2] * (weight / total_weight) for component, weight in zip(components, weights)),
                scale=sum(component[3] * (weight / total_weight) for component, weight in zip(components, weights)),
            )
        smoothed.append(
            _sample_from_matrix(
                sample.frame,
                fps,
                matrix,
                status=sample.status,
                inlier_ratio=sample.inlier_ratio,
                inliers=sample.inliers,
                matches=sample.matches,
                reason=sample.reason,
                estimator=sample.estimator,
            )
        )
    return _limit_camera_motion_sample_jumps(smoothed, fps, reference_pitch_polygon=reference_pitch_polygon)


def _limit_camera_motion_sample_jumps(
    samples: list[CameraMotionSample],
    fps: float,
    *,
    reference_pitch_polygon: np.ndarray | None = None,
) -> list[CameraMotionSample]:
    if len(samples) < 2:
        return samples
    limited: list[CameraMotionSample] = []
    last_matrix: np.ndarray | None = None
    recent: list[CameraMotionSample] = []
    window_frames = int(round(DEFAULT_CAMERA_MOTION_DRIFT_WINDOW_SEC * max(fps, 0.0)))
    for sample in samples:
        matrix = np.asarray(sample.matrix_current_to_reference, dtype=np.float32)
        if last_matrix is not None and sample.status in CAMERA_MOTION_TRUSTED_STATUSES:
            previous_dx, previous_dy, _, _ = _camera_motion_components(last_matrix)
            dx, dy, _, _ = _camera_motion_components(matrix)
            step = math.hypot(dx - previous_dx, dy - previous_dy)
            rejection_reason = None
            if step > DEFAULT_CAMERA_MOTION_MAX_SAMPLE_STEP_PX:
                rejection_reason = "sample_step_out_of_range_hold"
            elif _camera_motion_window_drift_rejection_reason(sample, recent):
                rejection_reason = "sample_window_drift_out_of_range_hold"
            elif reference_pitch_polygon is not None:
                rejection_reason = _camera_motion_polygon_rejection_reason(matrix, last_matrix, reference_pitch_polygon)
            if rejection_reason is not None:
                sample = _sample_from_matrix(
                    sample.frame,
                    fps,
                    last_matrix,
                    status="fallback",
                    inlier_ratio=sample.inlier_ratio,
                    inliers=sample.inliers,
                    matches=sample.matches,
                    reason=rejection_reason,
                    estimator=sample.estimator,
                )
                matrix = last_matrix
        limited.append(sample)
        if sample.status in CAMERA_MOTION_TRUSTED_STATUSES:
            last_matrix = np.asarray(sample.matrix_current_to_reference, dtype=np.float32)
            recent.append(sample)
            if window_frames > 0:
                recent = [item for item in recent if sample.frame - item.frame <= window_frames]
    return limited


def _camera_motion_window_drift_rejection_reason(sample: CameraMotionSample, recent: list[CameraMotionSample]) -> str | None:
    if not recent:
        return None
    if not sample.reason or not any(
        token in sample.reason for token in ("ecc", "low_confidence", "estimate_failed", "local_motion")
    ):
        return None
    sample_dx, sample_dy, _, _ = _camera_motion_components(
        np.asarray(sample.matrix_current_to_reference, dtype=np.float32)
    )
    for previous in recent:
        previous_dx, previous_dy, _, _ = _camera_motion_components(
            np.asarray(previous.matrix_current_to_reference, dtype=np.float32)
        )
        if math.hypot(sample_dx - previous_dx, sample_dy - previous_dy) > DEFAULT_CAMERA_MOTION_MAX_WINDOW_STEP_PX:
            return "sample_window_drift_out_of_range"
    return None


def _camera_motion_polygon_rejection_reason(
    matrix: np.ndarray,
    previous_matrix: np.ndarray,
    reference_pitch_polygon: np.ndarray,
) -> str | None:
    current_polygon = _predicted_pitch_polygon(reference_pitch_polygon, matrix)
    previous_polygon = _predicted_pitch_polygon(reference_pitch_polygon, previous_matrix)
    if current_polygon is None or previous_polygon is None:
        return None
    current_area = abs(float(cv2.contourArea(current_polygon.astype(np.float32))))
    reference_area = abs(float(cv2.contourArea(reference_pitch_polygon.astype(np.float32))))
    if reference_area > 1.0:
        area_ratio = current_area / reference_area
        if area_ratio < DEFAULT_CAMERA_MOTION_MIN_POLYGON_AREA_RATIO:
            return "sample_polygon_area_too_small_hold"
        if area_ratio > DEFAULT_CAMERA_MOTION_MAX_POLYGON_AREA_RATIO:
            return "sample_polygon_area_too_large_hold"
    if len(current_polygon) == len(previous_polygon):
        corner_step = float(np.max(np.linalg.norm(current_polygon - previous_polygon, axis=1)))
        if corner_step > DEFAULT_CAMERA_MOTION_MAX_POLYGON_CORNER_STEP_PX:
            return "sample_polygon_corner_step_out_of_range_hold"
    return None


def _sample_from_matrix(
    frame_idx: int,
    fps: float,
    matrix_current_to_reference: np.ndarray,
    *,
    status: str,
    inlier_ratio: float | None = None,
    inliers: int = 0,
    matches: int = 0,
    reason: str | None = None,
    estimator: str | None = None,
) -> CameraMotionSample:
    matrix_current_to_reference = _normalize_homography(matrix_current_to_reference)
    if matrix_current_to_reference is None:
        matrix_current_to_reference = _identity_matrix()
    inverse = _safe_inverse(matrix_current_to_reference)
    dx, dy, rotation, scale = _camera_motion_components(matrix_current_to_reference)
    return CameraMotionSample(
        frame=int(frame_idx),
        time_sec=round(frame_idx / max(fps, 0.001), 3),
        status=status,
        matrix_current_to_reference=matrix_current_to_reference.astype(float).round(6).tolist(),
        matrix_reference_to_current=inverse.astype(float).round(6).tolist(),
        inlier_ratio=round(float(inlier_ratio), 4) if inlier_ratio is not None else None,
        inliers=int(inliers),
        matches=int(matches),
        dx_px=dx,
        dy_px=dy,
        rotation_deg=rotation,
        scale=scale,
        reason=reason,
        estimator=estimator,
    )


def _camera_motion_components(matrix: np.ndarray) -> tuple[float, float, float, float]:
    dx = float(matrix[0, 2])
    dy = float(matrix[1, 2])
    rotation = math.degrees(math.atan2(float(matrix[1, 0]), float(matrix[0, 0])))
    scale = math.sqrt(float(matrix[0, 0]) ** 2 + float(matrix[1, 0]) ** 2)
    return dx, dy, rotation, scale


def _matrix_from_components(*, dx: float, dy: float, rotation_deg: float, scale: float) -> np.ndarray:
    radians = math.radians(float(rotation_deg))
    cos_value = math.cos(radians) * float(scale)
    sin_value = math.sin(radians) * float(scale)
    matrix = np.eye(3, dtype=np.float32)
    matrix[0, 0] = cos_value
    matrix[0, 1] = -sin_value
    matrix[1, 0] = sin_value
    matrix[1, 1] = cos_value
    matrix[0, 2] = float(dx)
    matrix[1, 2] = float(dy)
    return matrix


def _lerp(a: float, b: float, ratio: float) -> float:
    return float(a) + (float(b) - float(a)) * float(ratio)


def _max_sample_step_px(samples: list[CameraMotionSample]) -> float:
    max_step = 0.0
    previous: CameraMotionSample | None = None
    for sample in samples:
        if previous is not None:
            step = math.hypot(sample.dx_px - previous.dx_px, sample.dy_px - previous.dy_px)
            max_step = max(max_step, step)
        previous = sample
    return max_step


def _transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    if len(points) == 0:
        return points.reshape(0, 2)
    points = points.astype(np.float32).reshape(-1, 1, 2)
    transformed = cv2.perspectiveTransform(points, matrix.astype(np.float32)).reshape(-1, 2)
    return transformed


def _safe_inverse(matrix: np.ndarray) -> np.ndarray:
    try:
        return np.linalg.inv(matrix).astype(np.float32)
    except np.linalg.LinAlgError:
        return _identity_matrix()


def _identity_matrix() -> np.ndarray:
    return np.eye(3, dtype=np.float32)
