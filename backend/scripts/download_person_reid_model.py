from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from urllib.request import urlopen


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_same_match_reid import DEFAULT_MODEL_NAME, default_model_paths


BASE_URL = (
    "https://storage.openvinotoolkit.org/repositories/open_model_zoo/2021.4/"
    "models_bin/1/person-reidentification-retail-0288/FP16"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download the optional Open Model Zoo same-match person ReID model.",
    )
    parser.add_argument("--models-dir", type=Path, default=BACKEND_DIR / "models")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    xml_path, bin_path = default_model_paths(args.models_dir.resolve())
    for destination in (xml_path, bin_path):
        if destination.exists() and not args.force:
            print(f"exists {destination} sha256={_sha256(destination)}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = f"{BASE_URL}/{destination.name}"
        partial = destination.with_suffix(destination.suffix + ".part")
        print(f"downloading {url}")
        with urlopen(url, timeout=120) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        partial.replace(destination)
        print(f"saved {destination} bytes={destination.stat().st_size} sha256={_sha256(destination)}")

    print(f"ready model={DEFAULT_MODEL_NAME} xml={xml_path} bin={bin_path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
