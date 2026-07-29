from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_discovery_audit import (  # noqa: E402
    INDEX_FILENAME,
    MANIFEST_FILENAME,
    build_discovery_dataset_from_review_gallery,
    build_discovery_dataset_from_subject_review,
    combine_discovery_datasets,
    derive_jersey_number_recovery_targets,
    prepare_jersey_number_discovery_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a research jersey-number discovery gate.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", type=Path)
    source.add_argument(
        "--match-dir",
        type=Path,
        help=(
            "Match directory containing the roster-subject review and/or the full "
            "identity-review gallery."
        ),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--target-cards", type=int, default=80)
    parser.add_argument("--target-confirmations", type=int, default=60)
    parser.add_argument("--target-negatives", type=int, default=0)
    parser.add_argument(
        "--recovery-from-readiness",
        type=Path,
        help="Canonical panel-readiness JSON used to derive a bounded data-recovery gate.",
    )
    parser.add_argument(
        "--recovery-card-cap",
        type=int,
        default=80,
        help="Maximum cards in a readiness-recovery gate.",
    )
    parser.add_argument("--team-label", default="A")
    parser.add_argument(
        "--unreviewed-only",
        action="store_true",
        help="Select only crop rows that have not received any prior jersey-number annotation.",
    )
    parser.add_argument(
        "--choice",
        action="append",
        required=False,
        metavar="NUMBER:LABEL",
        help="One confirmed roster number, for example 10:Krzysiek #10.",
    )
    args = parser.parse_args()
    if args.dataset is not None:
        dataset = _read_object(args.dataset.resolve())
        if not args.choice:
            parser.error("--choice is required when --dataset is used")
        choices = [_parse_choice(value) for value in args.choice]
    else:
        match_dir = args.match_dir.resolve()
        match = _read_object(match_dir / "match.json")
        team = _team_from_match(match, args.team_label)
        choices = _numeric_roster_choices(team)
        if args.choice:
            choices = [_parse_choice(value) for value in args.choice]
        source_match_key = str(match.get("id") or match_dir.name)
        source_video_key = str(match.get("video_filename") or match.get("id") or match_dir.name)
        datasets = []
        review_path = match_dir / "identity_roster_subject_review_shadow.json"
        if review_path.is_file():
            datasets.append(
                build_discovery_dataset_from_subject_review(
                    _read_object(review_path),
                    artifact_root=match_dir,
                    source_match_key=source_match_key,
                    source_video_key=source_video_key,
                    team_label_value=args.team_label,
                )
            )
        gallery_path = match_dir / "identity_review_gallery.json"
        if gallery_path.is_file():
            datasets.append(
                build_discovery_dataset_from_review_gallery(
                    _read_object(gallery_path),
                    artifact_root=match_dir,
                    source_match_key=source_match_key,
                    source_video_key=source_video_key,
                    team_label_value=args.team_label,
                )
            )
        if not datasets:
            raise FileNotFoundError(
                "match directory has neither identity_roster_subject_review_shadow.json "
                "nor identity_review_gallery.json"
            )
        dataset = combine_discovery_datasets(*datasets)
    output_root = args.output_root.resolve()
    recovery = None
    if args.recovery_from_readiness is not None:
        recovery = derive_jersey_number_recovery_targets(
            _read_object(args.recovery_from_readiness.resolve()),
            card_cap=args.recovery_card_cap,
        )
    manifest = prepare_jersey_number_discovery_audit(
        dataset,
        output_root=output_root,
        roster_choices=choices,
        target_cards=int((recovery or {}).get("target_cards", args.target_cards)),
        target_confirmations=int((recovery or {}).get("target_confirmations", args.target_confirmations)),
        target_negatives=int((recovery or {}).get("target_negatives", args.target_negatives)),
        team_label=args.team_label,
        unreviewed_only=args.unreviewed_only,
        audit_purpose=str((recovery or {}).get("audit_purpose", "research_discovery")),
    )
    print(json.dumps(manifest["summary"], indent=2, ensure_ascii=False))
    print(f"manifest={output_root / MANIFEST_FILENAME}")
    print(f"audit={output_root / INDEX_FILENAME}")


def _read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _team_from_match(match: dict[str, object], label: str) -> dict[str, object]:
    teams = match.get("teams") or []
    if not isinstance(teams, list):
        raise ValueError("match.json has no teams list")
    index = 0 if str(label).upper() == "A" else 1
    if index >= len(teams) or not isinstance(teams[index], dict):
        raise ValueError(f"match.json has no Team {label}")
    return teams[index]


def _numeric_roster_choices(team: dict[str, object]) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for player in team.get("players") or []:
        if not isinstance(player, dict):
            continue
        number = str(player.get("number") or "").strip()
        name = str(player.get("name") or "").strip()
        if number.isdigit() and name:
            values.append({"number": number, "label": f"{name} #{number}"})
    if not values:
        raise ValueError("selected team has no numeric roster jersey numbers")
    return values


def _parse_choice(value: str) -> dict[str, str]:
    number, separator, label = value.partition(":")
    if not separator or not number.strip() or not label.strip():
        raise ValueError("--choice must use NUMBER:LABEL")
    return {"number": number.strip(), "label": label.strip()}


if __name__ == "__main__":
    main()
