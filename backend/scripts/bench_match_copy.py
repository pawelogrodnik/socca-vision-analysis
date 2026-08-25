from __future__ import annotations

"""Self-provisioning real-match copies for benchmark scripts.

Benchmark copies live under ``backend/storage/matches/`` (git-ignored) and
would otherwise pollute the operator match list forever.  Scripts create the
copy on demand and delete it afterwards.  Set ``KEEP_BENCH_COPY=1`` to keep
a copy for inspection.
"""

import os
import shutil
import sys
from pathlib import Path

STORAGE = Path("backend/storage/matches")


def ensure_bench_copy(copy_name: str, source_match_id: str) -> Path:
    root = STORAGE / copy_name
    if root.exists():
        return root
    source = STORAGE / source_match_id
    if not source.exists():
        sys.exit(f"benchmark source match is missing: {source}")
    print(f"bench_copy: creating {copy_name} from {source_match_id} ...")
    shutil.copytree(source, root)
    return root


def remove_bench_copy(copy_name: str) -> None:
    if os.environ.get("KEEP_BENCH_COPY") == "1":
        print(f"bench_copy: KEEP_BENCH_COPY=1 -> keeping {copy_name}")
        return
    root = STORAGE / copy_name
    if root.exists():
        shutil.rmtree(root)
        print(f"bench_copy: removed {copy_name}")
