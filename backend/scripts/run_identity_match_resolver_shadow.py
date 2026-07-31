from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from app.services.identity_match_resolver import build_identity_resolver_shadow
from app.services.identity_jersey_number_common import canonical_digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--reid-experiment-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    options = parser.parse_args()
    h1 = options.source_root / "h1_workspace"; final = _load(options.reid_experiment_root / "final_h2_finetuned_holdout.json")
    fps, source = _fps(h1 / "video.mp4")
    artifact = build_identity_resolver_shadow(tracklets_doc=_load(h1 / "tracklets.json"), subjects_doc=_load(h1 / "identity_candidate_shadow.json"), seeds_doc=_load(h1 / "identity_operator_seeds.json"), match_doc=_load(h1 / "match.json"), reid_evidence_doc=_load(options.reid_experiment_root / "tracklet_reid_evidence.json"), reid_gate_passed=bool((final.get("canonical_gate") or {}).get("display_eligible")), fps_value=fps, fps_source=source)
    artifact["source_digests"] = {"tracklets": canonical_digest(_load(h1 / "tracklets.json")), "subjects": canonical_digest(_load(h1 / "identity_candidate_shadow.json")), "operator_seeds": canonical_digest(_load(h1 / "identity_operator_seeds.json")), "reid_report": canonical_digest(final)}
    options.output_root.mkdir(parents=True, exist_ok=True)
    _write(options.output_root / "identity_match_resolver_shadow.json", artifact)
    _write(options.output_root / "identity_tracklet_assignments_shadow.json", {"variants": {name: variant["assignments"] for name, variant in artifact["variants"].items()}})
    _write(options.output_root / "identity_edge_scores.json", {"variants": {name: variant["edge_scores"] for name, variant in artifact["variants"].items()}})
    _write(options.output_root / "identity_conflicts.json", {"operator_anchor_conflicts": artifact["operator_anchor_conflicts"], "variants": {name: [row for row in variant["assignments"] if row["conflicts"] or row["hard_blockers"]] for name, variant in artifact["variants"].items()}})
    _write(options.output_root / "tracklet_continuity_edges.json", {"fps": artifact["fps"], "edges": artifact["continuity_edges"]})
    _write(options.output_root / "identity_stability_metrics.json", {"variants": {name: variant["metrics"] for name, variant in artifact["variants"].items()}})
    _write(options.output_root / "identity_match_resolver_report.json", {"status": "MATCH_IDENTITY_RESOLVER_SHADOW_COMPLETE", "variants": {name: variant["metrics"] for name, variant in artifact["variants"].items()}, "reid_contribution": artifact["reid_evidence"], "fps": artifact["fps"], "safety": artifact["safety"]})
    print(json.dumps({"status": "MATCH_IDENTITY_RESOLVER_SHADOW_COMPLETE", "fps": artifact["fps"], "variants": {name: variant["metrics"] for name, variant in artifact["variants"].items()}}))
    return 0


def _fps(video: Path) -> tuple[float | None, str]:
    capture = cv2.VideoCapture(str(video))
    try:
        value = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        return (value if value > 0 else None, "video_metadata" if value > 0 else "unavailable")
    finally:
        capture.release()


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
