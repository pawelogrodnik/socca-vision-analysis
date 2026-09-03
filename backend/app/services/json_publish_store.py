from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import MATCHES_DIR, PUBLISHED_DIR
from app.services.aggregate_inputs import build_aggregate_inputs
from app.services import public_match_report
from app.services.artifact_lineage import canonical_json_sha256
from app.services.published_video import stage_published_video


PUBLISHED_MATCHES_DIR = PUBLISHED_DIR / "matches"


def write_public_match_report_bundle(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Late-bind the public-report service so its configured mirror remains patchable."""

    return public_match_report.write_public_match_report_bundle(*args, **kwargs)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_publish_store() -> None:
    PUBLISHED_MATCHES_DIR.mkdir(parents=True, exist_ok=True)


def _published_id_from_package(package: dict[str, Any]) -> str:
    match = package.get("match") or {}
    source_match_id = str(match.get("id") or "unknown")
    return f"published-{source_match_id}"


def _published_match_dir(match_id: str) -> Path:
    return PUBLISHED_MATCHES_DIR / match_id


def _load_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _staging_directory(parent: Path, *, name: str) -> Path:
    staging_parent = parent / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{name}-", dir=staging_parent))


def _remove_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _remove_empty_directory(path: Path) -> None:
    try:
        path.rmdir()
    except (FileNotFoundError, OSError):
        pass


def _commit_publication_generation(
    *,
    staged_match_dir: Path,
    target_match_dir: Path,
    staged_public_dir: Path,
    target_public_dir: Path,
) -> None:
    """Replace the private and public projections together after full staging.

    Each directory swap is atomic.  If the second swap cannot complete, the
    already-swapped directory is restored from its preserved previous version.
    All failure-prone report construction happens before this point.
    """

    generation_token = uuid.uuid4().hex
    committed: list[tuple[Path, Path | None]] = []
    try:
        for staged_dir, target_dir in (
            (staged_match_dir, target_match_dir),
            (staged_public_dir, target_public_dir),
        ):
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            backup_dir: Path | None = None
            if target_dir.exists():
                backup_dir = target_dir.parent / f".{target_dir.name}.backup-{generation_token}"
                target_dir.replace(backup_dir)
            try:
                staged_dir.replace(target_dir)
            except Exception:
                if backup_dir is not None and backup_dir.exists():
                    backup_dir.replace(target_dir)
                raise
            committed.append((target_dir, backup_dir))
    except Exception:
        for target_dir, backup_dir in reversed(committed):
            _remove_directory(target_dir)
            if backup_dir is not None and backup_dir.exists():
                backup_dir.replace(target_dir)
        raise
    else:
        for _, backup_dir in committed:
            if backup_dir is not None:
                _remove_directory(backup_dir)


def _match_teams(package: dict[str, Any]) -> list[dict[str, Any]]:
    match = package.get("match") if isinstance(package.get("match"), dict) else {}
    teams = match.get("teams") if isinstance(match.get("teams"), list) else []
    return [team for team in teams if isinstance(team, dict)]


def _match_players(package: dict[str, Any]) -> list[dict[str, Any]]:
    players: list[dict[str, Any]] = []
    for team_index, team in enumerate(_match_teams(package)):
        team_id = str(team.get("id") or f"team-{team_index + 1}")
        team_players = team.get("players") if isinstance(team.get("players"), list) else []
        for player_index, player in enumerate(team_players):
            if not isinstance(player, dict):
                continue
            player_id = str(player.get("id") or f"{team_id}-player-{player_index + 1}")
            players.append(
                {
                    "id": player_id,
                    "match_id": _published_id_from_package(package),
                    "team_id": team_id,
                    "name": str(player.get("name") or player_id),
                    "number": player.get("number"),
                    "role": player.get("role"),
                    "is_guest": bool(player.get("is_guest")),
                }
            )
    return players


def _stable_players(package: dict[str, Any]) -> list[dict[str, Any]]:
    stable_doc = package.get("stable_players") if isinstance(package.get("stable_players"), dict) else {}
    players = stable_doc.get("players") if isinstance(stable_doc.get("players"), list) else []
    normalized = []
    published_id = _published_id_from_package(package)
    for player in players:
        if not isinstance(player, dict):
            continue
        stable_player_id = str(player.get("stable_player_id") or player.get("stable_subject_id") or "")
        if not stable_player_id:
            continue
        normalized.append(
            {
                "id": stable_player_id,
                "match_id": published_id,
                "stable_subject_id": player.get("stable_subject_id"),
                "team_id": player.get("team_id"),
                "team_label": str(player.get("team_label") or "U"),
                "team_name": player.get("team_name"),
                "duration_sec": float(player.get("duration_sec") or 0),
                "confidence": str(player.get("confidence") or "low"),
                "confidence_score": player.get("confidence_score"),
                "tracklet_ids": player.get("tracklet_ids") or [],
            }
        )
    return normalized


def _summary_from_package(
    package: dict[str, Any],
    *,
    published_id: str,
    created_at: str,
    updated_at: str,
) -> dict[str, Any]:
    match = package.get("match")
    if not isinstance(match, dict):
        raise ValueError("Package must contain a match object.")

    source_match_id = str(match.get("id") or "")
    if not source_match_id:
        raise ValueError("Package match.id is required.")

    analysis = package.get("analysis_report") if isinstance(package.get("analysis_report"), dict) else {}
    warnings = analysis.get("warnings") if isinstance(analysis, dict) else []
    warnings_count = len(warnings) if isinstance(warnings, list) else 0
    teams = _match_teams(package)
    player_count = sum(
        len(team.get("players") or [])
        for team in teams
        if isinstance(team.get("players"), list)
    )
    return {
        "id": published_id,
        "source_match_id": source_match_id,
        "title": str(match.get("title") or "Untitled match"),
        "match_date": match.get("match_date"),
        "season": match.get("season"),
        "venue": match.get("venue"),
        "format": match.get("format"),
        "teams": [
            {"id": str(team.get("id") or ""), "name": str(team.get("name") or team.get("id") or "")}
            for team in teams
        ],
        "status": "published",
        "schema_version": str(package.get("schema_version") or "unknown"),
        "team_count": int(package.get("team_count") or len(teams)),
        "player_count": int(package.get("player_count") or player_count),
        "tracks_count": analysis.get("tracks_count") if isinstance(analysis, dict) else None,
        "frames_processed": analysis.get("frames_processed") if isinstance(analysis, dict) else None,
        "detections_kept": analysis.get("detections_kept") if isinstance(analysis, dict) else None,
        "warnings_count": warnings_count,
        "created_at": created_at,
        "updated_at": updated_at,
        "storage": "json",
    }


def import_match_package(package: dict[str, Any], *, replace: bool = False) -> dict[str, Any]:
    init_publish_store()
    published_id = _published_id_from_package(package)
    target_dir = _published_match_dir(published_id)
    summary_path = target_dir / "summary.json"

    if target_dir.exists() and not replace:
        raise FileExistsError(f"Published match {published_id} already exists. Re-import with replace=true to overwrite it.")
    existing_summary = _load_json_object(summary_path) if summary_path.exists() and replace else {}
    generated = now_iso()
    created_at = str(existing_summary.get("created_at") or generated)
    source_match = package.get("match") if isinstance(package.get("match"), dict) else {}
    source_match_id = str(source_match.get("id") or "unknown")
    summary = _summary_from_package(
        package,
        published_id=published_id,
        created_at=created_at,
        updated_at=generated,
    )
    public_root = public_match_report.CLIENT_PUBLIC_MATCHES_DIR
    target_public_dir = public_root / published_id
    staged_match_dir = _staging_directory(PUBLISHED_MATCHES_DIR.parent, name=published_id)
    staged_public_dir = _staging_directory(public_root.parent, name=published_id)
    try:
        _atomic_write_json(staged_match_dir / "package.json", package)
        public_report = write_public_match_report_bundle(
            package,
            target_dir=staged_match_dir,
            source_match_dir=MATCHES_DIR / source_match_id,
            mirror_dir=staged_public_dir,
        )
        stage_published_video(
            descriptor=package.get("published_video") if isinstance(package.get("published_video"), dict) else None,
            source_match_dir=MATCHES_DIR / source_match_id,
            target_dir=staged_match_dir,
            public_report_semantic_digest=canonical_json_sha256(public_report),
        )
        if package.get("identity_report_source") == "reviewed_identity":
            # This is server-side publication data, not a public client asset.
            # It is written after the exact public report generation it fingerprints.
            aggregate_inputs = build_aggregate_inputs(
                package,
                public_report=public_report,
                published_id=published_id,
            )
            _atomic_write_json(staged_match_dir / "aggregate_inputs.json", aggregate_inputs)
        summary["report_type"] = str(public_report.get("report_type") or "")
        _atomic_write_json(staged_match_dir / "summary.json", summary)
        _commit_publication_generation(
            staged_match_dir=staged_match_dir,
            target_match_dir=target_dir,
            staged_public_dir=staged_public_dir,
            target_public_dir=target_public_dir,
        )
    finally:
        _remove_directory(staged_match_dir)
        _remove_directory(staged_public_dir)
        _remove_empty_directory(staged_match_dir.parent)
        _remove_empty_directory(staged_public_dir.parent)

    result = get_published_match(published_id)
    result["public_report"] = public_report
    return result


def list_published_matches() -> list[dict[str, Any]]:
    init_publish_store()
    rows = []
    for summary_path in PUBLISHED_MATCHES_DIR.glob("*/summary.json"):
        try:
            rows.append(_load_json_object(summary_path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("match_date") or row.get("created_at") or ""),
            str(row.get("created_at") or ""),
        ),
        reverse=True,
    )


def list_eligible_match_group_sources() -> list[dict[str, Any]]:
    """Return compact metadata for only physical, aggregatable publications."""

    init_publish_store()
    rows: list[dict[str, Any]] = []
    for summary_path in PUBLISHED_MATCHES_DIR.glob("*/summary.json"):
        try:
            summary = _load_json_object(summary_path)
            published_id = str(summary.get("id") or summary_path.parent.name)
            aggregate = _load_json_object(summary_path.parent / "aggregate_inputs.json")
            report_type = str(summary.get("report_type") or "")
            if not report_type:
                # Legacy summaries predate the compact field. aggregate_inputs
                # exist only for physical reviewed publications, so they are a
                # compact authoritative compatibility prerequisite without
                # loading every potentially large public_report.json.
                report_type = "public_match_report"
            if report_type != "public_match_report":
                continue
            timing = aggregate.get("timing") if isinstance(aggregate.get("timing"), dict) else {}
            compact_teams = summary.get("teams") if isinstance(summary.get("teams"), list) else []
            rows.append({
                "id": published_id,
                "source_match_id": str(summary.get("source_match_id") or ""),
                "title": str(summary.get("title") or "Untitled match"),
                "match_date": summary.get("match_date"),
                "teams": [
                    str(team.get("name") or team.get("id") or "")
                    for team in compact_teams
                    if isinstance(team, dict)
                ],
                "analyzed_duration_sec": float(timing.get("analyzed_duration_sec") or 0),
                "status": str(summary.get("status") or "published"),
                "report_type": report_type,
            })
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return sorted(rows, key=lambda row: (str(row.get("match_date") or ""), str(row["title"])), reverse=True)


def get_published_match(match_id: str) -> dict[str, Any]:
    init_publish_store()
    target_dir = _published_match_dir(match_id)
    summary_path = target_dir / "summary.json"
    package_path = target_dir / "package.json"
    if not summary_path.exists() or not package_path.exists():
        raise KeyError(match_id)
    summary = _load_json_object(summary_path)
    package = _load_json_object(package_path)
    public_report_path = target_dir / "public_report.json"
    public_report = _load_json_object(public_report_path) if public_report_path.exists() else None
    teams = [
        {
            "id": str(team.get("id") or f"team-{index + 1}"),
            "match_id": match_id,
            "name": str(team.get("name") or team.get("id") or f"Team {index + 1}"),
            "color": team.get("color"),
            "players_json": team.get("players") if isinstance(team.get("players"), list) else [],
        }
        for index, team in enumerate(_match_teams(package))
    ]
    return {
        **summary,
        "package": package,
        "public_report": public_report,
        "teams": teams,
        "players": _match_players(package),
        "stable_players": _stable_players(package),
    }


def delete_published_match(match_id: str) -> dict[str, Any]:
    init_publish_store()
    target_dir = _published_match_dir(match_id)
    summary_path = target_dir / "summary.json"
    if not summary_path.exists():
        raise KeyError(match_id)
    summary = _load_json_object(summary_path)
    shutil.rmtree(target_dir)
    return summary


def publish_store_health() -> dict[str, Any]:
    init_publish_store()
    return {
        "path": str(PUBLISHED_MATCHES_DIR),
        "published_matches": len(list_published_matches()),
        "storage": "json",
    }
