from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.services.stabilization import (
    HEATMAP_DENSITY_GAMMA,
    HEATMAP_DENSITY_PERCENTILE,
    HEATMAP_METHOD,
    HEATMAP_PALETTE_STOPS,
    _colorize_heatmap_density,
    _heatmap_palette_lut,
    _normalize_heatmap_density,
    _write_player_heatmap_png,
    build_player_heatmaps_document,
)


PITCH_GREEN = (0x1A, 0x46, 0x30)
DEEP_RED = (0x99, 0x1B, 0x1B)


def _representative_blurred(*, hotspot_value: float = 150.0) -> np.ndarray:
    """Synthetic blurred-density-like distribution with halo, corridor, zone and hotspot."""
    values = (
        [2.0] * 4000
        + [8.0] * 1500
        + [20.0] * 800
        + [45.0] * 200
        + [hotspot_value] * 10
    )
    return np.array(values, dtype=np.float32)


class HeatmapNormalizationTests(unittest.TestCase):
    def test_empty_heatmap_renders_without_error(self) -> None:
        blurred = np.zeros((24, 12), dtype=np.float32)
        normalized = _normalize_heatmap_density(blurred)

        self.assertTrue(np.all(np.isfinite(normalized)))
        self.assertTrue(np.all(normalized == 0.0))

    def test_empty_rows_render_pitch_background(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "heatmap_empty.png"
            _write_player_heatmap_png(
                output,
                [],
                pitch_width_m=30.0,
                pitch_length_m=47.4,
                width_px=36,
                length_px=72,
            )
            self.assertTrue(output.exists())
            image = Image.open(output)
            self.assertEqual(image.size, (36, 72))
            pixels = np.asarray(image, dtype=np.float32)
            self.assertTrue(np.all(np.isfinite(pixels)))
            # Interior pixel far from pitch lines/boxes, so it must stay pitch green.
            self.assertEqual(tuple(int(v) for v in pixels[30, 18]), PITCH_GREEN)

    def test_normalization_is_deterministic(self) -> None:
        blurred = _representative_blurred()

        first = _normalize_heatmap_density(blurred)
        second = _normalize_heatmap_density(blurred)

        np.testing.assert_array_equal(first, second)

    def test_normalization_uses_percentile_reference_not_max(self) -> None:
        blurred = _representative_blurred()
        positive = blurred[blurred > 0]
        expected_reference = float(np.percentile(positive, HEATMAP_DENSITY_PERCENTILE))

        normalized = _normalize_heatmap_density(blurred)

        self.assertLess(expected_reference, float(blurred.max()))
        # Moderate density must map to the percentile-based level, not max-based.
        moderate = (np.float32(8.0) / expected_reference) ** np.float32(HEATMAP_DENSITY_GAMMA)
        self.assertAlmostEqual(float(normalized[4000]), float(moderate), places=5)

    def test_outlier_hotspot_does_not_rescale_moderate_density(self) -> None:
        base = _normalize_heatmap_density(_representative_blurred(hotspot_value=150.0))
        spiked = _normalize_heatmap_density(_representative_blurred(hotspot_value=15000.0))

        # Only the hotspot pixels themselves may differ; everything else is identical.
        np.testing.assert_array_equal(base[:-10], spiked[:-10])
        # Moderate density is not washed out to ~0 as pure max-normalization would do.
        self.assertGreater(float(base[4000]), 0.15)

    def test_density_mapping_is_monotonic(self) -> None:
        blurred = np.array([0.0, 2.0, 8.0, 20.0, 45.0, 150.0], dtype=np.float32)
        normalized = _normalize_heatmap_density(blurred)

        ordered = [float(normalized[idx]) for idx in range(len(blurred))]
        self.assertEqual(ordered, sorted(ordered))
        self.assertLess(ordered[1], ordered[-1])

        image = _colorize_heatmap_density(normalized.reshape(1, -1))
        pixels = np.asarray(image, dtype=np.int32).reshape(-1, 3)
        lut = _heatmap_palette_lut()
        indices = [int(np.argmin(np.abs(lut.astype(np.int32) - pixel).sum(axis=1))) for pixel in pixels]
        self.assertEqual(indices, sorted(indices))

    def test_medium_density_is_not_red(self) -> None:
        normalized = _normalize_heatmap_density(_representative_blurred())
        medium_value = float(normalized[4000])

        red_start = int(round(0.84 * 255))
        medium_index = int(round(medium_value * 255))
        self.assertLess(medium_index, red_start)

        pixel = tuple(int(v) for v in np.asarray(_colorize_heatmap_density(
            np.array([[medium_value]], dtype=np.float32)
        ))[0, 0])
        # Yellow/amber/orange all keep a strong green channel; red/deep-red do not.
        self.assertGreater(pixel[1], 100)

    def test_true_hotspot_reaches_deep_red(self) -> None:
        normalized = _normalize_heatmap_density(_representative_blurred())

        self.assertAlmostEqual(float(normalized[-1]), 1.0, places=5)
        pixel = tuple(int(v) for v in np.asarray(_colorize_heatmap_density(
            np.array([[normalized[-1]]], dtype=np.float32)
        ))[0, 0])
        self.assertEqual(pixel, DEEP_RED)

    def test_blur_halo_stays_close_to_pitch_green(self) -> None:
        normalized = _normalize_heatmap_density(_representative_blurred())
        halo_value = float(normalized[0])

        pixel = np.asarray(_colorize_heatmap_density(
            np.array([[halo_value]], dtype=np.float32)
        ), dtype=np.int32)[0, 0]
        green = np.array(PITCH_GREEN, dtype=np.int32)
        self.assertLessEqual(int(np.abs(pixel - green).max()), 30)

    def test_palette_has_yellow_to_red_resolution(self) -> None:
        positions = [position for position, _ in HEATMAP_PALETTE_STOPS]
        self.assertGreaterEqual(len(positions), 6)
        self.assertLess(0.30, 0.50)
        mid_colors = {
            round(position, 2): rgb for position, rgb in HEATMAP_PALETTE_STOPS
            if 0.30 <= position <= 0.68
        }
        # Yellow, amber and orange stops must exist between yellow and red.
        self.assertGreaterEqual(len(mid_colors), 3)

    def test_nonempty_rows_render_png(self) -> None:
        from PIL import Image

        rows = [
            {"pitch_m": [15.0, 23.7], "source": "detected"},
            {"pitch_m": [16.0, 24.0], "source": "detected"},
            {"pitch_m": [10.0, 10.0], "source": "interpolated"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "heatmap_player.png"
            _write_player_heatmap_png(
                output,
                rows,
                pitch_width_m=30.0,
                pitch_length_m=47.4,
                width_px=36,
                length_px=72,
            )
            image = Image.open(output)
            self.assertEqual(image.size, (36, 72))
            self.assertEqual(image.mode, "RGB")

    def test_heatmap_document_reports_v2_method(self) -> None:
        self.assertEqual(HEATMAP_METHOD, "pitch_meter_gaussian_heatmap_v2")
        stable_doc: dict = {
            "source": "conservative_identity_v2",
            "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
            "players": [
                {
                    "stable_subject_id": "subject-1",
                    "stable_player_id": "player-1",
                    "overlay_positions": [
                        {"pitch_m": [15.0, 23.7], "source": "detected"},
                        {"pitch_m": [16.0, 24.0], "source": "detected"},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            match_dir = Path(tmp)
            document = build_player_heatmaps_document(stable_doc, match_dir, width_px=36, length_px=72)

            self.assertEqual(document["method"], "pitch_meter_gaussian_heatmap_v2")
            heatmap_path = match_dir / str(document["heatmaps"][0]["path"])
            self.assertTrue(heatmap_path.exists())


if __name__ == "__main__":
    unittest.main()
