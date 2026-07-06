from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode

from app.config import PRODUCTION_API_TOKEN, PRODUCTION_API_URL, PUBLISH_TARGET
from app.services.json_publish_store import import_match_package


class PublishError(RuntimeError):
    pass


def publish_match_package(package: dict[str, Any], *, replace: bool = False) -> dict[str, Any]:
    if PUBLISH_TARGET in {"local-json", "local-db"}:
        return import_match_package(package, replace=replace)
    if PUBLISH_TARGET == "remote-api":
        return publish_match_package_to_remote(package, replace=replace)
    raise PublishError(f"Unsupported ORLIK_PUBLISH_TARGET={PUBLISH_TARGET!r}. Use local-json or remote-api.")


def publish_match_package_to_remote(package: dict[str, Any], *, replace: bool = False) -> dict[str, Any]:
    if not PRODUCTION_API_URL:
        raise PublishError("ORLIK_PRODUCTION_API_URL is required when ORLIK_PUBLISH_TARGET=remote-api.")
    if not PRODUCTION_API_TOKEN:
        raise PublishError("ORLIK_PRODUCTION_API_TOKEN is required when ORLIK_PUBLISH_TARGET=remote-api.")

    base_url = PRODUCTION_API_URL.rstrip("/")
    query = urlencode({"replace": str(replace).lower()})
    url = f"{base_url}/api/admin/import-match?{query}"
    body = json.dumps(package).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {PRODUCTION_API_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise PublishError(f"Remote import failed with HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise PublishError(f"Remote import failed: {exc.reason}") from exc
