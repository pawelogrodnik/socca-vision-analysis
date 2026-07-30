#!/usr/bin/env python3
"""Rosetta OpenVINO embedding worker; input/output are JSON plus NumPy files."""

import argparse
import json

import numpy as np
import openvino as ov
from PIL import Image


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    arguments = parser.parse_args()
    manifest = json.loads(open(arguments.manifest, encoding="utf-8").read())
    crop = np.load(manifest["input_npy"])
    core = ov.Core()
    model = core.read_model(manifest["model_xml"], manifest["model_bin"])
    compiled = core.compile_model(model, "CPU")
    rgb = Image.fromarray(crop[:, :, ::-1]).resize((128, 256))
    bgr = np.asarray(rgb)[:, :, ::-1]
    tensor = bgr.transpose(2, 0, 1)[None, ...].astype(np.float32)
    vector = np.asarray(compiled({"data": tensor})[compiled.output(0)]).reshape(-1)
    vector = vector.astype(np.float32)
    vector /= max(float(np.linalg.norm(vector)), 1e-12)
    np.save(manifest["output_npy"], vector)
    print(json.dumps({"embedding_dimension": int(vector.size), "openvino": ov.__version__}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
