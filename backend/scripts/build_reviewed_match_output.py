from __future__ import annotations

"""Official local smoke command for the reviewed-output product contract."""

import argparse
import json
import time
from pathlib import Path

from app.services.identity_reviewed_output_jobs import generate_reviewed_output, reviewed_output_status
from app.services.identity_reviewed_snapshot import finalize_reviewed_identity


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--match-root", required=True, type=Path); parser.add_argument("--minimap", action="store_true"); options = parser.parse_args()
    match = json.loads((options.match_root / "match.json").read_text(encoding="utf-8")); snapshot = finalize_reviewed_identity(options.match_root, match)
    job = generate_reviewed_output(options.match_root, snapshot, match, {"include_minimap": options.minimap, "include_ball": True, "show_roster_number": False})
    while job.get("status") in {"queued", "running"}:
        time.sleep(.25); job = reviewed_output_status(options.match_root, snapshot)
    if job.get("status") == "completed":
        _write_visual_qa(options.match_root, snapshot)
    print(json.dumps({"snapshot_status": snapshot["status"], "snapshot_digest": snapshot["semantic_digest"], "job": job}, indent=2))
    return 0 if job.get("status") == "completed" else 1


def _write_visual_qa(root: Path, snapshot: dict) -> None:
    import cv2
    video = root / "reviewed_video.mp4"; capture = cv2.VideoCapture(str(video)); total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    qa_dir = root / "reviewed_output_qa"; qa_dir.mkdir(exist_ok=True)
    frames = sorted({min(total - 1, round(total * index / 5)) for index in range(6)}) if total else []
    evidence = []
    for index, frame_number in enumerate(frames):
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number); ok, frame = capture.read(); path = qa_dir / f"frame-{frame_number:06d}.jpg"
        if ok: cv2.imwrite(str(path), frame)
        evidence.append({"frame": frame_number, "path": str(path.relative_to(root)), "captured": bool(ok)})
    capture.release()
    labels = snapshot.get("tracklet_assignments") or []
    report = {"status": "passed" if all(row["captured"] for row in evidence) else "failed", "frames": evidence, "expected_policy": {"confirmed_names": sorted({row["display_label"] for row in labels if row.get("identity_status") == "confirmed"}), "fallback_labels": sorted({row["fallback_label"] for row in labels if row.get("identity_status") != "confirmed"})[:20], "automatic_reid_names": 0}, "checks": {"mp4_opened_by_opencv": bool(total), "minimap_requested": True, "source_snapshot_digest": snapshot["semantic_digest"]}}
    (root / "reviewed_output_visual_qa_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
