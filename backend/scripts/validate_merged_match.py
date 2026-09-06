#!/usr/bin/env python3
"""Read-only acceptance audit for a canonical merged published match."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.services.merged_match_acceptance import audit_merged_match, format_audit_summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("identifier", help="match-group ID or published-merged ID")
    parser.add_argument("--storage-dir", type=Path, default=Path(__file__).resolve().parents[1] / "storage")
    parser.add_argument("--output", type=Path, help="Optional path for the JSON reconciliation result")
    args = parser.parse_args()
    result = audit_merged_match(args.storage_dir.resolve(), args.identifier)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    sys.stdout.write(format_audit_summary(result) + "\n")
    sys.stdout.write(encoded)
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
