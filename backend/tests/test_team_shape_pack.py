from app.services.reviewed_match_report import build_reviewed_match_report
from app.services.stabilization import build_team_shape_pack_document_from_team_stats


def test_team_shape_pack_uses_team_stats():
    team_stats = {
        "teams": [
            {"team_label": "A", "team_id": "team-a", "team_name": "Team A", "players": 3, "playing_time_sec": 180.0, "total_distance_m": 90.0},
            {"team_label": "B", "team_id": "team-b", "team_name": "Team B", "players": 4, "playing_time_sec": 240.0, "total_distance_m": 120.0},
        ]
    }

    doc = build_team_shape_pack_document_from_team_stats(team_stats)

    assert doc["available"] is True
    assert len(doc["teams"]) == 2
    assert doc["teams"][0]["summary"]["sample_count"] == 3
    assert doc["teams"][0]["timeline"]

