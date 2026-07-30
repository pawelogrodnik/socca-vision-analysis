#!/usr/bin/env python3
"""Execute one import order in a fresh process, then compile an IR."""

import argparse
import importlib
import json
import platform
import sys
import traceback


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--imports", required=True)
    parser.add_argument("--xml", required=True)
    parser.add_argument("--bin", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    imports = [item for item in arguments.imports.split(",") if item]
    report = {
        "imports": imports,
        "python_executable": sys.executable,
        "python_version": sys.version,
        "architecture": platform.machine(),
        "versions": {},
    }
    try:
        loaded = {name: importlib.import_module(name) for name in imports}
        ov = loaded["openvino"]
        report["versions"] = {
            name: getattr(module, "__version__", None)
            for name, module in loaded.items()
        }
        core = ov.Core()
        report["available_devices"] = core.available_devices
        model = core.read_model(arguments.xml, arguments.bin)
        core.compile_model(model, "CPU")
        report["status"] = "passed"
        exit_code = 0
    except Exception as error:
        report.update({
            "status": "failed",
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
        })
        exit_code = 3
    with open(arguments.output, "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
