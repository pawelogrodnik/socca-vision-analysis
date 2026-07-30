#!/usr/bin/env python3
"""Minimal third-party-import OpenVINO compile probe."""

import argparse
import json
import platform
import sys
import traceback

import openvino as ov


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xml", required=True)
    parser.add_argument("--bin", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    report = {
        "python_executable": sys.executable,
        "python_version": sys.version,
        "architecture": platform.machine(),
        "openvino_version": ov.__version__,
        "steps": [],
    }
    try:
        core = ov.Core()
        report["steps"].append({"step": "create_core", "passed": True})
        report["available_devices"] = core.available_devices
        model = core.read_model(arguments.xml, arguments.bin)
        report["steps"].append({"step": "read_model", "passed": True})
        core.compile_model(model, "CPU")
        report["steps"].append({"step": "compile_cpu", "passed": True})
        report["status"] = "passed"
        exit_code = 0
    except Exception as error:
        report["steps"].append({
            "step": "compile_cpu",
            "passed": False,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
        })
        report["status"] = "failed"
        exit_code = 3
    with open(arguments.output, "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
