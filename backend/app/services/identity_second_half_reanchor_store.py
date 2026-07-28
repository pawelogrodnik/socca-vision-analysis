from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import (
    load_operator_identity_audit_seeds,
    save_operator_identity_audit_seeds,
)
from app.services.identity_second_half_reanchor import (
    MODE,
    REANCHOR_DIRECTORY,
    load_second_half_reanchor_selection,
)


SEEDS_FILENAME = "identity_second_half_reanchor_seeds.json"


def load_second_half_identity_reanchor_seeds(
    match_path: Path,
    match_document: dict[str, Any],
) -> dict[str, Any]:
    selection = load_second_half_reanchor_selection(match_path)
    return load_operator_identity_audit_seeds(
        match_path,
        match_document,
        selection=selection,
        seed_path=_seed_path(match_path),
        mode=f"{MODE}_operator_seeds",
    )


def save_second_half_identity_reanchor_seeds(
    match_path: Path,
    match_document: dict[str, Any],
    updates: list[dict[str, Any]],
    *,
    telemetry_events: list[dict[str, Any]] | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    selection = load_second_half_reanchor_selection(match_path)
    return save_operator_identity_audit_seeds(
        match_path,
        match_document,
        updates,
        selection=selection,
        seed_path=_seed_path(match_path),
        mode=f"{MODE}_operator_seeds",
        audit_stage=MODE,
        telemetry_events=telemetry_events,
        updated_at=updated_at,
    )


def second_half_reanchor_seed_path(match_path: Path) -> Path:
    return _seed_path(match_path)


def _seed_path(match_path: Path) -> Path:
    return match_path / REANCHOR_DIRECTORY / SEEDS_FILENAME
