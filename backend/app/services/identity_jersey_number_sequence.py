from __future__ import annotations

import hashlib
import json
from typing import Any

import cv2
import numpy as np
import torch
from torch import Tensor, nn

from app.services.identity_jersey_number_sequence_contract import (
    DIGIT_ALPHABET,
    MAX_DIGIT_LENGTH,
    VISUAL_STATES,
    normalize_sequence_prediction,
    validate_sequence_checkpoint_metadata,
)


IMAGE_HEIGHT = 32
IMAGE_WIDTH = 96
BLANK_INDEX = len(DIGIT_ALPHABET)


def sequence_preprocessing_metadata() -> dict[str, Any]:
    return {
        "artifact_kinds": ["torso_crop", "anchor_crop", "full_person_crop"],
        "anchor_crop_bbox_aware": True,
        "color": "grayscale_bgr_or_bgra",
        "roi": {
            "name": "tight_upper_torso_panel_v1",
            "x_fraction": [0.16, 0.84],
            "y_fraction": [0.18, 0.78],
        },
        "resize": "opencv_inter_area_aspect_pad",
        "shape": [1, IMAGE_HEIGHT, IMAGE_WIDTH],
    }


def sequence_architecture_metadata() -> dict[str, Any]:
    return {
        "name": "small_crnn_cnn_bigru_v1",
        "cnn_channels": [1, 32, 64],
        "gru_hidden_size": 64,
        "gru_bidirectional": True,
        "sequence_steps": 24,
        "ctc_logits": BLANK_INDEX + 1,
        "visual_state_logits": len(VISUAL_STATES),
    }


def sequence_visual_state_mapping() -> dict[str, int]:
    return {state: index for index, state in enumerate(VISUAL_STATES)}


def preprocess_jersey_number_sequence(
    image: Any,
    *,
    artifact_kind: str = "torso_crop",
    bbox_xyxy: list[float] | None = None,
) -> Tensor | None:
    if not isinstance(image, np.ndarray) or image.size == 0 or image.ndim not in (2, 3):
        return None
    try:
        if artifact_kind == "anchor_crop":
            image = _anchor_person_crop(image, bbox_xyxy)
        elif artifact_kind not in {"torso_crop", "full_person_crop"}:
            return None
        image = _upper_torso_panel_crop(image)
        if image.ndim == 2:
            gray = image
        elif image.shape[2] == 1:
            gray = image[:, :, 0]
        elif image.shape[2] == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        elif image.shape[2] == 4:
            gray = cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        else:
            return None
        if gray.shape[0] < 1 or gray.shape[1] < 1:
            return None
        scale = min(IMAGE_WIDTH / gray.shape[1], IMAGE_HEIGHT / gray.shape[0])
        resized = cv2.resize(
            gray,
            (max(1, round(gray.shape[1] * scale)), max(1, round(gray.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
        canvas = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH), dtype=np.uint8)
        top = (IMAGE_HEIGHT - resized.shape[0]) // 2
        left = (IMAGE_WIDTH - resized.shape[1]) // 2
        canvas[top : top + resized.shape[0], left : left + resized.shape[1]] = resized
        return torch.from_numpy(canvas.astype(np.float32) / 255.0).unsqueeze(0)
    except (cv2.error, TypeError, ValueError):
        return None


def _anchor_person_crop(image: np.ndarray, bbox_xyxy: list[float] | None) -> np.ndarray:
    height, width = image.shape[:2]
    if isinstance(bbox_xyxy, list) and len(bbox_xyxy) == 4:
        person_width = max(1, min(width, int(round(float(bbox_xyxy[2]) - float(bbox_xyxy[0])))))
        person_height = max(1, min(height, int(round(float(bbox_xyxy[3]) - float(bbox_xyxy[1])))))
    else:
        person_width = max(1, int(round(width / 1.6)))
        person_height = max(1, int(round(height / 1.4)))
    left = max(0, (width - person_width) // 2)
    top = max(0, (height - person_height) // 2)
    return image[top : top + person_height, left : left + person_width]


def _upper_torso_panel_crop(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    top = int(round(height * 0.18))
    bottom = max(top + 1, int(round(height * 0.78)))
    left = int(round(width * 0.16))
    right = max(left + 1, int(round(width * 0.84)))
    return image[top:min(height, bottom), left:min(width, right)]


class JerseyNumberSequenceCRNN(nn.Module):
    def __init__(self, hidden_size: int = 64) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.AdaptiveAvgPool2d((1, 24)),
        )
        self.sequence = nn.GRU(64, hidden_size, batch_first=True, bidirectional=True)
        self.sequence_head = nn.Linear(hidden_size * 2, BLANK_INDEX + 1)
        self.visual_head = nn.Linear(hidden_size * 2, len(VISUAL_STATES))

    def forward(self, image: Tensor) -> tuple[Tensor, Tensor]:
        features = self.features(image).squeeze(2).transpose(1, 2)
        sequence, _ = self.sequence(features)
        return self.sequence_head(sequence), self.visual_head(sequence.mean(dim=1))


def decode_ctc_greedy(logits: Tensor) -> str | None:
    indices = logits.detach().argmax(dim=-1).reshape(-1).tolist()
    digits: list[str] = []
    previous = BLANK_INDEX
    for index in indices:
        if index != previous and index != BLANK_INDEX:
            if index < 0 or index >= len(DIGIT_ALPHABET):
                return None
            digits.append(DIGIT_ALPHABET[index])
        previous = index
    value = "".join(digits)
    return value if 0 < len(value) <= MAX_DIGIT_LENGTH else None


def sequence_checkpoint_digest(metadata: dict[str, Any], state_dict: dict[str, Tensor]) -> str:
    digest = hashlib.sha256()
    validate_sequence_checkpoint_metadata(metadata)
    digest.update(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    for name, value in sorted(state_dict.items()):
        tensor = value.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(tensor.dtype).encode("utf-8"))
        digest.update(repr(tuple(tensor.shape)).encode("utf-8"))
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


def load_sequence_checkpoint(checkpoint: dict[str, Any], *, device: str | torch.device = "cpu") -> JerseyNumberSequenceCRNN:
    metadata = checkpoint.get("metadata")
    state_dict = checkpoint.get("state_dict")
    if not isinstance(metadata, dict) or not isinstance(state_dict, dict):
        raise ValueError("checkpoint metadata and state_dict are required")
    validate_sequence_checkpoint_metadata(metadata)
    _validate_runtime_checkpoint_metadata(metadata)
    if checkpoint.get("checkpoint_digest") != sequence_checkpoint_digest(metadata, state_dict):
        raise ValueError("checkpoint digest does not match")
    model = JerseyNumberSequenceCRNN()
    model.load_state_dict(state_dict)
    return model.to(device).eval()


@torch.inference_mode()
def predict_jersey_number_sequence(
    model: JerseyNumberSequenceCRNN,
    image: Any,
    *,
    artifact_kind: str = "torso_crop",
    bbox_xyxy: list[float] | None = None,
) -> dict[str, Any]:
    prepared = preprocess_jersey_number_sequence(
        image, artifact_kind=artifact_kind, bbox_xyxy=bbox_xyxy
    )
    if prepared is None:
        return _diagnostic_prediction(None, "unknown", 0.0)
    device = next(model.parameters()).device
    sequence_logits, visual_logits = model(prepared.unsqueeze(0).to(device))
    visual_probabilities = visual_logits.softmax(dim=-1)[0]
    visual_index = int(visual_probabilities.argmax().item())
    visual_state = VISUAL_STATES[visual_index]
    digit_string = decode_ctc_greedy(sequence_logits[0])
    state_confidence = float(visual_probabilities[visual_index].item())
    digit_confidence = _decoded_digit_confidence(sequence_logits[0]) if digit_string else 0.0
    return _diagnostic_prediction(digit_string, visual_state, state_confidence * digit_confidence)


def _decoded_digit_confidence(logits: Tensor) -> float:
    probabilities = logits.softmax(dim=-1)
    indices = probabilities.argmax(dim=-1).tolist()
    values: list[float] = []
    previous = BLANK_INDEX
    for position, index in enumerate(indices):
        if index != previous and index != BLANK_INDEX and 0 <= index < len(DIGIT_ALPHABET):
            values.append(float(probabilities[position, index].item()))
        previous = index
    return sum(values) / len(values) if values else 0.0


def _diagnostic_prediction(digit_string: str | None, visual_state: str, confidence: float) -> dict[str, Any]:
    prediction = normalize_sequence_prediction(
        {"digit_string": None, "visual_state": visual_state, "confidence": 0.0}
    )
    return {
        **prediction,
        "raw_digit_string": digit_string,
        "raw_sequence_confidence": confidence,
        "accepted": False,
        "activation_eligible": False,
        "accepted_identity_evidence": None,
        "reason_codes": ["diagnostic_single_match_uncalibrated"],
    }


def _validate_runtime_checkpoint_metadata(metadata: dict[str, Any]) -> None:
    training = metadata.get("training")
    if (
        metadata.get("architecture") != sequence_architecture_metadata()
        or metadata.get("preprocessing") != sequence_preprocessing_metadata()
        or metadata.get("visual_state_mapping") != sequence_visual_state_mapping()
        or not isinstance(training, dict)
        or not isinstance(training.get("train_dataset_digest"), str)
        or not isinstance(training.get("train_split_digest"), str)
        or not isinstance(training.get("parameters"), dict)
    ):
        raise ValueError("checkpoint runtime metadata is incompatible")
