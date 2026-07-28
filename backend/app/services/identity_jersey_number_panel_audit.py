from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_jersey_number_common import normalize_jersey_number_annotation
from app.services.identity_jersey_number_common import normalize_normalized_bbox
from app.services.identity_jersey_number_common import normalize_safe_relative_artifact_path


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_jersey_number_panel_audit"
ALGORITHM_VERSION = "1.1.0"
PANEL_RESIZE_WIDTH = 96
PANEL_RESIZE_HEIGHT = 64
MONTAGE_FILENAME = "number_panel_montage.jpg"
READINESS_FILENAME = "number_panel_dataset_readiness.json"
SELECTION_FILENAME = "panel_experiment_selection.json"
APPROVAL_FILENAME = "number_panel_montage_approval.json"
MONTAGE_REVIEW_FILENAME = "number_panel_montage_review.html"
FINDINGS_FILENAME = "J8_3_PANEL_READINESS_FINDINGS.md"
SELECTION_VERSION = "panel-experiment-selection-v1"
MIN_READABLE_CROPS = 50
MIN_READABLE_EPISODES = 20
MIN_NEGATIVES = 30
MIN_MEDIAN_DIGIT_HEIGHT = 8.0
# Kept only for compatibility with the original fixture.  Real-number coverage
# must come from a confirmed semantic label, never from a frame number.
LEGACY_REAL10_FRAMES = frozenset({3509, 3510, 3512})
INVALID_SELECTED_STATUSES = frozenset(
    {
        "missing_panel_bbox",
        "missing_panel_artifact",
        "missing_source_artifact",
        "corrupt_source_artifact",
        "empty_panel_crop",
        "invalid_bbox",
        "stale_panel_definition",
    }
)


def audit_identity_jersey_number_panels(
    dataset_doc: dict[str, Any],
    *,
    output_root: Path,
    generated_at: str | None = None,
    selection_doc: dict[str, Any] | None = None,
    approval_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    samples = [dict(row) for row in dataset_doc.get("samples") or [] if isinstance(row, dict)]
    ordered = sorted(
        samples,
        key=lambda row: (
            str(row.get("source_match_key") or ""),
            str(row.get("source_video_key") or ""),
            int(row.get("frame") or 0),
            str(row.get("anchor_crop_id") or ""),
        ),
    )
    selection = normalize_panel_experiment_selection(
        selection_doc or build_panel_experiment_selection(dataset_doc)
    )
    selected_keys = set(selection["sample_keys"])
    rows: list[dict[str, Any]] = []
    for sample in ordered:
        sample_key = str(sample.get("sample_key") or "")
        if sample_key not in selected_keys:
            rows.append(_unselected_row(sample))
            continue
        try:
            rows.append(_audit_row(sample))
        except ValueError as exc:
            rows.append(_invalid_row(sample, str(exc)))

    output_root.mkdir(parents=True, exist_ok=True)
    montage_path = output_root / MONTAGE_FILENAME
    selected_rows = [row for row in rows if row["status"] != "not_selected_for_panel_experiment"]
    _write_montage(selected_rows, montage_path)
    montage_sha256 = _file_sha256(montage_path)
    summary = _summary(rows, dataset_doc)
    approval = validate_montage_approval(
        approval_doc,
        montage_sha256=montage_sha256,
        dataset_digest=str(dataset_doc.get("dataset_digest") or ""),
        selection_digest=selection["selection_digest"],
    )
    digit_height_median = summary["estimated_digit_height_px"]["median"]
    machine_gates = {
        "selected_sample_nonempty": summary["selected_samples"] > 0,
        "selected_invalid_zero": summary["selected_invalid_samples"] == 0,
        "audited_panel_coverage_complete": summary["audited_panel_coverage"] == 1.0,
        "readable_panel_crop_minimum": summary["readable_confirmed_panels"] >= MIN_READABLE_CROPS,
        "readable_visibility_episode_minimum": summary["readable_visibility_episodes"] >= MIN_READABLE_EPISODES,
        "negative_crop_minimum": (
            summary["number_absent_panels"] + summary["number_unreadable_panels"]
        )
        >= MIN_NEGATIVES,
        "median_digit_height_minimum": (
            digit_height_median is not None
            and float(digit_height_median) >= MIN_MEDIAN_DIGIT_HEIGHT
        ),
        "real10_panel_minimum": summary["real10_panels_found"] >= 1,
    }
    machine_ready = all(machine_gates.values())
    human_approved = bool(approval["valid"] and approval["status"] == "approved")
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "mode": "shadow_panel_readiness_audit",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": {
                "panel_resize_width": PANEL_RESIZE_WIDTH,
                "panel_resize_height": PANEL_RESIZE_HEIGHT,
                "minimum_readable_crops": MIN_READABLE_CROPS,
                "minimum_readable_visibility_episodes": MIN_READABLE_EPISODES,
                "minimum_negative_crops": MIN_NEGATIVES,
                "minimum_median_digit_height_px": MIN_MEDIAN_DIGIT_HEIGHT,
            },
        },
        "source": {
            "dataset_digest": dataset_doc.get("dataset_digest"),
            "dataset_version": dataset_doc.get("dataset_version"),
            "dataset_summary_digest": canonical_digest(dataset_doc.get("summary") or {}),
            "samples": len(ordered),
        },
        "panel_experiment_selection": selection,
        "montage": {
            "filename": MONTAGE_FILENAME,
            "sha256": montage_sha256,
            "human_approval": approval,
        },
        "outputs": {
            "number_panel_dataset_readiness": READINESS_FILENAME,
            "number_panel_montage": MONTAGE_FILENAME,
            "number_panel_montage_approval": APPROVAL_FILENAME,
            "number_panel_montage_review": MONTAGE_REVIEW_FILENAME,
            "panel_experiment_selection": SELECTION_FILENAME,
            "findings": FINDINGS_FILENAME,
        },
        "summary": summary,
        "gates": {
            **machine_gates,
            "machine_ready": machine_ready,
            "manual_panel_audit_required": True,
            "human_montage_approval_valid": human_approved,
        },
        "samples": rows,
    }
    if not machine_ready:
        report["status"] = "insufficient_panel_readiness"
    elif human_approved:
        report["status"] = "ready_for_panel_digit_experiment"
    elif approval["valid"] and approval["status"] == "rejected":
        report["status"] = "human_review_rejected"
    else:
        report["status"] = "machine_ready_waiting_for_human_review"
    report["final_decision"] = panel_readiness_final_decision(report)
    return report


def build_panel_experiment_selection(dataset_doc: dict[str, Any]) -> dict[str, Any]:
    """Select deterministic high-value panels; never make all dataset noise blocking."""
    samples = [row for row in dataset_doc.get("samples") or [] if isinstance(row, dict)]
    normalized: list[tuple[dict[str, Any], dict[str, str | None]]] = []
    for sample in samples:
        sample_key = str(sample.get("sample_key") or "")
        if not sample_key:
            continue
        # Discovery-source rows reserve the jersey fields with null values before
        # an operator has reviewed them.  They are not negative examples: a
        # negative requires an explicit "absent" or "unreadable" decision.
        has_explicit_annotation = any(
            sample.get(field) not in (None, "")
            for field in ("jersey_number_state", "label_state", "number")
        )
        if not has_explicit_annotation:
            continue
        try:
            annotation = normalize_jersey_number_annotation(sample, allow_missing=False)
        except ValueError:
            continue
        normalized.append((sample, annotation))

    def sort_key(item: tuple[dict[str, Any], dict[str, str | None]]) -> tuple[Any, ...]:
        sample, annotation = item
        state = annotation["jersey_number_state"]
        state_order = {"number_confirmed": 0, "number_absent": 1, "number_unreadable": 2}
        return (
            0 if str(annotation.get("jersey_number") or "") == "10" else 1,
            state_order.get(str(state), 9),
            str(sample.get("visibility_episode_id") or ""),
            int(sample.get("frame") or 0),
            str(sample.get("sample_key") or ""),
        )

    ordered = sorted(normalized, key=sort_key)
    selected_set: set[str] = set()

    def choose_diverse(
        rows: list[tuple[dict[str, Any], dict[str, str | None]]],
        *,
        limit: int,
    ) -> list[str]:
        chosen: list[str] = []
        chosen_set: set[str] = set()
        episodes: set[str] = set()
        for sample, _ in rows:
            if len(chosen) >= limit:
                break
            episode = str(sample.get("visibility_episode_id") or "")
            if episode and episode in episodes:
                continue
            key = str(sample["sample_key"])
            chosen.append(key)
            chosen_set.add(key)
            if episode:
                episodes.add(episode)
        for sample, _ in rows:
            if len(chosen) >= limit:
                break
            key = str(sample["sample_key"])
            if key in chosen_set:
                continue
            chosen.append(key)
            chosen_set.add(key)
        return chosen

    confirmed = [item for item in ordered if item[1]["jersey_number_state"] == "number_confirmed"]
    absent = [item for item in ordered if item[1]["jersey_number_state"] == "number_absent"]
    unreadable = [
        item for item in ordered if item[1]["jersey_number_state"] == "number_unreadable"
    ]
    negatives: list[tuple[dict[str, Any], dict[str, str | None]]] = []
    for index in range(max(len(absent), len(unreadable))):
        if index < len(absent):
            negatives.append(absent[index])
        if index < len(unreadable):
            negatives.append(unreadable[index])

    selected_set.update(choose_diverse(confirmed, limit=MIN_READABLE_CROPS))
    selected_set.update(choose_diverse(negatives, limit=MIN_NEGATIVES))
    selection_keys = sorted(selected_set)
    selection_digest = canonical_digest(
        {"selection_version": SELECTION_VERSION, "sample_keys": selection_keys}
    )
    return {
        "selection_version": SELECTION_VERSION,
        "sample_keys": selection_keys,
        "selection_digest": selection_digest,
    }


def normalize_panel_experiment_selection(value: dict[str, Any]) -> dict[str, Any]:
    version = str(value.get("selection_version") or SELECTION_VERSION)
    keys = sorted(
        {
            str(key).strip()
            for key in value.get("sample_keys") or []
            if isinstance(key, str) and str(key).strip()
        }
    )
    expected = canonical_digest({"selection_version": version, "sample_keys": keys})
    supplied = value.get("selection_digest")
    if supplied not in (None, expected):
        raise ValueError("panel experiment selection digest mismatch")
    return {
        "selection_version": version,
        "sample_keys": keys,
        "selection_digest": expected,
    }


def validate_montage_approval(
    value: dict[str, Any] | None,
    *,
    montage_sha256: str,
    dataset_digest: str,
    selection_digest: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"valid": False, "status": None, "reasons": ["approval_missing"]}
    status = str(value.get("status") or "").strip().lower()
    reasons: list[str] = []
    if status not in {"approved", "rejected"}:
        reasons.append("approval_status_invalid")
    for key, expected in (
        ("montage_sha256", montage_sha256),
        ("dataset_digest", dataset_digest),
        ("selection_digest", selection_digest),
    ):
        if str(value.get(key) or "") != expected:
            reasons.append(f"{key}_mismatch")
    if not str(value.get("reviewer") or "").strip():
        reasons.append("reviewer_missing")
    if not str(value.get("reviewed_at") or "").strip():
        reasons.append("reviewed_at_missing")
    return {
        "valid": not reasons,
        "status": status or None,
        "reviewer": value.get("reviewer"),
        "reviewed_at": value.get("reviewed_at"),
        "notes": value.get("notes"),
        "reasons": reasons,
    }


def build_montage_approval_template(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "montage_sha256": report["montage"]["sha256"],
        "dataset_digest": str(report["source"].get("dataset_digest") or ""),
        "selection_digest": report["panel_experiment_selection"]["selection_digest"],
        "reviewer": None,
        "reviewed_at": None,
        "status": None,
        "notes": "",
    }


def render_montage_approval_page(report: dict[str, Any]) -> str:
    """Render a one-decision J8.3 review without exposing technical metadata."""
    summary = report["summary"]
    template = json.dumps(build_montage_approval_template(report), ensure_ascii=False)
    return f"""<!doctype html>
<html lang=\"pl\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>J8.3 - akceptacja paneli numerow</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    body {{ margin: 0; background: #07111f; color: #edf3ff; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 32px 20px 48px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    p {{ color: #b9c7dc; line-height: 1.5; max-width: 900px; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 20px 0; }}
    .pill {{ border: 1px solid #2a4569; border-radius: 999px; padding: 7px 11px; color: #dbe9ff; }}
    .montage {{ width: 100%; border: 1px solid #2a4569; background: #020817; display: block; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 22px; align-items: end; }}
    label {{ display: grid; gap: 6px; color: #b9c7dc; font-size: 14px; }}
    input, textarea {{ background: #0c1b30; color: #edf3ff; border: 1px solid #36587f; border-radius: 4px; padding: 10px; min-width: 220px; }}
    textarea {{ min-width: 320px; min-height: 42px; }}
    button {{ border: 0; border-radius: 4px; padding: 12px 16px; font-weight: 750; cursor: pointer; }}
    .approve {{ background: #2fcf62; color: #04140a; }}
    .reject {{ background: #f46969; color: #220606; }}
    #message {{ min-height: 22px; color: #b9c7dc; }}
  </style>
</head>
<body>
  <main>
    <h1>Ostatnia akceptacja paneli numerow</h1>
    <p>To nie jest kolejna annotacja. Sprawdz tylko, czy montage przedstawia sensowne ciasne wycinki numerow i negatywne przyklady, ktore oznaczyles. Akceptuj, gdy material ogolnie wyglada poprawnie; odrzuc tylko, gdy panelowy dataset jest wyraznie bledny.</p>
    <div class=\"summary\">
      <span class=\"pill\">Czytelne numery: {summary['readable_confirmed_panels']}</span>
      <span class=\"pill\">Negatywne: {summary['negative_crops']}</span>
      <span class=\"pill\">Epizody widocznosci: {summary['readable_visibility_episodes']}</span>
      <span class=\"pill\">Numer #10: {summary['real10_panels_found']}</span>
    </div>
    <img class=\"montage\" src=\"{MONTAGE_FILENAME}\" alt=\"Montaz paneli numerow koszulek\">
    <div class=\"actions\">
      <label>Osoba sprawdzajaca<input id=\"reviewer\" value=\"operator\" autocomplete=\"name\"></label>
      <label>Opcjonalna notatka<textarea id=\"notes\" placeholder=\"Np. panele czytelne, mozna przejsc dalej\"></textarea></label>
      <button class=\"approve\" onclick=\"downloadDecision('approved')\">Akceptuje montage</button>
      <button class=\"reject\" onclick=\"downloadDecision('rejected')\">Odrzucam montage</button>
    </div>
    <p id=\"message\"></p>
  </main>
  <script>
    const template = {template};
    function downloadDecision(status) {{
      const reviewer = document.getElementById('reviewer').value.trim() || 'operator';
      const notes = document.getElementById('notes').value.trim();
      const payload = {{ ...template, reviewer, notes, status, reviewed_at: new Date().toISOString() }};
      const blob = new Blob([JSON.stringify(payload, null, 2) + '\\n'], {{ type: 'application/json' }});
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = '{APPROVAL_FILENAME}';
      anchor.click();
      URL.revokeObjectURL(url);
      document.getElementById('message').textContent = 'Pobrano decyzje. Dolacz pobrany plik JSON w rozmowie.';
    }}
  </script>
</body>
</html>
"""


def panel_readiness_final_decision(report: dict[str, Any]) -> str:
    summary = report["summary"]
    if summary["selected_invalid_samples"] > 0 or summary["audited_panel_coverage"] < 1.0:
        return "FIX_PANEL_PIPELINE_FIRST"
    if summary["readable_confirmed_panels"] < MIN_READABLE_CROPS:
        return "AVAILABLE_DATA_NOT_SUFFICIENT"
    if summary["readable_visibility_episodes"] < MIN_READABLE_EPISODES:
        return "AVAILABLE_DATA_NOT_SUFFICIENT"
    if summary["number_absent_panels"] + summary["number_unreadable_panels"] < MIN_NEGATIVES:
        return "AVAILABLE_DATA_NOT_SUFFICIENT"
    if summary["real10_panels_found"] < 1:
        return "AVAILABLE_DATA_NOT_SUFFICIENT"
    median_height = summary["estimated_digit_height_px"]["median"]
    if median_height is None or float(median_height) < MIN_MEDIAN_DIGIT_HEIGHT:
        return "FIX_PANEL_PIPELINE_FIRST"
    if report["status"] == "ready_for_panel_digit_experiment":
        return "PROCEED_TO_J8_4_LATER"
    if report["status"] == "machine_ready_waiting_for_human_review":
        return "PENDING_HUMAN_APPROVAL"
    return "FIX_PANEL_PIPELINE_FIRST"


def _audit_row(sample: dict[str, Any]) -> dict[str, Any]:
    cv2, _ = _image_libs()
    annotation = normalize_jersey_number_annotation(sample, allow_missing=False)
    artifact_root = Path(str(sample.get("artifact_root") or ""))
    base_artifact = normalize_safe_relative_artifact_path(
        sample.get("artifact"),
        field_name="artifact",
    )
    panel_artifact = normalize_safe_relative_artifact_path(
        sample.get("number_panel_artifact"),
        field_name="number_panel_artifact",
    )
    panel_bbox = normalize_normalized_bbox(
        sample.get("number_panel_bbox_normalized"),
        field_name="number_panel_bbox_normalized",
    )
    row = {
        "sample_key": sample.get("sample_key"),
        "anchor_crop_id": sample.get("anchor_crop_id"),
        "source_match_key": sample.get("source_match_key"),
        "source_video_key": sample.get("source_video_key"),
        "candidate_subject_id": sample.get("candidate_subject_id"),
        "tracklet_id": sample.get("tracklet_id"),
        "visibility_episode_id": sample.get("visibility_episode_id"),
        "frame": int(sample.get("frame") or 0),
        "view": sample.get("view"),
        "jersey_number_state": annotation["jersey_number_state"],
        "jersey_number": annotation["jersey_number"],
        "label_state": annotation["jersey_number_state"],
        "number": annotation["jersey_number"],
        "digit_visibility": sample.get("digit_visibility"),
        "artifact": base_artifact,
        "number_panel_artifact": panel_artifact,
        "number_panel_bbox_normalized": panel_bbox,
        "panel_source_kind": (
            "explicit_panel_artifact"
            if panel_artifact
            else "deterministic_crop_from_artifact"
            if panel_bbox and base_artifact
            else "missing_panel_definition"
        ),
    }
    source_name = panel_artifact or base_artifact
    source_path = artifact_root / (source_name or "")
    row["panel_source_path"] = str(source_path) if source_name else None
    if panel_artifact is None and panel_bbox is None:
        return {
            **row,
            "status": "missing_panel_bbox",
            "panel_digest": None,
            "panel_width_px": None,
            "panel_height_px": None,
            "resized_panel_shape": None,
            "estimated_digit_height_px": None,
        }
    if panel_artifact is not None and not source_path.is_file():
        return {
            **row,
            "status": "missing_panel_artifact",
            "panel_digest": None,
            "panel_width_px": None,
            "panel_height_px": None,
            "resized_panel_shape": None,
            "estimated_digit_height_px": None,
        }
    if panel_artifact is None and (base_artifact is None or not source_path.is_file()):
        return {
            **row,
            "status": "missing_source_artifact",
            "panel_digest": None,
            "panel_width_px": None,
            "panel_height_px": None,
            "resized_panel_shape": None,
            "estimated_digit_height_px": None,
        }
    image = cv2.imread(str(source_path))
    if image is None:
        return {
            **row,
            "status": "corrupt_source_artifact",
            "panel_digest": None,
            "panel_width_px": None,
            "panel_height_px": None,
            "resized_panel_shape": None,
            "estimated_digit_height_px": None,
        }
    panel = image if panel_artifact is not None else _crop_panel(image, panel_bbox)
    if panel is None or panel.size == 0:
        return {
            **row,
            "status": "empty_panel_crop",
            "panel_digest": None,
            "panel_width_px": None,
            "panel_height_px": None,
            "resized_panel_shape": None,
            "estimated_digit_height_px": None,
        }
    resized = cv2.resize(panel, (PANEL_RESIZE_WIDTH, PANEL_RESIZE_HEIGHT), interpolation=cv2.INTER_AREA)
    return {
        **row,
        "status": "audited",
        "panel_digest": _image_digest(panel),
        "panel_width_px": int(panel.shape[1]),
        "panel_height_px": int(panel.shape[0]),
        "resized_panel_shape": [PANEL_RESIZE_HEIGHT, PANEL_RESIZE_WIDTH],
        "estimated_digit_height_px": _estimate_digit_height(resized),
    }


def _unselected_row(sample: dict[str, Any]) -> dict[str, Any]:
    try:
        annotation = normalize_jersey_number_annotation(sample, allow_missing=True)
    except ValueError:
        annotation = {}
    return {
        "sample_key": sample.get("sample_key"),
        "anchor_crop_id": sample.get("anchor_crop_id"),
        "source_match_key": sample.get("source_match_key"),
        "source_video_key": sample.get("source_video_key"),
        "candidate_subject_id": sample.get("candidate_subject_id"),
        "tracklet_id": sample.get("tracklet_id"),
        "visibility_episode_id": sample.get("visibility_episode_id"),
        "frame": int(sample.get("frame") or 0),
        "view": sample.get("view"),
        "jersey_number_state": annotation.get("jersey_number_state"),
        "jersey_number": annotation.get("jersey_number"),
        "label_state": annotation.get("jersey_number_state"),
        "number": annotation.get("jersey_number"),
        "status": "not_selected_for_panel_experiment",
        "panel_digest": None,
        "panel_width_px": None,
        "panel_height_px": None,
        "resized_panel_shape": None,
        "estimated_digit_height_px": None,
    }


def _invalid_row(sample: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        **_unselected_row(sample),
        "status": "invalid_bbox" if "bbox" in error.lower() else "stale_panel_definition",
        "error": error,
    }


def _crop_panel(image: Any, bbox: list[float] | None) -> Any:
    if bbox is None:
        return None
    _, np = _image_libs()
    height, width = image.shape[:2]
    x1 = max(0, min(width - 1, int(np.floor(bbox[0] * width))))
    y1 = max(0, min(height - 1, int(np.floor(bbox[1] * height))))
    x2 = max(x1 + 1, min(width, int(np.ceil(bbox[2] * width))))
    y2 = max(y1 + 1, min(height, int(np.ceil(bbox[3] * height))))
    if x2 <= x1 or y2 <= y1:
        return None
    return image[y1:y2, x1:x2].copy()


def _image_digest(image: Any) -> str:
    payload = hashlib.sha256()
    payload.update(str(tuple(image.shape)).encode("utf-8"))
    payload.update(image.tobytes())
    return payload.hexdigest()


def _estimate_digit_height(image: Any) -> float | None:
    cv2, _ = _image_libs()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[tuple[int, int, int, int]] = []
    area_floor = max(6, int(round(image.shape[0] * image.shape[1] * 0.003)))
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = width * height
        if area < area_floor or height < 4 or width < 2:
            continue
        if height > image.shape[0] * 0.95 or width > image.shape[1] * 0.95:
            continue
        boxes.append((x, y, width, height))
    if not boxes:
        return None
    top = min(box[1] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return round(float(bottom - top), 3)


def _summary(rows: list[dict[str, Any]], dataset_doc: dict[str, Any]) -> dict[str, Any]:
    selected = [row for row in rows if row["status"] != "not_selected_for_panel_experiment"]
    audited = [row for row in selected if row["status"] == "audited"]
    invalid = [row for row in selected if row["status"] in INVALID_SELECTED_STATUSES]
    readable = [
        row
        for row in audited
        if row.get("jersey_number_state") == "number_confirmed" and row.get("jersey_number")
    ]
    readable_full = [row for row in readable if row.get("digit_visibility") != "partial"]
    readable_partial = [
        row
        for row in readable
        if row.get("digit_visibility") == "partial"
    ]
    plain_shirt = [row for row in audited if row.get("jersey_number_state") == "number_absent"]
    unreadable = [row for row in audited if row.get("jersey_number_state") == "number_unreadable"]
    digits = Counter()
    for row in readable_full:
        for digit in str(row.get("number") or ""):
            digits[digit] += 1
    readable_episode_ids = {
        str(row.get("visibility_episode_id"))
        for row in readable_full
        if row.get("visibility_episode_id")
    }
    return {
        "total_samples": len(rows),
        "selected_samples": len(selected),
        "selected_audited_samples": len(audited),
        "selected_invalid_samples": len(invalid),
        "audited_panel_coverage": round(len(audited) / len(selected), 6) if selected else 0.0,
        "readable_confirmed_panels": len(readable_full),
        "number_absent_panels": len(plain_shirt),
        "number_unreadable_panels": len(unreadable),
        "real10_panels_found": sum(
            str(row.get("jersey_number") or "") == "10"
            # Older immutable fixture artifacts did not carry the semantic label.
            or (
                row.get("source_match_key") == "real10"
                and int(row.get("frame") or -1) in LEGACY_REAL10_FRAMES
            )
            for row in readable_full
        ),
        "total_panel_crops": len(audited),
        "readable_full_number_crops": len(readable_full),
        "partial_number_crops": len(readable_partial),
        "plain_shirt_crops": len(plain_shirt),
        "unreadable_crops": len(unreadable),
        "negative_crops": len(plain_shirt) + len(unreadable),
        "unique_visibility_episodes": len(
            {str(row.get("visibility_episode_id")) for row in audited if row.get("visibility_episode_id")}
        ),
        "readable_visibility_episodes": len(readable_episode_ids),
        "unique_tracklets": len({str(row.get("tracklet_id")) for row in audited if row.get("tracklet_id")}),
        "unique_subjects": len({str(row.get("candidate_subject_id")) for row in audited if row.get("candidate_subject_id")}),
        "counts_per_number": dict(sorted(Counter(str(row.get("number")) for row in readable_full).items())),
        "counts_per_digit": {digit: digits.get(digit, 0) for digit in [str(index) for index in range(10)]},
        "counts_per_view": dict(
            sorted(Counter(str(row.get("view") or "unknown") for row in audited).items())
        ),
        "panel_width_px": _distribution([float(row["panel_width_px"]) for row in audited if row.get("panel_width_px")]),
        "panel_height_px": _distribution([float(row["panel_height_px"]) for row in audited if row.get("panel_height_px")]),
        "estimated_digit_height_px": _distribution(
            [
                float(row["estimated_digit_height_px"])
                for row in readable_full
                if row.get("estimated_digit_height_px") is not None
            ]
        ),
        "missing_panel_bbox_count": sum(row["status"] == "missing_panel_bbox" for row in rows),
        "status_counts": dict(sorted(Counter(str(row["status"]) for row in rows).items())),
        "dataset_samples": (dataset_doc.get("summary") or {}).get("samples"),
    }


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "median": round(float(median(values)), 3),
        "max": round(max(values), 3),
    }


def render_panel_readiness_findings(report: dict[str, Any]) -> str:
    summary = report["summary"]
    approval = report["montage"]["human_approval"]
    selection = report["panel_experiment_selection"]
    source = report["source"]
    return "\n".join(
        [
            "# J8.3 Panel Readiness Findings",
            "",
            f"- Source dataset digest: `{source.get('dataset_digest') or 'missing'}`",
            f"- Selection version: `{selection['selection_version']}`",
            f"- Selection digest: `{selection['selection_digest']}`",
            f"- Selected samples: {summary['selected_samples']}",
            f"- Audited selected samples: {summary['selected_audited_samples']}",
            f"- Invalid selected samples: {summary['selected_invalid_samples']}",
            f"- Panel coverage: {summary['audited_panel_coverage']:.3f}",
            f"- Confirmed readable panels: {summary['readable_confirmed_panels']}",
            f"- Number absent panels: {summary['number_absent_panels']}",
            f"- Number unreadable panels: {summary['number_unreadable_panels']}",
            f"- Readable visibility episodes: {summary['readable_visibility_episodes']}",
            f"- Median confirmed digit height: {summary['estimated_digit_height_px']['median']}",
            f"- real10 panels found: {summary['real10_panels_found']}",
            f"- Montage SHA-256: `{report['montage']['sha256']}`",
            f"- Human montage decision: `{approval.get('status') or 'pending'}`",
            f"- Human approval valid: `{approval.get('valid')}`",
            f"- Runtime status: `{report['status']}`",
            "",
            f"## Final Decision: {report['final_decision']}",
            "",
        ]
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_montage(rows: list[dict[str, Any]], path: Path) -> None:
    cv2, np = _image_libs()
    cards = [_render_row(row) for row in rows] or [_placeholder_card("No panel samples")]
    columns = 2 if len(cards) > 1 else 1
    card_height, card_width = cards[0].shape[:2]
    rows_needed = int(np.ceil(len(cards) / columns))
    canvas = np.full((rows_needed * card_height, columns * card_width, 3), 18, dtype=np.uint8)
    for index, card in enumerate(cards):
        row = index // columns
        column = index % columns
        top = row * card_height
        left = column * card_width
        canvas[top : top + card_height, left : left + card_width] = card
    cv2.imwrite(str(path), canvas)


def _render_row(row: dict[str, Any]) -> Any:
    _, np = _image_libs()
    height = 260
    width = 900
    canvas = np.full((height, width, 3), 20, dtype=np.uint8)
    _draw_text(canvas, 18, 26, f"{row.get('anchor_crop_id')} | {row.get('label_state')} | {row.get('number') or '-'}")
    _draw_text(
        canvas,
        18,
        48,
        f"f{row.get('frame')} | tracklet {row.get('tracklet_id') or '-'} | episode {row.get('visibility_episode_id') or '-'}",
        scale=0.45,
    )
    _draw_text(
        canvas,
        18,
        68,
        f"status: {row.get('status')} | digit-h: {row.get('estimated_digit_height_px') or 'n/a'}",
        scale=0.45,
    )
    source = _render_source_preview(row)
    panel = _render_panel_preview(row)
    resized = _render_resized_preview(row)
    canvas[86:246, 18:258] = source
    canvas[86:246, 274:514] = panel
    canvas[86:246, 530:770] = resized
    _draw_text(canvas, 18, 84, "source", scale=0.45)
    _draw_text(canvas, 274, 84, "panel", scale=0.45)
    _draw_text(canvas, 530, 84, "panel 96x64", scale=0.45)
    return canvas


def _render_source_preview(row: dict[str, Any]) -> Any:
    cv2, np = _image_libs()
    preview = np.full((160, 240, 3), 36, dtype=np.uint8)
    path_text = row.get("panel_source_path")
    if not isinstance(path_text, str) or not Path(path_text).is_file():
        return _placeholder_into(preview, "missing source")
    image = cv2.imread(path_text)
    if image is None:
        return _placeholder_into(preview, "corrupt source")
    if row.get("number_panel_artifact") is None and row.get("number_panel_bbox_normalized") is not None:
        bbox = row["number_panel_bbox_normalized"]
        draw = image.copy()
        height, width = draw.shape[:2]
        x1 = int(round(float(bbox[0]) * width))
        y1 = int(round(float(bbox[1]) * height))
        x2 = int(round(float(bbox[2]) * width))
        y2 = int(round(float(bbox[3]) * height))
        cv2.rectangle(draw, (x1, y1), (x2, y2), (0, 200, 255), 2)
        image = draw
    return _fit_image(image, preview.shape[1], preview.shape[0])


def _render_panel_preview(row: dict[str, Any]) -> Any:
    _, np = _image_libs()
    preview = np.full((160, 240, 3), 36, dtype=np.uint8)
    image = _load_panel_image(row)
    return _fit_image(image, preview.shape[1], preview.shape[0]) if image is not None else _placeholder_into(preview, "missing panel")


def _render_resized_preview(row: dict[str, Any]) -> Any:
    cv2, np = _image_libs()
    preview = np.full((160, 240, 3), 36, dtype=np.uint8)
    image = _load_panel_image(row)
    if image is None:
        return _placeholder_into(preview, "missing panel")
    resized = cv2.resize(image, (PANEL_RESIZE_WIDTH, PANEL_RESIZE_HEIGHT), interpolation=cv2.INTER_AREA)
    return _fit_image(resized, preview.shape[1], preview.shape[0])


def _load_panel_image(row: dict[str, Any]) -> Any:
    cv2, _ = _image_libs()
    path_text = row.get("panel_source_path")
    if not isinstance(path_text, str) or not Path(path_text).is_file():
        return None
    image = cv2.imread(path_text)
    if image is None:
        return None
    if row.get("number_panel_artifact") is not None:
        return image
    return _crop_panel(image, row.get("number_panel_bbox_normalized"))


def _fit_image(image: Any, width: int, height: int) -> Any:
    cv2, np = _image_libs()
    canvas = np.full((height, width, 3), 36, dtype=np.uint8)
    scale = min(width / max(1, image.shape[1]), height / max(1, image.shape[0]))
    target_width = max(1, int(round(image.shape[1] * scale)))
    target_height = max(1, int(round(image.shape[0] * scale)))
    resized = cv2.resize(image, (target_width, target_height), interpolation=cv2.INTER_AREA)
    top = (height - target_height) // 2
    left = (width - target_width) // 2
    canvas[top : top + target_height, left : left + target_width] = resized
    return canvas


def _placeholder_card(text: str) -> Any:
    _, np = _image_libs()
    canvas = np.full((260, 900, 3), 20, dtype=np.uint8)
    return _placeholder_into(canvas, text)


def _placeholder_into(canvas: Any, text: str) -> Any:
    _draw_text(canvas, 18, max(24, canvas.shape[0] // 2), text)
    return canvas


def _draw_text(
    image: Any,
    x: int,
    y: int,
    text: str,
    *,
    scale: float = 0.55,
    color: tuple[int, int, int] = (230, 230, 230),
) -> None:
    cv2, _ = _image_libs()
    cv2.putText(
        image,
        text[:90],
        (x, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        1,
        cv2.LINE_AA,
    )


def _image_libs() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("OpenCV and numpy are required for jersey number panel audits") from exc
    return cv2, np
