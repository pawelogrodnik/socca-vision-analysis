#!/usr/bin/env python3
"""Compile synthetic and IR models in one OpenVINO-only runtime process."""

import argparse
import json
import traceback

import numpy as np
import openvino as ov
from openvino import opset13 as ops


def _compile(core: ov.Core, name: str, model: ov.Model) -> dict[str, object]:
    try:
        model.validate_nodes_and_infer_types()
        core.compile_model(model, "CPU")
        return {"name": name, "compile_passed": True, "error": None}
    except Exception as error:
        return {
            "name": name,
            "compile_passed": False,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }


def _trivial() -> ov.Model:
    parameter = ops.parameter([1, 3, 16, 16], ov.Type.f32, name="data")
    return ov.Model([ops.result(ops.relu(parameter))], [parameter], "trivial")


def _convolution() -> ov.Model:
    parameter = ops.parameter([1, 3, 16, 16], ov.Type.f32, name="data")
    weights = ops.constant(np.full((4, 3, 3, 3), 0.1, dtype=np.float32))
    convolution = ops.convolution(parameter, weights, [1, 1], [1, 1], [1, 1], [1, 1])
    return ov.Model([ops.result(ops.reduce_mean(ops.relu(convolution), [2, 3], False))], [parameter], "convolution")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xml", required=True)
    parser.add_argument("--bin", required=True)
    parser.add_argument("--control-xml")
    parser.add_argument("--control-bin")
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    core = ov.Core()
    report = {
        "openvino_version": ov.__version__,
        "available_devices": core.available_devices,
        "models": [
            _compile(core, "trivial_relu", _trivial()),
            _compile(core, "convolution_relu_gap", _convolution()),
        ],
    }
    try:
        reid = core.read_model(arguments.xml, arguments.bin)
        report["models"].append(_compile(core, "person_reid_0288", reid))
    except Exception as error:
        report["models"].append({
            "name": "person_reid_0288",
            "compile_passed": False,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        })
    if arguments.control_xml and arguments.control_bin:
        try:
            control = core.read_model(arguments.control_xml, arguments.control_bin)
            report["models"].append(_compile(core, "official_control_model", control))
        except Exception as error:
            report["models"].append({
                "name": "official_control_model",
                "compile_passed": False,
                "error_type": type(error).__name__,
                "error": str(error),
                "traceback": traceback.format_exc(),
            })
    with open(arguments.output, "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
