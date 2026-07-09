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
DEFAULT_CAMERA_MOTION_MAX_SAMPLE_STEP_PX = 28.0


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
        before_matrix = np.asarray(before.matrix_current_to_reference, dtype=np.float32)
        after_matrix = np.asarray(after.matrix_current_to_reference, dtype=np.float32)
        matrix = before_matrix + (after_matrix - before_matrix) * float(ratio)
        matrix[2] = np.array([0.0, 0.0, 1.0], dtype=np.float32)
        status = "interpolated" if before.status in {"ok", "identity"} and after.status in {"ok", "identity"} else "fallback"
        reason = None if status == "interpolated" else f"interpolated_{before.reason or before.status}_to_{after.reason or after.status}"
        inlier_values = [value for value in (before.inlier_ratio, after.inlier_ratio) if value is not None]
        inlier_ratio = sum(inlier_values) / len(inlier_values) if inlier_values else None
        return _sample_from_matrix(
            frame,
            self.fps,
            matrix,
            status=status,
            inlier_ratio=inlier_ratio,
            inliers=min(before.inliers, after.inliers),
            matches=min(before.matches, after.matches),
            reason=reason,
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
        ok_samples = [sample for sample in self.samples if sample.status in {"ok", "identity"}]
        fallback_samples = [sample for sample in self.samples if sample.status == "fallback"]
        failed_samples = [sample for sample in self.samples if sample.status == "failed"]
        inlier_ratios = [float(sample.inlier_ratio) for sample in ok_samples if sample.inlier_ratio is not None]
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
                "estimator": "orb_affine_partial_ransac",
            },
            "summary": {
                "sample_count": len(self.samples),
                "ok_samples": len(ok_samples),
                "fallback_samples": len(fallback_samples),
                "failed_samples": len(failed_samples),
                "mean_inlier_ratio": round(sum(inlier_ratios) / len(inlier_ratios), 4) if inlier_ratios else None,
                "max_abs_dx_px": round(max((abs(sample.dx_px) for sample in self.samples), default=0.0), 2),
                "max_abs_dy_px": round(max((abs(sample.dy_px) for sample in self.samples), default=0.0), 2),
                "max_abs_rotation_deg": round(max((abs(sample.rotation_deg) for sample in self.samples), default=0.0), 4),
                "max_abs_scale_delta": round(max((abs(sample.scale - 1.0) for sample in self.samples), default=0.0), 5),
                "max_sample_step_px": round(_max_sample_step_px(self.samples), 2),
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
        samples: list[CameraMotionSample] = []
        last_good = _identity_matrix()
        last_good_frame = reference_frame
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
                )
                samples.append(sample)
                last_good = _identity_matrix()
                last_good_frame = frame_idx
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
            estimate = _estimate_current_to_reference(_preprocess_frame(frame), reference_gray)
            if estimate is None:
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
            matrix, inlier_ratio, inliers, matches = estimate
            if inliers < DEFAULT_CAMERA_MOTION_MIN_INLIERS or inlier_ratio < min_inlier_ratio:
                samples.append(
                    _fallback_sample_from_last_good(
                        frame_idx,
                        fps,
                        last_good,
                        last_good_frame,
                        inlier_ratio=inlier_ratio,
                        inliers=inliers,
                        matches=matches,
                        reason="low_confidence",
                    )
                )
                continue
            rejection_reason = _camera_motion_sanity_rejection_reason(matrix)
            if rejection_reason is not None:
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
            last_good = matrix
            last_good_frame = frame_idx
            samples.append(
                _sample_from_matrix(
                    frame_idx,
                    fps,
                    matrix,
                    status="ok",
                    inlier_ratio=inlier_ratio,
                    inliers=inliers,
                    matches=matches,
                )
            )
    finally:
        cap.release()

    samples = _smooth_successful_samples(samples, fps)
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


def _estimate_current_to_reference(current_gray: Any, reference_gray: Any) -> tuple[np.ndarray, float, int, int] | None:
    orb = cv2.ORB_create(nfeatures=DEFAULT_CAMERA_MOTION_MAX_FEATURES)
    kp_current, des_current = orb.detectAndCompute(current_gray, None)
    kp_ref, des_ref = orb.detectAndCompute(reference_gray, None)
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
    affine, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
    if affine is None or mask is None:
        return None
    inliers = int(mask.ravel().sum())
    inlier_ratio = float(inliers / max(1, len(good)))
    matrix = np.eye(3, dtype=np.float32)
    matrix[:2, :] = affine.astype(np.float32)
    return matrix, inlier_ratio, inliers, len(good)


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


def _camera_motion_sanity_rejection_reason(matrix: np.ndarray) -> str | None:
    dx, dy, rotation, scale = _camera_motion_components(matrix)
    if max(abs(dx), abs(dy)) > DEFAULT_CAMERA_MOTION_MAX_TRANSLATION_PX:
        return "motion_translation_out_of_range"
    if abs(rotation) > DEFAULT_CAMERA_MOTION_MAX_ROTATION_DEG:
        return "motion_rotation_out_of_range"
    if abs(scale - 1.0) > DEFAULT_CAMERA_MOTION_MAX_SCALE_DELTA:
        return "motion_scale_out_of_range"
    return None


def _smooth_successful_samples(samples: list[CameraMotionSample], fps: float) -> list[CameraMotionSample]:
    if len(samples) < 3:
        return samples
    smoothed: list[CameraMotionSample] = []
    matrices = [np.asarray(sample.matrix_current_to_reference, dtype=np.float32) for sample in samples]
    for index, sample in enumerate(samples):
        if sample.status != "ok":
            smoothed.append(sample)
            continue
        candidates = []
        weights = []
        for neighbor_index, weight in [(index - 1, 0.25), (index, 0.5), (index + 1, 0.25)]:
            if neighbor_index < 0 or neighbor_index >= len(samples):
                continue
            neighbor = samples[neighbor_index]
            if neighbor.status not in {"ok", "identity"}:
                continue
            candidates.append(matrices[neighbor_index])
            weights.append(weight)
        if len(candidates) < 2:
            smoothed.append(sample)
            continue
        total_weight = float(sum(weights))
        matrix = sum(candidate * (weight / total_weight) for candidate, weight in zip(candidates, weights))
        matrix[2] = np.array([0.0, 0.0, 1.0], dtype=np.float32)
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
            )
        )
    return _limit_camera_motion_sample_jumps(smoothed, fps)


def _limit_camera_motion_sample_jumps(samples: list[CameraMotionSample], fps: float) -> list[CameraMotionSample]:
    if len(samples) < 2:
        return samples
    limited: list[CameraMotionSample] = []
    last_matrix: np.ndarray | None = None
    for sample in samples:
        matrix = np.asarray(sample.matrix_current_to_reference, dtype=np.float32)
        if last_matrix is not None and sample.status in {"ok", "identity", "fallback"}:
            previous_dx, previous_dy, _, _ = _camera_motion_components(last_matrix)
            dx, dy, _, _ = _camera_motion_components(matrix)
            step = math.hypot(dx - previous_dx, dy - previous_dy)
            if step > DEFAULT_CAMERA_MOTION_MAX_SAMPLE_STEP_PX:
                sample = _sample_from_matrix(
                    sample.frame,
                    fps,
                    last_matrix,
                    status="fallback",
                    inlier_ratio=sample.inlier_ratio,
                    inliers=sample.inliers,
                    matches=sample.matches,
                    reason="sample_step_out_of_range_hold",
                )
                matrix = last_matrix
        limited.append(sample)
        if sample.status in {"ok", "identity", "fallback"}:
            last_matrix = np.asarray(sample.matrix_current_to_reference, dtype=np.float32)
    return limited


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
) -> CameraMotionSample:
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
    )


def _camera_motion_components(matrix: np.ndarray) -> tuple[float, float, float, float]:
    dx = float(matrix[0, 2])
    dy = float(matrix[1, 2])
    rotation = math.degrees(math.atan2(float(matrix[1, 0]), float(matrix[0, 0])))
    scale = math.sqrt(float(matrix[0, 0]) ** 2 + float(matrix[1, 0]) ** 2)
    return dx, dy, rotation, scale


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
