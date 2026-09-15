from __future__ import annotations

"""Resolve a public logical timestamp to its physical video source."""

import math
from typing import Any, Callable, Mapping

from app.services.json_publish_store import MERGED_SOURCE_KIND


def source_context_for_published_time(
    match: Mapping[str, Any],
    published_id: str,
    time_sec: float,
    *,
    group_id_for_merged_published_id: Callable[[str], str | None],
    get_match_group: Callable[[str], Mapping[str, Any]],
) -> tuple[str, float] | None:
    """Return the physical source ID and source-local time for a public time.

    This is deliberately the one mapping used by canonical location reads and
    operator frame correction.  A logical merged timestamp is never applied
    directly to a physical member video.
    """

    if not math.isfinite(time_sec) or time_sec < 0:
        return None
    if str(match.get("source_kind") or "physical") != MERGED_SOURCE_KIND:
        source_id = str(match.get("source_match_id") or "")
        return (source_id, time_sec) if source_id else None
    group_id = group_id_for_merged_published_id(published_id)
    if not group_id:
        return None
    for member in get_match_group(group_id).get("members") or []:
        if not isinstance(member, Mapping):
            continue
        start = member.get("logical_start_sec")
        end = member.get("logical_end_sec")
        source_id = str(member.get("source_match_id") or "")
        if not isinstance(start, (int, float)) or isinstance(start, bool):
            continue
        if not isinstance(end, (int, float)) or isinstance(end, bool):
            continue
        if source_id and math.isfinite(float(start)) and math.isfinite(float(end)) and float(start) <= time_sec <= float(end):
            return source_id, time_sec - float(start)
    return None
