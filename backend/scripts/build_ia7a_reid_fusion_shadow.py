from __future__ import annotations

"""Create the IA7a ReID evidence-fusion shadow contract without mutation."""

import argparse
import json
from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--session-root", required=True, type=Path)
    parser.add_argument("--bakeoff-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    options = parser.parse_args()
    report_path = options.bakeoff_root / "final_h2_finetuned_holdout.json"
    bakeoff = _load(
        report_path if report_path.is_file()
        else options.bakeoff_root / "reid_quality_bakeoff_report.json"
    )
    decisions = _load(options.session_root / "operator_decisions.json")
    gate = bakeoff.get("canonical_gate") or bakeoff.get("gate") or {}
    reid_enabled = bool(gate.get("display_eligible") or gate.get("passed"))
    observations = []
    for decision in decisions.get("decisions") or []:
        if decision.get("action") != "player":
            continue
        observations.append({
            "observation_key": decision.get("observation_key"),
            "candidate_subject_id": decision.get("candidate_subject_id"),
            "team_label": decision.get("team_label"),
            "operator_seed": {
                "player_id": decision.get("player_id"),
                "certainty": "operator_certain",
                "weight": 1.0,
            },
            "reid_evidence": {
                "status": "eligible_shadow_only" if reid_enabled else "disabled_quality_gate_failed",
                "enabled": reid_enabled,
                "weight": 0.0 if not reid_enabled else 0.20,
                "reason": "cross_capture_gate_failed" if not reid_enabled else "gated_shadow_only",
                "proposed_player_id": None,
            },
            "team_constraint": {
                "enforced": True,
                "team_a_roster_only": True,
                "cross_team_candidates": 0,
            },
            "outcome": "operator_seed_retained_no_auto_assignment",
        })
    artifact = {
        "schema_version": "1.0.0",
        "mode": "ia7a_reid_evidence_fusion_shadow",
        "status": "REID_DISABLED_BY_CROSS_CAPTURE_GATE" if not reid_enabled else "SHADOW_ONLY",
        "source": {
            "bakeoff_digest": canonical_digest(bakeoff),
            "operator_decisions_digest": canonical_digest(decisions),
            "source_v4": options.source_root.name,
        },
        "reid_gate": gate,
        "reid_evidence_weight": 0.0 if not reid_enabled else 0.20,
        "observations": observations,
        "summary": {
            "operator_seed_observations": len(observations),
            "reid_scored_observations": 0,
            "automatic_assignments": 0,
            "automatic_merges": 0,
            "production_identity_mutations": 0,
            "team_constraint_violations": 0,
        },
        "safety": {
            "shadow_only": True,
            "reran_yolo": False,
            "reran_tracking": False,
            "automatic_identity_assignments": 0,
            "identity_production_mutations": 0,
            "reid_disabled_when_gate_failed": not reid_enabled,
        },
    }
    report = {
        "status": artifact["status"], "reid_enabled": reid_enabled,
        "reason": "H2 quality gate is below 0.75 / 0.90; no ReID evidence can influence identity." if not reid_enabled else "Shadow-only gated evidence.",
        "summary": artifact["summary"], "safety": artifact["safety"],
    }
    _write(options.output_root / "identity_evidence_fusion_shadow.json", artifact)
    _write(options.output_root / "identity_evidence_fusion_shadow_report.json", report)
    return 0


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
