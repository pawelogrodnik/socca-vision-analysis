from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.ball_prelabeling import (
    bbox_xyxy_to_yolo_line,
    default_output_dir,
    list_image_paths,
    write_yolo_data_yaml,
    zip_directory,
)


class BallPrelabelingTests(unittest.TestCase):
    def test_bbox_xyxy_to_yolo_line_exports_single_ball_class(self) -> None:
        line = bbox_xyxy_to_yolo_line([10, 20, 30, 60], frame_size=(100, 200))

        self.assertEqual(line, "0 0.200000 0.200000 0.200000 0.200000")

    def test_bbox_xyxy_to_yolo_line_clamps_to_frame(self) -> None:
        line = bbox_xyxy_to_yolo_line([-10, 10, 110, 30], frame_size=(100, 100))

        self.assertEqual(line, "0 0.500000 0.200000 1.000000 0.200000")

    def test_bbox_xyxy_to_yolo_line_skips_invalid_box(self) -> None:
        self.assertIsNone(bbox_xyxy_to_yolo_line([10, 10, 10, 20], frame_size=(100, 100)))

    def test_list_image_paths_ignores_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "frame_000002.jpg").write_text("", encoding="utf-8")
            (root / "frame_000001.png").write_text("", encoding="utf-8")
            (root / "metadata.json").write_text("{}", encoding="utf-8")

            self.assertEqual([path.name for path in list_image_paths(root)], ["frame_000001.png", "frame_000002.jpg"])

    def test_default_output_dir_is_sibling(self) -> None:
        frames_dir = Path("training_frames") / "clip_100frames"

        self.assertEqual(default_output_dir(frames_dir), Path("training_frames") / "clip_100frames_ball_prelabels")

    def test_write_yolo_data_yaml_uses_train_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.yaml"

            write_yolo_data_yaml(path)

            self.assertIn("train: train/images", path.read_text(encoding="utf-8"))
            self.assertIn("0: ball", path.read_text(encoding="utf-8"))

    def test_zip_directory_uses_relative_paths(self) -> None:
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            (root / "train" / "labels").mkdir(parents=True)
            (root / "train" / "labels" / "frame.txt").write_text("0 0.5 0.5 0.1 0.1\n", encoding="utf-8")
            zip_path = Path(tmp) / "dataset.zip"

            zip_directory(root, zip_path)

            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(archive.namelist(), ["train/labels/frame.txt"])


if __name__ == "__main__":
    unittest.main()
