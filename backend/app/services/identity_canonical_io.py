from __future__ import annotations

"""Request-scoped reuse of parsed large canonical JSON documents.

One authoritative Reviewed Identity request (cold progress build, finalize
refresh) historically re-parsed multi-hundred-MB artifacts dozens of times
through independent helpers.  Within a single request the files cannot
change, so the top-level entrypoints open a :func:`review_build_context`
scope and every hot read goes through :func:`load_json_cached`, which parses
each path at most once per scope.

The cache lives only for the duration of the scope.  Nothing global or
cross-request is retained, so durability semantics are unchanged.
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

_SCOPE: ContextVar[dict[str, Any] | None] = ContextVar("review_build_cache", default=None)


@contextmanager
def review_build_context():
    """Reuse parsed canonical documents for the duration of one request.

    Reentrant: nested builders join the active scope instead of shadowing
    it, so a whole authoritative pass shares one parse per artifact.
    """
    if _SCOPE.get() is not None:
        yield
        return
    token = _SCOPE.set({})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def load_json_cached(path: Path) -> Any:
    """Parse ``path`` once per active review-build scope."""
    key = str(path)
    scope = _SCOPE.get()
    if scope is None:
        return json.loads(path.read_text(encoding="utf-8"))
    if key not in scope:
        scope[key] = json.loads(path.read_text(encoding="utf-8"))
    return scope[key]


def load_json_cached_or(path: Path, default: Any = None) -> Any:
    """``load_json_cached`` that tolerates a missing file."""
    if not path.exists():
        return default
    try:
        return load_json_cached(path)
    except (OSError, ValueError):
        return default


def invalidate_cached_json(path: Path) -> None:
    """Drop a scope-cached document so a later read sees the written file.

    Authoritative flows write derived artifacts inside an active scope
    (snapshot, progress).  Any later same-scope read of those paths must
    observe the new bytes, never the pre-write object.
    """
    scope = _SCOPE.get()
    if scope is not None:
        scope.pop(str(path), None)


_SCOPE_MISS = object()


def has_active_scope() -> bool:
    """True when a review-build scope is active on this call context."""
    return _SCOPE.get() is not None


def scoped_memo_get(key: str) -> Any:
    """Fetch a request-scoped derived artifact (None when absent/unused)."""
    scope = _SCOPE.get()
    if scope is None:
        return None
    value = scope.get(key, _SCOPE_MISS)
    return None if value is _SCOPE_MISS else value


def scoped_memo_put(key: str, value: Any) -> None:
    """Store a request-scoped derived artifact for exact-request reuse."""
    scope = _SCOPE.get()
    if scope is not None:
        scope[key] = value
