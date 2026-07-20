from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.services.identity_same_match_reid import (
    JsonEmbeddingCache,
    build_same_match_reid_evidence,
)


class _ColorEmbedder:
    model_name = "test-color-embedder"
    model_version = "1"
    embedding_dimension = 3

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        vector = crop_bgr.mean(axis=(0, 1)).astype(np.float32) + 1e-3
        return vector / np.linalg.norm(vector)


class _CountingColorEmbedder(_ColorEmbedder):
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        self.calls += 1
        return super().embed(crop_bgr)


class IdentitySameMatchReIdTests(unittest.TestCase):
    def test_missing_model_is_explicitly_unavailable_and_never_merges(self) -> None:
        documents = build_same_match_reid_evidence(
            _candidate_doc(),
            _timeline_doc(),
            _consolidation_doc(),
            video_path=Path("missing.mp4"),
            fps=10.0,
            embedder=None,
            model_status={"available": False, "reason": "model_files_missing"},
            generated_at="fixed",
        )

        evidence = documents["identity_same_match_reid"]
        self.assertEqual(evidence["pairs"][0]["status"], "unavailable")
        self.assertFalse(evidence["pairs"][0]["appearance_reliable"])
        self.assertFalse(evidence["safety"]["automatically_merges_fragments"])

    def test_clean_crops_build_medoid_prototypes_and_pair_distance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "sample.mp4"
            _write_video(video_path)
            documents = build_same_match_reid_evidence(
                _candidate_doc(),
                _timeline_doc(),
                _consolidation_doc(),
                video_path=video_path,
                fps=10.0,
                embedder=_ColorEmbedder(),
                generated_at="fixed",
                parameters={
                    "min_blur_variance": 0.0,
                    "min_bbox_width_px": 8,
                    "min_bbox_height_px": 20,
                    "min_bbox_area_px": 160,
                },
            )

        evidence = documents["identity_same_match_reid"]
        self.assertEqual(evidence["summary"]["subjects_with_prototype"], 2)
        self.assertEqual(evidence["summary"]["reliable_subjects"], 2)
        self.assertEqual(evidence["pairs"][0]["status"], "available")
        self.assertLess(evidence["pairs"][0]["prototype_distance"], 0.01)
        self.assertTrue(evidence["pairs"][0]["advisory_only"])
        self.assertTrue(
            documents["identity_same_match_reid_report"]["gates"]["only_reliable_crops_used"]
        )

    def test_embedding_cache_reuses_exact_crops_without_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "sample.mp4"
            cache_path = temp_path / "embeddings.json"
            _write_video(video_path)
            first_embedder = _CountingColorEmbedder()
            first_cache = JsonEmbeddingCache.load(
                cache_path,
                model_name=first_embedder.model_name,
                model_version=first_embedder.model_version,
                embedding_dimension=first_embedder.embedding_dimension,
            )
            first_documents = build_same_match_reid_evidence(
                _candidate_doc(),
                _timeline_doc(),
                _consolidation_doc(),
                video_path=video_path,
                fps=10.0,
                embedder=first_embedder,
                embedding_cache=first_cache,
                generated_at="fixed",
                parameters={
                    "min_blur_variance": 0.0,
                    "min_bbox_width_px": 8,
                    "min_bbox_height_px": 20,
                    "min_bbox_area_px": 160,
                },
            )
            first_cache.save()

            second_embedder = _CountingColorEmbedder()
            second_cache = JsonEmbeddingCache.load(
                cache_path,
                model_name=second_embedder.model_name,
                model_version=second_embedder.model_version,
                embedding_dimension=second_embedder.embedding_dimension,
            )
            second_documents = build_same_match_reid_evidence(
                _candidate_doc(),
                _timeline_doc(),
                _consolidation_doc(),
                video_path=video_path,
                fps=10.0,
                embedder=second_embedder,
                embedding_cache=second_cache,
                generated_at="fixed",
                parameters={
                    "min_blur_variance": 0.0,
                    "min_bbox_width_px": 8,
                    "min_bbox_height_px": 20,
                    "min_bbox_area_px": 160,
                },
            )

        self.assertGreater(first_embedder.calls, 0)
        self.assertEqual(second_embedder.calls, 0)
        self.assertGreater(second_cache.hits, 0)
        self.assertEqual(
            first_documents["identity_same_match_reid"]["pairs"],
            second_documents["identity_same_match_reid"]["pairs"],
        )

    def test_overlap_and_unreliable_appearance_are_rejected(self) -> None:
        timeline = _timeline_doc()
        timeline["subjects"][0]["observations"][0]["appearance_reliable"] = False
        for row in timeline["subjects"][1]["observations"]:
            row["bbox_xyxy"] = [10, 10, 30, 50]
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "sample.mp4"
            _write_video(video_path)
            documents = build_same_match_reid_evidence(
                _candidate_doc(),
                timeline,
                _consolidation_doc(),
                video_path=video_path,
                fps=10.0,
                embedder=_ColorEmbedder(),
                generated_at="fixed",
                parameters={"min_blur_variance": 0.0},
            )

        subjects = {
            row["candidate_subject_id"]: row
            for row in documents["identity_same_match_reid"]["subjects"]
        }
        self.assertGreater(subjects["subject-a"]["rejection_counts"]["appearance_unreliable"], 0)
        self.assertGreater(subjects["subject-b"]["rejection_counts"]["strong_bbox_overlap"], 0)
        self.assertEqual(documents["identity_same_match_reid"]["pairs"][0]["status"], "unavailable")

    def test_video_offset_maps_global_frames_to_local_clip(self) -> None:
        timeline = _timeline_doc(frame_offset=100)
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "sample.mp4"
            _write_video(video_path)
            documents = build_same_match_reid_evidence(
                _candidate_doc(),
                timeline,
                _consolidation_doc(),
                video_path=video_path,
                fps=10.0,
                video_time_offset_sec=10.0,
                embedder=_ColorEmbedder(),
                generated_at="fixed",
                parameters={
                    "min_blur_variance": 0.0,
                    "min_bbox_width_px": 8,
                    "min_bbox_height_px": 20,
                    "min_bbox_area_px": 160,
                },
            )

        self.assertEqual(documents["identity_same_match_reid"]["summary"]["subjects_with_prototype"], 2)


def _candidate_doc() -> dict:
    return {
        "algorithm": {"name": "candidate", "version": "1"},
        "subjects": [
            {"candidate_subject_id": "subject-a", "candidate_player_id": "A01", "team_label": "A"},
            {"candidate_subject_id": "subject-b", "candidate_player_id": "A01~2", "team_label": "A"},
        ],
    }


def _timeline_doc(*, frame_offset: int = 0) -> dict:
    def observations(x1: int) -> list[dict]:
        return [
            {
                "frame": frame_offset + frame,
                "time_sec": (frame_offset + frame) / 10.0,
                "status": "detected",
                "bbox_xyxy": [x1, 10, x1 + 20, 50],
                "confidence": 0.9,
                "appearance_reliable": True,
                "footpoint_reliable": True,
                "play_area_status": "inside_play",
                "quality_class": "trusted",
                "tracklet_id": f"track-{x1}",
            }
            for frame in (0, 2, 4, 6)
        ]

    return {
        "algorithm": {"name": "timeline", "version": "1"},
        "subjects": [
            {"shadow_subject_id": "subject-a", "observations": observations(10)},
            {"shadow_subject_id": "subject-b", "observations": observations(40)},
        ],
    }


def _consolidation_doc() -> dict:
    return {
        "algorithm": {"name": "consolidation", "version": "1"},
        "proposals": [
            {
                "proposal_key": "proposal-1",
                "source_candidate_subject_id": "subject-a",
                "target_candidate_subject_id": "subject-b",
            }
        ],
    }


def _write_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (80, 60))
    if not writer.isOpened():
        raise RuntimeError("Could not create test video")
    try:
        for frame_index in range(10):
            frame = np.full((60, 80, 3), 40, dtype=np.uint8)
            color = (40 + frame_index, 80, 180)
            cv2.rectangle(frame, (10, 10), (30, 50), color, -1)
            cv2.rectangle(frame, (40, 10), (60, 50), color, -1)
            writer.write(frame)
    finally:
        writer.release()


if __name__ == "__main__":
    unittest.main()
