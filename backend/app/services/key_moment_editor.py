from __future__ import annotations

"""Small durable editorial layer for the final public Key Moments list."""

import copy
import json
import math
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app import config
from app.services.aggregate_inputs import build_aggregate_inputs
from app.services.artifact_lineage import canonical_json_sha256
from app.services.json_publish_store import (
    MERGED_SOURCE_KIND,
    PHYSICAL_SOURCE_KIND,
    PUBLISHED_MATCHES_DIR,
    _commit_publication_generation,
    get_published_match,
)
from app.services.public_match_report import CLIENT_PUBLIC_MATCHES_DIR


EDITORIAL_SCHEMA_VERSION = "1.0.0"
EDITORIAL_DIRECTORY = config.STORAGE_DIR / "editorial" / "key-moments"
MOMENT_CATEGORIES = {
    "goal_for_us", "goal_for_opponent", "chance", "good_action", "mistake",
    "goalkeeper_intervention", "defensive_action", "tactical_note", "other",
    "momentum_peak", "possession_dominance",
}


class KeyMomentEditorError(ValueError):
    def __init__(self, code: str, detail: str, status_code: int = 422) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


def key_moment_editor_capability() -> dict[str, Any]:
    """The query parameter is the UI opt-in; a reachable backend permits edits."""
    return {"key_moment_editor_allowed": True, "reason": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sidecar_path(published_id: str) -> Path:
    if not published_id or published_id != Path(published_id).name:
        raise KeyMomentEditorError("key_moment_publication_invalid", "Nieprawidłowy identyfikator publikacji.")
    return EDITORIAL_DIRECTORY / f"{published_id}.json"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _editorial_content(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": EDITORIAL_SCHEMA_VERSION,
        "published_id": str(document.get("published_id") or ""),
        "moments": copy.deepcopy(document.get("moments") or []),
    }


def _revision(document: Mapping[str, Any]) -> str:
    return canonical_json_sha256(_editorial_content(document))


def _empty_document(published_id: str) -> dict[str, Any]:
    document = {
        "schema_version": EDITORIAL_SCHEMA_VERSION,
        "published_id": published_id,
        "created_at": _now(),
        "updated_at": _now(),
        "moments": [],
        "_exists": False,
    }
    document["revision"] = _revision(document)
    return document


def load_editorial_document(published_id: str) -> dict[str, Any]:
    path = _sidecar_path(published_id)
    if not path.exists():
        return _empty_document(published_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise KeyMomentEditorError("key_moment_editorial_store_invalid", "Nie można odczytać danych redakcyjnych.", 409) from error
    if not isinstance(value, dict) or value.get("published_id") != published_id or not isinstance(value.get("moments"), list):
        raise KeyMomentEditorError("key_moment_editorial_store_invalid", "Dane redakcyjne nie pasują do publikacji.", 409)
    value["_exists"] = True
    value["revision"] = _revision(value)
    return value


def _source_kind(published_id: str) -> str:
    return str(get_published_match(published_id).get("source_kind") or PHYSICAL_SOURCE_KIND)


def _report_team_players(report: Mapping[str, Any]) -> tuple[set[str], dict[str, str]]:
    teams = {str(row.get("team_id") or "") for row in report.get("teams", []) if isinstance(row, dict)} - {""}
    players = {
        str(row.get("player_id") or ""): str(row.get("team_id") or "")
        for row in report.get("players", []) if isinstance(row, dict) and row.get("player_id")
    }
    return teams, players


def _validate_presentation(row: Mapping[str, Any], report: Mapping[str, Any]) -> tuple[str, str, str | None, str | None, str | None]:
    category = str(row.get("category") or row.get("public_category") or row.get("type") or "other")
    if category not in MOMENT_CATEGORIES:
        raise KeyMomentEditorError("key_moment_editor_invalid", "Nieznana kategoria momentu.")
    headline, note = row.get("headline"), row.get("note")
    if (headline is not None and not isinstance(headline, str)) or (note is not None and not isinstance(note, str)):
        raise KeyMomentEditorError("key_moment_editor_invalid", "Tytuł i notatka muszą być tekstem.")
    team_id, player_id = row.get("team_id") or None, row.get("player_id") or None
    teams, players = _report_team_players(report)
    if team_id is not None and str(team_id) not in teams:
        raise KeyMomentEditorError("key_moment_team_invalid", "Wybrana drużyna nie występuje w raporcie.")
    if player_id is not None and str(player_id) not in players:
        raise KeyMomentEditorError("key_moment_player_invalid", "Wybrany zawodnik nie występuje w raporcie.")
    if team_id is not None and player_id is not None and players[str(player_id)] != str(team_id):
        raise KeyMomentEditorError("key_moment_player_invalid", "Zawodnik nie należy do wybranej drużyny.")
    return category, str(headline or "Notatka"), str(note) if note is not None else None, str(team_id) if team_id is not None else None, str(player_id) if player_id is not None else None


def _generated_moments(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [copy.deepcopy(row) for row in _record(report.get("key_moments")).get("moments", []) if isinstance(row, dict)]


def _sort_moments(moments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(moments, key=lambda row: (float(row.get("time_sec") or 0), str(row.get("moment_id") or "")))


def _curated_key_moments(report: Mapping[str, Any], moments: list[dict[str, Any]], source_kind: str) -> dict[str, Any]:
    return {
        "schema_version": "1.1.0",
        "policy_version": "editorial-curated:v1",
        "timeline_semantics": "logical_match_video" if source_kind == MERGED_SOURCE_KIND else "physical_match_video",
        "status": "ready" if moments else "not_available",
        "reason": None if moments else "no_visible_key_moments",
        "moments": _sort_moments(copy.deepcopy(moments)),
    }


def apply_editorial_key_moments(report: Mapping[str, Any], published_id: str, *, source_kind: str | None = None) -> dict[str, Any]:
    """Return the durable curated list when present, otherwise preserve generation."""
    editorial = load_editorial_document(published_id)
    if not editorial["_exists"]:
        return copy.deepcopy(_record(report.get("key_moments")))
    return _curated_key_moments(report, editorial["moments"], source_kind or _source_kind(published_id))


def _editor_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    category = str(row.get("category") or row.get("public_category") or row.get("type") or "other")
    return {
        "moment_id": str(row.get("moment_id") or ""), "time_sec": float(row.get("time_sec") or 0),
        "category": category, "headline": str(row.get("headline") or "Notatka"),
        "note": row.get("note") if isinstance(row.get("note"), str) else None,
        "team_id": row.get("team_id") or None, "player_id": row.get("player_id") or None,
        "origin": str(row.get("origin") or "generated"),
    }


def editor_state(published_id: str) -> dict[str, Any]:
    match = get_published_match(published_id)
    report = _record(match.get("public_report"))
    editorial = load_editorial_document(published_id)
    moments = editorial["moments"] if editorial["_exists"] else _generated_moments(report)
    return {
        **key_moment_editor_capability(), "published_id": published_id, "revision": editorial["revision"],
        "moments": [_editor_dto(row) for row in _sort_moments(moments)],
        "has_editorial_sidecar": bool(editorial["_exists"]),
    }


def _candidate_moments(payload: Mapping[str, Any], report: Mapping[str, Any], current_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_moments = payload.get("moments")
    if not isinstance(raw_moments, list) or len(raw_moments) > 100:
        raise KeyMomentEditorError("key_moment_editor_invalid", "Nieprawidłowa lista momentów.")
    duration = _number(_record(report.get("match")).get("duration_sec"))
    if duration is None:
        raise KeyMomentEditorError("key_moment_timestamp_invalid", "Brakuje czasu trwania meczu.")
    existing = {str(row.get("moment_id") or ""): row for row in current_rows if str(row.get("moment_id") or "")}
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_moments:
        if not isinstance(raw, dict):
            raise KeyMomentEditorError("key_moment_editor_invalid", "Moment musi być obiektem.")
        supplied_id = str(raw.get("moment_id") or "")
        if supplied_id and supplied_id not in existing:
            raise KeyMomentEditorError("key_moment_editor_invalid", "Nieznany moment.")
        moment_id = supplied_id or f"manual-km-{uuid.uuid4()}"
        if moment_id in seen:
            raise KeyMomentEditorError("key_moment_editor_invalid", "Moment występuje więcej niż raz.")
        seen.add(moment_id)
        time_sec = _number(raw.get("time_sec"))
        if time_sec is None or time_sec < 0 or time_sec > duration:
            raise KeyMomentEditorError("key_moment_timestamp_invalid", "Czas momentu jest poza zakresem meczu.")
        category, headline, note, team_id, player_id = _validate_presentation(raw, report)
        previous = copy.deepcopy(existing.get(moment_id) or {})
        origin = str(previous.get("origin") or ("manual" if not supplied_id else "generated"))
        before = max(0.0, _number(previous.get("context_before_sec")) or 5.0)
        after = max(0.0, _number(previous.get("context_after_sec")) or 5.0)
        result.append({
            **previous, "moment_id": moment_id, "origin": origin, "operator_edited": True,
            "time_sec": round(time_sec, 6), "window_start_sec": max(0.0, round(time_sec - before, 6)),
            "window_end_sec": min(duration, round(time_sec + after, 6)), "type": category,
            "public_category": category, "headline": headline, "note": note, "team_id": team_id,
            "player_id": player_id, "context_before_sec": before, "context_after_sec": after,
        })
    return _sort_moments(result)


def save_editorial_document(published_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    match = get_published_match(published_id)
    report = _record(match.get("public_report"))
    current = load_editorial_document(published_id)
    if str(payload.get("expected_revision") or "") != current["revision"]:
        raise KeyMomentEditorError("key_moment_editor_revision_conflict", "Stan momentów zmienił się na serwerze. Odśwież edytor.", 409)
    current_rows = current["moments"] if current["_exists"] else _generated_moments(report)
    moments = _candidate_moments(payload, report, current_rows)
    candidate = {"schema_version": EDITORIAL_SCHEMA_VERSION, "published_id": published_id, "moments": moments, "created_at": current.get("created_at") or _now(), "updated_at": _now(), "_exists": True}
    candidate["revision"] = _revision(candidate)
    source_kind = _source_kind(published_id)
    candidate_report = copy.deepcopy(report)
    candidate_report["key_moments"] = _curated_key_moments(report, moments, source_kind)
    _promote_projection(published_id, candidate, candidate_report, source_kind)
    return {**editor_state(published_id), "public_report": candidate_report}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise KeyMomentEditorError("key_moment_projection_failed", f"{path.name} nie jest obiektem JSON.", 409)
    return value


def _promote_projection(published_id: str, editorial: Mapping[str, Any], report: Mapping[str, Any], source_kind: str) -> None:
    target = PUBLISHED_MATCHES_DIR / published_id
    mirror = CLIENT_PUBLIC_MATCHES_DIR / published_id
    if not target.is_dir() or not mirror.is_dir():
        raise KeyMomentEditorError("key_moment_projection_failed", "Brakuje kanonicznego lub statycznego raportu.", 409)
    staging_parent = PUBLISHED_MATCHES_DIR.parent / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="key-moment-editor-", dir=staging_parent))
    staged_target, staged_mirror = root / "canonical", root / "mirror"
    try:
        shutil.copytree(target, staged_target)
        shutil.copytree(mirror, staged_mirror)
        if source_kind == PHYSICAL_SOURCE_KIND:
            package = _load_json(staged_target / "package.json")
            _write_json(staged_target / "aggregate_inputs.json", build_aggregate_inputs(package, public_report=dict(report), published_id=published_id))
        elif source_kind == MERGED_SOURCE_KIND:
            provenance = _load_json(staged_target / "provenance.json")
            provenance["report_digest"] = canonical_json_sha256(report)
            _write_json(staged_target / "provenance.json", provenance)
        _write_json(staged_target / "public_report.json", dict(report))
        _write_json(staged_mirror / "public_report.json", dict(report))
        sidecar = _sidecar_path(published_id)
        staged_sidecar = root / "editorial.json"
        _write_json(staged_sidecar, _editorial_content(editorial))
        previous = sidecar.read_bytes() if sidecar.exists() else None
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        staged_sidecar.replace(sidecar)
        try:
            _commit_publication_generation(staged_match_dir=staged_target, target_match_dir=target, staged_public_dir=staged_mirror, target_public_dir=mirror)
        except Exception:
            if previous is None:
                sidecar.unlink(missing_ok=True)
            else:
                sidecar.write_bytes(previous)
            raise
    except KeyMomentEditorError:
        raise
    except Exception as error:
        raise KeyMomentEditorError("key_moment_projection_failed", "Nie udało się atomowo zapisać projekcji momentów.", 409) from error
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
