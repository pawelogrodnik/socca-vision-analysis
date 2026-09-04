from __future__ import annotations

"""Crash recovery for the coupled logical-group manifest/report documents."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


PAIR_TRANSACTION_FILENAME = ".manifest-report.transaction.json"
PREVIOUS_MANIFEST_FILENAME = ".manifest-report.previous-manifest.json"
PREVIOUS_REPORT_FILENAME = ".manifest-report.previous-report.json"
PAIR_TRANSACTION_SCHEMA_VERSION = "1.0.0"
MAINTENANCE_LOCK_FILENAME = "video_job.lock"


class MatchGroupPairTransactionError(RuntimeError):
    pass


class MatchGroupPairTransactionInProgress(MatchGroupPairTransactionError):
    pass


def prepare_pair_recovery(
    directory: Path,
    *,
    previous_manifest: bytes,
    previous_report: bytes | None,
) -> None:
    """Durably record enough state to roll back an interrupted pair commit."""

    if not directory.is_dir():
        raise FileNotFoundError(directory)
    _write_bytes(directory / PREVIOUS_MANIFEST_FILENAME, previous_manifest)
    if previous_report is None:
        (directory / PREVIOUS_REPORT_FILENAME).unlink(missing_ok=True)
    else:
        _write_bytes(directory / PREVIOUS_REPORT_FILENAME, previous_report)
    _write_json(
        directory / PAIR_TRANSACTION_FILENAME,
        {
            "schema_version": PAIR_TRANSACTION_SCHEMA_VERSION,
            "state": "rollback_to_previous_pair",
            "previous_report_exists": previous_report is not None,
        },
    )
    _fsync_directory(directory)


def finish_pair_recovery(directory: Path) -> None:
    """Remove recovery state only after both replacement files are durable."""

    for name in (PAIR_TRANSACTION_FILENAME, PREVIOUS_MANIFEST_FILENAME, PREVIOUS_REPORT_FILENAME):
        (directory / name).unlink(missing_ok=True)
    _fsync_directory(directory)


def recover_interrupted_pair(directory: Path, *, allow_live_owner: bool = False) -> bool:
    """Restore a previous coherent pair after an interrupted two-file commit."""

    marker = directory / PAIR_TRANSACTION_FILENAME
    if not marker.exists():
        return False
    if _maintenance_owner_is_live(directory) and not allow_live_owner:
        raise MatchGroupPairTransactionInProgress("Logical match maintenance is still committing a manifest/report pair.")
    try:
        transaction = _read_json(marker)
        if (
            transaction.get("schema_version") != PAIR_TRANSACTION_SCHEMA_VERSION
            or transaction.get("state") != "rollback_to_previous_pair"
            or not isinstance(transaction.get("previous_report_exists"), bool)
        ):
            raise MatchGroupPairTransactionError("Logical match pair transaction is invalid.")
        previous_manifest = (directory / PREVIOUS_MANIFEST_FILENAME).read_bytes()
        if not previous_manifest:
            raise MatchGroupPairTransactionError("Logical match pair transaction has no previous manifest.")
        previous_report_exists = bool(transaction["previous_report_exists"])
        previous_report = (directory / PREVIOUS_REPORT_FILENAME).read_bytes() if previous_report_exists else None
        if previous_report_exists and not previous_report:
            raise MatchGroupPairTransactionError("Logical match pair transaction has no previous report.")
        _write_bytes(directory / "manifest.json", previous_manifest)
        if previous_report is None:
            (directory / "public_report.json").unlink(missing_ok=True)
        else:
            _write_bytes(directory / "public_report.json", previous_report)
        _fsync_directory(directory)
        finish_pair_recovery(directory)
        return True
    except MatchGroupPairTransactionError:
        raise
    except OSError as error:
        raise MatchGroupPairTransactionError("Could not recover the interrupted logical match pair transaction.") from error
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise MatchGroupPairTransactionError("Logical match pair transaction is invalid.") from error


def _maintenance_owner_is_live(directory: Path) -> bool:
    lock = directory / MAINTENANCE_LOCK_FILENAME
    if not lock.is_file():
        return False
    try:
        owner = _read_json(lock)
        pid = int(owner.get("pid"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MatchGroupPairTransactionError("Logical match pair transaction is invalid.")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write_bytes(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))


def _write_bytes(path: Path, value: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
