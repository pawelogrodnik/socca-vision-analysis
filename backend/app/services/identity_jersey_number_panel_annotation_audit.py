from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import shutil
from typing import Any

from app.services.identity_jersey_number_common import (
    canonical_digest,
    normalize_jersey_number_annotation,
    normalize_normalized_bbox,
)
from app.services.identity_jersey_number_dataset import (
    identity_jersey_number_dataset_digest,
)
from app.services.identity_jersey_number_panel_audit import (
    build_panel_experiment_selection,
    normalize_panel_experiment_selection,
)


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_panel_annotation_audit"
ALGORITHM_VERSION = "1.0.0"
MANIFEST_FILENAME = "identity_jersey_number_panel_annotation_audit.json"
REVIEWED_FILENAME = "identity_jersey_number_panel_annotation_audit_reviewed.json"
INDEX_FILENAME = "index.html"


def prepare_panel_annotation_audit(
    dataset_doc: dict[str, Any],
    *,
    output_root: Path,
    selection_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a bounded, resumable panel-box audit from the canonical selection."""
    selection = normalize_panel_experiment_selection(
        selection_doc or build_panel_experiment_selection(dataset_doc)
    )
    selected_keys = set(selection["sample_keys"])
    samples = {
        str(row.get("sample_key") or ""): row
        for row in dataset_doc.get("samples") or []
        if isinstance(row, dict) and row.get("sample_key")
    }
    missing_keys = sorted(selected_keys - samples.keys())
    if missing_keys:
        raise ValueError(
            f"panel selection references {len(missing_keys)} missing dataset samples"
        )

    output_root.mkdir(parents=True, exist_ok=True)
    image_root = output_root / "images"
    image_root.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for index, sample_key in enumerate(selection["sample_keys"], start=1):
        sample = samples[sample_key]
        jersey_annotation = normalize_jersey_number_annotation(
            sample,
            allow_missing=False,
        )
        image_filename = f"{index:03d}-{_safe_filename(sample_key)}.jpg"
        copied = _copy_sample_artifact(sample, image_root / image_filename)
        items.append(
            {
                "audit_index": index,
                "sample_key": sample_key,
                "anchor_crop_id": sample.get("anchor_crop_id"),
                "source_match_key": sample.get("source_match_key"),
                "source_video_key": sample.get("source_video_key"),
                "frame": int(sample.get("frame") or 0),
                "team_label": sample.get("team_label"),
                "jersey_number_state": jersey_annotation["jersey_number_state"],
                "jersey_number": jersey_annotation["jersey_number"],
                "visibility_episode_id": sample.get("visibility_episode_id"),
                "image_filename": f"images/{image_filename}" if copied else None,
                "image_available": copied,
                "existing_number_panel_bbox_normalized": sample.get(
                    "number_panel_bbox_normalized"
                ),
                "manual_review": {
                    "status": "pending",
                    "number_panel_bbox_normalized": None,
                    "reviewed_at": None,
                },
            }
        )

    audit_contract = {
        "schema_version": SCHEMA_VERSION,
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
        },
        "dataset_digest": str(dataset_doc.get("dataset_digest") or ""),
        "selection_digest": selection["selection_digest"],
        "items": [
            {
                key: item.get(key)
                for key in (
                    "audit_index",
                    "sample_key",
                    "anchor_crop_id",
                    "source_match_key",
                    "source_video_key",
                    "frame",
                    "jersey_number_state",
                    "jersey_number",
                    "visibility_episode_id",
                    "image_filename",
                    "image_available",
                    "existing_number_panel_bbox_normalized",
                )
            }
            for item in items
        ],
    }
    manifest = {
        **audit_contract,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "mode": "operator_panel_box_audit",
        "audit_digest": canonical_digest(audit_contract),
        "panel_experiment_selection": selection,
        "summary": {
            "selected_samples": len(items),
            "available_images": sum(bool(item["image_available"]) for item in items),
            "confirmed_number_samples": sum(
                item["jersey_number_state"] == "number_confirmed" for item in items
            ),
            "negative_samples": sum(
                item["jersey_number_state"] in {"number_absent", "number_unreadable"}
                for item in items
            ),
        },
        "items": items,
    }
    (output_root / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_root / INDEX_FILENAME).write_text(
        _render_audit_html(manifest),
        encoding="utf-8",
    )
    return manifest


def apply_panel_annotation_audit(
    dataset_doc: dict[str, Any],
    reviewed_doc: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Apply operator-drawn panel boxes without changing jersey-number labels."""
    expected_dataset_digest = str(dataset_doc.get("dataset_digest") or "")
    if str(reviewed_doc.get("dataset_digest") or "") != expected_dataset_digest:
        raise ValueError("reviewed panel audit dataset digest mismatch")

    audit_contract = {
        "schema_version": reviewed_doc.get("schema_version"),
        "algorithm": reviewed_doc.get("algorithm"),
        "dataset_digest": reviewed_doc.get("dataset_digest"),
        "selection_digest": reviewed_doc.get("selection_digest"),
        "items": [
            {
                key: item.get(key)
                for key in (
                    "audit_index",
                    "sample_key",
                    "anchor_crop_id",
                    "source_match_key",
                    "source_video_key",
                    "frame",
                    "jersey_number_state",
                    "jersey_number",
                    "visibility_episode_id",
                    "image_filename",
                    "image_available",
                    "existing_number_panel_bbox_normalized",
                )
            }
            for item in reviewed_doc.get("items") or []
            if isinstance(item, dict)
        ],
    }
    if canonical_digest(audit_contract) != str(reviewed_doc.get("audit_digest") or ""):
        raise ValueError("reviewed panel audit contract digest mismatch")

    decisions: dict[str, dict[str, Any]] = {}
    duplicate_keys: set[str] = set()
    for item in reviewed_doc.get("items") or []:
        if not isinstance(item, dict) or not item.get("sample_key"):
            continue
        sample_key = str(item["sample_key"])
        if sample_key in decisions:
            duplicate_keys.add(sample_key)
        decisions[sample_key] = item
    if duplicate_keys:
        raise ValueError("reviewed panel audit contains duplicate sample decisions")

    result = deepcopy(dataset_doc)
    applied = 0
    skipped = 0
    pending = 0
    for sample in result.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        item = decisions.get(str(sample.get("sample_key") or ""))
        if item is None:
            continue
        review = item.get("manual_review") or {}
        status = str(review.get("status") or "pending")
        if status == "panel_confirmed":
            sample["number_panel_bbox_normalized"] = normalize_normalized_bbox(
                review.get("number_panel_bbox_normalized"),
                field_name="number_panel_bbox_normalized",
            )
            applied += 1
        elif status == "skipped":
            skipped += 1
        elif status == "pending":
            pending += 1
        else:
            raise ValueError(f"Unsupported panel audit decision status: {status}")

    new_digest = identity_jersey_number_dataset_digest(result.get("samples") or [])
    result["generated_at"] = generated_at or datetime.now(timezone.utc).isoformat()
    result["dataset_digest"] = new_digest
    result["dataset_version"] = f"jersey-number-dataset:v3:{new_digest}"
    result["panel_annotation_import"] = {
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
        },
        "source_dataset_digest": expected_dataset_digest,
        "audit_digest": reviewed_doc.get("audit_digest"),
        "reviewed_document_digest": canonical_digest(reviewed_doc),
        "selection_digest": reviewed_doc.get("selection_digest"),
        "applied": applied,
        "skipped": skipped,
        "pending": pending,
    }
    summary = dict(result.get("summary") or {})
    summary["number_panel_bbox_samples"] = sum(
        isinstance(row, dict) and row.get("number_panel_bbox_normalized") is not None
        for row in result.get("samples") or []
    )
    result["summary"] = summary
    return result


def _copy_sample_artifact(sample: dict[str, Any], destination: Path) -> bool:
    root_value = str(sample.get("artifact_root") or "").strip()
    artifact_value = str(sample.get("artifact") or "").strip()
    if not root_value or not artifact_value:
        return False
    root = Path(root_value).expanduser().resolve()
    source = (root / artifact_value).resolve()
    try:
        source.relative_to(root)
    except ValueError:
        return False
    if not source.is_file():
        return False
    shutil.copy2(source, destination)
    return True


def _safe_filename(value: str) -> str:
    safe = "".join(character if character.isalnum() else "-" for character in value)
    return safe[:72].strip("-") or "sample"


def _render_audit_html(manifest: dict[str, Any]) -> str:
    payload = json.dumps(manifest, ensure_ascii=True).replace("</", "<\\/")
    title = html.escape("J8.3 Jersey Number Panel Audit")
    return f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #08111f; color: #eef5ff; }}
    header {{ position: sticky; top: 0; z-index: 4; display: flex; gap: 16px;
      align-items: center; padding: 14px 20px; background: #0d1a2d; border-bottom: 1px solid #2b405f; }}
    header h1 {{ margin: 0; font-size: 20px; }}
    .progress {{ flex: 1; height: 8px; background: #263650; }}
    .progress > div {{ height: 100%; background: #28c76f; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 20px; }}
    .meta {{ color: #9eb0c9; margin-bottom: 12px; }}
    .instruction {{ padding: 12px; background: #13233b; border-left: 4px solid #39bdf8; }}
    .zoom-tools {{ display: flex; align-items: center; gap: 8px; margin-top: 16px; }}
    .zoom-tools output {{ min-width: 68px; text-align: center; color: #c8d7ec; }}
    .canvas-wrap {{ margin: 10px 0 16px; height: 68vh; min-height: 520px;
      display: grid; place-items: center; background: #020817;
      border: 1px solid #2a3c59; overflow: auto; }}
    canvas {{ max-width: none; max-height: none; image-rendering: auto;
      cursor: crosshair; touch-action: none; }}
    .buttons {{ display: flex; flex-wrap: wrap; gap: 10px; }}
    button {{ min-height: 44px; padding: 0 16px; border: 1px solid #456080;
      background: #18304f; color: white; font-weight: 700; cursor: pointer; }}
    button.primary {{ background: #168a4b; border-color: #2ad477; }}
    button.warn {{ background: #76520d; border-color: #d4a22a; }}
    button:disabled {{ opacity: .4; cursor: not-allowed; }}
    .status {{ margin: 12px 0; font-weight: 700; }}
    details {{ margin-top: 18px; color: #9eb0c9; }}
    kbd {{ background: #27364d; border: 1px solid #526784; padding: 2px 6px; }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="progress"><div id="progressBar"></div></div>
    <strong id="progressText"></strong>
    <button id="downloadTop">Finish audit</button>
  </header>
  <main>
    <div class="meta" id="meta"></div>
    <div class="instruction" id="instruction"></div>
    <div class="zoom-tools">
      <button id="zoomOut" title="Zoom out">−</button>
      <output id="zoomValue">100%</output>
      <button id="zoomIn" title="Zoom in">+</button>
      <button id="zoomFit">Fit crop</button>
    </div>
    <div class="canvas-wrap"><canvas id="canvas"></canvas></div>
    <div class="status" id="status"></div>
    <div class="buttons">
      <button id="previous">Previous</button>
      <button id="confirm" class="primary">Save panel</button>
      <button id="suggested" class="primary">Save suggested jersey panel</button>
      <button id="clear">Clear box</button>
      <button id="skip" class="warn">Skip / not sure</button>
      <button id="next">Next</button>
    </div>
    <details>
      <summary>Short instructions</summary>
      <p>Draw one tight rectangle around the visible number panel. For a confirmed
      number, include only the digits and a small margin. For number absent or unreadable,
      use <strong>Save suggested jersey panel</strong> only when the back/bib is clearly
      visible without digits. If the player, back or expected panel area is obscured,
      choose Skip. Decisions save automatically.</p>
      <p><kbd>Left/Right</kbd> navigation, <kbd>S</kbd> save, <kbd>K</kbd> skip.</p>
    </details>
  </main>
  <script>
    const audit = {payload};
    const storageKey = "panel-audit:" + audit.audit_digest;
    const stored = JSON.parse(localStorage.getItem(storageKey) || "{{}}");
    let index = Math.max(0, Math.min(audit.items.length - 1, stored.index || 0));
    let decisions = stored.decisions || {{}};
    let image = new Image();
    let dragStart = null;
    let draftBox = null;
    let zoom = Number(stored.zoom) || 1;
    const defaultPanelBox = [0.35, 0.28, 0.65, 0.45];
    const canvas = document.getElementById("canvas");
    const context = canvas.getContext("2d");
    const canvasWrap = document.querySelector(".canvas-wrap");

    function saveLocal() {{
      localStorage.setItem(storageKey, JSON.stringify({{index, decisions, zoom}}));
    }}
    function clampZoom(value) {{
      return Math.max(1, Math.min(24, value));
    }}
    function fitZoom() {{
      if (!image.naturalWidth || !image.naturalHeight) return 1;
      const availableWidth = Math.max(320, canvasWrap.clientWidth - 48);
      const availableHeight = Math.max(320, canvasWrap.clientHeight - 48);
      return clampZoom(Math.min(
        availableWidth / image.naturalWidth,
        availableHeight / image.naturalHeight
      ));
    }}
    function applyZoom() {{
      if (!image.naturalWidth || !image.naturalHeight) return;
      canvas.style.width = `${{Math.round(image.naturalWidth * zoom)}}px`;
      canvas.style.height = `${{Math.round(image.naturalHeight * zoom)}}px`;
      document.getElementById("zoomValue").textContent = `${{Math.round(zoom * 100)}}%`;
      saveLocal();
    }}
    function current() {{ return audit.items[index]; }}
    function decision(item) {{
      return decisions[item.sample_key] || item.manual_review || {{
        status: "pending", number_panel_bbox_normalized: null, reviewed_at: null
      }};
    }}
    function completedCount() {{
      return audit.items.filter(item => decision(item).status !== "pending").length;
    }}
    function render() {{
      const item = current();
      const review = decision(item);
      draftBox = review.number_panel_bbox_normalized;
      document.getElementById("meta").textContent =
        `#${{item.audit_index}} / ${{audit.items.length}} | frame ${{item.frame}} | ` +
        `team ${{item.team_label}} | state ${{item.jersey_number_state}} | ` +
        `number ${{item.jersey_number || "-"}}`;
      document.getElementById("instruction").textContent =
        item.jersey_number_state === "number_confirmed"
          ? "Draw tightly around the visible digits. Do not infer a number from player identity."
          : "Draw the stable jersey panel where digits would be visible. If it is not safely visible, skip.";
      document.getElementById("status").textContent =
        review.status === "panel_confirmed" ? "Panel saved" :
        review.status === "skipped" ? "Skipped / unresolved" : "Pending";
      const done = completedCount();
      document.getElementById("progressText").textContent = `${{done}}/${{audit.items.length}}`;
      document.getElementById("progressBar").style.width =
        `${{audit.items.length ? 100 * done / audit.items.length : 0}}%`;
      document.getElementById("previous").disabled = index === 0;
      document.getElementById("next").disabled = index >= audit.items.length - 1;
      document.getElementById("confirm").disabled = !draftBox || !item.image_available;
      const canUseSuggestedPanel = item.image_available &&
        item.jersey_number_state !== "number_confirmed";
      document.getElementById("suggested").hidden =
        item.jersey_number_state === "number_confirmed";
      document.getElementById("suggested").disabled = !canUseSuggestedPanel;
      if (!item.image_filename) {{
        canvas.width = 800; canvas.height = 520;
        context.fillStyle = "#020817"; context.fillRect(0, 0, canvas.width, canvas.height);
        context.fillStyle = "#ef6b73"; context.font = "24px system-ui";
        context.fillText("Source crop unavailable - choose Skip", 160, 260);
        return;
      }}
      image = new Image();
      image.onload = () => {{
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        zoom = fitZoom();
        applyZoom();
        draw();
      }};
      image.src = item.image_filename;
      saveLocal();
    }}
    function draw() {{
      if (!image.complete || !image.naturalWidth) return;
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      if (!draftBox) return;
      const [x1, y1, x2, y2] = draftBox;
      context.strokeStyle = "#ffd028";
      context.lineWidth = Math.max(2, canvas.width / 180);
      context.strokeRect(x1 * canvas.width, y1 * canvas.height,
        (x2 - x1) * canvas.width, (y2 - y1) * canvas.height);
      context.fillStyle = "rgba(255,208,40,.13)";
      context.fillRect(x1 * canvas.width, y1 * canvas.height,
        (x2 - x1) * canvas.width, (y2 - y1) * canvas.height);
      document.getElementById("confirm").disabled = false;
    }}
    function point(event) {{
      const rect = canvas.getBoundingClientRect();
      return [
        Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
        Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))
      ];
    }}
    canvas.addEventListener("pointerdown", event => {{
      if (!current().image_available) return;
      dragStart = point(event);
      canvas.setPointerCapture(event.pointerId);
    }});
    canvas.addEventListener("pointermove", event => {{
      if (!dragStart) return;
      const end = point(event);
      draftBox = [
        Math.min(dragStart[0], end[0]), Math.min(dragStart[1], end[1]),
        Math.max(dragStart[0], end[0]), Math.max(dragStart[1], end[1])
      ];
      draw();
    }});
    canvas.addEventListener("pointerup", event => {{
      if (!dragStart) return;
      const end = point(event);
      draftBox = [
        Math.min(dragStart[0], end[0]), Math.min(dragStart[1], end[1]),
        Math.max(dragStart[0], end[0]), Math.max(dragStart[1], end[1])
      ];
      if (draftBox[2] - draftBox[0] < .01 || draftBox[3] - draftBox[1] < .01) {{
        draftBox = null;
      }}
      dragStart = null;
      draw();
    }});
    function move(delta) {{
      index = Math.max(0, Math.min(audit.items.length - 1, index + delta));
      render();
    }}
    document.getElementById("previous").onclick = () => move(-1);
    document.getElementById("next").onclick = () => move(1);
    document.getElementById("zoomOut").onclick = () => {{
      zoom = clampZoom(zoom / 1.35); applyZoom();
    }};
    document.getElementById("zoomIn").onclick = () => {{
      zoom = clampZoom(zoom * 1.35); applyZoom();
    }};
    document.getElementById("zoomFit").onclick = () => {{
      zoom = fitZoom(); applyZoom();
    }};
    document.getElementById("clear").onclick = () => {{ draftBox = null; draw(); }};
    function savePanel(box, source) {{
      if (!box || !current().image_available) return;
      decisions[current().sample_key] = {{
        status: "panel_confirmed",
        number_panel_bbox_normalized: box.map(value => Number(value.toFixed(6))),
        panel_source: source,
        reviewed_at: new Date().toISOString()
      }};
      saveLocal(); move(1);
    }}
    document.getElementById("confirm").onclick = () => {{
      savePanel(draftBox, "operator_drawn");
    }};
    document.getElementById("suggested").onclick = () => {{
      const item = current();
      const existing = item.existing_number_panel_bbox_normalized;
      const suggested = Array.isArray(existing) && existing.length === 4
        ? existing : defaultPanelBox;
      savePanel(suggested, "operator_suggested");
    }};
    document.getElementById("skip").onclick = () => {{
      decisions[current().sample_key] = {{
        status: "skipped", number_panel_bbox_normalized: null,
        reviewed_at: new Date().toISOString()
      }};
      saveLocal(); move(1);
    }};
    function download() {{
      const reviewed = JSON.parse(JSON.stringify(audit));
      reviewed.reviewed_at = new Date().toISOString();
      reviewed.items.forEach(item => {{
        item.manual_review = decisions[item.sample_key] || item.manual_review;
      }});
      reviewed.summary.reviewed = completedCount();
      reviewed.summary.confirmed_panels = reviewed.items.filter(
        item => item.manual_review.status === "panel_confirmed").length;
      reviewed.summary.skipped = reviewed.items.filter(
        item => item.manual_review.status === "skipped").length;
      const blob = new Blob([JSON.stringify(reviewed, null, 2) + "\\n"],
        {{type: "application/json"}});
      const anchor = document.createElement("a");
      anchor.href = URL.createObjectURL(blob);
      anchor.download = "{REVIEWED_FILENAME}";
      anchor.click();
      URL.revokeObjectURL(anchor.href);
    }}
    document.getElementById("downloadTop").onclick = download;
    document.addEventListener("keydown", event => {{
      if (event.key === "ArrowLeft") move(-1);
      if (event.key === "ArrowRight") move(1);
      if (event.key.toLowerCase() === "s") document.getElementById("confirm").click();
      if (event.key.toLowerCase() === "k") document.getElementById("skip").click();
    }});
    render();
  </script>
</body>
</html>
"""
