"""Small, safe file and JSON operations used by the installer."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union


PathLike = Union[str, os.PathLike[str]]


class JsonLoadError(ValueError):
    """Raised when a JSON file exists but cannot be decoded."""

    def __init__(self, path: Path, cause: json.JSONDecodeError) -> None:
        super().__init__(f"Malformed JSON in {path}: {cause.msg}")
        self.path = path
        self.cause = cause


def atomic_write(path: PathLike, content: str) -> None:
    """Write text to ``path`` atomically.

    Args:
        path: Destination file path.
        content: Text to write.

    Raises:
        OSError: If the destination cannot be created or replaced.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = temporary_file.name
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def load_json(path: PathLike, default: Any) -> Any:
    """Load JSON from a file, returning ``default`` when it is absent.

    Args:
        path: JSON file path.
        default: Value to return if the file does not exist.

    Returns:
        The decoded JSON value, or ``default`` for a missing file.

    Raises:
        JsonLoadError: If the file contains malformed JSON.
        OSError: If the file cannot be read.
    """
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as json_file:
            return json.load(json_file)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as error:
        raise JsonLoadError(source, error) from error


def merge_json_preserving(
    existing: Dict[str, Any], incoming: Dict[str, Any]
) -> Dict[str, Any]:
    """Deep-merge ``incoming`` into a copy of ``existing``.

    Unknown keys already present in ``existing`` are retained. Nested mappings
    are merged recursively; values supplied by ``incoming`` otherwise replace
    existing values.

    Args:
        existing: Existing JSON object.
        incoming: JSON object containing updates.

    Returns:
        A merged dictionary without modifying either input dictionary.
    """
    merged = copy.deepcopy(existing)
    _deep_merge(merged, incoming)
    return merged


def _deep_merge(target: Dict[str, Any], updates: Dict[str, Any]) -> None:
    """Merge updates into target in place."""
    for key, value in updates.items():
        if isinstance(target.get(key), dict) and isinstance(value, dict):
            _deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def copytree_excluding(
    src: PathLike,
    dst: PathLike,
    exclude: Iterable[str] = ("node_modules", "__pycache__"),
) -> None:
    """Recursively copy a directory while skipping named entries.

    Args:
        src: Source directory.
        dst: Destination directory.
        exclude: Directory or file names to omit at any depth.

    Raises:
        OSError: If the source cannot be read or the destination cannot be
            written.
    """
    excluded = set(exclude)
    shutil.copytree(
        Path(src),
        Path(dst),
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*excluded),
    )
