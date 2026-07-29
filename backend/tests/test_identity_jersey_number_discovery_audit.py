from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from app.services.identity_jersey_number_dataset import identity_jersey_number_dataset_digest
from app.services.identity_jersey_number_discovery_audit import (
    INDEX_FILENAME,
    MANIFEST_FILENAME,
    apply_jersey_number_discovery_audit,
    build_discovery_dataset_from_review_gallery,
    build_discovery_dataset_from_subject_review,
    combine_discovery_datasets,
    derive_jersey_number_recovery_targets,
    prepare_jersey_number_discovery_audit,
)


class JerseyNumberDiscoveryAuditTests(unittest.TestCase):
    def test_derives_bounded_recovery_targets_from_collection_gap(self) -> None:
        targets = derive_jersey_number_recovery_targets(
            {"summary": {"collection_gap": {
                "additional_confirmed_labels_needed_from_new_source": 22,
                "additional_negative_panels_needed": 27,
            }}},
            card_cap=80,
        )

        self.assertEqual(targets["target_cards"], 73)
        self.assertEqual(targets["target_confirmations"], 22)
        self.assertEqual(targets["target_negatives"], 27)
        self.assertEqual(targets["audit_purpose"], "panel_readiness_recovery")

    def test_prepare_prioritizes_diverse_team_samples_and_renders_choices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = _dataset(root)
            manifest = prepare_jersey_number_discovery_audit(
                dataset,
                output_root=root / "audit",
                roster_choices=[{"number": "10", "label": "Krzysiek #10"}],
                target_cards=5,
                team_label="A",
                generated_at="2026-07-28T10:00:00+00:00",
            )

            self.assertEqual(manifest["summary"]["selected_cards"], 2)
            self.assertEqual(manifest["summary"]["target_confirmations"], 60)
            self.assertEqual(manifest["summary"]["target_negatives"], 0)
            self.assertEqual(manifest["summary"]["unique_visibility_episodes"], 2)
            self.assertTrue((root / "audit" / MANIFEST_FILENAME).is_file())
            html = (root / "audit" / INDEX_FILENAME).read_text(encoding="utf-8")
            self.assertIn("Krzysiek #10", html)
            self.assertIn("Brak numeru na koszulce", html)
        self.assertIn("Pomin / nie wiem", html)

    def test_build_from_subject_review_keeps_only_requested_team_and_ranks_crops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "anchor_crops").mkdir()
            (root / "anchor_crops" / "large.jpg").write_bytes(b"jpeg-placeholder")
            (root / "anchor_crops" / "small.jpg").write_bytes(b"jpeg-placeholder")
            document = {
                "cards": [
                    {
                        "candidate_subject_id": "subject-a",
                        "team_label": "A",
                        "role": "field_player",
                        "visual_evidence": {"anchor_crops": [
                            _review_crop("large", 900, [0, 0, 20, 100]),
                            _review_crop("small", 120, [0, 0, 20, 30]),
                        ]},
                    },
                    {
                        "candidate_subject_id": "subject-b",
                        "team_label": "B",
                        "visual_evidence": {"anchor_crops": [
                            _review_crop("team-b", 500, [0, 0, 20, 100]),
                        ]},
                    },
                ]
            }

            dataset = build_discovery_dataset_from_subject_review(
                document,
                artifact_root=root,
                source_match_key="match-a",
                source_video_key="video-a",
                team_label_value="A",
                episode_window_frames=300,
            )

        self.assertEqual(dataset["summary"]["samples"], 2)
        self.assertEqual([row["anchor_crop_id"] for row in dataset["samples"]], ["large", "small"])
        self.assertTrue(dataset["samples"][0]["artifact_available"])
        self.assertIsNone(dataset["samples"][0]["jersey_number_state"])

    def test_build_from_review_gallery_and_combination_deduplicate_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "identity_review" / "crops" / "slot-A01").mkdir(parents=True)
            (root / "identity_review" / "crops" / "slot-A01" / "first.jpg").write_bytes(b"jpeg")
            (root / "identity_review" / "crops" / "slot-A01" / "second.jpg").write_bytes(b"jpeg")
            gallery = {
                "players": [
                    {
                        "stable_subject_id": "slot-A01",
                        "stable_player_id": "A01",
                        "team_label": "A",
                        "stints": [{
                            "stint_id": "A01-S01",
                            "crops": [
                                {
                                    "artifact": "identity_review/crops/slot-A01/first.jpg",
                                    "frame": 100,
                                    "track_id": 100001,
                                    "bbox_xyxy": [0, 0, 24, 110],
                                    "confidence": 0.92,
                                },
                                {
                                    "artifact": "identity_review/crops/slot-A01/second.jpg",
                                    "frame": 460,
                                    "track_id": 100001,
                                    "bbox_xyxy": [0, 0, 20, 80],
                                    "confidence": 0.86,
                                },
                            ],
                        }],
                    },
                    {"stable_subject_id": "slot-B01", "team_label": "B", "stints": []},
                ]
            }
            gallery_dataset = build_discovery_dataset_from_review_gallery(
                gallery,
                artifact_root=root,
                source_match_key="match-a",
                source_video_key="video-a",
                team_label_value="A",
            )
            duplicate = deepcopy(gallery_dataset)
            duplicate["samples"] = [deepcopy(gallery_dataset["samples"][0])]
            combined = combine_discovery_datasets(gallery_dataset, duplicate)

        self.assertEqual(gallery_dataset["summary"]["samples"], 2)
        self.assertTrue(all(row["artifact_available"] for row in gallery_dataset["samples"]))
        self.assertEqual(combined["summary"]["samples"], 2)
        self.assertEqual(combined["summary"]["source"], "identity_review_gallery")

    def test_combination_prefers_operator_label_and_keeps_other_video_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            automated = _dataset(root)
            labeled = deepcopy(automated)
            labeled_row = labeled["samples"][0]
            labeled_row.update({
                "discovery_review_status": "labeled",
                "jersey_number_state": "number_confirmed",
                "label_state": "number_confirmed",
                "jersey_number": "10",
                "number": "10",
                "number_panel_bbox_normalized": [0.3, 0.2, 0.6, 0.7],
            })
            other_video = deepcopy(automated)
            other_video_row = deepcopy(other_video["samples"][0])
            other_video_row["source_video_key"] = "video-b"
            other_video["samples"] = [other_video_row]

            combined = combine_discovery_datasets(automated, labeled, other_video)

        self.assertEqual(combined["summary"]["samples"], 4)
        matching_rows = [row for row in combined["samples"] if row["sample_key"] == "a-first"]
        self.assertEqual(len(matching_rows), 2)
        reviewed = next(row for row in matching_rows if row["source_video_key"] == "video")
        self.assertEqual(reviewed["jersey_number"], "10")
        self.assertEqual(reviewed["discovery_review_status"], "labeled")
        self.assertEqual(combined["summary"]["states"]["number_confirmed"], 1)

    def test_apply_changes_only_labeled_samples_and_recomputes_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = _dataset(root)
            original = deepcopy(dataset)
            reviewed = prepare_jersey_number_discovery_audit(
                dataset,
                output_root=root / "audit",
                roster_choices=[{"number": "10", "label": "Krzysiek #10"}],
                target_cards=2,
                team_label="A",
            )
            reviewed["items"][0]["manual_review"] = {
                "status": "labeled",
                "jersey_number_state": "number_confirmed",
                "jersey_number": "10",
                "number_panel_bbox_normalized": [0.3, 0.2, 0.6, 0.5],
                "reviewed_at": "2026-07-28T10:00:00+00:00",
            }
            reviewed["items"][1]["manual_review"] = {
                "status": "skipped",
                "jersey_number_state": None,
                "jersey_number": None,
                "number_panel_bbox_normalized": None,
                "reviewed_at": "2026-07-28T10:00:01+00:00",
            }

            updated = apply_jersey_number_discovery_audit(
                dataset,
                reviewed,
                generated_at="2026-07-28T10:05:00+00:00",
            )

        rows = {row["sample_key"]: row for row in updated["samples"]}
        self.assertEqual(rows["a-first"]["jersey_number"], "10")
        self.assertEqual(rows["a-first"]["number_panel_bbox_normalized"], [0.3, 0.2, 0.6, 0.5])
        self.assertIsNone(rows["a-second"]["jersey_number"])
        self.assertEqual(rows["a-second"]["discovery_review_status"], "skipped")
        self.assertEqual(updated["discovery_audit_import"]["confirmed"], 1)
        self.assertEqual(updated["discovery_audit_import"]["skipped"], 1)
        self.assertEqual(updated["dataset_digest"], identity_jersey_number_dataset_digest(updated["samples"]))
        self.assertEqual(dataset, original)

    def test_apply_rejects_tampered_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = _dataset(root)
            reviewed = prepare_jersey_number_discovery_audit(
                dataset,
                output_root=root / "audit",
                roster_choices=[{"number": "10", "label": "Krzysiek #10"}],
                target_cards=2,
                team_label="A",
            )
            reviewed["items"][0]["frame"] = 999

            with self.assertRaisesRegex(ValueError, "contract digest mismatch"):
                apply_jersey_number_discovery_audit(dataset, reviewed)

    def test_prepare_unreviewed_only_excludes_existing_number_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = _dataset(root)
            dataset["samples"][0]["jersey_number_state"] = None
            dataset["samples"][0]["label_state"] = None
            dataset["samples"][0]["clean_jersey_visible"] = None
            dataset["samples"][0]["number_panel_visible"] = None
            dataset["samples"][0]["annotation_confidence"] = 0.0
            dataset["dataset_digest"] = identity_jersey_number_dataset_digest(dataset["samples"])

            manifest = prepare_jersey_number_discovery_audit(
                dataset,
                output_root=root / "audit",
                roster_choices=[{"number": "10", "label": "Krzysiek #10"}],
                target_cards=5,
                team_label="A",
                unreviewed_only=True,
            )

        self.assertEqual([item["sample_key"] for item in manifest["items"]], ["a-first"])
        self.assertEqual(manifest["selection_mode"], "unreviewed_only")
        self.assertTrue(manifest["summary"]["unreviewed_only"])

    def test_prepare_unreviewed_only_excludes_prior_operator_skip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = _dataset(root)
            dataset["samples"][0]["discovery_review_status"] = "skipped"
            dataset["samples"][1]["jersey_number_state"] = None
            dataset["samples"][1]["label_state"] = None
            dataset["samples"][1]["clean_jersey_visible"] = None
            dataset["samples"][1]["number_panel_visible"] = None
            dataset["samples"][1]["annotation_confidence"] = 0.0
            dataset["dataset_digest"] = identity_jersey_number_dataset_digest(dataset["samples"])

            manifest = prepare_jersey_number_discovery_audit(
                dataset,
                output_root=root / "audit",
                roster_choices=[{"number": "10", "label": "Krzysiek #10"}],
                target_cards=5,
                team_label="A",
                unreviewed_only=True,
            )

        self.assertEqual([item["sample_key"] for item in manifest["items"]], ["a-second"])


def _dataset(root: Path) -> dict[str, object]:
    (root / "crop-a.jpg").write_bytes(b"jpeg-placeholder")
    (root / "crop-b.jpg").write_bytes(b"jpeg-placeholder")
    (root / "crop-team-b.jpg").write_bytes(b"jpeg-placeholder")
    samples = [
        _sample(root, "a-first", "episode-one", "A", "crop-a.jpg", 10),
        _sample(root, "a-second", "episode-two", "A", "crop-b.jpg", 20),
        _sample(root, "b-only", "episode-three", "B", "crop-team-b.jpg", 30),
    ]
    digest = identity_jersey_number_dataset_digest(samples)
    return {
        "dataset_digest": digest,
        "dataset_version": f"jersey-number-dataset:v3:{digest}",
        "summary": {"samples": len(samples)},
        "samples": samples,
    }


def _sample(
    root: Path,
    sample_key: str,
    episode: str,
    team_label: str,
    artifact: str,
    frame: int,
) -> dict[str, object]:
    return {
        "sample_key": sample_key,
        "anchor_crop_id": f"crop-{sample_key}",
        "source_match_key": "match",
        "source_video_key": "video",
        "candidate_subject_id": "subject",
        "frame": frame,
        "team_label": team_label,
        "jersey_number_state": "number_unreadable",
        "jersey_number": None,
        "label_state": "number_unreadable",
        "number": None,
        "view": "back",
        "clean_jersey_visible": True,
        "number_panel_visible": True,
        "annotation_confidence": 0.8,
        "visibility_episode_id": episode,
        "artifact": artifact,
        "artifact_root": str(root),
        "artifact_available": True,
        "number_panel_bbox_normalized": None,
    }


def _review_crop(crop_id: str, frame: int, bbox: list[int]) -> dict[str, object]:
    return {
        "anchor_crop_id": crop_id,
        "artifact": f"anchor_crops/{crop_id}.jpg",
        "frame": frame,
        "bbox_xyxy": bbox,
        "selection_score": 0.8,
        "detection_confidence": 0.9,
        "tracklet_id": f"track-{crop_id}",
    }


if __name__ == "__main__":
    unittest.main()
