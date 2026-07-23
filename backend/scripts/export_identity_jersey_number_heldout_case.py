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

from app.services.identity_jersey_number_heldout_validation import (
    REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS,
    build_identity_jersey_number_heldout_case_contract,
)


SNAPSHOT_SCHEMA_VERSION = "0.1.0"
SNAPSHOT_ALGORITHM_NAME = "identity_production_artifact_snapshot"
SNAPSHOT_ALGORITHM_VERSION = "0.1.0"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Snapshot production identity and export a canonical N5.8 case.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    snapshot = commands.add_parser(
        "snapshot",
        help="Capture immutable production identity hashes before a shadow evaluation.",
    )
    snapshot.add_argument("--match-dir", type=Path, required=True)
    snapshot.add_argument("--output", type=Path, required=True)

    package = commands.add_parser(
        "package",
        help="Package shadow evidence and before/after production hashes as one N5.8 case.",
    )
    package.add_argument("--benchmark-id", required=True)
    package.add_argument("--source-match-key", required=True)
    package.add_argument("--recognizer", type=Path, required=True)
    package.add_argument("--assignment", type=Path, required=True)
    package.add_argument("--propagation", type=Path, required=True)
    package.add_argument("--targeted-evaluation", type=Path, required=True)
    package.add_argument("--production-before-snapshot", type=Path, required=True)
    package.add_argument("--production-after-match-dir", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--not-held-out", action="store_true")

    args = parser.parse_args()
    if args.command == "snapshot":
        document = build_production_identity_snapshot(args.match_dir.resolve())
    else:
        before = production_hashes_from_snapshot(
            _load_json(args.production_before_snapshot.resolve())
        )
        after_snapshot = build_production_identity_snapshot(
            args.production_after_match_dir.resolve()
        )
        document = build_identity_jersey_number_heldout_case_contract(
            benchmark_id=str(args.benchmark_id),
            source_match_key=str(args.source_match_key),
            recognizer_doc=_load_json(args.recognizer.resolve()),
            assignment_doc=_load_json(args.assignment.resolve()),
            propagation_doc=_load_json(args.propagation.resolve()),
            targeted_evaluation_doc=_load_json(args.targeted_evaluation.resolve()),
            production_before=before,
            production_after=production_hashes_from_snapshot(after_snapshot),
            held_out=not args.not_held_out,
        )
    _write_new_json(args.output.resolve(), document)
    print(json.dumps(_summary(document), indent=2, sort_keys=True))


def build_production_identity_snapshot(
    match_dir: Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = match_dir.resolve()
    artifacts = {
        name: _sha256(root / name) if (root / name).is_file() else None
        for name in REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS
    }
    missing = sorted(name for name, digest in artifacts.items() if not digest)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "algorithm": {
            "name": SNAPSHOT_ALGORITHM_NAME,
            "version": SNAPSHOT_ALGORITHM_VERSION,
            "parameters": {
                "required_artifacts": list(REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS),
                "digest": "sha256_raw_bytes",
            },
        },
        "source_match_dir": str(root),
        "complete": not missing,
        "missing_required_artifacts": missing,
        "artifacts": artifacts,
    }


def production_hashes_from_snapshot(snapshot: dict[str, Any]) -> dict[str, str | None]:
    algorithm = snapshot.get("algorithm") or {}
    artifacts = snapshot.get("artifacts") or {}
    required = set(REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS)
    valid = (
        snapshot.get("schema_version") == SNAPSHOT_SCHEMA_VERSION
        and algorithm.get("name") == SNAPSHOT_ALGORITHM_NAME
        and algorithm.get("version") == SNAPSHOT_ALGORITHM_VERSION
        and isinstance(artifacts, dict)
        and required.issubset(artifacts)
    )
    if not valid:
        raise ValueError("Invalid production identity snapshot contract")
    return {
        name: str(artifacts[name]) if artifacts.get(name) else None
        for name in REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS
    }


def _summary(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("algorithm", {}).get("name") == SNAPSHOT_ALGORITHM_NAME:
        return {
            "kind": "production_identity_snapshot",
            "complete": bool(document.get("complete")),
            "missing_required_artifacts": document.get("missing_required_artifacts") or [],
        }
    case = document.get("case") or {}
    comparison = document.get("production_artifact_comparison") or {}
    return {
        "kind": "heldout_case_contract",
        "benchmark_id": case.get("benchmark_id"),
        "case_contract_valid": bool(case.get("case_contract_valid")),
        "production_identity_unchanged": bool(
            comparison.get("production_identity_unchanged")
        ),
        "reason_codes": case.get("reason_codes") or [],
    }


def _load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return document


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_new_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite immutable artifact: {path}")
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
