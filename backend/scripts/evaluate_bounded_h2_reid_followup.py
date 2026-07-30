#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.identity_bounded_h2_reid_followup import (
    evaluate_bounded_h2_reid_followup,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--session-path", required=True)
    arguments = parser.parse_args()
    result = evaluate_bounded_h2_reid_followup(
        source_root=Path(arguments.source_root),
        session_path=Path(arguments.session_path),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
