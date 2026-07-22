from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import shutil
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_assignment_shadow import (
    build_identity_jersey_number_assignment_shadow,
)
from app.services.identity_jersey_number_consensus_shadow import (
    build_identity_jersey_number_consensus_shadow,
)
from app.services.identity_jersey_number_evidence_shadow import (
    build_identity_jersey_number_evidence_shadow,
)
from app.services.identity_jersey_number_propagation_shadow import (
    build_identity_jersey_number_propagation_shadow,
)
from app.services.identity_jersey_number_roster import (
    build_identity_jersey_number_roster_shadow,
)
from app.services.identity_roster_subject_review_shadow import (
    build_identity_roster_subject_review_shadow,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate N0-N5 jersey-number identity in shadow mode.")
    parser.add_argument("--match-config", type=Path, required=True)
    parser.add_argument("--anchor-crops", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--goldset", type=Path)
    parser.add_argument("--roster-anchor", type=Path)
    parser.add_argument("--candidate-identity", type=Path)
    parser.add_argument("--shadow-timeline", type=Path)
    parser.add_argument("--activate-n4-if-benchmark-passed", action="store_true")
    args = parser.parse_args()

    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    generated_at = datetime.now(timezone.utc).isoformat()
    match_doc = _load(args.match_config)
    anchor_doc = _load(args.anchor_crops)
    reference_doc = _load(args.reference)
    observations_doc = _load(args.observations) if args.observations else {}
    goldset_doc = _load(args.goldset) if args.goldset else {}

    roster_doc = build_identity_jersey_number_roster_shadow(
        match_doc,
        reference_doc=reference_doc,
        generated_at=generated_at,
    )
    evidence_documents = build_identity_jersey_number_evidence_shadow(
        anchor_doc,
        roster_doc,
        observations_doc=observations_doc,
        generated_at=generated_at,
    )
    evidence_doc = evidence_documents["identity_jersey_number_evidence_shadow"]
    audit_doc = evidence_documents["identity_jersey_number_audit"]
    _enrich_audit_cards(audit_doc, anchor_doc)
    consensus_documents = build_identity_jersey_number_consensus_shadow(
        evidence_doc,
        roster_doc,
        goldset_doc=goldset_doc,
        generated_at=generated_at,
    )
    consensus_doc = consensus_documents["identity_jersey_number_consensus_shadow"]
    number_report = consensus_documents["identity_jersey_number_report"]

    if args.roster_anchor:
        subject_review_documents = build_identity_roster_subject_review_shadow(
            _load(args.roster_anchor),
            anchor_doc,
            jersey_consensus_doc=consensus_doc,
            generated_at=generated_at,
        )
        subject_review_doc = subject_review_documents["identity_roster_subject_review_shadow"]
    else:
        subject_review_documents = {}
        subject_review_doc = {"cards": []}

    assignment_doc = build_identity_jersey_number_assignment_shadow(
        consensus_doc,
        subject_review_doc,
        number_report,
        activation_requested=args.activate_n4_if_benchmark_passed,
        generated_at=generated_at,
    )
    propagation_documents = {}
    if bool(args.candidate_identity) != bool(args.shadow_timeline):
        parser.error("--candidate-identity and --shadow-timeline must be supplied together")
    if args.candidate_identity and args.shadow_timeline:
        propagation_documents["identity_jersey_number_propagation_shadow"] = (
            build_identity_jersey_number_propagation_shadow(
                assignment_doc,
                evidence_doc,
                _load(args.candidate_identity),
                _load(args.shadow_timeline),
                subject_review_doc=subject_review_doc,
                generated_at=generated_at,
            )
        )
    _materialize_torso_crops(args.anchor_crops.resolve().parent, output, audit_doc)

    documents = {
        "identity_jersey_number_roster_shadow": roster_doc,
        **evidence_documents,
        **consensus_documents,
        **subject_review_documents,
        "identity_jersey_number_assignment_shadow": assignment_doc,
        **propagation_documents,
    }
    for name, document in documents.items():
        _write(output / f"{name}.json", document)
    (output / "index.html").write_text(_audit_html(audit_doc), encoding="utf-8")
    (output / "JERSEY_NUMBER_SHADOW_REPORT.md").write_text(
        _markdown(
            roster_doc,
            evidence_doc,
            consensus_doc,
            assignment_doc,
            propagation_documents.get("identity_jersey_number_propagation_shadow"),
        ),
        encoding="utf-8",
    )
    print(json.dumps({name: doc.get("summary") for name, doc in documents.items()}, indent=2))


def _materialize_torso_crops(source_root: Path, output_root: Path, audit_doc: dict[str, Any]) -> None:
    try:
        import cv2
    except ImportError:
        return
    crop_root = output_root / "jersey_number_crops"
    for card in audit_doc.get("cards") or []:
        source_relative = Path(str(card.get("artifact") or ""))
        if source_relative.is_absolute() or ".." in source_relative.parts:
            continue
        source = source_root / source_relative
        image = cv2.imread(str(source))
        if image is None:
            continue
        height, width = image.shape[:2]
        x1n, y1n, x2n, y2n = card.get("torso_roi_normalized") or [0.1, 0.02, 0.9, 0.68]
        x1, y1 = max(0, int(width * x1n)), max(0, int(height * y1n))
        x2, y2 = min(width, int(width * x2n)), min(height, int(height * y2n))
        torso = image[y1:y2, x1:x2]
        if torso.size == 0:
            continue
        target = crop_root / f"{card['evidence_key'].split(':')[-1]}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(target), torso)
        card["torso_artifact"] = str(target.relative_to(output_root))


def _enrich_audit_cards(audit_doc: dict[str, Any], anchor_doc: dict[str, Any]) -> None:
    selection_by_subject = {
        str(card.get("candidate_subject_id")): card.get("benchmark_selection") or {}
        for card in anchor_doc.get("cards") or []
        if isinstance(card, dict) and card.get("candidate_subject_id")
    }
    for card in audit_doc.get("cards") or []:
        selection = selection_by_subject.get(str(card.get("candidate_subject_id") or ""))
        if selection:
            card["benchmark_selection"] = selection


def _audit_html(audit_doc: dict[str, Any]) -> str:
    cards = []
    for card in audit_doc.get("cards") or []:
        artifact = html.escape(str(card.get("torso_artifact") or card.get("artifact") or ""))
        key = html.escape(str(card.get("anchor_crop_id") or ""))
        team = html.escape(str(card.get("team_label") or "U"))
        subject_id = html.escape(str(card.get("candidate_subject_id") or ""))
        tracklet_id = html.escape(str(card.get("tracklet_id") or ""))
        selection = card.get("benchmark_selection") or {}
        target_tracklets = ", ".join(str(value) for value in selection.get("target_tracklet_ids") or [])
        target_text = html.escape(target_tracklets or "n/a")
        current = card.get("current_evidence") or {}
        current_state = str(current.get("state") or "number_unreadable")
        current_number = html.escape(str(current.get("number") or ""))
        current_confidence = float(current.get("confidence") or 0.0)
        current_view = str(current.get("view") or "unknown")
        clean_checked = " checked" if current.get("clean_jersey_visible") else ""
        state_options = "".join(
            f'<option value="{value}"{" selected" if value == current_state else ""}>{label}</option>'
            for value, label in (
                ("number_unreadable", "Unreadable"),
                ("number_confirmed", "Number confirmed"),
                ("number_absent", "Number absent"),
                ("number_conflict", "Conflict"),
            )
        )
        view_options = "".join(
            f'<option value="{value}"{" selected" if value == current_view else ""}>{label}</option>'
            for value, label in (("unknown", "Unknown"), ("front", "Front"), ("back", "Back"))
        )
        cards.append(
            f"""<article class="card" data-key="{key}" data-team="{team}">
            <img src="{artifact}" alt="Torso crop {key}">
            <div class="meta"><strong>{key}</strong><span>frame {card.get('frame')} / team {team}</span></div>
            <div class="lineage"><span>subject {subject_id}</span><span>seed {tracklet_id}</span><span>N5 target {target_text}</span></div>
            <label>State<select class="state">{state_options}</select></label>
            <label>Number<input class="number" inputmode="numeric" maxlength="3" value="{current_number}"></label>
            <label>View<select class="view">{view_options}</select></label>
            <label>Confidence<input class="confidence" type="number" min="0" max="1" step="0.01" value="{current_confidence:.2f}"></label>
            <label class="check"><input class="clean" type="checkbox"{clean_checked}> Clean jersey surface is visible</label>
            </article>"""
        )
    teams = sorted({str(card.get("team_label") or "U") for card in audit_doc.get("cards") or []})
    team_options = "".join(f'<option value="{html.escape(team)}">Team {html.escape(team)}</option>' for team in teams)
    payload = json.dumps({"schema_version": "0.1.0", "observations": []}).replace("</", "<\\/")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Jersey number audit</title><style>
    body{{margin:0;background:#081120;color:#eef5ff;font:15px system-ui}}header{{position:sticky;top:0;z-index:2;background:#101c30;padding:16px 24px;display:flex;align-items:center;justify-content:space-between;gap:20px}}.tools{{display:flex;align-items:center;gap:10px}}button{{padding:10px 14px;background:#32b7eb;border:0;font-weight:700;cursor:pointer}}main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;padding:18px}}.card{{background:#101c30;border:1px solid #33445f;padding:10px}}.card[hidden]{{display:none}}img{{width:100%;height:300px;object-fit:contain;background:#020817}}.meta{{display:flex;justify-content:space-between;gap:8px;margin:8px 0}}.lineage{{display:grid;gap:3px;color:#9fb1ca;font:12px ui-monospace,monospace;overflow-wrap:anywhere}}label{{display:grid;gap:4px;margin-top:8px}}select,input{{background:#020817;color:white;border:1px solid #566883;padding:8px}}.check{{display:flex;align-items:center;gap:8px}}.check input{{width:18px;height:18px}}#count{{color:#9fb1ca}}
    </style></head><body><header><div><strong>Jersey number audit</strong><div>Mark only clear numbers. Use Number absent only when a clean jersey surface is visible.</div></div><div class="tools"><label>Team<select id="team"><option value="all">All</option>{team_options}</select></label><span id="count"></span><button id="export">Export decisions</button></div></header><main>{''.join(cards)}</main><script>
    const base={payload};
    const cards=[...document.querySelectorAll('.card')];
    const teamSelect=document.getElementById('team');
    if ([...teamSelect.options].some(option=>option.value==='A')) teamSelect.value='A';
    function filterCards(){{let visible=0;cards.forEach(card=>{{const show=teamSelect.value==='all'||card.dataset.team===teamSelect.value;card.hidden=!show;if(show)visible+=1}});document.getElementById('count').textContent=`${{visible}} reliable crops`}}
    teamSelect.onchange=filterCards;filterCards();
    cards.forEach(card=>card.querySelector('.state').addEventListener('change',event=>{{if(event.target.value==='number_confirmed'&&Number(card.querySelector('.confidence').value)===0)card.querySelector('.confidence').value='1.00'}}));
    document.getElementById('export').onclick=()=>{{base.observations=cards.map(card=>{{const state=card.querySelector('.state').value;const number=card.querySelector('.number').value.trim();return {{anchor_crop_id:card.dataset.key,state,number:number||null,confidence:Number(card.querySelector('.confidence').value),view:card.querySelector('.view').value,clean_jersey_visible:card.querySelector('.clean').checked,source:'operator'}}}});const blob=new Blob([JSON.stringify(base,null,2)],{{type:'application/json'}});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='identity_jersey_number_observations_reviewed.json';link.click();URL.revokeObjectURL(link.href)}};
    </script></body></html>"""


def _markdown(
    roster: dict[str, Any],
    evidence: dict[str, Any],
    consensus: dict[str, Any],
    assignment: dict[str, Any],
    propagation: dict[str, Any] | None,
) -> str:
    gate = (assignment.get("safety") or {}).get("benchmark_gate") or {}
    return "\n".join(
        [
            "# Jersey Number Identity Shadow Report",
            "",
            f"- Roster players: {(roster.get('summary') or {}).get('players', 0)}",
            f"- Trusted unique numbers: {(roster.get('summary') or {}).get('unique_trusted_numbers', 0)}",
            f"- Evidence rows: {(evidence.get('summary') or {}).get('evidence_rows', 0)}",
            f"- Strong subject consensus: {(consensus.get('summary') or {}).get('strong_subject_consensus', 0)}",
            f"- Strictly eligible N4 candidates: {(assignment.get('summary') or {}).get('strictly_eligible', 0)}",
            f"- N4 benchmark gate: {'passed' if gate.get('passed') else 'blocked'}",
            f"- N4 blocker reasons: {', '.join(gate.get('reason_codes') or []) or 'none'}",
            f"- N5 propagated tracklets: {((propagation or {}).get('summary') or {}).get('propagated_tracklets', 0)}",
            f"- N5 blocked unsafe edges: {((propagation or {}).get('summary') or {}).get('blocked_edges', 0)}",
            "",
            "All outputs are shadow-only and automatic assignments remain disabled.",
        ]
    )


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return document


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
