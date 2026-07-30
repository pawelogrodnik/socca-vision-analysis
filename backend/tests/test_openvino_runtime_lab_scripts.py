from __future__ import annotations

import ast
from pathlib import Path
import unittest


class OpenVinoRuntimeLabScriptsTests(unittest.TestCase):
    def test_minimal_compile_probe_only_has_one_third_party_import(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "probe_openvino_compile_minimal.py"
        tree = ast.parse(script.read_text(encoding="utf-8"))
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        self.assertEqual(imports, ["argparse", "json", "platform", "sys", "traceback", "openvino"])
