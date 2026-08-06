from __future__ import annotations

import importlib.util
import builtins
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

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
    def test_helper_contract_import_does_not_require_openvino(self) -> None:
        previous = sys.modules.pop("openvino", None)
        real_import = builtins.__import__

        def import_without_openvino(name: str, *args: object, **kwargs: object) -> object:
            if name == "openvino" or name.startswith("openvino."):
                raise ModuleNotFoundError("OpenVINO intentionally blocked by test")
            return real_import(name, *args, **kwargs)

        try:
            with patch("builtins.__import__", side_effect=import_without_openvino):
                module = _probe_module()
                vector = np.ones(256, dtype=np.float32)
                steps: list[dict[str, object]] = []
                module._embedding_contract_step(steps, vector, vector.copy())
                self.assertTrue(steps[0]["passed"])
        finally:
            if previous is not None:
                sys.modules["openvino"] = previous

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
