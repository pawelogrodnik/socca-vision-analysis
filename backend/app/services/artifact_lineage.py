from __future__ import annotations

import hashlib
import json
from typing import Any


TECHNICAL_TIMESTAMP_FIELDS = {
    "created_at",
    "generated_at",
    "reviewed_at",
    "updated_at",
}


def canonical_json_bytes(value: Any) -> bytes:
    normalized = _without_technical_timestamps(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def generated_from_entry(filename: str, document: dict[str, Any]) -> dict[str, str]:
    return {"artifact": filename, "sha256": canonical_json_sha256(document)}


def _without_technical_timestamps(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _without_technical_timestamps(item)
            for key, item in value.items()
            if str(key) not in TECHNICAL_TIMESTAMP_FIELDS
        }
    if isinstance(value, list):
        return [_without_technical_timestamps(item) for item in value]
    if isinstance(value, tuple):
        return [_without_technical_timestamps(item) for item in value]
    return value
