from __future__ import annotations

import unittest

from app.services.runtime import (
    build_performance_report,
    ensure_yolo_device_available,
    normalize_yolo_device,
    requested_device_label,
)


class RuntimeTests(unittest.TestCase):
    def test_normalize_yolo_device_maps_common_aliases(self) -> None:
        self.assertIsNone(normalize_yolo_device(None))
        self.assertIsNone(normalize_yolo_device(""))
        self.assertIsNone(normalize_yolo_device("auto"))
        self.assertEqual(normalize_yolo_device("cpu"), "cpu")
        self.assertEqual(normalize_yolo_device("mps"), "mps")
        self.assertEqual(normalize_yolo_device("cuda"), "0")
        self.assertEqual(normalize_yolo_device("gpu"), "0")
        self.assertEqual(normalize_yolo_device("cuda:1"), "1")

    def test_requested_device_label_is_stable_for_reports(self) -> None:
        self.assertEqual(requested_device_label(None), "auto")
        self.assertEqual(requested_device_label("auto"), "auto")
        self.assertEqual(requested_device_label("cuda"), "cuda")

    def test_ensure_yolo_device_available_allows_visible_cuda(self) -> None:
        runtime_info = {
            "torch": {
                "available": True,
                "cuda_available": True,
                "cuda_device_count": 1,
                "cuda_device_names": ["NVIDIA GeForce GTX 1650"],
                "mps_available": False,
            }
        }

        self.assertEqual(ensure_yolo_device_available("cuda", runtime_info=runtime_info), "0")
        self.assertEqual(ensure_yolo_device_available("0", runtime_info=runtime_info), "0")

    def test_ensure_yolo_device_available_rejects_cuda_without_runtime_support(self) -> None:
        runtime_info = {
            "torch": {
                "available": True,
                "cuda_available": False,
                "cuda_device_count": 0,
                "cuda_device_names": [],
                "mps_available": False,
            }
        }

        with self.assertRaisesRegex(RuntimeError, "does not see an NVIDIA CUDA device"):
            ensure_yolo_device_available("0", runtime_info=runtime_info)

    def test_ensure_yolo_device_available_rejects_missing_cuda_index(self) -> None:
        runtime_info = {
            "torch": {
                "available": True,
                "cuda_available": True,
                "cuda_device_count": 1,
                "cuda_device_names": ["NVIDIA GeForce GTX 1650"],
                "mps_available": False,
            }
        }

        with self.assertRaisesRegex(RuntimeError, "device index 1"):
            ensure_yolo_device_available("cuda:1", runtime_info=runtime_info)

    def test_build_performance_report_estimates_throughput(self) -> None:
        report = build_performance_report(
            label="test",
            requested_device="mps",
            normalized_device="mps",
            elapsed_wall_sec=10.0,
            analysis_report={
                "status": "completed",
                "analysis_type": "yolo-ultralytics",
                "frames_processed": 100,
                "tracks_count": 12,
                "stable_players_count": 10,
                "video": {"fps": 25.0, "duration_sec": 60.0},
                "parameters": {"max_seconds": 20.0, "frame_stride": 2},
            },
            runtime_info={"torch": {"available": True, "cuda_available": False, "cuda_device_names": []}},
        )

        self.assertEqual(report["normalized_yolo_device"], "mps")
        self.assertFalse(report["cuda_available"])
        self.assertEqual(report["throughput"]["processed_frames_per_wall_sec"], 10.0)
        self.assertEqual(report["throughput"]["analyzed_video_sec"], 8.0)
        self.assertEqual(report["throughput"]["video_seconds_per_wall_second"], 0.8)


if __name__ == "__main__":
    unittest.main()
