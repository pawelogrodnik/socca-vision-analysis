from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_initial_audit import (
    AUDIT_DIRECTORY,
    SELECTION_FILENAME,
    build_initial_identity_audit_document,
)
from app.services.identity_initial_audit_store import (
    InitialIdentityAuditConflictError,
    InitialIdentityAuditStaleError,
    SEEDS_FILENAME,
    load_initial_identity_audit_seeds,
    save_initial_identity_audit_seeds,
)
from app.services import identity_initial_audit_store


class InitialIdentityAuditStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.match_path = Path(self.temporary_directory.name)
        self.match_document = {
            "teams": [
                {
                    "id": "team-a",
                    "name": "Corgi",
                    "players": [
                        {
                            "id": "player-1",
                            "name": "Pawel",
                            "number": "92",
                            "role": "player",
                        },
                        {
                            "id": "player-2",
                            "name": "Krzysiek",
                            "number": "10",
                            "role": "player",
                        },
                    ],
                },
                {
                    "id": "team-b",
                    "name": "Verisk",
                    "players": [
                        {
                            "id": "player-3",
                            "name": "Roman",
                            "number": "6",
                            "role": "player",
                        }
                    ],
                },
            ]
        }
        self.selection = {
            "schema_version": "0.1.0",
            "selection_digest": "selection-1",
            "source": {"analysis_run_id": "analysis-1"},
            "video": {
                "fps": 30.0,
                "frame_count": 300,
                "duration_sec": 10.0,
                "width": 1920,
                "height": 1080,
            },
            "selected_frames": [
                {
                    "frame": 30,
                    "time_sec": 1.0,
                    "capture_domain": {"period": "first_half"},
                    "full_frame_artifact": (
                        "identity_initial_audit/frames/frame-000030.jpg"
                    ),
                    "thumbnail_artifact": (
                        "identity_initial_audit/frames/frame-000030-thumb.jpg"
                    ),
                    "visible_detections": [
                        self._detection(
                            stable_subject_id="subject-a",
                            tracklet_id="tracklet-a",
                            raw_track_id=12,
                            bbox=[100, 200, 180, 400],
                            team_label="B",
                        ),
                        self._detection(
                            stable_subject_id="subject-b",
                            tracklet_id="tracklet-b",
                            raw_track_id=13,
                            bbox=[300, 200, 380, 400],
                            team_label="A",
                        ),
                    ],
                }
            ],
        }
        audit_directory = self.match_path / AUDIT_DIRECTORY
        audit_directory.mkdir(parents=True)
        self.selection_path = audit_directory / SELECTION_FILENAME
        self._write_selection()
        self.production_path = self.match_path / "global_identity.json"
        self.production_path.write_text(
            '{"stable_subjects":["subject-a","subject-b"]}\n',
            encoding="utf-8",
        )
        audit = build_initial_identity_audit_document(
            self.selection,
            self.match_document,
        )
        self.observation_keys = [
            row["observation_key"]
            for row in audit["frames"][0]["observations"]
        ]

    def _detection(
        self,
        *,
        stable_subject_id: str,
        tracklet_id: str,
        raw_track_id: int,
        bbox: list[int],
        team_label: str,
    ) -> dict[str, object]:
        return {
            "stable_subject_id": stable_subject_id,
            "stable_player_id": "A01",
            "slot_id": "A01",
            "tracklet_id": tracklet_id,
            "raw_track_id": raw_track_id,
            "stint_id": "A01-S01",
            "team_label": team_label,
            "role": "field_player",
            "source": "detected",
            "bbox_xyxy": bbox,
        }

    def _write_selection(self) -> None:
        self.selection_path.write_text(
            json.dumps(self.selection, indent=2),
            encoding="utf-8",
        )

    def test_save_and_resume_preserve_operator_decision(self) -> None:
        production_before = self.production_path.read_bytes()
        saved = save_initial_identity_audit_seeds(
            self.match_path,
            self.match_document,
            [
                {
                    "update_id": "update-1",
                    "observation_key": self.observation_keys[0],
                    "action": "assign_roster_player",
                    "player_id": "player-1",
                }
            ],
            telemetry_events=[
                {
                    "event_id": "event-1",
                    "session_id": "session-1",
                    "event_type": "frame_shown",
                    "audit_frame_key": "audit-frame-01",
                    "active_delta_seconds": 2.5,
                },
                {
                    "event_id": "event-2",
                    "session_id": "session-1",
                    "event_type": "action",
                    "observation_key": self.observation_keys[0],
                    "active_delta_seconds": 1.25,
                },
            ],
            updated_at="2026-07-27T10:00:00+00:00",
        )

        self.assertEqual(saved["status"], "fresh")
        self.assertEqual(len(saved["decisions"]), 1)
        self.assertEqual(
            saved["decisions"][0]["assigned_player"]["player_id"],
            "player-1",
        )
        self.assertTrue(
            saved["decisions"][0]["team_assignment_corrected"]
        )
        self.assertNotIn("bbox_xyxy", saved["decisions"][0])
        self.assertNotIn("provenance", saved["decisions"][0])

        resumed = load_initial_identity_audit_seeds(
            self.match_path,
            self.match_document,
        )
        self.assertEqual(resumed["decisions"], saved["decisions"])
        self.assertEqual(
            resumed["operator_telemetry"]["metrics"]["audit_frames_shown"],
            1,
        )
        self.assertEqual(
            resumed["operator_telemetry"]["metrics"]["audit_actions"],
            1,
        )
        self.assertEqual(
            resumed["operator_telemetry"]["metrics"][
                "active_operator_seconds"
            ],
            3.75,
        )
        self.assertEqual(self.production_path.read_bytes(), production_before)

        stored = json.loads(
            (self.match_path / SEEDS_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(
            stored["decisions"][0]["bbox_xyxy"],
            [100.0, 200.0, 180.0, 400.0],
        )
        self.assertEqual(
            stored["decisions"][0]["provenance"]["tracklet_id"],
            "tracklet-a",
        )
        self.assertEqual(
            stored["decisions"][0]["capture_domain"]["period"],
            "first_half",
        )

    def test_repeated_update_and_telemetry_are_idempotent(self) -> None:
        update = {
            "update_id": "update-1",
            "observation_key": self.observation_keys[0],
            "action": "assign_roster_player",
            "player_id": "player-1",
        }
        event = {
            "event_id": "event-1",
            "session_id": "session-1",
            "event_type": "action",
            "observation_key": self.observation_keys[0],
            "active_delta_seconds": 4.0,
        }
        save_initial_identity_audit_seeds(
            self.match_path,
            self.match_document,
            [update],
            telemetry_events=[event],
        )
        repeated = save_initial_identity_audit_seeds(
            self.match_path,
            self.match_document,
            [update],
            telemetry_events=[event],
        )

        self.assertEqual(len(repeated["decisions"]), 1)
        self.assertEqual(
            repeated["operator_telemetry"]["metrics"]["audit_actions"],
            1,
        )
        self.assertEqual(
            repeated["operator_telemetry"]["metrics"][
                "active_operator_seconds"
            ],
            4.0,
        )

    def test_incremental_save_reuses_production_hash_baseline(self) -> None:
        for filename in ("stable_players.json", "tracklets.json", "tracks.json"):
            (self.match_path / filename).write_text("{}\n", encoding="utf-8")
        real_file_sha256 = identity_initial_audit_store._file_sha256

        with patch.object(
            identity_initial_audit_store,
            "_file_sha256",
            wraps=real_file_sha256,
        ) as hash_file:
            save_initial_identity_audit_seeds(
                self.match_path,
                self.match_document,
                [
                    {
                        "update_id": "update-1",
                        "observation_key": self.observation_keys[0],
                        "action": "team_a_unknown",
                    }
                ],
            )
            self.assertEqual(hash_file.call_count, 4)
            hash_file.reset_mock()

            saved = save_initial_identity_audit_seeds(
                self.match_path,
                self.match_document,
                [
                    {
                        "update_id": "update-2",
                        "observation_key": self.observation_keys[1],
                        "action": "team_b_unknown",
                    }
                ],
                telemetry_events=[
                    {
                        "event_id": "event-2",
                        "session_id": "session-1",
                        "event_type": "action",
                        "observation_key": self.observation_keys[1],
                        "active_delta_seconds": 1.0,
                    }
                ],
            )

        self.assertEqual(hash_file.call_count, 0)
        self.assertEqual(len(saved["decisions"]), 2)
        stored = json.loads(
            (self.match_path / SEEDS_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(
            stored["production_identity_snapshot"]["metadata_fingerprint"]["kind"],
            "file_metadata_size_mtime_v1",
        )
        self.assertEqual(
            stored["operator_telemetry"]["metrics"]["audit_actions"],
            1,
        )

    def test_same_player_cannot_claim_two_observations_in_one_frame(self) -> None:
        save_initial_identity_audit_seeds(
            self.match_path,
            self.match_document,
            [
                {
                    "update_id": "update-1",
                    "observation_key": self.observation_keys[0],
                    "action": "assign_roster_player",
                    "player_id": "player-1",
                }
            ],
        )
        seed_path = self.match_path / SEEDS_FILENAME
        stored_before = seed_path.read_bytes()

        with self.assertRaises(InitialIdentityAuditConflictError):
            save_initial_identity_audit_seeds(
                self.match_path,
                self.match_document,
                [
                    {
                        "update_id": "update-2",
                        "observation_key": self.observation_keys[1],
                        "action": "assign_roster_player",
                        "player_id": "player-1",
                    }
                ],
            )

        self.assertEqual(seed_path.read_bytes(), stored_before)

    def test_changed_selection_marks_saved_decisions_stale(self) -> None:
        save_initial_identity_audit_seeds(
            self.match_path,
            self.match_document,
            [
                {
                    "update_id": "update-1",
                    "observation_key": self.observation_keys[0],
                    "action": "team_a_unknown",
                }
            ],
        )
        self.selection["selected_frames"][0]["time_sec"] = 1.1
        self._write_selection()

        loaded = load_initial_identity_audit_seeds(
            self.match_path,
            self.match_document,
        )
        self.assertEqual(loaded["status"], "stale")
        self.assertFalse(loaded["decisions_fresh"])
        self.assertEqual(loaded["decisions"], [])
        with self.assertRaises(InitialIdentityAuditStaleError):
            save_initial_identity_audit_seeds(
                self.match_path,
                self.match_document,
                [],
            )

    def test_atomic_save_leaves_no_temporary_file(self) -> None:
        save_initial_identity_audit_seeds(
            self.match_path,
            self.match_document,
            [
                {
                    "update_id": "update-1",
                    "observation_key": self.observation_keys[0],
                    "action": "skip",
                }
            ],
        )
        temporary_files = list(
            self.match_path.glob(f".{SEEDS_FILENAME}.*.tmp")
        )
        self.assertEqual(temporary_files, [])


if __name__ == "__main__":
    unittest.main()
