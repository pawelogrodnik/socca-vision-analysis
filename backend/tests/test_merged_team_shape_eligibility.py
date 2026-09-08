"""Team Shape merge eligibility for the real Corgi-Verisk publication chain.

The real physical packages (published-9c7485e4, published-6d8fc20c,
published-5e62625e) carry team_shape docs with available=False and
readiness=not_available. The merged report must therefore omit team_shape
(case D: genuine missing/ineligible evidence) instead of inventing one.
Once sources satisfy the ready contract, the same aggregation path merges.
"""

import unittest

from app.services.merged_public_match import _merge_team_shape
from app.services.public_match_report import _public_team_shape


def _not_available_doc():
    return {
        "available": False,
        "readiness": "not_available",
        "algorithm_version": "team_shape_spatial_v1_1",
    }


def _ready_team(team_id, label, name, eligible, width):
    return {
        "team_label": label,
        "team_id": team_id,
        "team_name": name,
        "readiness": "ready",
        "summary": {
            "average_width_m": width,
            "average_depth_m": 18.0,
            "average_compactness_m": 7.0,
            "average_block_height_percent": 50.0,
        },
        "average_shape": {
            "grid": {"columns": 6, "rows": 10},
            "cells": [{"column": 0, "row": 0, "value": 0.6}],
        },
        "timeline": [
            {
                "minute": 1,
                "label": "00:00",
                "width_m": width,
                "depth_m": 18.0,
                "compactness_m": 7.0,
                "block_height_percent": 50.0,
            }
        ],
        "diagnostics": {"eligible_frames": eligible},
    }


def _ready_doc(eligible, width):
    return {
        "available": True,
        "readiness": "ready",
        "algorithm_version": "team_shape_spatial_v1_1",
        "scope": "all_in_play",
        "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
        "parameters": {"timeline_bin_sec": 60.0, "density_columns": 6, "density_rows": 10},
        "teams": [
            _ready_team("team-corgi", "A", "Corgi", eligible, width),
            _ready_team("team-verisk", "B", "Verisk", eligible, width),
        ],
        "takeaways": [],
    }


def _source(doc, offset):
    return {
        "package": {"team_shape": doc},
        "aggregate": {
            "teams": [
                {"source_team_label": "A", "team_id": "team-corgi"},
                {"source_team_label": "B", "team_id": "team-verisk"},
            ]
        },
        "member": {"logical_start_sec": offset},
    }


CANONICAL_OF_STABLE = {"team-corgi": "A", "team-verisk": "B"}


class MergedTeamShapeEligibilityTest(unittest.TestCase):
    def test_not_available_sources_yield_no_merged_team_shape(self) -> None:
        sources = [
            _source(_not_available_doc(), 0.0),
            _source(_not_available_doc(), 1156.3),
            _source(_not_available_doc(), 1761.878),
        ]
        self.assertIsNone(_merge_team_shape(sources, CANONICAL_OF_STABLE, duration_sec=2113.9))

    def test_single_not_ready_source_blocks_merge(self) -> None:
        sources = [_source(_ready_doc(200, 20.0), 0.0), _source(_not_available_doc(), 595.0)]
        self.assertIsNone(_merge_team_shape(sources, CANONICAL_OF_STABLE, duration_sec=895.0))

    def test_ready_contract_sources_merge_with_evidence_weighting(self) -> None:
        sources = [_source(_ready_doc(200, 20.0), 0.0), _source(_ready_doc(500, 30.0), 595.0)]
        merged = _merge_team_shape(sources, CANONICAL_OF_STABLE, duration_sec=895.0)
        self.assertIsNotNone(merged)
        assert merged is not None
        corgi = next(row for row in merged["teams"] if row["team_id"] == "team-corgi")
        self.assertAlmostEqual(corgi["summary"]["average_width_m"], (200 * 20.0 + 500 * 30.0) / 700, places=2)
        self.assertEqual(len(merged["teams"]), 2)

    def test_physical_public_projection_omits_not_available_team_shape(self) -> None:
        package = {"team_shape": _not_available_doc()}
        public_teams = [
            {"team_label": "A", "team_id": "team-corgi", "team_name": "Corgi"},
            {"team_label": "B", "team_id": "team-verisk", "team_name": "Verisk"},
        ]
        self.assertIsNone(_public_team_shape(package, public_teams))


if __name__ == "__main__":
    unittest.main()
