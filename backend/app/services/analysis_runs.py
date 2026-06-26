from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_analysis_run_id(adapter: str) -> str:
    safe_adapter = "".join(ch if ch.isalnum() else "-" for ch in adapter.lower()).strip("-") or "analysis"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{safe_adapter}-{uuid.uuid4().hex[:8]}"


def finalize_analysis_report(match_dir: Path, report: dict[str, Any]) -> dict[str, Any]:
    run_id = str(report.get("run_id") or new_analysis_run_id(str(report.get("analysis_type") or "analysis")))
    run_dir = match_dir / "analysis_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    report["run_id"] = run_id
    report["generated_at"] = report.get("generated_at") or now_iso()
    report["run_directory"] = f"analysis_runs/{run_id}"
    report["run_manifest"] = f"analysis_runs/{run_id}/run_metadata.json"

    artifacts = report.get("artifacts") if isinstance(report.get("artifacts"), dict) else {}
    report["run_artifacts"] = {
        key: f"analysis_runs/{run_id}/{Path(str(filename)).name}"
        for key, filename in artifacts.items()
        if filename
    }

    report_path = match_dir / "analysis_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (run_dir / "analysis_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (run_dir / "run_metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "run_id": run_id,
                "generated_at": report["generated_at"],
                "status": report.get("status"),
                "analysis_type": report.get("analysis_type"),
                "parameters": report.get("parameters") or {},
                "artifacts": report.get("artifacts") or {},
                "run_artifacts": report.get("run_artifacts") or {},
                "report": "analysis_report.json",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    snapshot_names = {"pitch_config.json", *[Path(str(filename)).name for filename in artifacts.values() if filename]}
    for filename in sorted(snapshot_names):
        source = match_dir / filename
        if source.exists() and source.is_file():
            shutil.copy2(source, run_dir / filename)
    return report
