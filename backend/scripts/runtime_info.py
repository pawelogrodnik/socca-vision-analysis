from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.runtime import collect_runtime_info


def main() -> None:
    print(json.dumps(collect_runtime_info(), indent=2))


if __name__ == "__main__":
    main()
