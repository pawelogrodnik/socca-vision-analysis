from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.identity_review_queue_diagnostics import diagnose_review_queue


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize the existing Reviewed Identity exception queue.",
    )
    parser.add_argument("match_path", type=Path)
    args = parser.parse_args()
    print(json.dumps(diagnose_review_queue(args.match_path), indent=2))


if __name__ == "__main__":
    main()
