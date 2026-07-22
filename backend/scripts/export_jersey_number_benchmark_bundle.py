from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest


BUNDLE_SCHEMA_VERSION = "0.1.0"


def _load(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _source_record(path: Path, document: dict[str, Any]) -> dict[str, Any]:
    return {
        "filename": path.name,
        "canonical_digest": canonical_digest(document),
        "schema_version": document.get("schema_version"),
        "algorithm": document.get("algorithm"),
    }


def _compact_report(document: dict[str, Any] | None, *fields: str) -> dict[str, Any]:
    if document is None:
        return {"available": False}
    compact = {"available": True}
    for field in fields:
        if field in document:
            compact[field] = document[field]
    return compact


def export_bundle(
    *,
    name: str,
    output_dir: Path,
    exporter_base_revision: str,
    artifact_source_revision: str,
    report_path: Path,
    consensus_path: Path,
    assignment_path: Path,
    propagation_path: Path | None = None,
    targeted_evaluation_path: Path | None = None,
) -> dict[str, Any]:
    source_paths = {
        "report": report_path,
        "consensus": consensus_path,
        "assignment": assignment_path,
        "propagation": propagation_path,
        "targeted_evaluation": targeted_evaluation_path,
    }
    documents = {key: _load(path) for key, path in source_paths.items()}
    output_dir.mkdir(parents=True, exist_ok=True)

    report = documents["report"] or {}
    goldset_summary = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "benchmark": name,
        "evaluation": report.get("goldset_evaluation") or {"available": False},
        "source_report_digest": canonical_digest(report),
    }
    consensus_report = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "benchmark": name,
        **_compact_report(documents["consensus"], "schema_version", "algorithm", "summary", "safety"),
    }
    assignment_gate_report = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "benchmark": name,
        **_compact_report(documents["assignment"], "schema_version", "algorithm", "summary", "gates", "safety"),
    }
    propagation_report = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "benchmark": name,
        **_compact_report(documents["propagation"], "schema_version", "algorithm", "summary", "safety"),
    }
    targeted_evaluation = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "benchmark": name,
        **_compact_report(documents["targeted_evaluation"], "schema_version", "algorithm", "summary"),
    }

    generated_files = {
        "goldset_summary.json": goldset_summary,
        "consensus_report.json": consensus_report,
        "assignment_gate_report.json": assignment_gate_report,
        "propagation_report.json": propagation_report,
        "targeted_evaluation.json": targeted_evaluation,
    }
    for filename, document in generated_files.items():
        (output_dir / filename).write_text(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "benchmark": name,
        "exporter_base_revision": exporter_base_revision,
        "artifact_source_revision": artifact_source_revision,
        "artifact_contract": "historical_shadow_baseline",
        "activation_eligible": False,
        "activation_blockers": [
            "historical_artifacts_precede_n5_hardened_contract",
            "heldout_multi_match_validation_required",
            "automatic_recognizer_calibration_required",
        ],
        "sources": {
            key: _source_record(path, documents[key] or {})
            for key, path in source_paths.items()
            if path is not None
        },
        "outputs": {
            filename: canonical_digest(document)
            for filename, document in generated_files.items()
        },
    }
    (output_dir / "benchmark_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a lightweight jersey-number benchmark bundle.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--exporter-base-revision", required=True)
    parser.add_argument("--artifact-source-revision", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--consensus", type=Path, required=True)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--propagation", type=Path)
    parser.add_argument("--targeted-evaluation", type=Path)
    args = parser.parse_args()
    manifest = export_bundle(
        name=args.name,
        output_dir=args.output_dir,
        exporter_base_revision=args.exporter_base_revision,
        artifact_source_revision=args.artifact_source_revision,
        report_path=args.report,
        consensus_path=args.consensus,
        assignment_path=args.assignment,
        propagation_path=args.propagation,
        targeted_evaluation_path=args.targeted_evaluation,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
