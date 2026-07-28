from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.services.identity_jersey_number_panel_audit import (
    MONTAGE_FILENAME,
    READINESS_FILENAME,
    audit_identity_jersey_number_panels,
    build_montage_approval_template,
    build_panel_experiment_selection,
    normalize_panel_experiment_selection,
    render_montage_approval_page,
)

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - validation environment may lack OpenCV
    cv2 = None
    np = None


class JerseyNumberPanelAuditTests(unittest.TestCase):
    @unittest.skipUnless(cv2 is not None and np is not None, "OpenCV test dependency unavailable")
    def test_builds_readiness_report_and_montage_from_deterministic_panel_crops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_torso(root / "torso-read-10.jpg", digits="10")
            _write_torso(root / "torso-plain.jpg", digits=None)
            _write_torso(root / "torso-read-92.jpg", digits="92")
            dataset = {
                "dataset_digest": "dataset-digest",
                "dataset_version": "dataset-version",
                "summary": {"samples": 4},
                "samples": [
                    _sample(
                        root,
                        "sample-10",
                        "torso-read-10.jpg",
                        state="number_confirmed",
                        number="10",
                        frame=3509,
                        visibility_episode_id="episode-10",
                    ),
                    _sample(
                        root,
                        "sample-92",
                        "torso-read-92.jpg",
                        state="number_confirmed",
                        number="92",
                        frame=4200,
                        visibility_episode_id="episode-92",
                    ),
                    _sample(
                        root,
                        "sample-plain",
                        "torso-plain.jpg",
                        state="number_absent",
                        number=None,
                        frame=100,
                        visibility_episode_id="episode-plain",
                    ),
                    {
                        **_sample(
                            root,
                            "sample-missing",
                            "torso-read-10.jpg",
                            state="number_unreadable",
                            number=None,
                            frame=200,
                            visibility_episode_id="episode-missing",
                        ),
                        "number_panel_bbox_normalized": None,
                    },
                ],
            }

            first = audit_identity_jersey_number_panels(dataset, output_root=root / "audit")
            second = audit_identity_jersey_number_panels(dataset, output_root=root / "audit-repeat")
            montage_exists = (root / "audit" / MONTAGE_FILENAME).is_file()

        self.assertEqual(first["status"], "insufficient_panel_readiness")
        self.assertEqual(first["summary"]["total_samples"], 4)
        self.assertEqual(first["summary"]["total_panel_crops"], 3)
        self.assertEqual(first["summary"]["readable_full_number_crops"], 2)
        self.assertEqual(first["summary"]["plain_shirt_crops"], 1)
        self.assertEqual(first["summary"]["missing_panel_bbox_count"], 1)
        self.assertEqual(first["summary"]["counts_per_number"], {"10": 1, "92": 1})
        self.assertEqual(first["summary"]["counts_per_digit"]["0"], 1)
        self.assertEqual(first["summary"]["counts_per_digit"]["1"], 1)
        self.assertEqual(first["summary"]["counts_per_digit"]["2"], 1)
        self.assertEqual(first["summary"]["counts_per_digit"]["9"], 1)
        self.assertTrue(montage_exists)
        self.assertEqual(first["outputs"]["number_panel_dataset_readiness"], READINESS_FILENAME)
        first_digests = {
            row["anchor_crop_id"]: row["panel_digest"]
            for row in first["samples"]
            if row["panel_digest"] is not None
        }
        second_digests = {
            row["anchor_crop_id"]: row["panel_digest"]
            for row in second["samples"]
            if row["panel_digest"] is not None
        }
        self.assertEqual(first_digests, second_digests)
        self.assertGreaterEqual(first["summary"]["estimated_digit_height_px"]["median"], 8.0)

    @unittest.skipUnless(cv2 is not None and np is not None, "OpenCV test dependency unavailable")
    def test_unselected_invalid_sample_does_not_block_selected_subset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_torso(root / "torso-read.jpg", digits="10")
            dataset = {
                "dataset_digest": "dataset-digest",
                "samples": [
                    _sample(
                        root,
                        "selected",
                        "torso-read.jpg",
                        state="number_confirmed",
                        number="10",
                        frame=3509,
                        visibility_episode_id="episode-selected",
                    ),
                    {
                        **_sample(
                            root,
                            "not-selected",
                            "missing.jpg",
                            state="number_unreadable",
                            number=None,
                            frame=99,
                            visibility_episode_id="episode-noise",
                        ),
                        "number_panel_bbox_normalized": None,
                    },
                ],
            }
            selection = _selection("selected")
            report = audit_identity_jersey_number_panels(
                dataset,
                output_root=root / "audit",
                selection_doc=selection,
            )

        self.assertEqual(report["summary"]["selected_samples"], 1)
        self.assertEqual(report["summary"]["selected_invalid_samples"], 0)
        self.assertEqual(report["summary"]["audited_panel_coverage"], 1.0)
        statuses = {row["sample_key"]: row["status"] for row in report["samples"]}
        self.assertEqual(statuses["not-selected"], "not_selected_for_panel_experiment")

    @unittest.skipUnless(cv2 is not None and np is not None, "OpenCV test dependency unavailable")
    def test_exact_approved_montage_contract_unlocks_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            samples = []
            for index in range(50):
                artifact = f"confirmed-{index}.jpg"
                _write_torso(root / artifact, digits="10")
                samples.append(
                    _sample(
                        root,
                        f"confirmed-{index}",
                        artifact,
                        state="number_confirmed",
                        number="10",
                        frame=3509 if index == 0 else 4000 + index,
                        visibility_episode_id=f"confirmed-episode-{index}",
                    )
                )
            for index in range(30):
                artifact = f"negative-{index}.jpg"
                _write_torso(root / artifact, digits=None)
                samples.append(
                    _sample(
                        root,
                        f"negative-{index}",
                        artifact,
                        state="number_absent",
                        number=None,
                        frame=5000 + index,
                        visibility_episode_id=f"negative-episode-{index}",
                    )
                )
            dataset = {"dataset_digest": "dataset-digest", "samples": samples}
            first = audit_identity_jersey_number_panels(dataset, output_root=root / "audit")
            approval = {
                **build_montage_approval_template(first),
                "reviewer": "operator",
                "reviewed_at": "2026-07-26T10:00:00+00:00",
                "status": "approved",
            }
            approved = audit_identity_jersey_number_panels(
                dataset,
                output_root=root / "audit",
                selection_doc=first["panel_experiment_selection"],
                approval_doc=approval,
            )

        self.assertTrue(approved["gates"]["machine_ready"])
        self.assertTrue(approved["gates"]["human_montage_approval_valid"])
        self.assertEqual(approved["status"], "ready_for_panel_digit_experiment")
        self.assertEqual(approved["final_decision"], "PROCEED_TO_J8_4_LATER")

    @unittest.skipUnless(cv2 is not None and np is not None, "OpenCV test dependency unavailable")
    def test_semantic_number_ten_satisfies_real10_gate_outside_legacy_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            samples = []
            for index in range(50):
                artifact = f"confirmed-{index}.jpg"
                _write_torso(root / artifact, digits="10")
                samples.append(
                    _sample(
                        root,
                        f"confirmed-{index}",
                        artifact,
                        state="number_confirmed",
                        number="10",
                        frame=8000 + index,
                        visibility_episode_id=f"confirmed-episode-{index}",
                    )
                )
            for index in range(30):
                artifact = f"negative-{index}.jpg"
                _write_torso(root / artifact, digits=None)
                samples.append(
                    _sample(
                        root,
                        f"negative-{index}",
                        artifact,
                        state="number_absent",
                        number=None,
                        frame=9000 + index,
                        visibility_episode_id=f"negative-episode-{index}",
                    )
                )
            report = audit_identity_jersey_number_panels(
                {"dataset_digest": "dataset-digest", "samples": samples},
                output_root=root / "audit",
            )

        self.assertGreaterEqual(report["summary"]["real10_panels_found"], 1)
        self.assertTrue(report["gates"]["real10_panel_minimum"])

    @unittest.skipUnless(cv2 is not None and np is not None, "OpenCV test dependency unavailable")
    def test_approval_digest_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_torso(root / "torso.jpg", digits="10")
            dataset = {
                "dataset_digest": "dataset-digest",
                "samples": [
                    _sample(
                        root,
                        "sample",
                        "torso.jpg",
                        state="number_confirmed",
                        number="10",
                        frame=3509,
                        visibility_episode_id="episode",
                    )
                ],
            }
            first = audit_identity_jersey_number_panels(dataset, output_root=root / "audit")
            approval = {
                **build_montage_approval_template(first),
                "montage_sha256": "wrong",
                "reviewer": "operator",
                "reviewed_at": "2026-07-26T10:00:00+00:00",
                "status": "approved",
            }
            report = audit_identity_jersey_number_panels(
                dataset,
                output_root=root / "audit",
                approval_doc=approval,
            )

        self.assertFalse(report["gates"]["human_montage_approval_valid"])
        self.assertIn(
            "montage_sha256_mismatch",
            report["montage"]["human_approval"]["reasons"],
        )
        self.assertNotEqual(report["status"], "ready_for_panel_digit_experiment")

    def test_selection_digest_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "selection digest mismatch"):
            normalize_panel_experiment_selection(
                {
                    "selection_version": "panel-experiment-selection-v1",
                    "sample_keys": ["sample"],
                    "selection_digest": "wrong",
                }
            )

    def test_default_selection_is_deterministic(self) -> None:
        dataset = {
            "samples": [
                {
                    "sample_key": "b",
                    "label_state": "number_unreadable",
                    "number": None,
                    "frame": 2,
                },
                {
                    "sample_key": "a",
                    "label_state": "number_confirmed",
                    "number": "10",
                    "frame": 1,
                },
            ]
        }
        first = build_panel_experiment_selection(dataset)
        second = build_panel_experiment_selection(dataset)
        self.assertEqual(first, second)
        self.assertEqual(first["sample_keys"], ["a", "b"])

    def test_default_selection_skips_unreviewed_null_placeholder_rows(self) -> None:
        dataset = {
            "samples": [
                {
                    "sample_key": "reviewed",
                    "jersey_number_state": "number_confirmed",
                    "jersey_number": "10",
                    "frame": 1,
                },
                {
                    "sample_key": "placeholder",
                    "jersey_number_state": None,
                    "jersey_number": None,
                    "label_state": None,
                    "number": None,
                    "frame": 2,
                },
            ]
        }

        selection = build_panel_experiment_selection(dataset)

        self.assertEqual(selection["sample_keys"], ["reviewed"])

    @unittest.skipUnless(cv2 is not None and np is not None, "OpenCV test dependency unavailable")
    def test_montage_approval_page_contains_simple_operator_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_torso(root / "torso.jpg", digits="10")
            report = audit_identity_jersey_number_panels(
                {
                    "dataset_digest": "dataset-digest",
                    "samples": [
                        _sample(
                            root,
                            "sample",
                            "torso.jpg",
                            state="number_confirmed",
                            number="10",
                            frame=8000,
                            visibility_episode_id="episode",
                        )
                    ],
                },
                output_root=root / "audit",
            )

        page = render_montage_approval_page(report)
        self.assertIn("Akceptuje montage", page)
        self.assertIn("Odrzucam montage", page)
        self.assertIn("number_panel_montage.jpg", page)
        self.assertIn("montage_sha256", page)

    def test_default_selection_is_bounded_and_balances_negative_states(self) -> None:
        samples = []
        for index in range(70):
            samples.append(
                {
                    "sample_key": f"confirmed-{index:03d}",
                    "label_state": "number_confirmed",
                    "number": "10",
                    "frame": 3509 if index == 0 else 1000 + index,
                    "visibility_episode_id": f"confirmed-episode-{index}",
                }
            )
        for state in ("number_absent", "number_unreadable"):
            for index in range(40):
                samples.append(
                    {
                        "sample_key": f"{state}-{index:03d}",
                        "label_state": state,
                        "number": None,
                        "frame": 5000 + index,
                        "visibility_episode_id": f"{state}-episode-{index}",
                    }
                )

        selection = build_panel_experiment_selection({"samples": samples})
        selected = set(selection["sample_keys"])

        self.assertEqual(len(selected), 80)
        self.assertEqual(
            len([key for key in selected if key.startswith("confirmed-")]),
            50,
        )
        self.assertEqual(
            len([key for key in selected if key.startswith("number_absent-")]),
            15,
        )
        self.assertEqual(
            len([key for key in selected if key.startswith("number_unreadable-")]),
            15,
        )
        self.assertIn("confirmed-000", selected)


def _sample(
    root: Path,
    sample_key: str,
    artifact: str,
    *,
    state: str,
    number: str | None,
    frame: int,
    visibility_episode_id: str,
) -> dict[str, object]:
    return {
        "sample_key": sample_key,
        "anchor_crop_id": sample_key,
        "source_match_key": "match-1",
        "source_video_key": "video-1",
        "candidate_subject_id": f"subject-{sample_key}",
        "tracklet_id": f"tracklet-{sample_key}",
        "visibility_episode_id": visibility_episode_id,
        "frame": frame,
        "view": "back",
        "label_state": state,
        "number": number,
        "artifact_root": str(root),
        "artifact": artifact,
        "number_panel_bbox_normalized": [0.25, 0.2, 0.75, 0.78],
    }


def _write_torso(path: Path, *, digits: str | None) -> None:
    image = np.full((180, 120, 3), 40, dtype=np.uint8)
    cv2.rectangle(image, (28, 22), (92, 146), (235, 235, 235), thickness=-1)
    if digits is not None:
        cv2.putText(image, digits, (34, 98), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3)
    cv2.imwrite(str(path), image)


def _selection(*sample_keys: str) -> dict[str, object]:
    selection = {
        "selection_version": "panel-experiment-selection-v1",
        "sample_keys": list(sample_keys),
    }
    return normalize_panel_experiment_selection(selection)


if __name__ == "__main__":
    unittest.main()
