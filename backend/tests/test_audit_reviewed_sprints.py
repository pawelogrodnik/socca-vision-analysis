from __future__ import annotations

import unittest

from scripts.audit_reviewed_sprints import audit_sample


def _event(player: str, index: int) -> dict[str, object]:
    return {
        "player_id": player,
        "player_name": player,
        "logical_start_sec": float(index * 10),
        "logical_end_sec": float(index * 10 + 1),
        "qualifying_time_sec": round(0.4 + index / 100, 3),
        "max_sustained_speed_kmh": float(40 - index / 10),
    }


def _sample_keys(sample: dict[str, list[dict[str, object]]]) -> dict[str, list[tuple[str, float]]]:
    return {
        bucket: [
            (str(event["player_id"]), float(event["logical_start_sec"]))
            for event in events
        ]
        for bucket, events in sample.items()
    }


class AuditReviewedSprintsTests(unittest.TestCase):
    def test_sampling_is_deterministic_regardless_of_input_order(self) -> None:
        events = [
            _event(player, index)
            for index, player in enumerate(("a", "b", "c", "d") * 5)
        ]

        first = audit_sample(events, bucket_size=8)
        second = audit_sample(list(reversed(events)), bucket_size=8)

        self.assertEqual(_sample_keys(first), _sample_keys(second))

    def test_strong_and_borderline_samples_cap_one_player_when_alternatives_exist(self) -> None:
        events = [
            _event(player, index)
            for index, player in enumerate(("a", "a", "a", "a", "a", "b", "b", "b", "b", "b", "c", "c", "c", "c", "c", "d", "d", "d", "d", "d"))
        ]

        sample = audit_sample(events, bucket_size=8)

        for bucket in ("strongest", "borderline"):
            counts: dict[str, int] = {}
            for event in sample[bucket]:
                player = str(event["player_id"])
                counts[player] = counts.get(player, 0) + 1
            self.assertTrue(all(count <= 3 for count in counts.values()))


if __name__ == "__main__":
    unittest.main()
