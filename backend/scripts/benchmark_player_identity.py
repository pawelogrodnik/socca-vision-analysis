from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from statistics import median
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import STORAGE_DIR
from app.services.identity_diagnostics import build_identity_diagnostics
from app.services.identity_occlusion_assignment_shadow import build_shadow_occlusion_assignments
from app.services.identity_stitching_shadow import build_shadow_stitching_candidates
from app.services.post_yolo_reprocess import reprocess_match_from_artifacts


DEFAULT_MANIFEST = REPO_ROOT / "examples" / "player_identity_benchmarks.json"
CORE_IDENTITY_ARTIFACTS = (
    "global_identity.json",
    "global_identity_report.json",
    "stable_players.json",
    "tracklets.json",
    "frame_detection_counts.json",
    "movement_stats.json",
    "player_stats.json",
    "player_heatmaps.json",
    "team_config.json",
    "team_stats.json",
)
VOLATILE_KEYS = {"generated_at", "created_at", "updated_at", "run_id"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark shadow player-identity diagnostics against frozen YOLO artifacts.",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--case", action="append", default=[], help="Benchmark label/id. Repeat or omit for all.")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--max-overhead-percent", type=float, default=15.0)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.resolve().read_text(encoding="utf-8"))
    selected = _select_cases(manifest.get("benchmarks") or [], args.case)
    if not selected:
        raise ValueError("No benchmark cases selected.")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = (
        args.output_root.resolve()
        if args.output_root
        else STORAGE_DIR / "benchmarks" / "player_identity" / timestamp
    )
    output_root.mkdir(parents=True, exist_ok=False)

    case_reports: list[dict[str, Any]] = []
    for case in selected:
        case_reports.append(
            run_benchmark_case(
                case,
                output_root=output_root,
                max_overhead_percent=float(args.max_overhead_percent),
            )
        )

    summary = _suite_summary(case_reports)
    suite_gate = summary["hard_benchmark_has_more_recoverable_and_occlusion_events"] is not False
    report = {
        "schema_version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(args.manifest.resolve()),
        "status": "passed" if all(row["status"] == "passed" for row in case_reports) and suite_gate else "failed",
        "summary": summary,
        "cases": case_reports,
    }
    (output_root / "identity_benchmark_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "output_root": str(output_root), "summary": report["summary"]}, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


def run_benchmark_case(
    case: dict[str, Any],
    *,
    output_root: Path,
    max_overhead_percent: float,
) -> dict[str, Any]:
    label = str(case.get("label") or case.get("benchmark_id"))
    case_dir = output_root / label
    baseline_dir = case_dir / "baseline-no-diagnostics"
    candidate_dir = case_dir / "candidate-shadow-diagnostics"
    source_dir = (REPO_ROOT / str(case["source_path"])).resolve()
    video_value = case.get("video_path")
    video_path = (REPO_ROOT / str(video_value)).resolve() if video_value else None
    common = {
        "source_dir": source_dir,
        "video_path": video_path,
        "include_ball": False,
        "build_possession": False,
        "write_raw_overlay": False,
        "write_debug_overlay": False,
        "render_stable_overlay": False,
        "start_sec": float(case.get("start_sec") or 0.0),
        "max_seconds": float(case.get("max_seconds") or 0.0) or None,
    }

    print(json.dumps({"benchmark": label, "run": "baseline", "status": "started"}), flush=True)
    baseline_started = time.perf_counter()
    reprocess_match_from_artifacts(
        output_dir=baseline_dir,
        label=f"{label}-baseline",
        enable_identity_diagnostics=False,
        progress=_progress_printer(label, "baseline"),
        **common,
    )
    baseline_seconds = time.perf_counter() - baseline_started

    print(json.dumps({"benchmark": label, "run": "candidate", "status": "started"}), flush=True)
    candidate_started = time.perf_counter()
    candidate_report = reprocess_match_from_artifacts(
        output_dir=candidate_dir,
        label=f"{label}-candidate",
        enable_identity_diagnostics=True,
        progress=_progress_printer(label, "candidate"),
        **common,
    )
    candidate_seconds = time.perf_counter() - candidate_started

    comparisons = compare_core_artifacts(baseline_dir, candidate_dir)
    diagnostics = _load_json(candidate_dir / "identity_fragmentation_report.json")
    quality = _load_json(candidate_dir / "identity_tracklet_quality.json")
    occlusions = _load_json(candidate_dir / "identity_occlusion_events.json")
    stitching = _load_json(candidate_dir / "identity_stitching_candidates.json")
    joint_assignments = _load_json(candidate_dir / "identity_occlusion_assignments.json")
    end_to_end_delta_percent = (
        ((candidate_seconds - baseline_seconds) / baseline_seconds) * 100.0
        if baseline_seconds > 0
        else 0.0
    )
    diagnostics_seconds = _measure_diagnostics_runtime(
        candidate_dir,
        fps=float((candidate_report.get("video") or {}).get("fps") or 25.0),
    )
    overhead_percent = (
        (diagnostics_seconds / baseline_seconds) * 100.0
        if baseline_seconds > 0
        else 0.0
    )
    verified_subjects = [str(item) for item in case.get("verified_stable_subjects") or []]
    verified_switches = [
        row
        for row in diagnostics.get("suspected_switches") or []
        if str(row.get("stable_player_id")) in verified_subjects
    ]
    no_impact = all(row["equal"] for row in comparisons)
    diagnostics_present = all(
        (candidate_dir / filename).exists()
        for filename in (
            "identity_tracklet_quality.json",
            "identity_occlusion_events.json",
            "identity_fragmentation_report.json",
            "identity_stitching_candidates.json",
            "identity_occlusion_assignments.json",
        )
    )
    stable_subject_gate = all(row.get("occlusion_event_ids") or row.get("conflict_evidence") for row in verified_switches)
    verified_aliases = {
        alias
        for subject in verified_subjects
        for alias in (subject, f"slot-{subject}")
    }
    verified_stitching_conflicts = [
        row
        for row in stitching.get("recommended_identity_contradictions") or []
        if verified_aliases
        & set((row.get("source_stable_subject_ids") or []) + (row.get("target_stable_subject_ids") or []))
    ]
    overhead_gate = overhead_percent <= max_overhead_percent
    gates = {
        "identity_outputs_unchanged": no_impact,
        "diagnostic_artifacts_present": diagnostics_present,
        "verified_subject_switches_have_evidence": stable_subject_gate,
        "verified_subjects_have_no_conflicting_stitch_recommendations": not verified_stitching_conflicts,
        "runtime_overhead_within_limit": overhead_gate,
    }
    report = {
        "benchmark_id": case.get("benchmark_id"),
        "label": label,
        "status": "passed" if all(gates.values()) else "failed",
        "source_dir": str(source_dir),
        "time_window": {
            "start_sec": float(case.get("start_sec") or 0.0),
            "max_seconds": float(case.get("max_seconds") or 0.0) or None,
        },
        "runtime": {
            "baseline_sec": round(baseline_seconds, 3),
            "candidate_sec": round(candidate_seconds, 3),
            "observed_end_to_end_delta_sec": round(candidate_seconds - baseline_seconds, 3),
            "observed_end_to_end_delta_percent": round(end_to_end_delta_percent, 2),
            "diagnostics_stage_sec_median": round(diagnostics_seconds, 3),
            "overhead_percent": round(overhead_percent, 2),
            "overhead_measurement": "isolated_diagnostics_stage_median_of_3",
            "max_overhead_percent": max_overhead_percent,
        },
        "gates": gates,
        "identity_comparison": comparisons,
        "diagnostic_summary": {
            "fragmentation": diagnostics.get("summary") or {},
            "tracklet_quality": quality.get("summary") or {},
            "occlusions": occlusions.get("summary") or {},
            "stitching": stitching.get("summary") or {},
            "joint_occlusion_assignments": joint_assignments.get("summary") or {},
            "verified_subject_suspected_switches": len(verified_switches),
            "verified_subject_conflicting_stitch_recommendations": len(verified_stitching_conflicts),
        },
        "outputs": {
            "baseline": str(baseline_dir),
            "candidate": str(candidate_dir),
        },
    }
    (case_dir / "case_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _measure_diagnostics_runtime(candidate_dir: Path, *, fps: float, repeats: int = 3) -> float:
    tracklets_doc = _load_json(candidate_dir / "tracklets.json")
    global_identity = _load_json(candidate_dir / "global_identity.json")
    assignments_path = candidate_dir / "player_identity_assignments.json"
    assignments = _load_json(assignments_path) if assignments_path.exists() else None
    durations: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        documents = build_identity_diagnostics(
            list(tracklets_doc.get("tracklets") or []),
            list(tracklets_doc.get("rejected_tracklets") or []),
            global_identity,
            fps=fps,
            manual_assignments_doc=assignments,
            generated_at="benchmark-runtime-measurement",
        )
        build_shadow_stitching_candidates(
            list(tracklets_doc.get("tracklets") or []),
            documents["identity_tracklet_quality"],
            documents["identity_occlusion_events"],
            global_identity,
            fps=fps,
            generated_at="benchmark-runtime-measurement",
        )
        build_shadow_occlusion_assignments(
            list(tracklets_doc.get("tracklets") or []),
            documents["identity_tracklet_quality"],
            documents["identity_occlusion_events"],
            global_identity,
            fps=fps,
            generated_at="benchmark-runtime-measurement",
        )
        durations.append(time.perf_counter() - started)
    return float(median(durations))


def compare_core_artifacts(baseline_dir: Path, candidate_dir: Path) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for filename in CORE_IDENTITY_ARTIFACTS:
        baseline_path = baseline_dir / filename
        candidate_path = candidate_dir / filename
        baseline_hash = _normalized_json_hash(baseline_path) if baseline_path.exists() else None
        candidate_hash = _normalized_json_hash(candidate_path) if candidate_path.exists() else None
        comparisons.append(
            {
                "artifact": filename,
                "baseline_sha256": baseline_hash,
                "candidate_sha256": candidate_hash,
                "equal": baseline_hash is not None and baseline_hash == candidate_hash,
            }
        )
    return comparisons


def _normalized_json_hash(path: Path) -> str:
    normalized = _normalize_json(_load_json(path))
    payload = json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_json(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    return value


def _select_cases(cases: list[dict[str, Any]], requested: list[str]) -> list[dict[str, Any]]:
    if not requested:
        return cases
    wanted = set(requested)
    return [
        row
        for row in cases
        if str(row.get("label")) in wanted or str(row.get("benchmark_id")) in wanted
    ]


def _suite_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_label = {str(row["label"]): row for row in cases}
    easy = by_label.get("easy90")
    hard = by_label.get("hard3m")
    hard_is_harder = None
    if easy and hard:
        easy_diag = easy["diagnostic_summary"]
        hard_diag = hard["diagnostic_summary"]
        hard_is_harder = bool(
            int((hard_diag["fragmentation"] or {}).get("recoverable_tracklets") or 0)
            > int((easy_diag["fragmentation"] or {}).get("recoverable_tracklets") or 0)
            and int((hard_diag["occlusions"] or {}).get("events") or 0)
            > int((easy_diag["occlusions"] or {}).get("events") or 0)
        )
    return {
        "cases": len(cases),
        "passed": sum(1 for row in cases if row["status"] == "passed"),
        "failed": sum(1 for row in cases if row["status"] != "passed"),
        "hard_benchmark_has_more_recoverable_and_occlusion_events": hard_is_harder,
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _progress_printer(label: str, run: str):
    last_stage: str | None = None
    last_percent = -10.0

    def report(stage: str, percent: float, message: str, extra: dict[str, Any] | None) -> None:
        nonlocal last_stage, last_percent
        value = float(percent)
        if stage == last_stage and value < last_percent + 5.0:
            return
        last_stage = stage
        last_percent = value
        print(
            json.dumps(
                {
                    "benchmark": label,
                    "run": run,
                    "stage": stage,
                    "progress_percent": round(value, 2),
                    "message": message,
                    "extra": extra or {},
                },
                ensure_ascii=True,
            ),
            flush=True,
        )

    return report


if __name__ == "__main__":
    main()
