from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_consensus_shadow import (
    build_identity_jersey_number_consensus_shadow,
)
from app.services.identity_jersey_number_dataset import (
    build_identity_jersey_number_dataset_manifest,
)
from app.services.identity_jersey_number_evidence_shadow import (
    build_identity_jersey_number_evidence_shadow,
)
from app.services.identity_jersey_number_learned import (
    train_identity_jersey_number_learned_baseline,
)
from app.services.identity_jersey_number_heldout_validation import (
    REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS,
    build_production_identity_artifact_comparison,
)
from app.services.identity_jersey_number_offline_evaluation import (
    evaluate_identity_jersey_number_learned,
)
from app.services.identity_jersey_number_recognizer_shadow import (
    build_identity_jersey_number_recognizer_shadow,
)


REPOSITORY_ROOT = BACKEND_DIR.parent
EASY_ROOT = (
    REPOSITORY_ROOT
    / "backend/storage/benchmarks/player_identity"
    / "n0-n4-jersey-number-easy90-20260721-v2"
)
REAL10_ROOT = (
    REPOSITORY_ROOT
    / "backend/storage/benchmarks/player_identity"
    / "n5-within-match-07d227bd-20260722-v1"
)
DEFAULT_PRODUCTION_IDENTITY_ROOT = (
    REPOSITORY_ROOT / "backend/storage/matches/07d227bd"
)
REAL_NUMBER_10_TRACKLET_ID = "100304:1"
REAL_NUMBER_10_SUBJECT_ID = "shadow-a-9ed36c5e3d45ec8d"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Close out J1-J7 jersey-number learned shadow evaluation."
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--easy-root", type=Path, default=EASY_ROOT)
    parser.add_argument("--real10-root", type=Path, default=REAL10_ROOT)
    parser.add_argument(
        "--production-identity-root",
        type=Path,
        default=DEFAULT_PRODUCTION_IDENTITY_ROOT,
    )
    args = parser.parse_args()

    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    generated_at = datetime.now(timezone.utc).isoformat()
    easy_root = args.easy_root.resolve()
    real_root = args.real10_root.resolve()
    real_anchor_root = real_root / "anchor-crops"
    production_identity_root = args.production_identity_root.resolve()
    production_before = _production_identity_snapshot(production_identity_root)

    easy_cards = _load(easy_root / "identity_jersey_number_audit.json")
    easy_reviews = _load(
        easy_root / "identity_jersey_number_observations_codex_operator_reviewed.json"
    )
    real_cards = _load(real_anchor_root / "identity_roster_anchor_crops_shadow.json")
    real_reviews = _load(real_root / "jersey_number_observations_reviewed.json")
    roster_doc = _load(
        real_root
        / "jersey-recognizer-v2-full-rerun"
        / "identity_jersey_number_roster_shadow.json"
    )

    dataset_doc = build_identity_jersey_number_dataset_manifest(
        [
            {
                "source_match_key": "match-corgi-verisk-source",
                "source_video_key": "easy90",
                "crop_root": easy_root,
                "cards_doc": easy_cards,
                "reviewed_observations_doc": easy_reviews,
            },
            {
                "source_match_key": "match-corgi-verisk-source",
                "source_video_key": "real10-targeted",
                "crop_root": real_anchor_root,
                "cards_doc": real_cards,
                "reviewed_observations_doc": real_reviews,
            },
        ],
        generated_at=generated_at,
    )
    model_doc = train_identity_jersey_number_learned_baseline(
        dataset_doc,
        generated_at=generated_at,
    )
    evaluation_doc = evaluate_identity_jersey_number_learned(
        dataset_doc,
        model_doc,
        generated_at=generated_at,
    )
    recognizer_doc = build_identity_jersey_number_recognizer_shadow(
        real_cards,
        roster_doc,
        crop_root=real_anchor_root,
        reviewed_observations_doc=real_reviews,
        learned_model_doc=model_doc,
        generated_at=generated_at,
    )
    evidence_documents = build_identity_jersey_number_evidence_shadow(
        real_cards,
        roster_doc,
        observations_doc={"observations": recognizer_doc["observations"]},
        generated_at=generated_at,
    )
    consensus_documents = build_identity_jersey_number_consensus_shadow(
        evidence_documents["identity_jersey_number_evidence_shadow"],
        roster_doc,
        generated_at=generated_at,
    )
    real_fixture = _real_number_10_fixture(recognizer_doc)
    production_after = _production_identity_snapshot(production_identity_root)
    production_comparison = build_production_identity_artifact_comparison(
        production_before,
        production_after,
    )
    closeout = _closeout_report(
        dataset_doc=dataset_doc,
        model_doc=model_doc,
        evaluation_doc=evaluation_doc,
        recognizer_doc=recognizer_doc,
        real_fixture=real_fixture,
        production_comparison=production_comparison,
        generated_at=generated_at,
    )

    documents = {
        "identity_jersey_number_dataset_manifest.json": dataset_doc,
        "identity_jersey_number_learned_model_shadow.json": model_doc,
        "identity_jersey_number_offline_evaluation.json": evaluation_doc,
        "identity_jersey_number_recognizer_shadow.json": recognizer_doc,
        "identity_jersey_number_evidence_shadow.json": evidence_documents[
            "identity_jersey_number_evidence_shadow"
        ],
        "identity_jersey_number_consensus_shadow.json": consensus_documents[
            "identity_jersey_number_consensus_shadow"
        ],
        "identity_jersey_number_closeout_report.json": closeout,
    }
    for name, document in documents.items():
        _write(output / name, document)
    print(json.dumps(closeout["summary"], indent=2, ensure_ascii=False))


def _real_number_10_fixture(recognizer_doc: dict[str, Any]) -> dict[str, Any]:
    expected_frames = {3509, 3510, 3512}
    rows = [
        row
        for row in recognizer_doc.get("observations") or []
        if int(row.get("frame") or -1) in expected_frames
        and str(row.get("tracklet_id") or "") == REAL_NUMBER_10_TRACKLET_ID
        and str(row.get("candidate_subject_id") or "") == REAL_NUMBER_10_SUBJECT_ID
    ]
    episode_ids = {
        str(row.get("visibility_episode_id"))
        for row in rows
        if row.get("visibility_episode_id")
    }
    passed = bool(
        len(rows) == len(expected_frames)
        and len(episode_ids) == 1
        and all(str(row.get("number")) == "10" for row in rows)
        and all(str(row.get("state")) == "number_confirmed" for row in rows)
    )
    return {
        "fixture_id": "real10-frames-3509-3510-3512",
        "expected_number": "10",
        "expected_tracklet_id": REAL_NUMBER_10_TRACKLET_ID,
        "expected_candidate_subject_id": REAL_NUMBER_10_SUBJECT_ID,
        "expected_frames": sorted(expected_frames),
        "observed_frames": sorted(int(row.get("frame") or 0) for row in rows),
        "visibility_episode_ids": sorted(episode_ids),
        "passed": passed,
        "observations": [
            {
                "frame": row.get("frame"),
                "number": row.get("number"),
                "state": row.get("state"),
                "calibrated_confidence": row.get("calibrated_confidence"),
                "confidence_tier": row.get("confidence_tier"),
                "recognition_method": row.get("recognition_method"),
            }
            for row in rows
        ],
        "limitations": [
            "same_source_match_regression_fixture",
            "not_an_independent_heldout_match",
        ],
    }


def _closeout_report(
    *,
    dataset_doc: dict[str, Any],
    model_doc: dict[str, Any],
    evaluation_doc: dict[str, Any],
    recognizer_doc: dict[str, Any],
    real_fixture: dict[str, Any],
    production_comparison: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    dataset_reasons = list(
        (dataset_doc.get("production_gate") or {}).get("reason_codes") or []
    )
    model_reasons = list(
        (model_doc.get("production_gate") or {}).get("reason_codes") or []
    )
    evaluation_reasons = list(
        (evaluation_doc.get("production_gate") or {}).get("reason_codes") or []
    )
    production_reasons = sorted(
        set(dataset_reasons + model_reasons + evaluation_reasons)
    )
    if not production_comparison.get("production_identity_unchanged"):
        production_reasons.append("canonical_production_digest_comparison_incomplete")
    return {
        "schema_version": "0.1.0",
        "generated_at": generated_at,
        "mode": "shadow_closeout",
        "summary": {
            "dataset_samples": (dataset_doc.get("summary") or {}).get("samples"),
            "trained_numbers": (model_doc.get("summary") or {}).get("trained_numbers"),
            "crop_metrics": evaluation_doc.get("crop_metrics"),
            "episode_metrics": evaluation_doc.get("episode_metrics"),
            "subject_metrics": evaluation_doc.get("subject_metrics"),
            "real_number_10_fixture_passed": real_fixture["passed"],
            "real_rerun_confirmed_numbers": (recognizer_doc.get("summary") or {}).get(
                "confirmed_numbers"
            ),
            "production_eligible": False,
        },
        "gates": {
            "dataset_manifest_versioned": bool(dataset_doc.get("dataset_digest")),
            "learned_model_versioned": bool(model_doc.get("model_digest")),
            "team_scoped_candidates": bool(
                (recognizer_doc.get("safety") or {}).get("team_scoped_roster_candidates")
            ),
            "real_number_10_episode_fixture": real_fixture["passed"],
            "shadow_only": True,
            "production_identity_unchanged": bool(
                production_comparison.get("production_identity_unchanged")
            ),
            "independent_multi_match_validation": False,
        },
        "production_blockers": sorted(
            set(production_reasons or ["independent_multi_match_validation_missing"])
        ),
        "production_artifact_comparison": production_comparison,
        "real_fixture": real_fixture,
    }


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _production_identity_snapshot(root: Path) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    for name in REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS:
        path = root / name
        snapshot[name] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        )
    return snapshot


if __name__ == "__main__":
    main()
