from __future__ import annotations

"""HTTP transport shaping for reviewed-identity correction responses.

Canonical decision artifacts keep full exact ownership on disk.  Operator
clients never read per-frame ownership from the save response, so the HTTP
payload replaces those large lists with counts while leaving the canonical
dictionary untouched.
"""

from typing import Any

COMPACTED_LIST_KEYS = ("owned_observations", "detected_pairs")


def correction_response_decision(saved_decision: dict[str, Any]) -> dict[str, Any]:
    """Return a lightweight transport copy of a saved decision.

    The input object is never mutated and remains safe for later persistence.
    Every nested list whose key names per-frame ownership is replaced by an
    exact count field; all other values are preserved as-is.
    """
    return _shape_value(saved_decision)


def _shape_value(value: Any) -> Any:
    if isinstance(value, dict):
        shaped: dict[str, Any] = {}
        for key, item in value.items():
            if key in COMPACTED_LIST_KEYS and isinstance(item, list):
                shaped[f"{key}_count"] = len(item)
                continue
            shaped[key] = _shape_value(item)
        return shaped
    if isinstance(value, list):
        return [_shape_value(item) for item in value]
    return value
