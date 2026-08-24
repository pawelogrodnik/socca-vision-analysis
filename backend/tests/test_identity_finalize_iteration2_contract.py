from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_canonical_io import (
    invalidate_cached_json,
    load_json_cached,
    review_build_context,
)
from app.services.identity_reviewed_snapshot import (
    _load as snapshot_strict_load,
)
from app.services.identity_reviewed_snapshot import (
    _optional as snapshot_optional,
)
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_snapshot import (
    _semantic_input,
    _source_descriptor,
    _source_digest,
)
from app.services.identity_reviewed_video import DIGEST_CACHE_FILENAME, reviewed_source_video_digest
from app.services.review_workflow_orchestrator import durable_review_progress


class RequestCacheWriteSafetyTests(unittest.TestCase):
    """Requirement: in-request writes must never be shadowed by the cache."""

    def test_invalidate_after_write_returns_fresh_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviewed_identity_progress.json"
            path.write_text(json.dumps({"version": "A"}), encoding="utf-8")
            with review_build_context():
                first = load_json_cached(path)
                self.assertEqual(first["version"], "A")
                # Same scope: cached object is reused.
                self.assertIs(load_json_cached(path), first)

                path.write_text(json.dumps({"version": "B"}), encoding="utf-8")
                invalidate_cached_json(path)
                second = load_json_cached(path)
                self.assertEqual(second["version"], "B")

    def test_scope_isolation_between_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doc.json"
            path.write_text(json.dumps({"n": 1}), encoding="utf-8")
            with review_build_context():
                first = load_json_cached(path)
            with review_build_context():
                second = load_json_cached(path)
            self.assertIsNot(first, second)


class StrictLoaderSemanticsTests(unittest.TestCase):
    def test_snapshot_strict_loader_raises_on_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviewed_identity_snapshot.json"
            path.write_text("{corrupt", encoding="utf-8")
            with review_build_context(), self.assertRaises(ValueError):
                snapshot_strict_load(path)

    def test_snapshot_optional_missing_file_is_empty_and_malformed_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "tracklets.json"
            self.assertEqual(snapshot_optional(missing), {})
            corrupt = Path(tmp) / "global_identity.json"
            corrupt.write_text("not-json", encoding="utf-8")
            with review_build_context(), self.assertRaises(ValueError):
                snapshot_optional(corrupt)

    def test_stable_anonymous_loader_reuses_cached_document(self) -> None:
        from app.services.identity_reviewed_segments import _load as segments_load

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity_candidate_shadow.json"
            path.write_text(json.dumps({"subjects": []}), encoding="utf-8")
            with review_build_context():
                first = snapshot_optional(path)
                second = segments_load(path)
                self.assertIs(first, second)

    def test_material_continuity_tolerant_loader_swallows_corruption(self) -> None:
        from app.services.identity_reviewed_material_continuity import (
            _load as material_load,
        )

        with tempfile.TemporaryDirectory() as tmp:
            corrupt = Path(tmp) / "decisions.json"
            corrupt.write_text("{bad", encoding="utf-8")
            self.assertEqual(material_load(corrupt), {})
            non_dict = Path(tmp) / "list.json"
            non_dict.write_text("[1,2]", encoding="utf-8")
            self.assertEqual(material_load(non_dict), {})


class SourceDigestEquivalenceTests(unittest.TestCase):
    """Old semantic_input_digest must equal the single-pass computation."""

    def _documents(self) -> dict:
        documents = {
            "tracklets": {"tracklets": [{"tracklet_id": "t1", "positions": [1, 2]}]},
            "subjects": {"subjects": [{"candidate_subject_id": "s1"}]},
            "timeline": {},
            "seeds": {"decisions": [{"candidate_subject_id": "s1", "player": "p1", "comment": "skip-me"}]},
            "review_decisions": {"decisions": [{"decision": "assign_roster_player"}]},
            "slot_review": {},
            "gallery": {"cards": [{"generated_at": "2026-01-01"}]},
            "stable_players": {"players": [{"stable_player_id": "A01"}]},
            "global_identity": {"slots": [{"updated_at": "x"}]},
            "segment_decisions": {},
            "material_continuity_decisions": {},
            "mixed_players": {"cases": [{"mixed_hint": "cross_team"}]},
        }
        return documents

    def test_descriptor_aggregate_matches_reference_implementation(self) -> None:
        documents = self._documents()
        match_doc = {
            "id": "m1",
            "teams": [{"team_label": "A", "players": [{"id": "p1"}]}],
            "created_at": "ignored",
        }

        descriptor = _source_descriptor(documents, match_doc, {"status": "fresh"})

        # Aggregate stays byte-identical to the former double-pass helper.
        self.assertEqual(descriptor["semantic_input_digest"], _source_digest(documents, match_doc))

    def test_per_document_digests_are_stable_and_semantic_fields_match(self) -> None:
        documents = self._documents()
        match_doc = {"id": "m1", "teams": [{"team_label": "A"}]}
        descriptor = _source_descriptor(documents, match_doc, {"status": "fresh"})

        self.assertEqual(descriptor["match_digest"], canonical_digest(_semantic_input(match_doc)))
        self.assertEqual(descriptor["roster_digest"], canonical_digest(_semantic_input(match_doc["teams"])))
        for key in ("tracklets", "subjects"):
            expected = canonical_digest(_semantic_input(documents[key]))
            self.assertEqual(descriptor[f"{key}_digest"], expected, key)
        self.assertEqual(
            descriptor["mixed_players_digest"],
            canonical_digest(_semantic_input(documents["mixed_players"])),
        )
        for key in ("gallery", "stable_players", "global_identity"):
            expected = canonical_digest(_semantic_input(documents[key]))
            self.assertEqual(descriptor["stable_identity_digests"][key], expected, key)


class DurableProgressContractTests(unittest.TestCase):
    def _progress(self) -> dict:
        unit = {
            "candidate_subject_id": "subject-large",
            "scope_kind": "whole_subject",
            "priority": "high",
            "current_resolution_status": "pending_high_priority",
            "source_ownership_digest": "digest",
            "detected_pairs": [["t-1", frame] for frame in range(500)],
            "owned_observations": [{"frame": f} for f in range(500)],
        }
        return {
            "schema_version": "2.9.0",
            "status": "ready",
            "summary": {"important_decisions_remaining": 1},
            "coverage_readiness": {"allows_finalize": True},
            "deferred_correction_context": {"schema_version": "1.0.0", "status": "ready", "subjects": [], "canonical_visible_counts": []},
            "next_cases": [unit],
            "optional_audit_cases": [],
            "review_units": [dict(unit) for _ in range(50)],
            "_internal_review_units": [unit],
            "_projection_inputs": {},
            "mixed_players": {
                "schema_version": "1.0.0",
                "mode": "queue",
                "match_id": "m1",
                "summary": {"total": 1, "unresolved": 1, "resolved": 0, "complex_unresolved": 0},
                "cases": [{
                    "case_id": "c1",
                    "candidate_subject_id": "subject-mixed",
                    "resolution_status": "unresolved",
                    "observation_count": 3,
                    "source": {"owned_observations": [{"tracklet_id": "t", "frame": 1}]},
                    "temporal_evidence": {"status": "ready", "anchor_crops": [{"artifact": "a.jpg"}]},
                    "source_tracklet_ids": ["t-1"],
                }],
            },
        }

    def test_durable_artifact_drops_dead_queue_copy_and_exact_mixed_payload(self) -> None:
        durable = durable_review_progress(self._progress())

        self.assertNotIn("review_units", durable)
        self.assertNotIn("_internal_review_units", durable)
        self.assertNotIn("_projection_inputs", durable)
        # Deferred-save action gate still validates against these lists.
        self.assertIsInstance(durable.get("next_cases"), list)
        self.assertIsInstance(durable.get("optional_audit_cases"), list)
        self.assertEqual(durable["next_cases"][0]["candidate_subject_id"], "subject-large")
        # Mixed exact payloads never persist; badges metadata stays.
        mixed_case = durable["mixed_players"]["cases"][0]
        self.assertNotIn("source", mixed_case)
        self.assertNotIn("temporal_evidence", mixed_case)
        self.assertNotIn("source_tracklet_ids", mixed_case)
        self.assertTrue(mixed_case["has_exact_source"])
        self.assertIn("deferred_correction_context", durable)

    def test_durable_artifact_is_substantially_smaller_than_full_progress(self) -> None:
        full = json.dumps(public_view := self._progress()).encode()
        compact = json.dumps(durable_review_progress(public_view)).encode()
        self.assertLess(len(compact), len(full) / 10)


class VideoDigestCacheHardeningTests(unittest.TestCase):
    def _video(self, root: Path, payload: bytes = b"payload") -> Path:
        video = root / "video.mp4"
        video.write_bytes(payload)
        return video

    def _write_cache(self, root: Path, document: dict) -> None:
        (root / DIGEST_CACHE_FILENAME).write_text(json.dumps(document), encoding="utf-8")

    def _valid_cache(self, root: Path, sha: str = "a" * 64) -> dict:
        stat = (root / "video.mp4").stat()
        return {
            "schema_version": "1.0.0",
            "fingerprint": {
                "path": str((root / "video.mp4").resolve()),
                "size_bytes": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            },
            "sha256": sha,
        }

    def test_well_formed_cache_hit_avoids_rehash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._video(root)
            self._write_cache(root, self._valid_cache(root))
            with patch(
                "app.services.identity_reviewed_video._sha",
                side_effect=AssertionError("must reuse valid cache"),
            ):
                digest = reviewed_source_video_digest(root, {"id": "m", "video_filename": "video.mp4"})
            self.assertEqual(digest, "a" * 64)

    def test_uppercase_hex_is_normalized_and_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._video(root)
            self._write_cache(root, self._valid_cache(root, sha="A" * 64))
            digest = reviewed_source_video_digest(root, {"id": "m", "video_filename": "video.mp4"})
            self.assertEqual(digest, "a" * 64)

    def _assert_recompute(self, root: Path) -> None:
        with patch(
            "app.services.identity_reviewed_video._sha",
            return_value="b" * 64,
        ) as sha:
            digest = reviewed_source_video_digest(root, {"id": "m", "video_filename": "video.mp4"})
        sha.assert_called_once()
        self.assertEqual(digest, "b" * 64)

    def test_malformed_sha_recomputes_and_rewrites_valid_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._video(root)
            broken = self._valid_cache(root, sha="foo")
            self._write_cache(root, broken)
            self._assert_recompute(root)
            repaired = json.loads((root / DIGEST_CACHE_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(repaired["sha256"], "b" * 64)
            self.assertEqual(repaired["schema_version"], "1.0.0")

    def test_wrong_schema_version_or_extra_fingerprint_keys_recompute(self) -> None:
        for mutate in (
            lambda cache: {**cache, "schema_version": "9.9.9"},
            lambda cache: {**cache, "fingerprint": {**cache["fingerprint"], "extra": 1}},
            lambda cache: {**cache, "fingerprint": {**cache["fingerprint"], "path": "/other/video.mp4"}},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._video(root)
                self._write_cache(root, mutate(self._valid_cache(root)))
                self._assert_recompute(root)


class AuthoritativeProgressMemoTests(unittest.TestCase):
    def test_progress_built_once_and_reused_within_scope(self) -> None:
        from app.services.identity_reviewed_progress import build_reviewed_identity_progress

        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.identity_reviewed_progress._build_reviewed_identity_progress_uncached",
            side_effect=lambda path, doc, *, include_internal_units=False: {"summary": {}, "n": 1},
        ) as build:
            with review_build_context():
                first = build_reviewed_identity_progress(Path(tmp), {"id": "m"}, include_internal_units=True)
                second = build_reviewed_identity_progress(Path(tmp), {"id": "m"}, include_internal_units=True)
            self.assertIs(first, second)
            build.assert_called_once()
        # Outside a scope nothing is memoized.
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.identity_reviewed_progress._build_reviewed_identity_progress_uncached",
            side_effect=lambda path, doc, *, include_internal_units=False: {"summary": {}},
        ) as build:
            build_reviewed_identity_progress(Path(tmp), {"id": "m"})
            build_reviewed_identity_progress(Path(tmp), {"id": "m"})
        self.assertEqual(build.call_count, 2)

    def test_memo_does_not_leak_between_scopes_after_writes(self) -> None:
        from app.services.identity_reviewed_progress import build_reviewed_identity_progress

        payload = {"summary": {"v": 1}}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.services.identity_reviewed_progress._build_reviewed_identity_progress_uncached",
            # Real builders construct fresh nested structures per call.
            side_effect=lambda path, doc, *, include_internal_units=False: {
                "summary": {"v": payload["summary"]["v"]}
            },
        ):
            with review_build_context():
                first = build_reviewed_identity_progress(Path(tmp), {"id": "m"})
            payload["summary"]["v"] = 2
            with review_build_context():
                second = build_reviewed_identity_progress(Path(tmp), {"id": "m"})
        self.assertEqual(first["summary"]["v"], 1)
        self.assertEqual(second["summary"]["v"], 2)


if __name__ == "__main__":
    unittest.main()
