from __future__ import annotations

import unittest

from app.services.identity_jersey_number_visibility_episodes import (
    attach_jersey_visibility_episode_ids,
    partition_jersey_visibility_episodes,
)


def row(frame: int, **overrides: object) -> dict:
    return {
        "frame": frame,
        "source_match_key": "match-1",
        "source_video_key": "video-1",
        "candidate_subject_id": "subject-1",
        "tracklet_id": "tracklet-1",
        "team_id": "team-1",
        "team_label": "A",
        **overrides,
    }


class JerseyVisibilityEpisodeTests(unittest.TestCase):
    def test_contiguous_frames_form_one_episode(self) -> None:
        episodes = partition_jersey_visibility_episodes([row(3509), row(3510), row(3512)])

        self.assertEqual([[value["frame"] for value in episode] for episode in episodes], [[3509, 3510, 3512]])

    def test_gap_boundary_partitions_at_46_frames(self) -> None:
        episodes = partition_jersey_visibility_episodes([row(0), row(45), row(91)])

        self.assertEqual([[value["frame"] for value in episode] for episode in episodes], [[0, 45], [91]])

    def test_same_subject_in_different_matches_is_separate(self) -> None:
        attached = attach_jersey_visibility_episode_ids([row(10), row(10, source_match_key="match-2")])

        self.assertNotEqual(attached[0]["visibility_episode_id"], attached[1]["visibility_episode_id"])

    def test_explicit_episode_ids_are_preserved(self) -> None:
        attached = attach_jersey_visibility_episode_ids(
            [row(0, visibility_episode_id="explicit-episode"), row(100, visibility_episode_id="explicit-episode")]
        )

        self.assertEqual([value["visibility_episode_id"] for value in attached], ["explicit-episode", "explicit-episode"])

    def test_malformed_scopes_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            partition_jersey_visibility_episodes([row(0, source_video_key="")])
        with self.assertRaises(ValueError):
            partition_jersey_visibility_episodes([row(0, team_id="", team_label="U")])
        with self.assertRaises(ValueError):
            partition_jersey_visibility_episodes([row(0, visibility_episode_id="x"), row(1)])
        with self.assertRaises(ValueError):
            partition_jersey_visibility_episodes(
                [row(0, visibility_episode_id="x"), row(0, source_match_key="match-2", visibility_episode_id="x")]
            )

    def test_attach_does_not_mutate_input(self) -> None:
        rows = [row(0), row(1)]
        snapshot = [dict(value) for value in rows]

        attach_jersey_visibility_episode_ids(rows)

        self.assertEqual(rows, snapshot)


if __name__ == "__main__":
    unittest.main()
