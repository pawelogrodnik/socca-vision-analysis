from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
from typing import Any

import cv2
import torch
import torch.nn.functional as functional
from torch import Tensor, nn

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_jersey_number_common import normalize_jersey_number_annotation
from app.services.identity_jersey_number_common import normalize_normalized_bbox
from app.services.identity_jersey_number_common import normalize_safe_relative_artifact_path
from app.services.identity_jersey_number_panel_digitnet import (
    BLANK_INDEX,
    DIGIT_CLASS_COUNT,
    MAX_DIGITS,
    PANEL_HEIGHT,
    PANEL_WIDTH,
    PanelDigitNetV1,
    VISUAL_STATE_TO_INDEX,
    VISUAL_STATES,
    contract_metadata,
    decode_digits,
    encode_digits,
    preprocess_number_panel,
)


MAX_EPOCHS = 500


# These profiles are intentionally limited to the diagnostic J8.4 model.  They
# do not affect the production identity resolver or any player assignment.
TRAINING_PROFILES: dict[str, dict[str, Any]] = {
    "overfit_baseline": {
        "shuffle_each_epoch": False,
        "augment_panels": False,
        "balance_digit_classes": False,
    },
    "same_match_generalization_v1": {
        "shuffle_each_epoch": True,
        "augment_panels": True,
        "balance_digit_classes": True,
        "max_rotation_degrees": 3.0,
        "max_translate_fraction": 0.035,
        "brightness_jitter": 0.08,
        "contrast_jitter": 0.08,
    },
}


def resolve_panel_training_profile(profile: str) -> dict[str, Any]:
    """Return a copy so callers cannot mutate the registered training profile."""
    try:
        return dict(TRAINING_PROFILES[profile])
    except KeyError as error:
        choices = ", ".join(sorted(TRAINING_PROFILES))
        raise ValueError(f"unknown PanelDigitNet training profile: {profile}; expected one of {choices}") from error


def select_panel_digitnet_device(preferred: str | torch.device | None = None) -> torch.device:
    requested = str(preferred or "mps").lower()
    if requested == "mps" and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def build_panel_training_sets(dataset_doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Produce deterministic R1/R2 samples with episode and number diversity."""
    normalized = _normalized_usable_samples(dataset_doc)
    confirmed = [row for row in normalized if row["jersey_number_state"] == "number_confirmed"]
    negatives = [row for row in normalized if row["jersey_number_state"] != "number_confirmed"]
    confirmed.sort(key=_sort_key)
    negatives.sort(key=_sort_key)
    r1_confirmed = _choose_diverse_confirmed(confirmed, limit=16)
    r2_negatives = _choose_diverse(negatives, limit=16)
    return {"r1": r1_confirmed, "r2": [*r1_confirmed, *r2_negatives]}


def build_panel_r3_split(dataset_doc: dict[str, Any]) -> dict[str, Any]:
    """Create a deterministic same-match holdout without visibility-episode leakage."""
    normalized = _normalized_usable_samples(dataset_doc)
    confirmed = [row for row in normalized if row["jersey_number_state"] == "number_confirmed"]
    negatives = [row for row in normalized if row["jersey_number_state"] != "number_confirmed"]
    confirmed_by_number: dict[str, list[str]] = {}
    for number in sorted({str(row.get("jersey_number") or "") for row in confirmed}):
        episodes = sorted(
            {
                str(row.get("visibility_episode_id") or "")
                for row in confirmed
                if str(row.get("jersey_number") or "") == number and row.get("visibility_episode_id")
            }
        )
        if len(episodes) >= 2:
            confirmed_by_number[number] = episodes

    heldout_episodes = {episodes[0] for episodes in confirmed_by_number.values()}
    negative_episodes = sorted({str(row.get("visibility_episode_id") or "") for row in negatives if row.get("visibility_episode_id")})
    heldout_episodes.update(negative_episodes[: min(8, len(negative_episodes))])
    train_rows = [row for row in normalized if str(row.get("visibility_episode_id") or "") not in heldout_episodes]
    holdout_rows = [row for row in normalized if str(row.get("visibility_episode_id") or "") in heldout_episodes]
    if not train_rows or not holdout_rows:
        raise ValueError("R3 needs both training and heldout visibility episodes")
    if any(str(row.get("visibility_episode_id") or "") in heldout_episodes for row in train_rows):
        raise AssertionError("R3 visibility episode leaked into training")
    return {
        "train": sorted(train_rows, key=_sort_key),
        "holdout": sorted(holdout_rows, key=_sort_key),
        "heldout_episode_ids": sorted(heldout_episodes),
        "heldout_confirmed_numbers": sorted(confirmed_by_number),
    }


def evaluate_panel_digitnet_r3(
    model: PanelDigitNetV1,
    holdout_samples: list[dict[str, Any]],
    *,
    device: str | torch.device | None = None,
) -> dict[str, Any]:
    """Report only same-match heldout diagnostic metrics required by J8.4 R3."""
    prepared = _prepare_samples(holdout_samples)
    evaluation = evaluate_panel_digitnet(model, prepared, device=device)
    predictions = evaluation["predictions"]
    by_episode: dict[str, list[dict[str, Any]]] = {}
    sample_lookup = {str(row.get("sample_key") or ""): row for row, _ in prepared}
    for prediction in predictions:
        sample = sample_lookup.get(str(prediction.get("sample_key") or ""), {})
        episode = str(sample.get("visibility_episode_id") or "")
        if episode:
            by_episode.setdefault(episode, []).append({**prediction, "target_episode": episode})

    confirmed_episodes = correct_confirmed_episodes = predicted_confirmed_episodes = 0
    episode_rows: list[dict[str, Any]] = []
    real10_result: dict[str, Any] | None = None
    plain_shirt_rows = [row for row in predictions if row["target_state"] == "number_absent"]
    plain_shirt_false_confirmed = sum(row["predicted_state"] == "number_confirmed" for row in plain_shirt_rows)
    for episode, rows in sorted(by_episode.items()):
        target_states = {str(row["target_state"]) for row in rows}
        target_numbers = {str(row["target_number"]) for row in rows if row["target_number"] is not None}
        # A visibility episode can contain both a readable frame and nearby
        # unreadable frames of the same shirt. It remains a confirmed episode
        # when its confirmed observations agree on exactly one number.
        is_confirmed = len(target_numbers) == 1 and "number_absent" not in target_states
        predicted_number_set = {str(row["predicted_number"]) for row in rows if row["predicted_number"] is not None}
        predicted_confirmed = any(row["predicted_state"] == "number_confirmed" for row in rows)
        exact = is_confirmed and all(
            (
                row["target_state"] == "number_unreadable"
                and row["predicted_state"] != "number_confirmed"
            )
            or (
                row["target_state"] == "number_confirmed"
                and row["predicted_state"] == "number_confirmed"
                and row["predicted_number"] in target_numbers
            )
            for row in rows
        )
        if is_confirmed:
            confirmed_episodes += 1
            correct_confirmed_episodes += int(exact)
        predicted_confirmed_episodes += int(predicted_confirmed)
        entry = {
            "visibility_episode_id": episode,
            "target_states": sorted(target_states),
            "target_numbers": sorted(target_numbers),
            "predicted_numbers": sorted(predicted_number_set),
            "predicted_confirmed": predicted_confirmed,
            "exact_sequence": exact,
            "sample_count": len(rows),
        }
        episode_rows.append(entry)
        if target_numbers == {"10"} and real10_result is None:
            real10_result = entry

    return {
        "stage": "r3_same_match_heldout",
        "status": "diagnostic_only",
        "holdout_sample_count": len(prepared),
        "holdout_episode_count": len(episode_rows),
        "crop_exact_sequence_accuracy": evaluation["exact_sequence_accuracy"],
        "episode_exact_sequence_accuracy": _ratio(correct_confirmed_episodes, confirmed_episodes),
        "episode_precision": _ratio(correct_confirmed_episodes, predicted_confirmed_episodes),
        "episode_recall": _ratio(correct_confirmed_episodes, confirmed_episodes),
        "plain_shirt_false_confirmed_reads": plain_shirt_false_confirmed if plain_shirt_rows else None,
        "plain_shirt_evaluation_status": "available" if plain_shirt_rows else "not_assessable_no_number_absent_panels",
        "real10_episode_result": real10_result,
        "episodes": episode_rows,
        "predictions": predictions,
    }


def _normalized_usable_samples(dataset_doc: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for source in dataset_doc.get("samples") or []:
        if not isinstance(source, dict):
            continue
        # Unreviewed discovery candidates have an empty jersey_number field for
        # compatibility. They are not negatives and must never enter training.
        if source.get("jersey_number_state") in (None, "") and source.get("label_state") in (None, ""):
            continue
        try:
            annotation = normalize_jersey_number_annotation(source, allow_missing=False)
        except ValueError:
            continue
        state = annotation["jersey_number_state"]
        if state not in VISUAL_STATE_TO_INDEX:
            continue
        row = {**source, **annotation}
        if _load_panel_tensor(row) is not None:
            normalized.append(row)

    return normalized


def train_panel_digitnet(
    samples: list[dict[str, Any]],
    *,
    epochs: int = MAX_EPOCHS,
    seed: int = 0,
    device: str | torch.device | None = None,
    model: PanelDigitNetV1 | None = None,
    profile: str = "overfit_baseline",
) -> dict[str, Any]:
    """Train only the tiny J8.4 diagnostic model, never production identity."""
    if not 1 <= epochs <= MAX_EPOCHS:
        raise ValueError(f"epochs must be between 1 and {MAX_EPOCHS}")
    torch.manual_seed(seed)
    profile_config = resolve_panel_training_profile(profile)
    target_device = select_panel_digitnet_device(device)
    prepared = _prepare_samples(samples)
    if not prepared:
        raise ValueError("no trainable panel samples")
    network = (model or PanelDigitNetV1()).to(target_device)
    optimizer = torch.optim.Adam(network.parameters(), lr=1e-3)
    visual_loss = nn.CrossEntropyLoss()
    digit_losses = _build_digit_losses(
        prepared,
        device=target_device,
        balance_classes=bool(profile_config["balance_digit_classes"]),
    )
    telemetry: list[dict[str, Any]] = []
    for epoch in range(epochs):
        network.train()
        total_losses: list[float] = []
        indices = list(range(len(prepared)))
        if profile_config["shuffle_each_epoch"]:
            indices = torch.randperm(len(prepared)).tolist()
        for index in indices:
            row, image = prepared[index]
            training_image = _augment_panel(image, profile_config) if profile_config["augment_panels"] else image
            try:
                loss = _train_one(network, optimizer, visual_loss, digit_losses, row, training_image, target_device)
            except RuntimeError:
                if target_device.type != "mps":
                    raise
                target_device = torch.device("cpu")
                network = network.to(target_device)
                optimizer = torch.optim.Adam(network.parameters(), lr=1e-3)
                digit_losses = _build_digit_losses(
                    prepared,
                    device=target_device,
                    balance_classes=bool(profile_config["balance_digit_classes"]),
                )
                loss = _train_one(network, optimizer, visual_loss, digit_losses, row, training_image, target_device)
            total_losses.append(loss)
        if epoch == 0 or (epoch + 1) % 25 == 0 or epoch + 1 == epochs:
            telemetry.append({"epoch": epoch + 1, "mean_loss": round(sum(total_losses) / len(total_losses), 6)})
    evaluation = evaluate_panel_digitnet(network, prepared, device=target_device)
    state_dict = {name: value.detach().cpu().clone() for name, value in network.state_dict().items()}
    metadata = {
        **contract_metadata(),
        "training": {
            "epochs": epochs,
            "seed": seed,
            "device": target_device.type,
            "samples_digest": canonical_digest([row["sample_key"] for row, _ in prepared]),
            "sample_count": len(prepared),
            "profile": profile,
            "profile_config": profile_config,
        },
    }
    checkpoint_digest = canonical_digest(
        {"metadata": metadata, "state_shapes": {key: list(value.shape) for key, value in state_dict.items()}}
    )
    return {
        "checkpoint": {"metadata": metadata, "state_dict": state_dict, "checkpoint_digest": checkpoint_digest},
        "report": {
            "status": "diagnostic_training_only",
            "device": target_device.type,
            "sample_count": len(prepared),
            "number_distribution": dict(sorted(Counter(str(row.get("jersey_number")) for row, _ in prepared).items())),
            "visual_state_distribution": dict(sorted(Counter(str(row.get("jersey_number_state")) for row, _ in prepared).items())),
            "training_profile": profile,
            "training_profile_config": profile_config,
            "telemetry": telemetry,
            "evaluation": evaluation,
        },
    }


def evaluate_panel_digitnet(
    model: PanelDigitNetV1,
    prepared: list[tuple[dict[str, Any], Tensor]],
    *,
    device: str | torch.device | None = None,
) -> dict[str, Any]:
    target_device = select_panel_digitnet_device(device)
    model = model.to(target_device)
    model.eval()
    visual_correct = readable_true_positive = readable_total = negative_true_negative = negative_total = 0
    exact_correct = exact_total = null_predictions = 0
    rows: list[dict[str, Any]] = []
    with torch.no_grad():
        for row, image in prepared:
            output = model(image.unsqueeze(0).to(target_device))
            visual_index = int(output["visual_logits"].argmax(dim=1).item())
            predicted_state = VISUAL_STATES[visual_index]
            digit_indices = output["digit_logits"].argmax(dim=2).squeeze(0).tolist()
            raw_predicted_number = decode_digits([int(value) for value in digit_indices])
            predicted_number = raw_predicted_number if predicted_state == "number_confirmed" else None
            target_state = str(row["jersey_number_state"])
            visual_correct += int(predicted_state == target_state)
            if target_state == "number_confirmed":
                readable_total += 1
                readable_true_positive += int(predicted_state == "number_confirmed")
                exact_total += 1
                exact_correct += int(predicted_state == target_state and predicted_number == row.get("jersey_number"))
                null_predictions += int(predicted_number is None)
            else:
                negative_total += 1
                negative_true_negative += int(predicted_state != "number_confirmed")
            rows.append({
                "sample_key": row.get("sample_key"),
                "target_state": target_state,
                "predicted_state": predicted_state,
                "target_number": row.get("jersey_number"),
                "predicted_number": predicted_number,
                "raw_predicted_number": raw_predicted_number,
            })
    return {
        "visual_state_accuracy": _ratio(visual_correct, len(prepared)),
        "readable_recall": _ratio(readable_true_positive, readable_total),
        "negative_specificity": _ratio(negative_true_negative, negative_total),
        "exact_sequence_accuracy": _ratio(exact_correct, exact_total),
        "null_prediction_count": null_predictions,
        "predictions": rows,
    }


def _prepare_samples(samples: list[dict[str, Any]]) -> list[tuple[dict[str, Any], Tensor]]:
    prepared: list[tuple[dict[str, Any], Tensor]] = []
    for row in sorted(samples, key=_sort_key):
        image = _load_panel_tensor(row)
        if image is not None:
            prepared.append((row, image))
    return prepared


def _train_one(
    model: PanelDigitNetV1,
    optimizer: torch.optim.Optimizer,
    visual_loss: nn.CrossEntropyLoss,
    digit_losses: list[nn.CrossEntropyLoss],
    row: dict[str, Any],
    image: Tensor,
    device: torch.device,
) -> float:
    output = model(image.unsqueeze(0).to(device))
    target_state = torch.tensor([VISUAL_STATE_TO_INDEX[str(row["jersey_number_state"])]], device=device)
    loss = visual_loss(output["visual_logits"], target_state)
    if row["jersey_number_state"] == "number_confirmed":
        encoded = encode_digits(str(row.get("jersey_number") or ""))
        if encoded is None:
            raise ValueError("confirmed panel without a valid target number")
        targets = torch.tensor(encoded, device=device)
        for position in range(MAX_DIGITS):
            loss = loss + digit_losses[position](output["digit_logits"][:, position, :], targets[position].unsqueeze(0))
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
    return float(loss.detach().cpu().item())


def _build_digit_losses(
    prepared: list[tuple[dict[str, Any], Tensor]],
    *,
    device: torch.device,
    balance_classes: bool,
) -> list[nn.CrossEntropyLoss]:
    if not balance_classes:
        return [nn.CrossEntropyLoss() for _ in range(MAX_DIGITS)]
    class_count = DIGIT_CLASS_COUNT
    counts = torch.zeros((MAX_DIGITS, class_count), dtype=torch.float32)
    for row, _ in prepared:
        if row["jersey_number_state"] != "number_confirmed":
            continue
        encoded = encode_digits(str(row.get("jersey_number") or ""))
        if encoded is None:
            continue
        for position, value in enumerate(encoded):
            counts[position, value] += 1
    losses: list[nn.CrossEntropyLoss] = []
    for position in range(MAX_DIGITS):
        weights = torch.ones(class_count, dtype=torch.float32)
        observed = counts[position] > 0
        if bool(observed.any()):
            mean_count = counts[position, observed].mean()
            # Square-root inverse-frequency weighting is deliberately mild:
            # it helps rare jerseys without turning a small audit into a
            # synthetic class-balanced dataset.
            weights[observed] = torch.sqrt(mean_count / counts[position, observed])
            weights = weights / weights.mean()
            weights = weights.clamp(min=0.25, max=3.0)
        losses.append(nn.CrossEntropyLoss(weight=weights.to(device)))
    return losses


def _augment_panel(image: Tensor, profile_config: dict[str, Any]) -> Tensor:
    """Apply small, readable-preserving panel transforms during training only."""
    max_rotation = math.radians(float(profile_config["max_rotation_degrees"]))
    angle = (torch.rand(1).item() * 2.0 - 1.0) * max_rotation
    translate = float(profile_config["max_translate_fraction"])
    tx = (torch.rand(1).item() * 2.0 - 1.0) * translate
    ty = (torch.rand(1).item() * 2.0 - 1.0) * translate
    cosine, sine = math.cos(angle), math.sin(angle)
    transform = torch.tensor([[cosine, -sine, tx], [sine, cosine, ty]], dtype=image.dtype).unsqueeze(0)
    batch = image.unsqueeze(0)
    grid = functional.affine_grid(transform, batch.size(), align_corners=False)
    augmented = functional.grid_sample(batch, grid, mode="bilinear", padding_mode="border", align_corners=False)
    contrast = 1.0 + (torch.rand(1).item() * 2.0 - 1.0) * float(profile_config["contrast_jitter"])
    brightness = (torch.rand(1).item() * 2.0 - 1.0) * float(profile_config["brightness_jitter"])
    return (augmented.squeeze(0) * contrast + brightness).clamp(0.0, 1.0)


def _load_panel_tensor(row: dict[str, Any]) -> Tensor | None:
    root = Path(str(row.get("artifact_root") or ""))
    try:
        artifact = normalize_safe_relative_artifact_path(row.get("number_panel_artifact") or row.get("artifact"), field_name="artifact")
        bbox = None if row.get("number_panel_artifact") else normalize_normalized_bbox(row.get("number_panel_bbox_normalized"), field_name="number_panel_bbox_normalized")
    except ValueError:
        return None
    if artifact is None:
        return None
    image = cv2.imread(str(root / artifact))
    if image is None:
        return None
    if bbox is not None:
        height, width = image.shape[:2]
        x1 = max(0, min(width - 1, math.floor(bbox[0] * width)))
        y1 = max(0, min(height - 1, math.floor(bbox[1] * height)))
        x2 = max(x1 + 1, min(width, math.ceil(bbox[2] * width)))
        y2 = max(y1 + 1, min(height, math.ceil(bbox[3] * height)))
        image = image[y1:y2, x1:x2]
    return preprocess_number_panel(image)


def _choose_diverse_confirmed(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    numbers: set[str] = set()
    episodes: set[str] = set()
    for row in rows:
        number = str(row.get("jersey_number") or "")
        episode = str(row.get("visibility_episode_id") or "")
        if number not in numbers or episode not in episodes:
            chosen.append(row)
            numbers.add(number)
            episodes.add(episode)
        if len(chosen) >= limit:
            return chosen
    return _fill_unique(chosen, rows, limit)


def _choose_diverse(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    episodes: set[str] = set()
    for row in rows:
        episode = str(row.get("visibility_episode_id") or "")
        if episode and episode in episodes:
            continue
        chosen.append(row)
        episodes.add(episode)
        if len(chosen) >= limit:
            return chosen
    return _fill_unique(chosen, rows, limit)


def _fill_unique(chosen: list[dict[str, Any]], rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen = {str(row.get("sample_key") or "") for row in chosen}
    for row in rows:
        key = str(row.get("sample_key") or "")
        if key and key not in seen:
            chosen.append(row)
            seen.add(key)
        if len(chosen) >= limit:
            break
    return chosen


def _sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (str(row.get("visibility_episode_id") or ""), int(row.get("frame") or 0), str(row.get("sample_key") or ""))


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None
