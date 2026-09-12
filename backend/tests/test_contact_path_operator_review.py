from __future__ import annotations

import importlib.util
from pathlib import Path

from app.services.reviewed_ball_diagnostic import _operator_timecode


SCRIPT = Path(__file__).parents[1] / "scripts" / "render_contact_path_operator_review.py"
SPEC = importlib.util.spec_from_file_location("contact_path_operator_review", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_build_operator_review_cases_uses_only_contact_paths_and_human_labels() -> None:
    analysis = {
        "cases": [
            {
                "timestamp_display": "10:54~",
                "source_match_id": "source-a",
                "path_diagnoses": [
                    {"contact_path": {"event_id": "internal-1", "timestamp_sec": 100.2, "team": "Blue", "player": "B04"}},
                    {"contact_path": {"event_id": "internal-2", "timestamp_sec": 101.1, "team": "Blue", "player": "B05"}},
                ],
            },
            {"timestamp_display": "11:00~", "path_diagnoses": [{"contact_path": {"timestamp_sec": 1}}]},
        ]
    }

    cases = MODULE.build_operator_review_cases(analysis, ("10:54",))

    assert cases == [
        {
            "timestamp_display": "10:54",
            "source_match_id": "source-a",
            "choices": [
                {"letter": "A", "team": "Blue", "player": "B04", "launch_sec": 100.2, "label": "Opcja A — Blue"},
                {"letter": "B", "team": "Blue", "player": "B05", "launch_sec": 101.1, "label": "Opcja B — Blue"},
            ],
        }
    ]


def test_operator_timecode_keeps_hundredths_of_a_second() -> None:
    assert _operator_timecode(654.34) == "10:54.34"
