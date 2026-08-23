from __future__ import annotations

import json
import unittest

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_ownership_compact import (
    EXPANSION_STATS,
    CompactOwnershipError,
    CompactPairIndexView,
    count_pair_runs,
    decode_index_rows,
    decode_observation_runs,
    decode_pair_runs,
    encode_index_rows,
    encode_observation_rows,
    encode_pair_runs,
    normalize_pairs,
    reset_expansion_stats,
    runs_difference,
    runs_intersection_size,
    runs_union,
    validate_compact_document,
    validate_index_runs,
    validate_pair_runs,
)


class OwnershipCompactCodecTests(unittest.TestCase):
    def test_round_trip_is_exact_for_contiguous_sparse_and_multi_tracklet_input(self) -> None:
        pairs = (
            [("t1", frame) for frame in range(18836, 19501)]
            + [("t2", frame) for frame in list(range(19510, 19530)) + [19535] + list(range(20000, 20201))]
            + [("t3", 20922), ("t3", 20250), ("t1", 5)]
        )
        encoded = encode_pair_runs(pairs)
        self.assertEqual(encoded, {
            "t1": [[5, 5], [18836, 19500]],
            "t2": [[19510, 19529], [19535, 19535], [20000, 20200]],
            "t3": [[20250, 20250], [20922, 20922]],
        })
        decoded = decode_pair_runs(encoded)
        self.assertEqual(decoded, sorted(set(tuple(pair) for pair in pairs)))

    def test_single_frame_run_and_empty_input(self) -> None:
        self.assertEqual(encode_pair_runs([]), {})
        self.assertEqual(decode_pair_runs({}), [])
        self.assertEqual(encode_pair_runs([("t9", 7)]), {"t9": [[7, 7]]})
        self.assertEqual(decode_pair_runs({"t9": [[7, 7]]}), [("t9", 7)])

    def test_decode_rejects_malformed_runs_instead_of_skipping_them(self) -> None:
        malformed = [
            {"t1": [[10, 5]]},                      # end < start
            {"t1": [["bad", 20]]},                  # non-integer start
            {"t1": [[12]]},                         # wrong arity
            {"t1": [[10, True]]},                   # bool frame
            {"t1": "nope"},                         # runs not a list
            {"t1": [[10, 20], [15, 25]]},           # overlap
            {"t1": [[10, 20], [21, 30]]},           # adjacent (non-canonical)
            {"": [[1, 2]]},                          # empty tracklet id
            {None: [[1, 2]]},                        # non-string tracklet id
        ]
        for runs in malformed:
            with self.subTest(runs=runs):
                with self.assertRaises(CompactOwnershipError):
                    validate_pair_runs(runs)
                with self.assertRaises(CompactOwnershipError):
                    decode_pair_runs(runs)
        with self.assertRaises(CompactOwnershipError):
            decode_index_rows({"t1": [[5, "x", {}]]})

    def test_validate_compact_document_covers_every_run_key(self) -> None:
        document = {
            "internal_review_units": [
                {"detected_pair_runs": {"t1": [[0, 5]]}},
                {"owned_observation_runs": {"t2": [[7, 9]]}},
                {
                    "_potential_named_observation_runs": {"t3": [[1, 2]]},
                    "continuity_members": [{"detected_pair_runs": {"t4": [[3, 4]]}}],
                },
            ],
            "projection_inputs": {
                "observed_pair_runs": {"t1": [[0, 99]]},
                "pair_index_runs": {"t1": [[0, 5, {"team_label": "A"}], [7, 8, {"team_label": "B"}]]},
            },
        }
        validate_compact_document(document)
        corrupt = json.loads(json.dumps(document))
        corrupt["projection_inputs"]["observed_pair_runs"]["t1"] = [[50, 40]]
        with self.assertRaises(CompactOwnershipError):
            validate_compact_document(corrupt)

    def test_interval_algebra_is_exact(self) -> None:
        a = {"t1": [[0, 10], [20, 29]], "t2": [[100, 100]]}
        b = {"t1": [[5, 24]], "t3": [[0, 999]]}
        self.assertEqual(runs_union(a, b), {
            "t1": [[0, 29]],
            "t2": [[100, 100]],
            "t3": [[0, 999]],
        })
        self.assertEqual(runs_difference(a, b), {"t1": [[0, 4], [25, 29]], "t2": [[100, 100]]})
        self.assertEqual(runs_intersection_size(a, b), 6 + 5)
        self.assertEqual(
            count_pair_runs(runs_union(a, b)),
            count_pair_runs(a) + count_pair_runs(b) - runs_intersection_size(a, b),
        )

    def test_expansion_stats_track_decoded_pairs(self) -> None:
        reset_expansion_stats()
        decode_pair_runs({"t1": [[0, 9]]})
        self.assertEqual(EXPANSION_STATS["expanded_pairs"], 10)
        reset_expansion_stats()

    def test_count_matches_decoded_cardinality(self) -> None:
        encoded = {"t1": [[10, 20]], "t2": [[100, 100], [300, 302]]}
        self.assertEqual(count_pair_runs(encoded), len(decode_pair_runs(encoded)))
        self.assertEqual(count_pair_runs(encoded), 15)

    def test_digest_over_decoded_pairs_equals_digest_over_originals(self) -> None:
        pairs = [(f"t{index}", frame) for index in range(3) for frame in range(index, 500, 7)]
        digest_of_original = canonical_digest({"pairs": normalize_pairs(pairs)})
        digest_of_decoded = canonical_digest({"pairs": decode_pair_runs(encode_pair_runs(pairs))})
        self.assertEqual(digest_of_original, digest_of_decoded)

    def test_index_rows_survive_identical_payload_and_changing_payload_runs(self) -> None:
        rows = []
        for frame in range(100, 140):
            rows.append({"tracklet_id": "a", "frame": frame, "value": {
                "identity_status": "unresolved", "team_label": "A", "canonical_player_id": None,
            }})
        for frame in range(140, 160):
            rows.append({"tracklet_id": "a", "frame": frame, "value": {
                "identity_status": "confirmed", "team_label": "A", "canonical_player_id": "p1",
            }})
        rows.append({"tracklet_id": "b", "frame": 55, "value": None})

        encoded = encode_index_rows(rows)
        self.assertEqual(encoded["a"], [[100, 139, rows[0]["value"]], [140, 159, rows[40]["value"]]])
        self.assertEqual(encoded["b"], [[55, 55, None]])

        decoded = decode_index_rows(encoded)
        self.assertEqual(decoded, rows)

    def test_encoded_representation_is_json_serializable_and_stable(self) -> None:
        pairs = [("t1", frame) for frame in range(10)]
        first = encode_pair_runs(pairs)
        second = json.loads(json.dumps(first))
        self.assertEqual(first, second)
        self.assertEqual(json.loads(json.dumps(encode_index_rows(
            [{"tracklet_id": "t1", "frame": frame, "value": {"team_label": "A"}} for frame in range(4)]
        ))), {"t1": [[0, 3, {"team_label": "A"}]]})


class CompactPairIndexViewTests(unittest.TestCase):
    def _view(self) -> CompactPairIndexView:
        return CompactPairIndexView({
            "t1": [[10, 12, {"team_label": "A"}], [20, 20, {"team_label": "B"}]],
            "t2": [[5, 6, {"team_label": "U"}]],
        })

    def test_lookup_membership_and_len_match_plain_dict_semantics(self) -> None:
        view = self._view()
        plain = dict(view.items())
        reference = {
            ("t1", 10): {"team_label": "A"},
            ("t1", 11): {"team_label": "A"},
            ("t1", 12): {"team_label": "A"},
            ("t1", 20): {"team_label": "B"},
            ("t2", 5): {"team_label": "U"},
            ("t2", 6): {"team_label": "U"},
        }
        self.assertEqual(plain, reference)
        self.assertEqual(len(view), len(reference))
        for pair, value in reference.items():
            self.assertIn(pair, view)
            self.assertEqual(view[pair], value)
        self.assertNotIn(("t1", 13), view)
        self.assertNotIn(("tX", 10), view)
        self.assertIsNone(view.get(("t1", 99)))
        self.assertEqual(view.get(("t2", 5)), reference[("t2", 5)])
        with self.assertRaises(KeyError):
            view[("t1", 13)]

    def test_full_scan_yields_every_frame_once(self) -> None:
        seen = [pair for pair, _value in self._view().items()]
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(seen, sorted(seen))


if __name__ == "__main__":
    unittest.main()
