from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services.artifact_lineage import canonical_json_sha256, generated_from_entry
from app.services.attacking_momentum import build_attacking_momentum_document
from app.services.ball_possession import append_restart_pass_candidates
from app.services.candidate_keys import (
    ensure_contact_candidate_keys,
    ensure_pass_candidate_keys,
    ensure_restart_candidate_keys,
)
from app.services.event_candidates import build_event_candidate_artifacts
from app.services.match_phase_config import load_match_phase_config
from app.services.pass_candidates import (
    apply_existing_pass_reviews,
    build_pass_review_report,
    update_pass_candidate_summary,
)

REBUILD_ALGORITHM = {"name": "canonical_ball_event_rebuild", "version": "1.0.0"}
FRESHNESS_STATUSES = {"fresh", "stale", "missing_inputs", "legacy_unknown"}
REBUILD_TRIGGERS = {"contact_review", "match_phase_review", "pass_review", "package_publish"}


def rebuild_ball_event_artifacts(
    match_path: Path,
    *,
    trigger: str,
    contact_candidates_doc: dict[str, Any] | None = None,
    match_phase_config_doc: dict[str, Any] | None = None,
    pass_candidates_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if trigger not in REBUILD_TRIGGERS:
        raise ValueError(f"Unsupported ball-event rebuild trigger: {trigger}")

    meta = _load_json(match_path / "match.json") or {}
    contact_doc = contact_candidates_doc or _load_json(match_path / "contact_candidates.json")
    phase_doc = match_phase_config_doc or load_match_phase_config(match_path, meta)
    possession_doc = _load_json(match_path / "possession_candidates.json") or {"frames": []}
    segments_doc = _load_json(match_path / "possession_segments.json") or {"segments": []}
    restart_doc = _load_json(match_path / "restart_candidates.json") or {"candidates": []}
    existing_pass_doc = pass_candidates_doc or _load_json(match_path / "pass_candidates.json")
    if contact_doc is not None:
        ensure_contact_candidate_keys(contact_doc)
    ensure_restart_candidate_keys(restart_doc)
    ensure_pass_candidate_keys(existing_pass_doc, contact_doc, restart_doc)

    if trigger == "pass_review" and existing_pass_doc is not None:
        pass_doc = existing_pass_doc
        update_pass_candidate_summary(pass_doc)
        event_doc = _load_json(match_path / "event_candidates.json")
        event_report = _load_json(match_path / "event_review_report.json")
    elif contact_doc is not None:
        event_artifacts = build_event_candidate_artifacts(contact_doc, phase_doc, possession_doc)
        event_doc = event_artifacts["event_candidates"]
        event_report = event_artifacts["event_review_report"]
        pass_doc = event_artifacts["pass_candidates"]
        append_restart_pass_candidates(pass_doc, restart_doc)
        apply_existing_pass_reviews(pass_doc, existing_pass_doc)
    else:
        extra_documents = {"match_phase_config.json": phase_doc} if trigger == "match_phase_review" else None
        return _write_unavailable_readiness(
            match_path,
            trigger,
            "contact_candidates.json",
            extra_documents=extra_documents,
        )

    pass_report = build_pass_review_report(pass_doc)
    pitch_width_m, pitch_length_m = _pitch_dimensions(match_path, possession_doc)
    duration_sec = _match_duration_sec(meta)
    momentum_doc = build_attacking_momentum_document(
        possession_doc,
        phase_doc,
        pitch_width_m=pitch_width_m,
        pitch_length_m=pitch_length_m,
        pass_candidates_doc=pass_doc,
        restart_candidates_doc=restart_doc,
        possession_segments_doc=segments_doc,
        match_duration_sec=duration_sec,
    )

    documents: dict[str, dict[str, Any]] = {}
    if trigger == "contact_review" and contact_doc is not None:
        documents["contact_candidates.json"] = contact_doc
    documents["match_phase_config.json"] = phase_doc
    if restart_doc.get("candidates"):
        documents["restart_candidates.json"] = restart_doc
    if event_doc is not None:
        documents["event_candidates.json"] = _with_lineage(
            event_doc,
            [("contact_candidates.json", contact_doc or {})],
            "event_candidates",
        )
    if event_report is not None:
        documents["event_review_report.json"] = _with_lineage(
            event_report,
            [("event_candidates.json", documents.get("event_candidates.json", event_doc or {}))],
            "event_review_report",
        )
    pass_inputs = [
        ("event_candidates.json", documents.get("event_candidates.json", event_doc or {})),
        ("match_phase_config.json", phase_doc),
        ("possession_candidates.json", possession_doc),
        ("restart_candidates.json", restart_doc),
    ]
    if trigger == "pass_review" and pass_doc.get("generated_from"):
        pass_with_lineage = dict(pass_doc)
        pass_with_lineage["freshness"] = "fresh"
    else:
        pass_with_lineage = _with_lineage(pass_doc, pass_inputs, "pass_candidates")
    documents["pass_candidates.json"] = pass_with_lineage
    documents["pass_review_report.json"] = _with_lineage(
        pass_report,
        [("pass_candidates.json", pass_with_lineage)],
        "pass_review_report",
    )
    momentum_inputs = [
        ("possession_candidates.json", possession_doc),
        ("possession_segments.json", segments_doc),
        ("match_phase_config.json", phase_doc),
        ("pass_candidates.json", pass_with_lineage),
        ("restart_candidates.json", restart_doc),
    ]
    documents["attacking_momentum.json"] = _with_lineage(momentum_doc, momentum_inputs, "attacking_momentum")
    readiness = build_analytics_readiness(
        possession_doc=possession_doc,
        pass_doc=pass_doc,
        momentum_doc=documents["attacking_momentum.json"],
        trigger=trigger,
    )
    documents["analytics_readiness.json"] = readiness
    manifest = _generation_manifest(trigger, documents)
    atomic_write_rebuild_documents(match_path, documents, manifest)
    return {
        "trigger": trigger,
        "artifacts": sorted(documents),
        "analytics_readiness": readiness,
        "manifest": manifest,
        "pass_candidates": pass_doc,
        "attacking_momentum": momentum_doc,
    }


def ensure_ball_event_artifacts_fresh(match_path: Path) -> dict[str, Any]:
    readiness = _load_json(match_path / "analytics_readiness.json")
    momentum = _load_json(match_path / "attacking_momentum.json")
    status = artifact_freshness_status(match_path, momentum)
    if status == "fresh" and readiness:
        return readiness
    try:
        result = rebuild_ball_event_artifacts(match_path, trigger="package_publish")
        return result["analytics_readiness"]
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _write_unavailable_readiness(match_path, "package_publish", str(exc))["analytics_readiness"]


def artifact_freshness_status(match_path: Path, document: dict[str, Any] | None) -> str:
    if not document:
        return "missing_inputs"
    generated_from = document.get("generated_from")
    if not isinstance(generated_from, list):
        return "legacy_unknown"
    for entry in generated_from:
        if not isinstance(entry, dict) or not entry.get("artifact") or not entry.get("sha256"):
            return "legacy_unknown"
        source = _load_json(match_path / str(entry["artifact"]))
        if source is None:
            return "missing_inputs"
        if canonical_json_sha256(source) != str(entry["sha256"]):
            return "stale"
    return "fresh"


def build_analytics_readiness(
    *,
    possession_doc: dict[str, Any],
    pass_doc: dict[str, Any],
    momentum_doc: dict[str, Any],
    trigger: str,
) -> dict[str, Any]:
    possession_available = bool(possession_doc.get("frames"))
    passes_available = isinstance(pass_doc.get("candidates"), list)
    momentum_available = momentum_doc.get("status") in {"completed", "available"}
    return {
        "schema_version": "0.1.0",
        "generated_at": _now_iso(),
        "source": REBUILD_ALGORITHM["name"],
        "trigger": trigger,
        "features": {
            "ball": {"status": "fresh" if possession_available else "missing_inputs"},
            "possession": {"status": "fresh" if possession_available else "missing_inputs"},
            "passes": {"status": "fresh" if passes_available else "missing_inputs"},
            "momentum": {
                "status": "fresh" if momentum_available else "missing_inputs",
                "signal_quality": momentum_doc.get("signal_quality")
                or (momentum_doc.get("summary") or {}).get("signal_quality")
                or (momentum_doc.get("summary") or {}).get("quality"),
                "product_readiness": momentum_doc.get("product_readiness") or "experimental",
            },
        },
        "warnings": [],
    }


def atomic_write_rebuild_documents(
    match_path: Path,
    documents: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    *,
    replace_file: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None] = os.replace,
) -> None:
    match_path.mkdir(parents=True, exist_ok=True)
    stage_path = Path(tempfile.mkdtemp(prefix=".ball-event-stage-", dir=match_path))
    backup_path = Path(tempfile.mkdtemp(prefix=".ball-event-backup-", dir=match_path))
    replaced: list[str] = []
    backed_up: list[str] = []
    manifest_name = "ball_event_generation.json"
    try:
        for filename, document in documents.items():
            (stage_path / filename).write_text(json.dumps(document, indent=2), encoding="utf-8")
        (stage_path / manifest_name).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for filename in [*documents.keys(), manifest_name]:
            target = match_path / filename
            if target.exists():
                replace_file(target, backup_path / filename)
                backed_up.append(filename)
            replace_file(stage_path / filename, target)
            replaced.append(filename)
    except Exception:
        for filename in reversed(replaced):
            target = match_path / filename
            if target.exists():
                target.unlink()
        for filename in reversed(backed_up):
            backup = backup_path / filename
            if backup.exists():
                os.replace(backup, match_path / filename)
        raise
    finally:
        shutil.rmtree(stage_path, ignore_errors=True)
        shutil.rmtree(backup_path, ignore_errors=True)


def _with_lineage(
    document: dict[str, Any],
    inputs: list[tuple[str, dict[str, Any]]],
    algorithm_name: str,
) -> dict[str, Any]:
    result = dict(document)
    result["generated_from"] = [generated_from_entry(filename, source) for filename, source in inputs]
    document_algorithm = document.get("algorithm")
    if not isinstance(document_algorithm, dict) or not document_algorithm.get("version"):
        document_algorithm = {"name": algorithm_name, "version": REBUILD_ALGORITHM["version"]}
    result["algorithm"] = dict(document_algorithm)
    result["freshness"] = "fresh"
    return result


def _write_unavailable_readiness(
    match_path: Path,
    trigger: str,
    reason: str,
    *,
    extra_documents: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    warning = {"code": "missing_canonical_input", "message": reason}
    readiness = {
        "schema_version": "0.1.0",
        "generated_at": _now_iso(),
        "source": REBUILD_ALGORITHM["name"],
        "trigger": trigger,
        "features": {
            "ball": {"status": "legacy_unknown"},
            "possession": {"status": "legacy_unknown"},
            "passes": {"status": "missing_inputs"},
            "momentum": {"status": "missing_inputs", "product_readiness": "not_available"},
        },
        "warnings": [warning],
    }
    unavailable_momentum = {
        "schema_version": "0.3.0",
        "generated_at": _now_iso(),
        "source": "attacking_momentum_v1",
        "status": "not_available",
        "signal_quality": "unavailable",
        "product_readiness": "not_available",
        "experimental": True,
        "points": [],
        "warnings": [warning],
    }
    documents = {
        **(extra_documents or {}),
        "analytics_readiness.json": readiness,
        "attacking_momentum.json": unavailable_momentum,
    }
    manifest = _generation_manifest(trigger, documents)
    atomic_write_rebuild_documents(match_path, documents, manifest)
    return {"analytics_readiness": readiness, "attacking_momentum": unavailable_momentum, "manifest": manifest}


def _generation_manifest(trigger: str, documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    generation_id = str(uuid.uuid4())
    return {
        "schema_version": "0.1.0",
        "generation_id": generation_id,
        "generated_at": _now_iso(),
        "trigger": trigger,
        "algorithm": REBUILD_ALGORITHM,
        "artifacts": [
            {"artifact": filename, "sha256": canonical_json_sha256(document)}
            for filename, document in sorted(documents.items())
        ],
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    return document if isinstance(document, dict) else None


def _pitch_dimensions(match_path: Path, possession_doc: dict[str, Any]) -> tuple[float, float]:
    pitch = _load_json(match_path / "pitch_config.json") or {}
    parameters = possession_doc.get("parameters") if isinstance(possession_doc.get("parameters"), dict) else {}
    width = pitch.get("width_m") or pitch.get("pitch_width_m") or parameters.get("pitch_width_m") or 30.0
    length = pitch.get("length_m") or pitch.get("pitch_length_m") or parameters.get("pitch_length_m") or 47.4
    return float(width), float(length)


def _match_duration_sec(meta: dict[str, Any]) -> float | None:
    video = meta.get("video") if isinstance(meta.get("video"), dict) else {}
    value = video.get("duration_sec")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
