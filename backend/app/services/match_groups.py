from __future__ import annotations

"""Durable logical-match definitions over published physical source generations.

This Phase 2 store deliberately reads only the compact aggregation inputs and
the public report needed to verify its pinned digest.  It neither creates a
synthetic physical match nor calculates aggregate statistics.
"""

import copy
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from collections.abc import Callable
from typing import Any

from app.config import PUBLISHED_DIR
from app.services.aggregate_inputs import AGGREGATE_INPUTS_SCHEMA_VERSION, AGGREGATION_POLICY_VERSION
from app.services.artifact_lineage import canonical_json_sha256
from app.services.public_match_report import PUBLIC_MATCH_REPORT_SCHEMA_VERSION, PUBLIC_MATCH_REPORT_TYPE


MATCH_GROUP_MANIFEST_SCHEMA_VERSION = "1.0.0"
MATCH_GROUPS_DIR = PUBLISHED_DIR / "match-groups"
PUBLISHED_MATCHES_DIR = PUBLISHED_DIR / "matches"
EXPECTED_TEAM_COUNT = 2
# Invalid means corrupted or untrustworthy evidence and therefore wins over a
# supported-contract incompatibility, which in turn wins over a reproducible
# but changed/missing source generation.  This order is lifecycle-safe and is
# independent of the caller's member order.
VALIDATION_STATUS_PRECEDENCE = {
    "compatible": 0,
    "stale": 1,
    "incompatible": 2,
    "invalid": 3,
}


class MatchGroupError(ValueError):
    """A structured, fail-closed match-group source or manifest error."""

    def __init__(self, code: str, detail: str, *, member: str | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.member = member

    def reason(self) -> dict[str, str]:
        result = {"code": self.code, "detail": self.detail}
        if self.member:
            result["member"] = self.member
        return result


def init_match_group_store() -> None:
    MATCH_GROUPS_DIR.mkdir(parents=True, exist_ok=True)


def create_match_group(*, member_published_ids: list[str], metadata: dict[str, Any]) -> dict[str, Any]:
    """Persist a new logical group from an explicit, operator-supplied order."""

    group_id = f"match-group-{uuid.uuid4()}"
    manifest = _build_manifest(group_id=group_id, member_published_ids=member_published_ids, metadata=metadata)
    _persist_new_manifest(group_id, manifest)
    return manifest


def preview_match_group(*, member_published_ids: list[str], metadata: dict[str, Any]) -> dict[str, Any]:
    """Build the server-authoritative compatibility preview without persisting it."""

    manifest = _build_manifest(
        group_id="match-group-preview",
        member_published_ids=member_published_ids,
        metadata=metadata,
    )
    return {
        "status": manifest["compatibility"]["status"],
        "compatibility": manifest["compatibility"],
        "timing": manifest["timing"],
        "members": manifest["members"],
    }


def get_match_group(group_id: str) -> dict[str, Any]:
    return _load_manifest(_group_dir(_validated_group_id(group_id)) / "manifest.json")


def list_match_groups() -> list[dict[str, Any]]:
    init_match_group_store()
    rows = []
    for manifest_path in sorted(MATCH_GROUPS_DIR.glob("match-group-*/manifest.json")):
        try:
            rows.append(_load_manifest(manifest_path))
        except (MatchGroupError, OSError, json.JSONDecodeError):
            continue
    return sorted(rows, key=lambda row: str(row["group_id"]))


def update_match_group(
    group_id: str,
    *,
    member_published_ids: list[str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Replace one group definition atomically, retaining its durable ID only."""

    normalized_group_id = _validated_group_id(group_id)
    manifest_path = _group_dir(normalized_group_id) / "manifest.json"
    if not manifest_path.exists():
        raise KeyError(normalized_group_id)
    # Read the old coherent document before calculating a replacement.  A
    # validation failure below therefore cannot affect the stored generation.
    _load_manifest(manifest_path)
    manifest = _build_manifest(
        group_id=normalized_group_id,
        member_published_ids=member_published_ids,
        metadata=metadata,
    )
    _atomic_write_json(manifest_path, manifest)
    return manifest


def update_match_group_and_generate_report(
    group_id: str,
    *,
    member_published_ids: list[str],
    metadata: dict[str, Any],
    generate_report: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Restore prior coherent group bytes when regeneration cannot complete."""

    normalized_group_id = _validated_group_id(group_id)
    directory = _group_dir(normalized_group_id)
    manifest_path = directory / "manifest.json"
    report_path = directory / "public_report.json"
    if not manifest_path.exists():
        raise KeyError(normalized_group_id)
    previous_manifest = manifest_path.read_bytes()
    previous_report = report_path.read_bytes() if report_path.exists() else None
    group = update_match_group(
        normalized_group_id,
        member_published_ids=member_published_ids,
        metadata=metadata,
    )
    try:
        report = generate_report(normalized_group_id)
    except Exception:
        _atomic_write_bytes(manifest_path, previous_manifest)
        if previous_report is not None:
            _atomic_write_bytes(report_path, previous_report)
        elif report_path.exists():
            report_path.unlink()
        raise
    return group, report


def delete_match_group(group_id: str) -> dict[str, Any]:
    normalized_group_id = _validated_group_id(group_id)
    directory = _group_dir(normalized_group_id)
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise KeyError(normalized_group_id)
    manifest = _load_manifest(manifest_path)
    shutil.rmtree(directory)
    return manifest


def validate_match_group(group_id: str) -> dict[str, Any]:
    """Compare persisted source pins with current published source artifacts."""

    manifest = get_match_group(group_id)
    stored_capabilities = _record(_record(manifest.get("compatibility")).get("capabilities"))
    manifest_reason = _manifest_digest_reason(manifest)
    if manifest_reason is not None:
        return _validation_result("invalid", [manifest_reason], stored_capabilities)
    structure_reason = _manifest_structure_reason(manifest)
    if structure_reason is not None:
        return _validation_result("invalid", [structure_reason], stored_capabilities)
    if not all(isinstance(member, dict) for member in manifest["members"]):
        return _validation_result(
            "invalid",
            [{"code": "manifest_members_invalid", "detail": "Manifest members must contain objects."}],
            stored_capabilities,
        )

    current_members: list[dict[str, Any]] = []
    reasons: list[dict[str, str]] = []
    status = "compatible"
    for expected_index, pinned_member in enumerate(manifest["members"]):
        published_id = str(pinned_member.get("published_id") or "")
        if not published_id:
            reasons.append({"code": "manifest_members_invalid", "detail": "Manifest member published_id is required."})
            status = "invalid"
            continue
        try:
            current = _authoritative_member(published_id, sequence_index=expected_index)
        except MatchGroupError as error:
            reasons.append(error.reason())
            status = _more_severe_status(status, _source_error_status(error.code))
            continue
        current_members.append(current)
        for key in (
            "source_match_id",
            "aggregation_input_schema_version",
            "aggregation_policy_version",
            "aggregation_input_semantic_digest",
            "public_report_schema_version",
            "public_report_semantic_digest",
            "reviewed_identity_digest",
        ):
            if current[key] != pinned_member.get(key):
                reasons.append(
                    {
                        "code": "source_generation_changed",
                        "member": published_id,
                        "detail": f"Current {key} differs from the persisted group pin.",
                    }
                )
                status = _more_severe_status(status, "stale")
                break

    if reasons:
        return _validation_result(status, reasons, stored_capabilities)

    compatibility = _compatibility(current_members)
    return _validation_result(compatibility["status"], compatibility["blocking_reasons"], compatibility["capabilities"])


def _build_manifest(*, group_id: str, member_published_ids: list[str], metadata: dict[str, Any]) -> dict[str, Any]:
    normalized_metadata = _metadata(metadata)
    published_ids = _member_ids(member_published_ids)
    members = [_authoritative_member(published_id, sequence_index=index) for index, published_id in enumerate(published_ids)]
    source_match_ids = [str(member["source_match_id"]) for member in members]
    if len(source_match_ids) != len(set(source_match_ids)):
        raise MatchGroupError(
            "duplicate_source_member",
            "A physical source_match_id cannot appear more than once in one group.",
        )
    compatibility = _compatibility(members)
    if compatibility["status"] != "compatible":
        first = compatibility["blocking_reasons"][0]
        raise MatchGroupError(first["code"], first["detail"], member=first.get("member"))

    timeline_members = _with_logical_offsets(members)
    analyzed_duration_sec = sum(float(member["analyzed_duration_sec"]) for member in timeline_members)
    document: dict[str, Any] = {
        "schema_version": MATCH_GROUP_MANIFEST_SCHEMA_VERSION,
        "group_id": group_id,
        "metadata": normalized_metadata,
        "members": timeline_members,
        "timing": {
            "analyzed_duration_sec": analyzed_duration_sec,
            "timeline_span_sec": analyzed_duration_sec,
            "mapping": "ordered_sequential_source_durations",
        },
        "compatibility": compatibility,
    }
    document["aggregate_semantic_digest"] = canonical_json_sha256(document)
    return document


def _authoritative_member(published_id: str, *, sequence_index: int) -> dict[str, Any]:
    source_dir = PUBLISHED_MATCHES_DIR / _validated_published_id(published_id)
    aggregate_path = source_dir / "aggregate_inputs.json"
    public_report_path = source_dir / "public_report.json"
    if not source_dir.is_dir():
        raise MatchGroupError("published_source_missing", "Published source directory does not exist.", member=published_id)
    if not aggregate_path.is_file():
        raise MatchGroupError("aggregate_inputs_missing", "Published source has no aggregate_inputs.json.", member=published_id)
    if not public_report_path.is_file():
        raise MatchGroupError("public_report_missing", "Published source has no public_report.json.", member=published_id)

    aggregate = _load_json_object(aggregate_path, member=published_id)
    public_report = _load_json_object(public_report_path, member=published_id)
    source = _record(aggregate.get("source"))
    aggregate_digest = _required_text(source.get("aggregation_input_semantic_digest"), "aggregation input semantic digest", member=published_id)
    digest_document = copy.deepcopy(aggregate)
    _record(digest_document.get("source")).pop("aggregation_input_semantic_digest", None)
    if canonical_json_sha256(digest_document) != aggregate_digest:
        raise MatchGroupError(
            "aggregation_input_digest_mismatch",
            "aggregate_inputs semantic digest does not match its self-excluding canonical content.",
            member=published_id,
        )

    # The aggregate source is now integrity-verified, so its public-report
    # digest can safely become the next link in the trust chain.
    public_digest = _required_text(
        source.get("public_report_semantic_digest"), "public report semantic digest", member=published_id
    )
    if canonical_json_sha256(public_report) != public_digest:
        raise MatchGroupError(
            "public_report_digest_mismatch",
            "public_report.json does not match the digest pinned by aggregate_inputs.",
            member=published_id,
        )

    public_schema_version = _required_text(
        public_report.get("schema_version"), "public report schema_version", member=published_id
    )
    report_type = _required_text(public_report.get("report_type"), "public report type", member=published_id)
    schema_version = _required_text(aggregate.get("schema_version"), "aggregate input schema_version", member=published_id)
    policy_version = _required_text(aggregate.get("aggregation_policy_version"), "aggregation policy version", member=published_id)
    if schema_version != AGGREGATE_INPUTS_SCHEMA_VERSION:
        raise MatchGroupError(
            "unsupported_aggregate_input_schema",
            f"Aggregate input schema {schema_version!r} is not supported.",
            member=published_id,
        )
    if policy_version != AGGREGATION_POLICY_VERSION:
        raise MatchGroupError(
            "unsupported_aggregation_policy",
            f"Aggregation policy {policy_version!r} is not supported.",
            member=published_id,
        )
    if public_schema_version != PUBLIC_MATCH_REPORT_SCHEMA_VERSION:
        raise MatchGroupError(
            "unsupported_public_report_schema",
            f"Public report schema {public_schema_version!r} is not supported.",
            member=published_id,
        )
    if report_type != PUBLIC_MATCH_REPORT_TYPE:
        raise MatchGroupError(
            "unsupported_public_report_type",
            f"Public report type {report_type!r} is not a physical source public report.",
            member=published_id,
        )

    source_match_id = _required_text(source.get("source_match_id"), "source_match_id", member=published_id)
    source_published_id = _required_text(source.get("published_id"), "source.published_id", member=published_id)
    if source_published_id != published_id:
        raise MatchGroupError(
            "published_id_mismatch",
            "aggregate_inputs source.published_id does not match its selected published source.",
            member=published_id,
        )
    if str(public_report.get("id") or "") != published_id or str(public_report.get("source_match_id") or "") != source_match_id:
        raise MatchGroupError(
            "public_report_identity_mismatch",
            "public_report.json does not identify the selected physical source publication.",
            member=published_id,
        )

    reviewed_identity_digest = _required_text(
        source.get("reviewed_identity_digest"), "reviewed identity digest", member=published_id
    )
    timing = _record(aggregate.get("timing"))
    analyzed_duration_sec = _positive_number(timing.get("analyzed_duration_sec"), "analyzed duration", member=published_id)
    team_ids = _stable_team_ids(aggregate, member=published_id)
    player_team_ids = _stable_player_team_ids(aggregate, team_ids=team_ids, member=published_id)
    return {
        "sequence_index": sequence_index,
        "published_id": published_id,
        "source_match_id": source_match_id,
        "aggregation_input_schema_version": schema_version,
        "aggregation_policy_version": policy_version,
        "aggregation_input_semantic_digest": aggregate_digest,
        "public_report_schema_version": public_schema_version,
        "public_report_semantic_digest": public_digest,
        "reviewed_identity_digest": reviewed_identity_digest,
        "analyzed_duration_sec": analyzed_duration_sec,
        "_team_ids": team_ids,
        "_player_team_ids": player_team_ids,
        "_spatial": _record(aggregate.get("spatial")),
        "_metrics": _record(aggregate.get("metric_readiness")),
        "_ball": _record(aggregate.get("ball")),
        "_identity_coverage": _record(aggregate.get("identity_coverage")),
        "_timelines": _record(aggregate.get("timelines")),
    }


def _with_logical_offsets(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    offset = 0.0
    result = []
    for member in members:
        duration = float(member["analyzed_duration_sec"])
        item = {key: value for key, value in member.items() if not key.startswith("_")}
        item["logical_start_sec"] = offset
        item["logical_end_sec"] = offset + duration
        result.append(item)
        offset += duration
    return result


def _compatibility(members: list[dict[str, Any]]) -> dict[str, Any]:
    reasons: list[dict[str, str]] = []
    expected_team_ids: set[str] | None = None
    player_team_ids: dict[str, str] = {}
    for member in members:
        member_id = str(member["published_id"])
        team_ids = set(member["_team_ids"])
        if len(team_ids) != EXPECTED_TEAM_COUNT:
            reasons.append(
                {
                    "code": "unsupported_team_cardinality",
                    "member": member_id,
                    "detail": f"Current logical football-match support requires exactly {EXPECTED_TEAM_COUNT} stable teams.",
                }
            )
        if expected_team_ids is None:
            expected_team_ids = team_ids
        elif team_ids != expected_team_ids:
            reasons.append(
                {
                    "code": "team_set_mismatch",
                    "member": member_id,
                    "detail": "Stable team_id set differs from the first selected physical source.",
                }
            )
        for player_id, team_id in member["_player_team_ids"].items():
            previous = player_team_ids.setdefault(player_id, team_id)
            if previous != team_id:
                reasons.append(
                    {
                        "code": "player_team_mismatch",
                        "member": member_id,
                        "detail": f"Stable player_id {player_id!r} maps to different stable team_ids across sources.",
                    }
                )
    capabilities = _capabilities(members)
    return {
        "status": "compatible" if not reasons else "incompatible",
        "blocking_reasons": reasons,
        "team_ids": sorted(expected_team_ids or set()),
        "capabilities": capabilities,
    }


def _capabilities(members: list[dict[str, Any]]) -> dict[str, Any]:
    def capability(values: list[Any]) -> dict[str, str]:
        available = [value for value in values if _is_available_status(value)]
        if len(available) == len(values) and values:
            return {"status": "available"}
        if available:
            return {"status": "partial", "reason": "not_available_for_every_member"}
        return {"status": "not_available", "reason": "not_available_for_any_member"}

    team_movement = capability([_record(member["_metrics"].get("team_movement")).get("status") for member in members])
    player_movement = capability([_record(member["_metrics"].get("player_movement")).get("status") for member in members])
    possession = capability([_record(member["_ball"].get("possession")).get("status") for member in members])
    passes = capability([_record(member["_ball"].get("passes")).get("status") for member in members])
    identity = capability([member["_identity_coverage"].get("status") for member in members])
    possession_timeline = capability([_record(member["_timelines"].get("possession")).get("status") for member in members])
    attacking_momentum = capability(
        [_record(member["_timelines"].get("attacking_momentum")).get("status") for member in members]
    )
    dimensions = [_pitch_dimensions(member["_spatial"]) for member in members]
    if any(dimension is not None for dimension in dimensions) and len({tuple(sorted(item.items())) for item in dimensions if item}) > 1:
        spatial = {"status": "incompatible", "reason": "pitch_dimensions_mismatch"}
    else:
        spatial = {"status": "not_available", "reason": "canonical_orientation_not_proven"}
    return {
        "movement": {"team": team_movement, "player": player_movement},
        "possession": possession,
        "passes": passes,
        "identity_coverage": identity,
        "timelines": {"possession": possession_timeline, "attacking_momentum": attacking_momentum},
        "spatial": spatial,
        "team_shape": {"status": "not_available", "reason": "canonical_orientation_and_sample_weights_not_proven"},
    }


def _is_available_status(value: Any) -> bool:
    return str(value or "").strip().lower() in {"available", "completed", "fresh", "ready"}


def _source_error_status(code: str) -> str:
    if code in {
        "unsupported_aggregate_input_schema",
        "unsupported_aggregation_policy",
        "unsupported_public_report_schema",
        "unsupported_public_report_type",
    }:
        return "incompatible"
    if code == "published_source_missing":
        return "stale"
    return "invalid"


def _more_severe_status(current: str, candidate: str) -> str:
    return candidate if VALIDATION_STATUS_PRECEDENCE[candidate] > VALIDATION_STATUS_PRECEDENCE[current] else current


def _pitch_dimensions(spatial: dict[str, Any]) -> dict[str, float] | None:
    dimensions = _record(spatial.get("pitch_dimensions_m"))
    width = _number_or_none(dimensions.get("width_m"))
    length = _number_or_none(dimensions.get("length_m"))
    if width is None or length is None:
        return None
    return {"width_m": width, "length_m": length}


def _stable_team_ids(aggregate: dict[str, Any], *, member: str) -> list[str]:
    teams = aggregate.get("teams")
    if not isinstance(teams, list):
        raise MatchGroupError("stable_team_ids_missing", "aggregate_inputs teams must be a list.", member=member)
    team_ids = [_required_text(_record(team).get("team_id"), "stable team_id", member=member) for team in teams]
    if len(team_ids) != len(set(team_ids)):
        raise MatchGroupError("duplicate_stable_team_id", "aggregate_inputs contains duplicate stable team_id values.", member=member)
    return sorted(team_ids)


def _stable_player_team_ids(aggregate: dict[str, Any], *, team_ids: list[str], member: str) -> dict[str, str]:
    players = aggregate.get("players")
    if not isinstance(players, list):
        raise MatchGroupError("stable_player_ids_missing", "aggregate_inputs players must be a list.", member=member)
    result: dict[str, str] = {}
    for row in players:
        item = _record(row)
        player_id = _required_text(item.get("player_id"), "stable player_id", member=member)
        team_id = _required_text(item.get("team_id"), "stable player team_id", member=member)
        if team_id not in team_ids:
            raise MatchGroupError(
                "player_team_outside_member_team_set",
                "A player row references a stable team_id not present in its source team set.",
                member=member,
            )
        previous = result.setdefault(player_id, team_id)
        if previous != team_id:
            raise MatchGroupError(
                "duplicate_player_team_mapping",
                "A source contains one stable player_id mapped to multiple stable team_ids.",
                member=member,
            )
    return result


def _member_ids(member_published_ids: list[str]) -> list[str]:
    if not isinstance(member_published_ids, list) or len(member_published_ids) < 2:
        raise MatchGroupError("members_required", "A logical match group requires at least two ordered published sources.")
    result = [_validated_published_id(value) for value in member_published_ids]
    if len(result) != len(set(result)):
        raise MatchGroupError("duplicate_published_member", "A published source cannot appear more than once in one group.")
    return result


def _metadata(value: dict[str, Any]) -> dict[str, str | None]:
    if not isinstance(value, dict):
        raise MatchGroupError("metadata_invalid", "Group metadata must be an object.")
    result: dict[str, str | None] = {}
    for key in ("title", "match_date", "season", "venue", "format"):
        item = value.get(key)
        if item is None:
            result[key] = None
        elif isinstance(item, str):
            result[key] = item.strip() or None
        else:
            raise MatchGroupError("metadata_invalid", f"metadata.{key} must be a string or null.")
    return result


def _persist_new_manifest(group_id: str, manifest: dict[str, Any]) -> None:
    init_match_group_store()
    target_dir = _group_dir(group_id)
    if target_dir.exists():
        raise FileExistsError(group_id)
    staging_root = MATCH_GROUPS_DIR / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staged_dir = Path(tempfile.mkdtemp(prefix=f"{group_id}-", dir=staging_root))
    try:
        _atomic_write_json(staged_dir / "manifest.json", manifest)
        staged_dir.replace(target_dir)
    finally:
        if staged_dir.exists():
            shutil.rmtree(staged_dir)
        try:
            staging_root.rmdir()
        except OSError:
            pass


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.rollback.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _manifest_digest_reason(manifest: dict[str, Any]) -> dict[str, str] | None:
    persisted = manifest.get("aggregate_semantic_digest")
    if not isinstance(persisted, str) or not persisted:
        return {"code": "manifest_digest_missing", "detail": "Manifest has no aggregate semantic digest."}
    digest_document = copy.deepcopy(manifest)
    digest_document.pop("aggregate_semantic_digest", None)
    if canonical_json_sha256(digest_document) != persisted:
        return {"code": "manifest_digest_mismatch", "detail": "Manifest semantic digest does not match its content."}
    return None


def _manifest_structure_reason(manifest: dict[str, Any]) -> dict[str, str] | None:
    members = manifest.get("members")
    if not isinstance(members, list) or len(members) < 2:
        return {"code": "manifest_members_invalid", "detail": "Manifest must contain at least two member objects."}
    seen_published_ids: set[str] = set()
    seen_source_match_ids: set[str] = set()
    expected_start = 0.0
    for index, raw_member in enumerate(members):
        member = _record(raw_member)
        if not member:
            return {"code": "manifest_members_invalid", "detail": "Manifest members must contain objects."}
        try:
            published_id = _validated_published_id(member.get("published_id"))
            source_match_id = _required_text(member.get("source_match_id"), "manifest source_match_id", member=published_id)
            _required_text(
                member.get("aggregation_input_schema_version"), "manifest aggregation input schema version", member=published_id
            )
            _required_text(
                member.get("aggregation_policy_version"), "manifest aggregation policy version", member=published_id
            )
            _required_text(
                member.get("aggregation_input_semantic_digest"), "manifest aggregation input digest", member=published_id
            )
            _required_text(
                member.get("public_report_schema_version"), "manifest public report schema version", member=published_id
            )
            _required_text(member.get("public_report_semantic_digest"), "manifest public report digest", member=published_id)
            _required_text(member.get("reviewed_identity_digest"), "manifest reviewed identity digest", member=published_id)
            sequence_index = int(member.get("sequence_index"))
            duration = _positive_number(member.get("analyzed_duration_sec"), "manifest analyzed duration", member=published_id)
            logical_start = _positive_or_zero_number(member.get("logical_start_sec"), "manifest logical start", member=published_id)
            logical_end = _positive_or_zero_number(member.get("logical_end_sec"), "manifest logical end", member=published_id)
        except (MatchGroupError, TypeError, ValueError) as error:
            return error.reason() if isinstance(error, MatchGroupError) else {
                "code": "manifest_members_invalid",
                "detail": "Manifest member timing is invalid.",
            }
        if sequence_index != index or logical_start != expected_start or logical_end != expected_start + duration:
            return {
                "code": "manifest_timeline_invalid",
                "detail": "Manifest members must use deterministic contiguous sequence offsets.",
            }
        if published_id in seen_published_ids or source_match_id in seen_source_match_ids:
            return {"code": "manifest_members_invalid", "detail": "Manifest contains duplicate published or physical source members."}
        seen_published_ids.add(published_id)
        seen_source_match_ids.add(source_match_id)
        expected_start = logical_end
    timing = _record(manifest.get("timing"))
    if _number_or_none(timing.get("analyzed_duration_sec")) != expected_start or _number_or_none(timing.get("timeline_span_sec")) != expected_start:
        return {"code": "manifest_timeline_invalid", "detail": "Manifest timing totals do not match its member offsets."}
    return None


def _validation_result(status: str, reasons: list[dict[str, str]], capabilities: Any) -> dict[str, Any]:
    return {
        "status": status,
        "blocking_reasons": reasons,
        "capabilities": capabilities if isinstance(capabilities, dict) else {},
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise KeyError(path.parent.name)
    manifest = _load_json_object(path)
    if manifest.get("schema_version") != MATCH_GROUP_MANIFEST_SCHEMA_VERSION:
        raise MatchGroupError("unsupported_manifest_schema", "Match-group manifest schema is not supported.")
    group_id = _validated_group_id(manifest.get("group_id"))
    if group_id != path.parent.name:
        raise MatchGroupError("manifest_group_id_mismatch", "Manifest group_id does not match its storage directory.")
    if not isinstance(manifest.get("members"), list):
        raise MatchGroupError("manifest_members_invalid", "Manifest members must be a list.")
    return manifest


def _load_json_object(path: Path, *, member: str | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MatchGroupError("source_json_invalid", f"Could not read {path.name} as a JSON object.", member=member) from error
    if not isinstance(value, dict):
        raise MatchGroupError("source_json_invalid", f"{path.name} must contain a JSON object.", member=member)
    return value


def _group_dir(group_id: str) -> Path:
    return MATCH_GROUPS_DIR / group_id


def _validated_group_id(value: Any) -> str:
    group_id = str(value or "").strip()
    prefix = "match-group-"
    if not group_id.startswith(prefix):
        raise MatchGroupError("group_id_invalid", "Group ID must use the match-group UUID format.")
    try:
        uuid.UUID(group_id[len(prefix) :])
    except (ValueError, AttributeError) as error:
        raise MatchGroupError("group_id_invalid", "Group ID must use the match-group UUID format.") from error
    return group_id


def _validated_published_id(value: Any) -> str:
    published_id = str(value or "").strip()
    if not published_id or published_id != Path(published_id).name or published_id in {".", ".."}:
        raise MatchGroupError("published_id_invalid", "Published source ID must be a safe directory component.")
    return published_id


def _required_text(value: Any, field: str, *, member: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise MatchGroupError("required_source_field_missing", f"{field} is required.", member=member)
    return result


def _positive_number(value: Any, field: str, *, member: str) -> float:
    number = _number_or_none(value)
    if number is None or number <= 0:
        raise MatchGroupError("analyzed_duration_invalid", f"{field} must be a positive finite number.", member=member)
    return number


def _positive_or_zero_number(value: Any, field: str, *, member: str) -> float:
    number = _number_or_none(value)
    if number is None or number < 0:
        raise MatchGroupError("manifest_members_invalid", f"{field} must be a finite non-negative number.", member=member)
    return number


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
