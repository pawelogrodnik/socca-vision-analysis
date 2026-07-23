from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from torch import Tensor, nn

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_jersey_number_sequence import (
    BLANK_INDEX,
    JerseyNumberSequenceCRNN,
    preprocess_jersey_number_sequence,
    sequence_architecture_metadata,
    sequence_checkpoint_digest,
    sequence_preprocessing_metadata,
    sequence_visual_state_mapping,
)
from app.services.identity_jersey_number_sequence_contract import (
    DIGIT_ALPHABET,
    MAX_DIGIT_LENGTH,
    VISUAL_STATES,
    build_sequence_training_eligibility_report,
    sequence_contract_metadata,
    validate_digit_string,
)


DEFAULT_EPOCHS = 1
MAX_EPOCHS = 100
DEFAULT_BLANK_REGULARIZATION_WEIGHT = 0.01


def select_sequence_training_device(preferred: str | torch.device | None = None) -> torch.device:
    requested = str(preferred or "mps").lower()
    if requested == "mps" and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def train_jersey_number_sequence(
    dataset_manifest: dict[str, Any],
    *,
    image_tensors: dict[str, Tensor] | None = None,
    model: nn.Module | None = None,
    epochs: int = DEFAULT_EPOCHS,
    seed: int = 0,
    device: str | torch.device | None = None,
    blank_regularization_weight: float = DEFAULT_BLANK_REGULARIZATION_WEIGHT,
) -> dict[str, Any]:
    if not 1 <= epochs <= MAX_EPOCHS:
        raise ValueError(f"epochs must be between 1 and {MAX_EPOCHS}")
    if not 0.0 <= blank_regularization_weight <= 0.1:
        raise ValueError("blank_regularization_weight must be between 0 and 0.1")
    torch.manual_seed(seed)
    np.random.seed(seed)
    train_rows = sorted(
        [row for row in dataset_manifest.get("samples") or [] if isinstance(row, dict) and row.get("split") == "train"],
        key=lambda row: str(row.get("sample_key") or ""),
    )
    train_manifest = {
        "split_contract": dataset_manifest.get("split_contract") or {},
        "samples": train_rows,
    }
    eligibility = build_sequence_training_eligibility_report(train_manifest)
    training_dataset_digest = canonical_digest(train_rows)
    train_split_digest = canonical_digest(
        {"split_contract": train_manifest["split_contract"], "sample_keys": [row.get("sample_key") for row in train_rows]}
    )
    target_device = select_sequence_training_device(device)
    network = model or JerseyNumberSequenceCRNN()
    if not isinstance(network, JerseyNumberSequenceCRNN) or network.hidden_size != 64:
        raise ValueError("training requires JerseyNumberSequenceCRNN")
    network = network.to(target_device)
    optimizer = torch.optim.Adam(network.parameters(), lr=1e-3)
    ctc_loss = nn.CTCLoss(blank=BLANK_INDEX, zero_infinity=True)
    visual_loss = nn.CrossEntropyLoss()
    usable = [(row, _sample_tensor(row, image_tensors)) for row in train_rows]
    usable = [(row, image) for row, image in usable if image is not None]
    readable_sequences = [
        (row, image, digits)
        for row, image in usable
        if (digits := _ctc_digits(row)) is not None
    ]
    if not readable_sequences:
        raise ValueError("checkpoint publication refused: no usable readable sequence labels")
    observed_visual_states = sorted(
        {state for row, _ in usable if (state := _visual_state(row)) is not None}
    )
    visual_loss_supported = len(observed_visual_states) >= 2
    trainable = usable if visual_loss_supported else [(row, image) for row, image, _ in readable_sequences]
    total_loss = 0.0
    steps = 0
    epoch_telemetry: list[dict[str, Any]] = []
    for epoch in range(epochs):
        totals: list[float] = []
        ctc_losses: list[float] = []
        regularizer_losses: list[float] = []
        visual_losses: list[float] = []
        decoded_reads = 0
        decoded_exact = 0
        readable_targets = 0
        blank_argmax = 0
        argmax_steps = 0
        overlength_decodes = 0
        null_decodes = 0
        for row, image in trainable:
            try:
                metrics = _train_sample(
                    network,
                    optimizer,
                    ctc_loss,
                    visual_loss,
                    row,
                    image,
                    target_device,
                    visual_loss_supported=visual_loss_supported,
                    blank_regularization_weight=blank_regularization_weight,
                )
            except RuntimeError:
                if target_device.type != "mps":
                    raise
                target_device = torch.device("cpu")
                network = network.to(target_device)
                optimizer = torch.optim.Adam(network.parameters(), lr=1e-3)
                metrics = _train_sample(
                    network,
                    optimizer,
                    ctc_loss,
                    visual_loss,
                    row,
                    image,
                    target_device,
                    visual_loss_supported=visual_loss_supported,
                    blank_regularization_weight=blank_regularization_weight,
                )
            total_loss += metrics["total_loss"]
            totals.append(metrics["total_loss"])
            if metrics["ctc_loss"] is not None:
                ctc_losses.append(metrics["ctc_loss"])
                regularizer_losses.append(metrics["blank_regularizer_loss"])
                readable_targets += 1
                decoded_reads += int(metrics["decoded"] is not None)
                decoded_exact += int(metrics["decoded"] == metrics["target"])
                overlength_decodes += int(metrics["overlength"])
                null_decodes += int(metrics["decoded"] is None)
            if metrics["visual_loss"] is not None:
                visual_losses.append(metrics["visual_loss"])
            blank_argmax += metrics["blank_argmax"]
            argmax_steps += metrics["argmax_steps"]
            steps += 1
        epoch_telemetry.append(
            {
                "epoch": epoch + 1,
                "total_loss": _mean(totals),
                "ctc_loss": _mean(ctc_losses),
                "blank_regularizer_loss": _mean(regularizer_losses),
                "visual_loss": _mean(visual_losses) if visual_loss_supported else None,
                "decoded_train_read_rate": round(decoded_reads / readable_targets, 6) if readable_targets else None,
                "decoded_train_exact_accuracy": round(decoded_exact / readable_targets, 6) if readable_targets else None,
                "blank_argmax_ratio": round(blank_argmax / argmax_steps, 6) if argmax_steps else None,
                "overlength_decode_count": overlength_decodes,
                "null_decode_count": null_decodes,
            }
        )
    if not steps:
        raise ValueError("checkpoint publication refused: zero optimization steps")
    metadata = {
        **sequence_contract_metadata(),
        "architecture": sequence_architecture_metadata(),
        "preprocessing": sequence_preprocessing_metadata(),
        "visual_state_mapping": sequence_visual_state_mapping(),
        "training": {
            "train_dataset_digest": training_dataset_digest,
            "train_split_digest": train_split_digest,
            "parameters": {
                "epochs": epochs,
                "learning_rate": 1e-3,
                "seed": seed,
                "blank_regularization_weight": blank_regularization_weight,
            },
        },
    }
    state_dict = {name: value.detach().cpu().clone() for name, value in network.state_dict().items()}
    checkpoint_digest = sequence_checkpoint_digest(metadata, state_dict)
    report = {
        "training_status": "diagnostic_training_only",
        "seed": seed,
        "device": target_device.type,
        "dataset_digest": training_dataset_digest,
        "split_digest": train_split_digest,
        "model_digest": checkpoint_digest,
        "train_samples": len(train_rows),
        "usable_train_samples": len(usable),
        "optimization_steps": steps,
        "mean_loss": round(total_loss / steps, 6) if steps else None,
        "training_telemetry": {
            "visual_state_loss": {
                "supported": visual_loss_supported,
                "observed_classes": observed_visual_states,
                "reason": None if visual_loss_supported else "fewer_than_two_observed_visual_states",
            },
            "epochs": epoch_telemetry,
        },
        "training_gate": eligibility["training_gate"],
    }
    return {
        "checkpoint": {
            "metadata": metadata,
            "state_dict": state_dict,
            "checkpoint_digest": checkpoint_digest,
            "dataset_digest": training_dataset_digest,
        },
        "report": report,
    }


def _sample_tensor(row: dict[str, Any], image_tensors: dict[str, Tensor] | None) -> Tensor | None:
    key = str(row.get("sample_key") or "")
    if image_tensors and key in image_tensors:
        image = image_tensors[key]
        return image.float() if image.shape == (1, 32, 96) else None
    path = Path(str(row.get("artifact_root") or "")) / str(row.get("artifact") or "")
    image = cv2.imread(str(path))
    return preprocess_jersey_number_sequence(
        image,
        artifact_kind=str(row.get("artifact_kind") or "torso_crop"),
        bbox_xyxy=row.get("bbox_xyxy"),
    )


def _train_sample(
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    ctc_loss: nn.CTCLoss,
    visual_loss: nn.CrossEntropyLoss,
    row: dict[str, Any],
    image: Tensor,
    device: torch.device,
    *,
    visual_loss_supported: bool,
    blank_regularization_weight: float,
) -> dict[str, Any]:
    visual_state = _visual_state(row)
    if visual_state is None:
        raise ValueError("sample visual state is invalid")
    optimizer.zero_grad()
    sequence_logits, visual_logits = model(image.unsqueeze(0).to(device))
    loss: Tensor | None = None
    visual_component: Tensor | None = None
    if visual_loss_supported:
        visual_component = visual_loss(
            visual_logits, torch.tensor([VISUAL_STATES.index(visual_state)], device=device)
        )
        loss = visual_component
    ctc_component: Tensor | None = None
    blank_regularizer = 0.0
    if (digits := _ctc_digits(row)) is not None:
        targets = torch.tensor([DIGIT_ALPHABET.index(digit) for digit in digits], device=device)
        log_probabilities = sequence_logits.log_softmax(dim=-1).transpose(0, 1)
        ctc_component = ctc_loss(
            log_probabilities,
            targets,
            torch.tensor([sequence_logits.shape[1]], dtype=torch.long),
            torch.tensor([len(digits)], dtype=torch.long),
        )
        loss = ctc_component if loss is None else loss + ctc_component
        blank_regularizer_tensor = sequence_logits.softmax(dim=-1)[..., BLANK_INDEX].mean()
        blank_regularizer = float(blank_regularizer_tensor.detach().cpu().item())
        loss = loss + blank_regularizer_tensor * blank_regularization_weight
    if loss is None:
        raise ValueError("sample has no enabled training loss")
    loss.backward()
    optimizer.step()
    decoded, overlength, blank_count, step_count = _decode_telemetry(sequence_logits)
    return {
        "total_loss": float(loss.detach().cpu().item()),
        "ctc_loss": float(ctc_component.detach().cpu().item()) if ctc_component is not None else None,
        "blank_regularizer_loss": blank_regularizer,
        "visual_loss": float(visual_component.detach().cpu().item()) if visual_component is not None else None,
        "target": digits,
        "decoded": decoded,
        "overlength": overlength,
        "blank_argmax": blank_count,
        "argmax_steps": step_count,
    }


def _decode_telemetry(logits: Tensor) -> tuple[str | None, bool, int, int]:
    indices = logits.detach().argmax(dim=-1).reshape(-1).tolist()
    digits: list[str] = []
    previous = BLANK_INDEX
    for index in indices:
        if index != previous and index != BLANK_INDEX:
            digits.append(DIGIT_ALPHABET[index])
        previous = index
    value = "".join(digits)
    overlength = len(value) > MAX_DIGIT_LENGTH
    return (
        value if value and not overlength else None,
        overlength,
        sum(index == BLANK_INDEX for index in indices),
        len(indices),
    )


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _ctc_digits(row: dict[str, Any]) -> str | None:
    if row.get("label_state") != "number_confirmed":
        return None
    value = row.get("visible_digits") if row.get("digit_visibility") == "partial" else row.get("number")
    try:
        return validate_digit_string(value)
    except ValueError:
        return None


def _visual_state(row: dict[str, Any]) -> str | None:
    label_state = str(row.get("label_state") or "")
    if label_state == "number_confirmed":
        return "partial" if row.get("digit_visibility") == "partial" else "full"
    if label_state == "number_absent":
        return "none"
    if label_state == "number_unreadable":
        return "occluded" if row.get("occlusion_state") in {"partial", "heavy"} else "unknown"
    return None
