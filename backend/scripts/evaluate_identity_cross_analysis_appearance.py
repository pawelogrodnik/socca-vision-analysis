from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_approved_appearance_reid import load_approved_appearance_embedder
from app.services.identity_cross_analysis_appearance_validation import build_cross_analysis_appearance_validation
from app.services.identity_same_match_reid import JsonEmbeddingCache


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate advisory player appearance matching across separate analyses.")
    parser.add_argument("--source-match-id", required=True)
    parser.add_argument("--target-match-id", required=True)
    parser.add_argument("--matches-root", type=Path, default=BACKEND_DIR / "storage" / "matches")
    parser.add_argument("--models-dir", type=Path, default=BACKEND_DIR / "models")
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    source_path = args.matches_root.resolve() / args.source_match_id
    target_path = args.matches_root.resolve() / args.target_match_id
    source_review = _load(source_path / "identity_roster_subject_review_shadow.json")
    source_decisions = _load(source_path / "identity_roster_subject_review_decisions_shadow.json")
    target_review = _load(target_path / "identity_roster_subject_review_shadow.json")
    target_decisions = _load(target_path / "identity_roster_subject_review_decisions_shadow.json")
    embedder, model_status = load_approved_appearance_embedder(args.models_dir.resolve())
    cache = JsonEmbeddingCache.load(
        output_root / "embedding_cache.json",
        model_name=embedder.model_name,
        model_version=embedder.model_version,
        embedding_dimension=embedder.embedding_dimension,
    )
    document = build_cross_analysis_appearance_validation(
        source_review,
        source_decisions,
        target_review,
        target_decisions,
        source_match_path=source_path,
        target_match_path=target_path,
        source_capture_domain=f"analysis:{args.source_match_id}",
        target_capture_domain=f"analysis:{args.target_match_id}",
        embedder=embedder,
        model_status=model_status,
        embedding_cache=cache,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    output_path = output_root / "identity_cross_analysis_appearance_validation.json"
    output_path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "status": document["status"], **document["summary"]}, indent=2))


def _load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required reviewed artifact is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
