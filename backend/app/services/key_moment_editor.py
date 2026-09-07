from __future__ import annotations

"""Durable, dev-only editorial overlays for published Key Moments.

Generated moments remain in the aggregate report.  This module owns only the
operator sidecar and the cheap public-report projection built from it.
"""

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
from app.services.match_groups import MATCH_GROUPS_DIR, get_match_group
from app.services.merged_public_match import group_id_for_merged_published_id


EDITORIAL_SCHEMA_VERSION = "1.0.0"
EDITORIAL_DIRECTORY = config.STORAGE_DIR / "editorial" / "key-moments"
MANUAL_CATEGORIES = {
    "goal_for_us", "goal_for_opponent", "chance", "good_action", "mistake",
    "goalkeeper_intervention", "defensive_action", "tactical_note", "other",
}
PRESENTATION_FIELDS = {"headline", "public_category", "note", "team_id", "player_id"}


class KeyMomentEditorError(ValueError):
    def __init__(self, code: str, detail: str, status_code: int = 422) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


def key_moment_editor_capability() -> dict[str, Any]:
    allowed = config.APP_MODE == "local-analysis"
    return {
        "key_moment_editor_allowed": allowed,
        "reason": None if allowed else "production_viewer",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sidecar_path(published_id: str) -> Path:
    if not published_id or published_id != Path(published_id).name:
        raise KeyMomentEditorError("key_moment_publication_invalid", "Nieprawidłowy identyfikator publikacji.")
    return EDITORIAL_DIRECTORY / f"{published_id}.json"


def _editorial_content(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": EDITORIAL_SCHEMA_VERSION,
        "published_id": str(document.get("published_id") or ""),
        "manual_moments": copy.deepcopy(document.get("manual_moments") or []),
        "generated_suppressions": copy.deepcopy(document.get("generated_suppressions") or []),
        "generated_overrides": copy.deepcopy(document.get("generated_overrides") or []),
    }


def _revision(document: Mapping[str, Any]) -> str:
    return canonical_json_sha256(_editorial_content(document))


def _empty_document(published_id: str) -> dict[str, Any]:
    document = {
        "schema_version": EDITORIAL_SCHEMA_VERSION,
        "published_id": published_id,
        "created_at": _now(),
        "updated_at": _now(),
        "manual_moments": [],
        "generated_suppressions": [],
        "generated_overrides": [],
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
    if not isinstance(value, dict) or value.get("published_id") != published_id:
        raise KeyMomentEditorError("key_moment_editorial_store_invalid", "Dane redakcyjne nie pasują do publikacji.", 409)
    value["revision"] = _revision(value)
    return value


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise KeyMomentEditorError("key_moment_projection_failed", f"{path.name} nie jest obiektem JSON.", 409)
    return value


def _source_kind(published_id: str) -> str:
    return str(get_published_match(published_id).get("source_kind") or PHYSICAL_SOURCE_KIND)


def _generated_snapshot(published_id: str, report: Mapping[str, Any]) -> list[dict[str, Any]]:
    if _source_kind(published_id) != MERGED_SOURCE_KIND:
        return []
    group_id = group_id_for_merged_published_id(published_id)
    if not group_id:
        raise KeyMomentEditorError("key_moment_source_anchor_unresolved", "Brakuje grupy dla scalonej publikacji.", 409)
    path = MATCH_GROUPS_DIR / group_id / "aggregate_report.json"
    aggregate = _load_json(path)
    moments = aggregate.get("key_moments", {}).get("moments", []) if isinstance(aggregate.get("key_moments"), dict) else []
    sources = report.get("merged_provenance", {}).get("sources", []) if isinstance(report.get("merged_provenance"), dict) else []
    return [
        {**copy.deepcopy(moment), "generated_editorial_key": generated_editorial_key(moment, sources)}
        for moment in moments if isinstance(moment, dict)
    ]


def generated_editorial_key(moment: Mapping[str, Any], sources: Any) -> str:
    """Stable generated target identity in physical-source coordinates."""
    start = _number(moment.get("window_start_sec"))
    end = _number(moment.get("window_end_sec"))
    anchor = _number(moment.get("time_sec"))
    slices: list[dict[str, Any]] = []
    for raw in sources if isinstance(sources, list) else []:
        source = raw if isinstance(raw, dict) else {}
        left, right = _number(source.get("logical_start_sec")), _number(source.get("logical_end_sec"))
        if left is None or right is None or start is None or end is None or right <= left:
            continue
        overlap_start, overlap_end = max(start, left), min(end, right)
        if overlap_start > overlap_end:
            continue
        slices.append({
            "source_published_id": str(source.get("published_id") or ""),
            "source_match_id": str(source.get("source_match_id") or ""),
            "start_sec": round(overlap_start - left, 3),
            "end_sec": round(overlap_end - left, 3),
            "anchor_sec": round(min(max(anchor or overlap_start, left), right) - left, 3),
        })
    identity = {
        "policy_version": str(moment.get("policy_version") or "logical-key-moments:v1"),
        "type": str(moment.get("type") or ""),
        "team_id": str(moment.get("team_id") or ""),
        "slices": slices,
        "signals": copy.deepcopy(_record(moment.get("evidence")).get("signals") or _record(moment.get("evidence")).get("primary") or {}),
    }
    return f"gkm-{canonical_json_sha256(identity)[:24]}"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _report_team_players(report: Mapping[str, Any]) -> tuple[set[str], dict[str, str]]:
    teams = {str(row.get("team_id") or "") for row in report.get("teams", []) if isinstance(row, dict)} - {""}
    players = {
        str(row.get("player_id") or ""): str(row.get("team_id") or "")
        for row in report.get("players", []) if isinstance(row, dict) and row.get("player_id")
    }
    return teams, players


def _validate_presentation(row: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: row.get(key) for key in PRESENTATION_FIELDS if key in row}
    team_id, player_id = result.get("team_id"), result.get("player_id")
    teams, players = _report_team_players(report)
    if team_id not in {None, ""} and str(team_id) not in teams:
        raise KeyMomentEditorError("key_moment_team_invalid", "Wybrana drużyna nie występuje w raporcie.")
    if player_id not in {None, ""} and str(player_id) not in players:
        raise KeyMomentEditorError("key_moment_player_invalid", "Wybrany zawodnik nie występuje w raporcie.")
    if team_id not in {None, ""} and player_id not in {None, ""} and players[str(player_id)] != str(team_id):
        raise KeyMomentEditorError("key_moment_player_invalid", "Zawodnik nie należy do wybranej drużyny.")
    for field in ("headline", "note", "public_category"):
        if field in result and result[field] is not None and not isinstance(result[field], str):
            raise KeyMomentEditorError("key_moment_editor_invalid", f"Pole {field} musi być tekstem.")
    return result


def _physical_anchor(report: Mapping[str, Any], requested_time: Any) -> dict[str, Any]:
    time_sec = _number(requested_time)
    duration = _number(_record(report.get("match")).get("duration_sec"))
    if time_sec is None or time_sec < 0 or duration is None or time_sec > duration:
        raise KeyMomentEditorError("key_moment_timestamp_invalid", "Czas momentu jest poza zakresem meczu.")
    published_id = str(report.get("id") or "")
    package = _record(get_published_match(published_id).get("package"))
    video = _record(_record(package.get("match")).get("video"))
    fps = _number(video.get("fps")) or _number(video.get("canonical_fps")) or 25.0
    frame_count = int(_number(video.get("decoded_frame_count")) or max(1, math.ceil(duration * fps)))
    frame = min(frame_count - 1, max(0, int(math.floor(time_sec * fps))))
    return {"source_published_id": published_id, "source_match_id": str(report.get("source_match_id") or ""), "source_frame": frame, "source_time_sec": round(frame / fps, 6)}


def _merged_anchor(report: Mapping[str, Any], requested_time: Any) -> dict[str, Any]:
    time_sec = _number(requested_time)
    duration = _number(_record(report.get("match")).get("duration_sec"))
    sources = _record(report.get("merged_provenance")).get("sources")
    if time_sec is None or time_sec < 0 or duration is None or time_sec > duration or not isinstance(sources, list):
        raise KeyMomentEditorError("key_moment_timestamp_invalid", "Czas momentu jest poza zakresem meczu.")
    chosen: dict[str, Any] | None = None
    for index, raw in enumerate(sources):
        source = _record(raw)
        start, end = _number(source.get("logical_start_sec")), _number(source.get("logical_end_sec"))
        if start is None or end is None:
            continue
        is_final = index == len(sources) - 1
        if start <= time_sec < end or (is_final and time_sec == end):
            chosen = source
            break
    if chosen is None:
        raise KeyMomentEditorError("key_moment_source_anchor_unresolved", "Nie można przypisać czasu do fragmentu źródłowego.")
    source_id = str(chosen.get("published_id") or "")
    source_report = _record(get_published_match(source_id).get("public_report"))
    anchor = _physical_anchor(source_report, max(0.0, time_sec - float(chosen["logical_start_sec"])))
    return anchor


def _manual_public(row: Mapping[str, Any], report: Mapping[str, Any], *, source_kind: str | None = None) -> dict[str, Any] | None:
    anchor = _record(row.get("source_anchor"))
    source_id = str(anchor.get("source_published_id") or "")
    source_time = _number(anchor.get("source_time_sec"))
    displayed = source_time
    if (source_kind or _source_kind(str(report.get("id") or ""))) == MERGED_SOURCE_KIND:
        sources = _record(report.get("merged_provenance")).get("sources")
        source = next((item for item in sources if isinstance(item, dict) and item.get("published_id") == source_id), None) if isinstance(sources, list) else None
        if not isinstance(source, dict) or source_time is None:
            return None
        displayed = float(source.get("logical_start_sec") or 0) + source_time
    if displayed is None:
        return None
    duration = _number(_record(report.get("match")).get("duration_sec")) or displayed
    before = max(0.0, _number(row.get("context_before_sec")) or 5.0)
    after = max(0.0, _number(row.get("context_after_sec")) or 5.0)
    return {
        "moment_id": str(row["moment_id"]), "origin": "manual", "operator_edited": True,
        "time_sec": round(displayed, 6), "window_start_sec": max(0.0, round(displayed - before, 6)),
        "window_end_sec": min(duration, round(displayed + after, 6)), "type": str(row.get("category") or "other"),
        "public_category": str(row.get("category") or "other"), "headline": str(row.get("headline") or "Notatka"),
        "note": row.get("note"), "team_id": row.get("team_id"), "player_id": row.get("player_id"),
    }


def resolve_effective_key_moments(report: Mapping[str, Any], generated: list[dict[str, Any]], editorial: Mapping[str, Any], *, source_kind: str | None = None) -> dict[str, Any]:
    suppressed = {str(row.get("generated_editorial_key") or "") for row in editorial.get("generated_suppressions", []) if isinstance(row, dict)}
    overrides = {str(row.get("generated_editorial_key") or ""): _record(row.get("presentation")) for row in editorial.get("generated_overrides", []) if isinstance(row, dict)}
    active_keys = {str(row.get("generated_editorial_key") or "") for row in generated}
    moments: list[dict[str, Any]] = []
    for base in generated:
        key = str(base["generated_editorial_key"])
        if key in suppressed:
            continue
        public = {key: copy.deepcopy(value) for key, value in base.items() if key != "generated_editorial_key"}
        presentation = overrides.get(key, {})
        public.update({field: value for field, value in presentation.items() if field in PRESENTATION_FIELDS})
        public["origin"] = "generated"
        public["operator_edited"] = bool(presentation)
        moments.append(public)
    unresolved_manual: list[dict[str, Any]] = []
    for manual in editorial.get("manual_moments", []):
        if not isinstance(manual, dict):
            continue
        public = _manual_public(manual, report, source_kind=source_kind)
        if public is None:
            unresolved_manual.append(copy.deepcopy(manual))
        else:
            moments.append(public)
    moments.sort(key=lambda row: (float(row.get("time_sec") or 0), str(row.get("origin") or ""), str(row.get("type") or ""), str(row.get("moment_id") or "")))
    return {
        "schema_version": "1.1.0", "policy_version": "editorial-effective:v1", "timeline_semantics": "logical_match_video" if (source_kind or _source_kind(str(report.get("id") or ""))) == MERGED_SOURCE_KIND else "physical_match_video",
        "status": "ready" if moments else "not_available", "reason": None if moments else "no_visible_key_moments", "moments": moments,
        "_unresolved_manual": unresolved_manual,
        "_orphaned_suppressions": [row for row in editorial.get("generated_suppressions", []) if isinstance(row, dict) and str(row.get("generated_editorial_key") or "") not in active_keys],
        "_orphaned_overrides": [row for row in editorial.get("generated_overrides", []) if isinstance(row, dict) and str(row.get("generated_editorial_key") or "") not in active_keys],
    }


def editor_state(published_id: str) -> dict[str, Any]:
    capability = key_moment_editor_capability()
    if not capability["key_moment_editor_allowed"]:
        return capability
    match = get_published_match(published_id)
    report = _record(match.get("public_report"))
    editorial = load_editorial_document(published_id)
    generated = _generated_snapshot(published_id, report)
    effective = resolve_effective_key_moments(report, generated, editorial)
    return {
        **capability, "published_id": published_id, "revision": editorial["revision"],
        "effective_key_moments": _public_effective(effective), "generated_moments": _editor_generated(generated, editorial),
        "manual_moments": copy.deepcopy(editorial["manual_moments"]), "orphaned_suppressions": effective["_orphaned_suppressions"],
        "orphaned_overrides": effective["_orphaned_overrides"], "unresolved_manual_moments": effective["_unresolved_manual"],
    }


def _public_effective(effective: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in effective.items() if not key.startswith("_")}


def _editor_generated(generated: list[dict[str, Any]], editorial: Mapping[str, Any]) -> list[dict[str, Any]]:
    suppressed = {str(row.get("generated_editorial_key") or "") for row in editorial.get("generated_suppressions", []) if isinstance(row, dict)}
    overrides = {str(row.get("generated_editorial_key") or ""): _record(row.get("presentation")) for row in editorial.get("generated_overrides", []) if isinstance(row, dict)}
    return [{**copy.deepcopy(row), "suppressed": row["generated_editorial_key"] in suppressed, "override": overrides.get(row["generated_editorial_key"]) or None} for row in generated]


def save_editorial_document(published_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    capability = key_moment_editor_capability()
    if not capability["key_moment_editor_allowed"]:
        raise KeyMomentEditorError("key_moment_editor_disabled", "Edycja momentów jest dostępna tylko lokalnie.", 403)
    match = get_published_match(published_id)
    report = _record(match.get("public_report"))
    current = load_editorial_document(published_id)
    if str(payload.get("expected_revision") or "") != current["revision"]:
        raise KeyMomentEditorError("key_moment_editor_revision_conflict", "Stan momentów zmienił się na serwerze. Odśwież edytor.", 409)
    generated = _generated_snapshot(published_id, report)
    generated_keys = {str(row["generated_editorial_key"]) for row in generated}
    candidate = _candidate_document(current, payload, report, generated_keys)
    effective = resolve_effective_key_moments(report, generated, candidate)
    candidate_report = copy.deepcopy(report)
    candidate_report["key_moments"] = _public_effective(effective)
    _promote_projection(published_id, candidate, candidate_report, _source_kind(published_id))
    return {**editor_state(published_id), "public_report": candidate_report}


def _candidate_document(current: Mapping[str, Any], payload: Mapping[str, Any], report: Mapping[str, Any], generated_keys: set[str]) -> dict[str, Any]:
    raw_manual = payload.get("manual_moments")
    if not isinstance(raw_manual, list) or len(raw_manual) > 100:
        raise KeyMomentEditorError("key_moment_editor_invalid", "Nieprawidłowa lista ręcznych momentów.")
    existing = {str(row.get("moment_id")): row for row in current.get("manual_moments", []) if isinstance(row, dict)}
    manual: list[dict[str, Any]] = []
    for raw in raw_manual:
        if not isinstance(raw, dict):
            raise KeyMomentEditorError("key_moment_editor_invalid", "Moment musi być obiektem.")
        category = str(raw.get("category") or "other")
        if category not in MANUAL_CATEGORIES:
            raise KeyMomentEditorError("key_moment_editor_invalid", "Nieznana kategoria momentu.")
        moment_id = str(raw.get("moment_id") or "")
        old = existing.get(moment_id)
        if moment_id and old is None:
            raise KeyMomentEditorError("key_moment_editor_invalid", "Nieznany ręczny moment.")
        requested = raw.get("time_sec")
        anchor = _merged_anchor(report, requested) if _source_kind(str(report.get("id") or "")) == MERGED_SOURCE_KIND else _physical_anchor(report, requested)
        manual.append({
            "moment_id": moment_id or f"manual-km-{uuid.uuid4()}", "category": category,
            "headline": str(raw.get("headline") or "Notatka"), "note": raw.get("note"),
            **_validate_presentation(raw, report), "context_before_sec": max(0.0, _number(raw.get("context_before_sec")) or 5.0),
            "context_after_sec": max(0.0, _number(raw.get("context_after_sec")) or 5.0), "source_anchor": anchor,
            "created_at": str(old.get("created_at") if old else _now()), "updated_at": _now(),
        })
    # The client deliberately edits only currently generated rows.  Retain
    # rows whose generator key has disappeared: a normal regeneration must
    # not silently lose an operator's decision if that moment later returns.
    orphaned_suppressions = [
        copy.deepcopy(row) for row in current.get("generated_suppressions", [])
        if isinstance(row, dict) and str(row.get("generated_editorial_key") or "") not in generated_keys
    ]
    orphaned_overrides = [
        copy.deepcopy(row) for row in current.get("generated_overrides", [])
        if isinstance(row, dict) and str(row.get("generated_editorial_key") or "") not in generated_keys
    ]
    suppressions = orphaned_suppressions + _validated_target_rows(payload.get("generated_suppressions"), generated_keys, "generated_suppressions")
    overrides = orphaned_overrides + _validated_target_rows(payload.get("generated_overrides"), generated_keys, "generated_overrides", report)
    candidate = {**_editorial_content(current), "manual_moments": manual, "generated_suppressions": suppressions, "generated_overrides": overrides, "created_at": current.get("created_at") or _now(), "updated_at": _now()}
    candidate["revision"] = _revision(candidate)
    return candidate


def _validated_target_rows(value: Any, keys: set[str], field: str, report: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise KeyMomentEditorError("key_moment_editor_invalid", f"Pole {field} musi być listą.")
    result = []
    for row in value:
        key = str(_record(row).get("generated_editorial_key") or "")
        if key not in keys:
            raise KeyMomentEditorError("key_moment_generated_target_unknown", "Wskazany automatyczny moment nie istnieje.")
        item = {"generated_editorial_key": key}
        if field == "generated_overrides":
            item["presentation"] = _validate_presentation(_record(row).get("presentation") if isinstance(_record(row).get("presentation"), dict) else {}, report or {})
        result.append(item)
    return result


def _promote_projection(published_id: str, editorial: Mapping[str, Any], report: Mapping[str, Any], source_kind: str) -> None:
    target = PUBLISHED_MATCHES_DIR / published_id
    mirror = Path(__file__).resolve().parents[3] / "client" / "public" / "published" / "matches" / published_id
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
        # Sidecar is staged independently but promoted before publication
        # directories, with rollback if a later swap fails.
        sidecar = _sidecar_path(published_id)
        staged_sidecar = root / "editorial.json"
        _write_json(staged_sidecar, dict(editorial))
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
