from __future__ import annotations

"""Small, diagnostic-only classifier for manually defined jersey-number panels.

This is intentionally not an OCR stack.  It receives the already-defined panel
crop and predicts its visual state plus three fixed digit positions.
"""

from typing import Any

import cv2
import numpy as np
import torch
from torch import Tensor, nn


PANEL_HEIGHT = 64
PANEL_WIDTH = 96
MAX_DIGITS = 3
VISUAL_STATES = ("number_confirmed", "number_absent", "number_unreadable")
VISUAL_STATE_TO_INDEX = {state: index for index, state in enumerate(VISUAL_STATES)}
BLANK_INDEX = 0
DIGIT_CLASS_COUNT = 11  # blank, then 0 through 9
ALGORITHM_NAME = "identity_jersey_number_panel_digitnet"
ALGORITHM_VERSION = "1.0.0-shadow"


class PanelDigitNetV1(nn.Module):
    """Shared compact CNN with one visual-state and three digit heads."""

    def __init__(self) -> None:
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv2d(1, 16, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2),
            nn.Conv2d(16, 32, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d((1, 1)),
        )
        self.visual_head = nn.Linear(64, len(VISUAL_STATES))
        self.digit_heads = nn.ModuleList(
            nn.Linear(64, DIGIT_CLASS_COUNT) for _ in range(MAX_DIGITS)
        )

    def forward(self, panels: Tensor) -> dict[str, Tensor]:
        shared = self.encoder(panels).flatten(1)
        return {
            "visual_logits": self.visual_head(shared),
            "digit_logits": torch.stack([head(shared) for head in self.digit_heads], dim=1),
        }


def preprocess_number_panel(image: Any) -> Tensor | None:
    """Convert a BGR panel to the canonical [1, 64, 96] float tensor."""
    if image is None or getattr(image, "size", 0) == 0:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    resized = cv2.resize(gray, (PANEL_WIDTH, PANEL_HEIGHT), interpolation=cv2.INTER_AREA)
    normalized = resized.astype(np.float32) / 255.0
    return torch.from_numpy(normalized).unsqueeze(0)


def encode_digits(number: str | None) -> list[int] | None:
    if number is None:
        return None
    text = str(number).strip()
    if not text or len(text) > MAX_DIGITS or not text.isdigit():
        return None
    return [int(char) + 1 for char in text] + [BLANK_INDEX] * (MAX_DIGITS - len(text))


def decode_digits(indices: list[int]) -> str | None:
    if len(indices) != MAX_DIGITS or any(index < 0 or index >= DIGIT_CLASS_COUNT for index in indices):
        return None
    digits: list[str] = []
    seen_blank = False
    for index in indices:
        if index == BLANK_INDEX:
            seen_blank = True
            continue
        if seen_blank:
            return None
        digits.append(str(index - 1))
    return "".join(digits) or None


def architecture_metadata() -> dict[str, Any]:
    return {
        "name": "PanelDigitNetV1",
        "input_shape": [1, PANEL_HEIGHT, PANEL_WIDTH],
        "shared_encoder": "conv16-relu-pool-conv32-relu-pool-conv64-relu-global_avg_pool",
        "heads": {
            "visual": list(VISUAL_STATES),
            "digit_positions": MAX_DIGITS,
            "digit_classes": ["blank", *[str(value) for value in range(10)]],
        },
        "forbidden_components": ["gru", "ctc", "beam_search", "ocr", "digit_detector"],
    }


def contract_metadata() -> dict[str, Any]:
    return {
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "architecture": architecture_metadata(),
        "loss": {
            "visual": "cross_entropy_all_samples",
            "digits": "cross_entropy_confirmed_samples_only",
        },
    }
