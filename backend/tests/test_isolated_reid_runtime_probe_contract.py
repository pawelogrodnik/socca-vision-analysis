from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

import numpy as np


def _probe_module() -> object:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "probe_isolated_reid_runtime.py"
    )
    specification = importlib.util.spec_from_file_location(
        "isolated_reid_probe",
        script,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("Cannot load isolated ReID probe script")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class IsolatedReidRuntimeProbeContractTests(unittest.TestCase):
    def test_embedding_contract_accepts_repeatable_256_vector(self) -> None:
        module = _probe_module()
        vector = np.ones(256, dtype=np.float32)
        steps: list[dict[str, object]] = []

        module._embedding_contract_step(steps, vector, vector.copy())

        self.assertTrue(steps[0]["passed"])
        self.assertEqual(steps[0]["embedding_dimension"], 256)
        self.assertTrue(steps[0]["repeatability_passed"])

    def test_embedding_contract_rejects_wrong_dimension(self) -> None:
        module = _probe_module()
        steps: list[dict[str, object]] = []

        module._embedding_contract_step(
            steps,
            np.ones(255, dtype=np.float32),
            np.ones(255, dtype=np.float32),
        )

        self.assertFalse(steps[0]["passed"])
        self.assertEqual(steps[0]["error_type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
