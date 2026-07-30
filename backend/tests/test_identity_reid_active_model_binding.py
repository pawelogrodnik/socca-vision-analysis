from __future__ import annotations

import unittest

from app.services.identity_reid_active_model_binding import (
    build_reid_active_model_binding,
)


class ReidActiveModelBindingTests(unittest.TestCase):
    def test_preferred_gate_cannot_unlock_portable_ranking(self) -> None:
        binding = _binding(preferred_gate=True)

        self.assertEqual(
            binding["diagnostic_models"]["portable"]["rankings"][0]
            ["suggestions"][0]["player_id"],
            "player-portable",
        )
        self.assertEqual(
            binding["operator_advisory"]["suggestions"][0]["suggestions"]
            [0]["player_id"],
            "player-preferred",
        )
        self.assertEqual(
            binding["active_operator_model_name"],
            "person-reidentification-retail-0288",
        )

    def test_preferred_unavailable_keeps_portable_diagnostic_only(self) -> None:
        binding = _binding(preferred_artifact=None, preferred_gate=False)

        self.assertEqual(binding["active_operator_run"], None)
        self.assertEqual(binding["operator_advisory"]["suggestions"], [])
        self.assertEqual(
            binding["diagnostic_models"]["portable"]["suggestions"][0]
            ["suggestions"][0]["player_id"],
            "player-portable",
        )

    def test_failed_preferred_gate_has_no_operator_advisory(self) -> None:
        binding = _binding(
            preferred_artifact=_artifact(
                "person-reidentification-retail-0288",
                "player-preferred",
            ),
            preferred_gate=False,
        )

        self.assertIsNone(binding["active_operator_run"])
        self.assertEqual(binding["operator_advisory"]["rankings"], [])
        self.assertEqual(binding["operator_advisory"]["suggestions"], [])


def _binding(
    *,
    preferred_artifact: dict[str, object] | None = None,
    preferred_gate: bool,
) -> dict[str, object]:
    portable = _artifact("portable-appearance-descriptor", "player-portable")
    preferred = preferred_artifact
    if preferred is None and preferred_gate:
        preferred = _artifact(
            "person-reidentification-retail-0288",
            "player-preferred",
        )
    elif preferred is None and not preferred_gate:
        preferred = None
    return build_reid_active_model_binding(
        portable_artifact=portable,
        portable_evaluation={"queries": 1},
        portable_display_gate={"display_eligible": False},
        preferred_artifact=preferred,
        preferred_evaluation={"queries": 8},
        preferred_display_gate={"display_eligible": preferred_gate},
        subject_tracklets={"subject-1": ["tracklet-1"]},
    )


def _artifact(model_name: str, player_id: str) -> dict[str, object]:
    return {
        "model": {"model_name": model_name, "runtime": model_name},
        "internal_reference_calibration": {"queries": 8},
        "unresolved_rankings": [{
            "candidate_subject_id": "subject-1",
            "team_label": "A",
            "status": "ranked",
            "suggestions": [{"player_id": player_id, "distance": 0.1}],
        }],
    }


if __name__ == "__main__":
    unittest.main()
