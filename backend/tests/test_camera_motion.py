from __future__ import annotations

import importlib.util
import math
import unittest

import numpy as np


CV2_AVAILABLE = importlib.util.find_spec("cv2") is not None


@unittest.skipUnless(CV2_AVAILABLE, "cv2 is required for camera motion tests")
class CameraMotionTests(unittest.TestCase):
    def test_identity_transform_keeps_point(self) -> None:
        from app.services.camera_motion import CameraMotionModel, CameraMotionSample

        model = CameraMotionModel(
            enabled=True,
            reference_frame=0,
            reference_time_sec=0.0,
            frame_count=10,
            fps=30.0,
            interval_sec=0.5,
            min_inlier_ratio=0.6,
            samples=[
                CameraMotionSample(
                    frame=0,
                    time_sec=0.0,
                    status="identity",
                    matrix_current_to_reference=np.eye(3, dtype=np.float32).tolist(),
                    matrix_reference_to_current=np.eye(3, dtype=np.float32).tolist(),
                    inlier_ratio=1.0,
                )
            ],
        )

        self.assertEqual(model.transform_point(0, [12.5, 20.0]), [12.5, 20.0])

    def test_translation_maps_current_point_to_reference(self) -> None:
        from app.services.camera_motion import CameraMotionModel, CameraMotionSample

        current_to_reference = np.array([[1, 0, -20], [0, 1, 0], [0, 0, 1]], dtype=np.float32)
        reference_to_current = np.linalg.inv(current_to_reference).astype(np.float32)
        model = CameraMotionModel(
            enabled=True,
            reference_frame=0,
            reference_time_sec=0.0,
            frame_count=30,
            fps=30.0,
            interval_sec=0.5,
            min_inlier_ratio=0.6,
            samples=[
                CameraMotionSample(
                    frame=15,
                    time_sec=0.5,
                    status="ok",
                    matrix_current_to_reference=current_to_reference.tolist(),
                    matrix_reference_to_current=reference_to_current.tolist(),
                    inlier_ratio=0.9,
                    inliers=40,
                    matches=50,
                )
            ],
        )

        self.assertEqual(model.transform_point(15, [110.0, 50.0]), [90.0, 50.0])

    def test_roi_accepts_point_after_compensation(self) -> None:
        from app.services.analysis import _accept_detection_by_pitch_roi
        from app.services.camera_motion import CameraMotionModel, CameraMotionSample

        pitch_polygon = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float32)
        current_to_reference = np.array([[1, 0, -20], [0, 1, 0], [0, 0, 1]], dtype=np.float32)
        reference_to_current = np.linalg.inv(current_to_reference).astype(np.float32)
        model = CameraMotionModel(
            enabled=True,
            reference_frame=0,
            reference_time_sec=0.0,
            frame_count=30,
            fps=30.0,
            interval_sec=0.5,
            min_inlier_ratio=0.6,
            samples=[
                CameraMotionSample(
                    frame=15,
                    time_sec=0.5,
                    status="ok",
                    matrix_current_to_reference=current_to_reference.tolist(),
                    matrix_reference_to_current=reference_to_current.tolist(),
                )
            ],
        )

        accepted_static, _, _ = _accept_detection_by_pitch_roi([130.0, 50.0], pitch_polygon, margin_px=0)
        accepted_compensated, _, _ = _accept_detection_by_pitch_roi(
            model.transform_point(15, [110.0, 50.0]),
            pitch_polygon,
            margin_px=0,
        )

        self.assertFalse(accepted_static)
        self.assertTrue(accepted_compensated)

    def test_report_counts_fallback_samples(self) -> None:
        from app.services.camera_motion import CameraMotionModel, CameraMotionSample

        model = CameraMotionModel(
            enabled=True,
            reference_frame=0,
            reference_time_sec=0.0,
            frame_count=30,
            fps=30.0,
            interval_sec=0.5,
            min_inlier_ratio=0.6,
            samples=[
                CameraMotionSample(
                    frame=0,
                    time_sec=0.0,
                    status="fallback",
                    matrix_current_to_reference=np.eye(3, dtype=np.float32).tolist(),
                    matrix_reference_to_current=np.eye(3, dtype=np.float32).tolist(),
                    reason="low_confidence",
                )
            ],
        )

        self.assertEqual(model.report()["summary"]["fallback_samples"], 1)

    def test_rejects_unreasonable_camera_motion(self) -> None:
        from app.services.camera_motion import _camera_motion_sanity_rejection_reason

        translated = np.eye(3, dtype=np.float32)
        translated[0, 2] = 50.0
        self.assertEqual(_camera_motion_sanity_rejection_reason(translated), "motion_translation_out_of_range")

        angle = math.radians(2.0)
        rotated = np.array(
            [[math.cos(angle), -math.sin(angle), 0.0], [math.sin(angle), math.cos(angle), 0.0], [0.0, 0.0, 1.0]],
            dtype=np.float32,
        )
        self.assertEqual(_camera_motion_sanity_rejection_reason(rotated), "motion_rotation_out_of_range")

        scaled = np.array([[0.97, 0.0, 0.0], [0.0, 0.97, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32)
        self.assertEqual(_camera_motion_sanity_rejection_reason(scaled), "motion_scale_out_of_range")

    def test_stale_fallback_holds_last_good_transform(self) -> None:
        from app.services.camera_motion import _fallback_matrix_and_reason

        last_good = np.eye(3, dtype=np.float32)
        last_good[0, 2] = 20.0

        fresh_matrix, fresh_reason = _fallback_matrix_and_reason(60, 30.0, last_good, 0, "low_confidence")
        self.assertEqual(fresh_reason, "low_confidence")
        self.assertEqual(float(fresh_matrix[0, 2]), 20.0)

        stale_matrix, stale_reason = _fallback_matrix_and_reason(120, 30.0, last_good, 0, "low_confidence")
        self.assertEqual(stale_reason, "low_confidence_stale_hold")
        self.assertEqual(float(stale_matrix[0, 2]), 20.0)

    def test_sample_for_frame_interpolates_between_motion_samples(self) -> None:
        from app.services.camera_motion import CameraMotionModel, CameraMotionSample

        start = np.eye(3, dtype=np.float32)
        end = np.eye(3, dtype=np.float32)
        end[0, 2] = 20.0
        model = CameraMotionModel(
            enabled=True,
            reference_frame=0,
            reference_time_sec=0.0,
            frame_count=20,
            fps=10.0,
            interval_sec=1.0,
            min_inlier_ratio=0.6,
            samples=[
                CameraMotionSample(
                    frame=0,
                    time_sec=0.0,
                    status="identity",
                    matrix_current_to_reference=start.tolist(),
                    matrix_reference_to_current=start.tolist(),
                    inlier_ratio=1.0,
                ),
                CameraMotionSample(
                    frame=10,
                    time_sec=1.0,
                    status="ok",
                    matrix_current_to_reference=end.tolist(),
                    matrix_reference_to_current=np.linalg.inv(end).astype(np.float32).tolist(),
                    inlier_ratio=0.9,
                ),
            ],
        )

        sample = model.sample_for_frame(5)

        self.assertEqual(sample.status, "interpolated")
        self.assertAlmostEqual(sample.dx_px, 10.0, places=2)


if __name__ == "__main__":
    unittest.main()
