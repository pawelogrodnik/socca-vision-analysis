from __future__ import annotations

"""Refresh logical-match source pins to the latest stable publications.

Physical publications retain their ``published_id`` while their authoritative
contents can be atomically replaced.  This service is the only lifecycle path
that advances an existing logical group to those new contents.
"""

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from app.services.match_group_aggregation import build_match_group_report_candidate
from app.services.match_group_external_video import get_match_group_external_video
from app.services.match_group_video import get_match_group_video_status, reserve_match_group_video_idle
from app.services.match_groups import (
    MATCH_GROUPS_DIR,
    MatchGroupError,
    build_current_match_group_manifest,
    get_match_group,
    validate_match_group_manifest,
)


REFRESH_PIN_FIELDS = (
    "source_match_id",
    "aggregation_input_schema_version",
    "aggregation_policy_version",
    "aggregation_input_semantic_digest",
    "public_report_schema_version",
    "public_report_semantic_digest",
    "reviewed_identity_digest",
    "analyzed_duration_sec",
)


def preview_match_group_refresh(group_id: str) -> dict[str, Any]:
    """Return an authoritative, mutation-free refresh decision."""

    group = get_match_group(group_id)
    try:
        candidate = _build_refresh_candidate(group)
    except MatchGroupError as error:
        return _preview(group, None, status="blocked", reasons=[error.reason()])
    validation = validate_match_group_manifest(candidate)
    if validation.get("status") != "compatible":
        return _preview(group, candidate, status="blocked", reasons=_reasons(validation))
    return _preview(
        group,
        candidate,
        status="refreshable" if _pins_changed(group, candidate) else "current",
        reasons=[],
    )


def refresh_match_group_to_latest(group_id: str) -> dict[str, Any]:
    """Atomically advance pins and report, without touching physical sources."""

    with reserve_match_group_video_idle(group_id, operation="refresh"):
        group = get_match_group(group_id)
        original_manifest_digest = str(group.get("aggregate_semantic_digest") or "")
        candidate = _build_refresh_candidate(group)
        validation = validate_match_group_manifest(candidate)
        if validation.get("status") != "compatible":
            reasons = _reasons(validation)
            detail = str((reasons[0] if reasons else {}).get("detail") or "Latest source publications are incompatible.")
            raise MatchGroupError("refresh_blocked", detail)
        if not _pins_changed(group, candidate):
            return _response(group, refreshed=False)

        report = build_match_group_report_candidate(candidate)
        # The generation can change while aggregate primitives are being read.
        # One final authoritative read prevents committing a mixed generation.
        precommit_group = get_match_group(group_id)
        if precommit_group.get("aggregate_semantic_digest") != original_manifest_digest:
            raise MatchGroupError(
                "source_generation_changed_during_refresh",
                "Logical-match definition changed while the report was refreshing.",
            )
        precommit = _build_refresh_candidate(precommit_group)
        if candidate.get("aggregate_semantic_digest") != precommit.get("aggregate_semantic_digest"):
            raise MatchGroupError(
                "source_generation_changed_during_refresh",
                "A source publication or logical-match definition changed while the report was refreshing.",
            )
        validation = validate_match_group_manifest(precommit)
        if validation.get("status") != "compatible":
            raise MatchGroupError(
                "source_generation_changed_during_refresh",
                "A source publication became incompatible while the logical report was refreshing.",
            )
        _commit_pair(
            group_id,
            precommit,
            report,
            expected_manifest_digest=original_manifest_digest,
        )
        return _response(precommit, refreshed=True)


def _build_refresh_candidate(group: dict[str, Any]) -> dict[str, Any]:
    persisted_validation = validate_match_group_manifest(group)
    if persisted_validation.get("status") == "invalid":
        reasons = _reasons(persisted_validation)
        detail = str((reasons[0] if reasons else {}).get("detail") or "Stored logical match is not trustworthy.")
        raise MatchGroupError("refresh_manifest_invalid", detail)
    member_ids = [str(member.get("published_id") or "") for member in group.get("members") or [] if isinstance(member, dict)]
    candidate = build_current_match_group_manifest(
        group_id=str(group["group_id"]),
        member_published_ids=member_ids,
        metadata=dict(group.get("metadata") or {}),
    )
    previous = group.get("members") if isinstance(group.get("members"), list) else []
    current = candidate.get("members") if isinstance(candidate.get("members"), list) else []
    if len(previous) != len(current):
        raise MatchGroupError("source_members_changed", "Logical match membership changed during refresh.")
    for old, new in zip(previous, current, strict=True):
        if not isinstance(old, dict) or not isinstance(new, dict):
            raise MatchGroupError("manifest_members_invalid", "Logical match members must be objects.")
        if old.get("published_id") != new.get("published_id") or old.get("source_match_id") != new.get("source_match_id"):
            raise MatchGroupError(
                "source_match_identity_changed",
                "A stable publication now identifies a different physical source and cannot be refreshed in place.",
                member=str(old.get("published_id") or "") or None,
            )
    return candidate


def _preview(
    group: dict[str, Any],
    candidate: dict[str, Any] | None,
    *,
    status: str,
    reasons: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "group_id": group["group_id"],
        "status": status,
        "members": _member_changes(group, candidate),
        "blocking_reasons": reasons,
    }


def _member_changes(group: dict[str, Any], candidate: dict[str, Any] | None) -> list[dict[str, Any]]:
    old_members = group.get("members") if isinstance(group.get("members"), list) else []
    new_members = candidate.get("members") if isinstance(candidate, dict) and isinstance(candidate.get("members"), list) else []
    rows = []
    for index, old in enumerate(old_members):
        before = dict(old) if isinstance(old, dict) else {}
        after = dict(new_members[index]) if index < len(new_members) and isinstance(new_members[index], dict) else None
        rows.append({
            "published_id": before.get("published_id"),
            "source_match_id": before.get("source_match_id"),
            "status": "refreshable" if after is not None and _member_changed(before, after) else "current",
            "current": _public_member(before),
            "latest": _public_member(after) if after is not None else None,
        })
    return rows


def _response(group: dict[str, Any], *, refreshed: bool) -> dict[str, Any]:
    group_id = str(group["group_id"])
    return {
        "status": "refreshed" if refreshed else "current",
        "group": group,
        "validation": validate_match_group_manifest(group),
        "video": get_match_group_video_status(group_id),
        "external_video": get_match_group_external_video(group_id),
    }


def _pins_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return not _same_pins(before, after)


def _same_pins(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_members = first.get("members") if isinstance(first.get("members"), list) else []
    second_members = second.get("members") if isinstance(second.get("members"), list) else []
    if len(first_members) != len(second_members):
        return False
    return all(
        isinstance(left, dict)
        and isinstance(right, dict)
        and left.get("published_id") == right.get("published_id")
        and left.get("sequence_index") == right.get("sequence_index")
        and all(left.get(field) == right.get(field) for field in REFRESH_PIN_FIELDS)
        for left, right in zip(first_members, second_members, strict=True)
    )


def _member_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return any(before.get(field) != after.get(field) for field in REFRESH_PIN_FIELDS)


def _public_member(member: dict[str, Any] | None) -> dict[str, Any] | None:
    if member is None:
        return None
    return {field: member.get(field) for field in ("published_id", "source_match_id", *REFRESH_PIN_FIELDS)}


def _reasons(validation: dict[str, Any]) -> list[dict[str, str]]:
    rows = validation.get("blocking_reasons")
    return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _commit_pair(
    group_id: str,
    manifest: dict[str, Any],
    report: dict[str, Any],
    *,
    expected_manifest_digest: str | None = None,
) -> None:
    """Replace the two coupled documents or restore their previous exact bytes."""

    directory = MATCH_GROUPS_DIR / group_id
    manifest_path = directory / "manifest.json"
    report_path = directory / "public_report.json"
    if not directory.is_dir() or not manifest_path.is_file():
        raise KeyError(group_id)
    if expected_manifest_digest is not None and get_match_group(group_id).get("aggregate_semantic_digest") != expected_manifest_digest:
        raise MatchGroupError("source_generation_changed_during_refresh", "Logical-match definition changed before refresh could commit.")
    previous_manifest = manifest_path.read_bytes()
    previous_report = report_path.read_bytes() if report_path.is_file() else None
    staged_manifest = _stage_json(directory, "manifest", manifest)
    staged_report = _stage_json(directory, "public_report", report)
    try:
        if not directory.is_dir() or not manifest_path.is_file():
            raise KeyError(group_id)
        if expected_manifest_digest is not None and get_match_group(group_id).get("aggregate_semantic_digest") != expected_manifest_digest:
            raise MatchGroupError("source_generation_changed_during_refresh", "Logical-match definition changed before refresh could commit.")
        os.replace(staged_manifest, manifest_path)
        if not directory.is_dir() or not manifest_path.is_file():
            raise KeyError(group_id)
        os.replace(staged_report, report_path)
    except Exception:
        if directory.is_dir() and manifest_path.is_file():
            _restore_bytes(manifest_path, previous_manifest)
            if previous_report is None:
                report_path.unlink(missing_ok=True)
            else:
                _restore_bytes(report_path, previous_report)
        raise
    finally:
        staged_manifest.unlink(missing_ok=True)
        staged_report.unlink(missing_ok=True)


def _stage_json(directory: Path, name: str, document: dict[str, Any]) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", suffix=".refresh.tmp", dir=directory)
    temporary = Path(temporary_name)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def _restore_bytes(path: Path, value: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".rollback.tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
