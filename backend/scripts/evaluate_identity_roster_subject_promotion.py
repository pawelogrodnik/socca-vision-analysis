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

from app.services.identity_roster_subject_promotion import (
    build_identity_roster_subject_promotion_plan,
)


PRODUCTION_ARTIFACTS = (
    "global_identity.json",
    "stable_players.json",
    "player_identity_assignments.json",
    "resolved_player_stats.json",
    "player_heatmaps.json",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate P1.20 controlled subject promotion plan.")
    parser.add_argument("--review-artifact", type=Path, required=True)
    parser.add_argument("--review-decisions", type=Path, required=True)
    parser.add_argument("--candidate-subjects", type=Path, required=True)
    parser.add_argument("--candidate-timeline", type=Path, required=True)
    parser.add_argument("--match-dir", type=Path, required=True)
    parser.add_argument("--team-label", choices=("A", "B"), required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--anchor-crops", type=Path)
    parser.add_argument("--team-config", type=Path)
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    match_dir = args.match_dir.resolve()
    generated_at = datetime.now(timezone.utc).isoformat()
    before = production_hashes(match_dir)
    inputs = {
        "review_artifact": _load(args.review_artifact),
        "review_decisions": _load(args.review_decisions),
        "candidate_subjects": _load(args.candidate_subjects),
        "candidate_timeline": _load(args.candidate_timeline),
        "match": _load(match_dir / "match.json"),
        "anchor_crops": _load(args.anchor_crops) if args.anchor_crops else {},
        "team_config": _load(args.team_config) if args.team_config else (
            _load(match_dir / "team_config.json") if (match_dir / "team_config.json").exists() else {}
        ),
    }
    plan = build_identity_roster_subject_promotion_plan(
        inputs["review_artifact"],
        inputs["review_decisions"],
        inputs["candidate_subjects"],
        inputs["candidate_timeline"],
        inputs["match"],
        team_label=args.team_label,
        generated_at=generated_at,
        anchor_crops_doc=inputs["anchor_crops"],
        team_config_doc=inputs["team_config"],
        review_contract_doc=inputs["review_artifact"],
    )
    repeated = build_identity_roster_subject_promotion_plan(
        inputs["review_artifact"],
        inputs["review_decisions"],
        inputs["candidate_subjects"],
        inputs["candidate_timeline"],
        inputs["match"],
        team_label=args.team_label,
        generated_at=generated_at,
        anchor_crops_doc=inputs["anchor_crops"],
        team_config_doc=inputs["team_config"],
        review_contract_doc=inputs["review_artifact"],
    )
    _write(output_root / "identity_roster_subject_promotion_plan.json", plan)
    _write(
        output_root / "identity_roster_subject_promotion_safety_report.json",
        {
            "schema_version": plan["schema_version"],
            "generated_at": generated_at,
            "status": plan["status"],
            "source": plan["source"],
            "summary": plan["summary"],
            "coverage": plan["coverage"],
            "active_player_validation": plan["active_player_validation"],
            "goalkeeper_validation": plan["goalkeeper_validation"],
            "structural_subjects": plan["structural_subjects"],
            "errors": plan["errors"],
            "warnings": plan["warnings"],
        },
    )
    _write(
        output_root / "identity_roster_subject_duplicate_audit.json",
        {
            "schema_version": plan["schema_version"],
            "generated_at": generated_at,
            "source": plan["source"],
            "summary": {
                "duplicates": len(plan["duplicate_observations"]),
                "safe": sum(bool(row.get("safe_to_deduplicate")) for row in plan["duplicate_observations"]),
                "unsafe": sum(not bool(row.get("safe_to_deduplicate")) for row in plan["duplicate_observations"]),
            },
            "duplicates": plan["duplicate_observations"],
        },
    )
    _write(
        output_root / "identity_roster_subject_readiness.json",
        {
            "schema_version": plan["schema_version"],
            "generated_at": generated_at,
            "source": plan["source"],
            "coverage": plan["coverage"],
            "downstream_readiness": plan["downstream_readiness"],
            "players": plan["player_readiness"],
        },
    )
    after = production_hashes(match_dir)
    evaluation = evaluate_promotion_plan(
        plan,
        deterministic=plan == repeated,
        before_hashes=before,
        after_hashes=after,
    )
    evaluation["generated_at"] = generated_at
    evaluation["inputs"] = {
        "review_artifact": str(args.review_artifact.resolve()),
        "review_decisions": str(args.review_decisions.resolve()),
        "candidate_subjects": str(args.candidate_subjects.resolve()),
        "candidate_timeline": str(args.candidate_timeline.resolve()),
        "match_dir": str(match_dir),
        "team_label": args.team_label,
    }
    _write(output_root / "p120_promotion_plan_evaluation.json", evaluation)
    (output_root / "P1_20_REPORT.md").write_text(_markdown(evaluation), encoding="utf-8")
    print(json.dumps(evaluation["summary"], indent=2))
    if evaluation["status"] != "passed":
        raise SystemExit(1)


def evaluate_promotion_plan(
    plan: dict[str, Any],
    *,
    deterministic: bool,
    before_hashes: dict[str, str | None],
    after_hashes: dict[str, str | None],
) -> dict[str, Any]:
    unchanged = before_hashes == after_hashes
    gates = {
        "promotion_state_explicit": plan.get("status") in {"ready_for_controlled_apply", "blocked"},
        "operator_audit_complete": int((plan.get("audit") or {}).get("pending_cards") or 0) == 0,
        "operator_decisions_fresh": bool((plan.get("audit") or {}).get("decisions_fresh")),
        "duplicates_classified": all(
            row.get("classification") and isinstance(row.get("safe_to_deduplicate"), bool)
            for row in plan.get("duplicate_observations") or []
        ),
        "unsafe_duplicates_not_silently_accepted": all(
            row.get("safe_to_deduplicate")
            or any(
                error.get("frame") == row.get("frame")
                and error.get("player_id") == row.get("player_id")
                for error in plan.get("errors") or []
            )
            for row in plan.get("duplicate_observations") or []
        ),
        "coverage_denominator_explicit": bool(
            (plan.get("coverage") or {}).get("coverage_denominator")
        ) and all(
            row.get("coverage_denominator")
            for row in plan.get("player_readiness") or []
        ),
        "structural_conflicts_explicit": int((plan.get("summary") or {}).get("structural_conflicts") or 0)
        == len(plan.get("structural_subjects") or []),
        "exact_frame_coverage_present": all(
            row.get("frame_records") for row in plan.get("canonical_coverage") or []
        ),
        "production_artifacts_unchanged": unchanged,
        "deterministic_output": deterministic,
        "still_requires_explicit_apply": bool(
            (plan.get("safety") or {}).get("requires_explicit_apply_step")
        ),
    }
    return {
        "schema_version": "0.1.0",
        "mode": "p120_controlled_promotion_plan_evaluation",
        "status": "passed" if all(gates.values()) else "failed",
        "summary": {
            **(plan.get("summary") or {}),
            "team_cards": (plan.get("audit") or {}).get("team_cards"),
            "reviewed_cards": (plan.get("audit") or {}).get("reviewed_cards"),
            "pending_cards": (plan.get("audit") or {}).get("pending_cards"),
            "recommendation_metrics": (plan.get("audit") or {}).get("recommendation_metrics"),
            "production_artifacts_unchanged": unchanged,
        },
        "gates": gates,
        "artifact_hashes": {
            name: {"before": before_hashes.get(name), "after": after_hashes.get(name)}
            for name in sorted(set(before_hashes) | set(after_hashes))
        },
    }


def production_hashes(match_dir: Path) -> dict[str, str | None]:
    return {
        name: _sha256(match_dir / name) if (match_dir / name).exists() else None
        for name in PRODUCTION_ARTIFACTS
    }


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _markdown(evaluation: dict[str, Any]) -> str:
    summary = evaluation["summary"]
    recommendation = summary.get("recommendation_metrics") or {}
    lines = [
        "# P1.20 Controlled Subject Promotion Plan",
        "",
        f"Status: **{evaluation['status']}**",
        "",
        "## Operator audit",
        "",
        f"- reviewed Team A cards: {summary.get('reviewed_cards', 0)} / {summary.get('team_cards', 0)}",
        f"- unresolved subjects: {summary.get('unresolved_subjects', 0)}",
        f"- recommendation precision: {recommendation.get('precision')}",
        "",
        "## Exact coverage",
        "",
        f"- resolved subjects: {summary.get('resolved_subjects', 0)}",
        f"- source observations: {summary.get('source_observations', 0)}",
        f"- canonical observations: {summary.get('canonical_observations', 0)}",
        f"- duplicate observations removed: {summary.get('duplicate_observations_removed', 0)}",
        f"- hard conflicts: {summary.get('hard_conflicts', 0)}",
        "",
        "## Safety gates",
        "",
    ]
    lines.extend(f"- [{'x' if passed else ' '}] {name}" for name, passed in evaluation["gates"].items())
    lines.extend(
        [
            "",
            "This is a dry-run promotion plan. It does not write production identity,",
            "player assignments, statistics, or heatmaps.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
