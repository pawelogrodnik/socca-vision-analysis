from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.identity_reid_training_dataset import export_audited_dataset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    options = parser.parse_args()
    result = export_audited_dataset(
        source_root=options.source_root,
        output_root=options.output_root,
    )
    print(json.dumps({"status": "AUDITED_REID_DATASET_READY", **result}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
