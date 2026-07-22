from __future__ import annotations

import hashlib
import json
import re
from typing import Any


NUMBER_PATTERN = re.compile(r"^[0-9]{1,3}$")
EVIDENCE_STATES = {
    "number_confirmed",
    "number_absent",
    "number_unreadable",
    "number_conflict",
}


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_key(kind: str, payload: dict[str, Any]) -> str:
    return f"{kind}:v1:{canonical_digest(payload)}"


def normalize_jersey_number(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or not NUMBER_PATTERN.fullmatch(text):
        return None
    return str(int(text))


def team_label(value: Any) -> str:
    text = str(value or "U").strip().upper()
    return text if text in {"A", "B"} else "U"


def round_or_none(value: Any, digits: int = 4) -> float | None:
    return round(float(value), digits) if isinstance(value, (int, float)) else None
