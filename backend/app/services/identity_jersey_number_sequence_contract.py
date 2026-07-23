from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_sequence_crnn_ctc"
ALGORITHM_VERSION = "1.0.0"
RECOGNITION_MODE = "unconstrained_digit_sequence_v1"
DIGIT_ALPHABET = tuple("0123456789")
BLANK_TOKEN = "<blank>"
MAX_DIGIT_LENGTH = 3
VISUAL_STATES = ("full", "partial", "none", "occluded", "unknown")
FORBIDDEN_CONSTRAINED_FIELDS = frozenset(
    {
        "candidate_numbers",
        "candidate_vocabulary",
        "roster",
        "roster_doc",
        "prototypes",
        "readable_centroid",
        "absent_centroid",
    }
)


def validate_digit_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("digit_string must be a string or null")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_DIGIT_LENGTH or any(digit not in DIGIT_ALPHABET for digit in normalized):
        raise ValueError("digit_string must contain one to three digits")
    return normalized


def normalize_sequence_prediction(prediction: dict[str, Any]) -> dict[str, Any]:
    _reject_constrained_fields(prediction)
    state = str(prediction.get("visual_state") or "unknown").strip().lower()
    if state not in VISUAL_STATES:
        raise ValueError("visual_state is invalid")
    digit_string = validate_digit_string(prediction.get("digit_string"))
    if state == "none" and digit_string is not None:
        raise ValueError("none visual_state cannot include digits")
    return {
        "digit_string": digit_string,
        "visual_state": state,
        "confidence": _confidence(prediction.get("confidence")),
    }


def sequence_contract_metadata() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "recognition_contract": {
            "name": RECOGNITION_MODE,
            "alphabet": [*DIGIT_ALPHABET, BLANK_TOKEN],
            "blank_token": BLANK_TOKEN,
            "max_digit_length": MAX_DIGIT_LENGTH,
            "visual_states": list(VISUAL_STATES),
        },
        "experiment_status": {
            "status": "deferred_diagnostic",
            "target_ready": False,
            "activation_eligible": False,
        },
    }


def validate_sequence_checkpoint_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    _reject_constrained_fields(metadata)
    algorithm_value = metadata.get("algorithm")
    contract_value = metadata.get("recognition_contract")
    algorithm = algorithm_value if isinstance(algorithm_value, dict) else {}
    contract = contract_value if isinstance(contract_value, dict) else {}
    if (
        metadata.get("schema_version") != SCHEMA_VERSION
        or algorithm.get("name") != ALGORITHM_NAME
        or algorithm.get("version") != ALGORITHM_VERSION
        or contract.get("name") != RECOGNITION_MODE
        or contract.get("alphabet") != [*DIGIT_ALPHABET, BLANK_TOKEN]
        or contract.get("blank_token") != BLANK_TOKEN
        or contract.get("max_digit_length") != MAX_DIGIT_LENGTH
        or contract.get("visual_states") != list(VISUAL_STATES)
    ):
        raise ValueError("checkpoint metadata does not satisfy sequence contract")
    return sequence_contract_metadata()


def build_sequence_training_eligibility_report(dataset_doc: dict[str, Any]) -> dict[str, Any]:
    _reject_constrained_fields(dataset_doc)
    samples = [row for row in dataset_doc.get("samples") or [] if isinstance(row, dict)]
    valid_labels = 0
    invalid_labels = 0
    for sample in samples:
        if sample.get("label_state") != "number_confirmed":
            continue
        try:
            valid_labels += int(validate_digit_string(sample.get("number")) is not None)
        except ValueError:
            invalid_labels += 1
    source_matches = sorted(
        {str(sample.get("source_match_key") or "").strip() for sample in samples if sample.get("source_match_key")}
    )
    split_contract = dataset_doc.get("split_contract") or {}
    mechanically_trainable = bool(valid_labels)
    calibration_eligible = bool(
        mechanically_trainable
        and len(source_matches) >= 3
        and split_contract.get("production_eligible") is True
    )
    reason_codes = ["diagnostic_only_sequence_contract"]
    if invalid_labels:
        reason_codes.append("invalid_digit_string_labels")
    if not mechanically_trainable:
        reason_codes.append("no_valid_digit_string_labels")
    if len(source_matches) < 3:
        reason_codes.append("insufficient_independent_source_matches")
    if not split_contract.get("production_eligible"):
        reason_codes.append("dataset_split_not_production_eligible")
    return {
        "schema_version": SCHEMA_VERSION,
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "recognition_contract": sequence_contract_metadata()["recognition_contract"],
        "summary": {
            "samples": len(samples),
            "valid_digit_string_labels": valid_labels,
            "invalid_digit_string_labels": invalid_labels,
            "independent_source_matches": len(source_matches),
            "source_match_keys": source_matches,
        },
        "training_gate": {
            "mechanically_trainable": mechanically_trainable,
            "diagnostic_only": True,
            "status": "deferred_diagnostic",
            "target_ready": False,
            "activation_eligible": False,
            "calibration_eligible": calibration_eligible,
            "generalization_eligible": calibration_eligible,
            "production_eligible": False,
            "reason_codes": sorted(set(reason_codes)),
        },
    }


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("confidence must be a number or null")
    normalized = float(value)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError("confidence must be between zero and one")
    return normalized


def _reject_constrained_fields(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_CONSTRAINED_FIELDS & set(value)
        if forbidden:
            raise ValueError(f"constrained input forbidden: {sorted(forbidden)[0]}")
        for nested in value.values():
            _reject_constrained_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_constrained_fields(nested)
