from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
