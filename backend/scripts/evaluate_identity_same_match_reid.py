from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_active_roster_shadow import build_identity_active_roster_shadow
from app.services.identity_candidate_shadow import build_identity_candidate_shadow
from app.services.identity_fragment_consolidation_shadow import (
    build_identity_fragment_consolidation_shadow,
)
from app.services.identity_same_match_reid import (
    JsonEmbeddingCache,
    build_same_match_reid_evidence,
    load_default_embedder,
)


DEFAULT_MANIFEST = REPO_ROOT / "examples" / "player_identity_benchmarks.json"
DEFAULT_GOLDSET = (
    BACKEND_DIR / "tests" / "fixtures" / "player_identity" / "identity_fragment_consolidation_goldset_v1.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate advisory same-match ReID evidence on frozen identity benchmarks.",
    )
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--goldset", type=Path, default=DEFAULT_GOLDSET)
    parser.add_argument("--models-dir", type=Path, default=BACKEND_DIR / "models")
    parser.add_argument(
        "--embedding-cache-dir",
        type=Path,
        default=BACKEND_DIR / "storage" / "cache" / "person_reid_embeddings",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()

    benchmark_root = args.benchmark_root.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    manifest = _load_json(args.manifest.resolve())
    goldset = _load_json(args.goldset.resolve())
    cases = _select_cases(manifest.get("benchmarks") or [], args.case)
    if not cases:
        raise ValueError("No benchmark cases selected")
    embedder, model_status = load_default_embedder(args.models_dir.resolve())
    generated_at = datetime.now(timezone.utc).isoformat()
    case_documents: dict[str, dict[str, Any]] = {}
    case_reports: list[dict[str, Any]] = []
    for case in cases:
        label = str(case.get("label") or case.get("benchmark_id"))
        embedding_cache = None
        if embedder is not None:
            embedding_cache = JsonEmbeddingCache.load(
                args.embedding_cache_dir.resolve() / f"{label}.json",
                model_name=embedder.model_name,
                model_version=embedder.model_version,
                embedding_dimension=embedder.embedding_dimension,
            )
        print(json.dumps({"case": label, "status": "started"}), flush=True)
        documents = _evaluate_case(
            case,
            benchmark_root=benchmark_root,
            embedder=embedder,
            model_status=model_status,
            generated_at=generated_at,
            embedding_cache=embedding_cache,
        )
        if embedding_cache is not None:
            embedding_cache.save()
        case_dir = output_root / label
        case_dir.mkdir(parents=True, exist_ok=False)
        for filename, document in (
            ("identity_same_match_reid.json", documents["identity_same_match_reid"]),
            ("identity_same_match_reid_report.json", documents["identity_same_match_reid_report"]),
        ):
            (case_dir / filename).write_text(
                json.dumps(document, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        case_documents[label] = documents["identity_same_match_reid"]
        case_reports.append(
            {
                "benchmark_id": case.get("benchmark_id"),
                "label": label,
                "status": documents["identity_same_match_reid_report"]["status"],
                "summary": documents["identity_same_match_reid"]["summary"],
                "output_dir": str(case_dir),
            }
        )
        print(json.dumps({"case": label, "status": "complete", **case_reports[-1]["summary"]}), flush=True)

    evaluation = _evaluate_goldset(goldset, case_documents, model_status=model_status)
    suite = {
        "schema_version": "0.1.0",
        "generated_at": generated_at,
        "mode": "same_match_reid_shadow_benchmark",
        "model": model_status,
        "benchmark_root": str(benchmark_root),
        "goldset": str(args.goldset.resolve()),
        "status": "ready" if embedder is not None else "unavailable",
        "cases": case_reports,
        "evaluation": evaluation,
        "safety": {
            "production_identity_untouched": True,
            "candidate_identity_untouched": True,
            "merge_threshold_enabled": False,
        },
    }
    (output_root / "identity_same_match_reid_evaluation.json").write_text(
        json.dumps(suite, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps({"output_root": str(output_root), "status": suite["status"], **evaluation["summary"]}, indent=2))


def _evaluate_case(
    case: dict[str, Any],
    *,
    benchmark_root: Path,
    embedder: Any,
    model_status: dict[str, Any],
    generated_at: str,
    embedding_cache: JsonEmbeddingCache | None,
) -> dict[str, dict[str, Any]]:
    label = str(case.get("label") or case.get("benchmark_id"))
    candidate_dir = benchmark_root / label / "candidate-shadow-diagnostics"
    analysis_report = _load_json(candidate_dir / "analysis_report.json")
    fps = float((analysis_report.get("video") or {}).get("fps") or 30.0)
    global_identity = _load_json(candidate_dir / "global_identity.json")
    offline_identity = _load_json(candidate_dir / "identity_offline_shadow.json")
    timeline = _load_json(candidate_dir / "identity_offline_shadow_timeline.json")
    candidate_documents = build_identity_candidate_shadow(
        offline_identity,
        timeline,
        global_identity,
        fps=fps,
        generated_at=generated_at,
        include_overlay=True,
    )
    candidate_overlay = candidate_documents["identity_candidate_shadow_overlay"]
    active_documents = build_identity_active_roster_shadow(
        candidate_documents["identity_candidate_shadow"],
        candidate_overlay,
        generated_at=generated_at,
    )
    consolidation_documents = build_identity_fragment_consolidation_shadow(
        candidate_documents["identity_candidate_shadow"],
        candidate_overlay,
        active_documents["identity_active_roster_shadow"],
        fps=fps,
        generated_at=generated_at,
    )
    video_value = case.get("visual_clip_path") or case.get("video_path")
    if not video_value:
        raise ValueError(f"Benchmark {label} has no visual source video")
    visual_clip = bool(case.get("visual_clip_path"))
    offset = float(case.get("start_sec") or 0.0) if visual_clip else 0.0
    return build_same_match_reid_evidence(
        candidate_documents["identity_candidate_shadow"],
        timeline,
        consolidation_documents["identity_fragment_consolidation_shadow"],
        video_path=(REPO_ROOT / str(video_value)).resolve(),
        fps=fps,
        video_time_offset_sec=offset,
        embedder=embedder,
        embedding_cache=embedding_cache,
        model_status=model_status,
        generated_at=generated_at,
    )


def _evaluate_goldset(
    goldset: dict[str, Any],
    case_documents: dict[str, dict[str, Any]],
    *,
    model_status: dict[str, Any],
) -> dict[str, Any]:
    pair_index = {
        (label, str(row.get("proposal_key") or "")): row
        for label, document in case_documents.items()
        for row in document.get("pairs") or []
    }
    rows: list[dict[str, Any]] = []
    for item in goldset.get("items") or []:
        label = str(item.get("benchmark_label") or "")
        candidate_key = str(item.get("candidate_key") or "")
        evidence = pair_index.get((label, candidate_key)) or {}
        rows.append(
            {
                "benchmark_label": label,
                "candidate_key": candidate_key,
                "review_status": item.get("review_status"),
                "expected_same_person": item.get("expected_same_person"),
                "evidence_status": evidence.get("status") or "missing",
                "prototype_distance": evidence.get("prototype_distance"),
                "appearance_reliable": bool(evidence.get("appearance_reliable")),
                "reason_codes": evidence.get("reason_codes") or ["pair_evidence_missing"],
            }
        )
    summary = _evaluation_summary(rows)
    per_benchmark = {
        label: _evaluation_summary([row for row in rows if row["benchmark_label"] == label])
        for label in sorted({row["benchmark_label"] for row in rows})
    }
    return {
        "summary": summary,
        "per_benchmark": per_benchmark,
        "threshold_policy": {
            "enabled": False,
            "recommended_threshold": None,
            "reason": "shadow_distance_distribution_not_yet_approved_for_identity_merges",
            "diagnostic_zero_false_positive_operating_point": summary["zero_false_positive_operating_point"],
        },
        "model_available": bool(model_status.get("available")),
        "items": rows,
    }


def _evaluation_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labeled = [row for row in rows if row["expected_same_person"] is not None]
    available = [
        row
        for row in labeled
        if row["appearance_reliable"] and row["prototype_distance"] is not None
    ]
    same = [
        float(row["prototype_distance"])
        for row in available
        if row["expected_same_person"] is True
    ]
    different = [
        float(row["prototype_distance"])
        for row in available
        if row["expected_same_person"] is False
    ]
    ranked = sorted(available, key=lambda row: float(row["prototype_distance"]))
    return {
        "gold_items": len(rows),
        "labeled_items": len(labeled),
        "available_labeled_pairs": len(available),
        "coverage": _round(len(available) / len(labeled)) if labeled else 0.0,
        "same_pairs_available": len(same),
        "different_pairs_available": len(different),
        "same_distance_median": _median(same),
        "different_distance_median": _median(different),
        "pairwise_auc": _pairwise_auc(same, different),
        "ranking_precision": {
            "at_5": _precision_at(ranked, 5),
            "at_10": _precision_at(ranked, 10),
            "at_20": _precision_at(ranked, 20),
        },
        "zero_false_positive_operating_point": _best_zero_false_positive_threshold(
            same,
            different,
        ),
    }


def _precision_at(rows: list[dict[str, Any]], limit: int) -> float | None:
    selected = rows[:limit]
    if not selected:
        return None
    positives = sum(row["expected_same_person"] is True for row in selected)
    return _round(positives / len(selected), 6)


def _best_zero_false_positive_threshold(same: list[float], different: list[float]) -> dict[str, Any] | None:
    if not same or not different:
        return None
    thresholds = sorted(set(same + different))
    best: dict[str, Any] | None = None
    for threshold in thresholds:
        false_positives = sum(value <= threshold for value in different)
        if false_positives:
            continue
        true_positives = sum(value <= threshold for value in same)
        candidate = {
            "threshold": _round(threshold, 6),
            "true_positives": true_positives,
            "false_positives": 0,
            "recall_on_available_same_pairs": _round(true_positives / len(same)),
        }
        if best is None or true_positives > int(best["true_positives"]):
            best = candidate
    return best


def _pairwise_auc(same: list[float], different: list[float]) -> float | None:
    if not same or not different:
        return None
    wins = 0.0
    total = len(same) * len(different)
    for same_value in same:
        for different_value in different:
            if same_value < different_value:
                wins += 1.0
            elif same_value == different_value:
                wins += 0.5
    return _round(wins / total, 6)


def _median(values: list[float]) -> float | None:
    return _round(statistics.median(values), 6) if values else None


def _round(value: float, digits: int = 4) -> float:
    return round(float(value), digits) if math.isfinite(float(value)) else 0.0


def _select_cases(cases: list[dict[str, Any]], requested: list[str]) -> list[dict[str, Any]]:
    if not requested:
        return cases
    wanted = set(requested)
    return [
        row
        for row in cases
        if str(row.get("label")) in wanted or str(row.get("benchmark_id")) in wanted
    ]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
