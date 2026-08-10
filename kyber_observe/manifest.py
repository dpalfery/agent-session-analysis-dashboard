"""Persistent record of components installed by ``kyber-observe``."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Union

from .config import KYBER_OBSERVE_HOME
from .io import atomic_write, load_json


PathLike = Union[str, os.PathLike[str]]
MANIFEST_FILENAME = "manifest.json"
ENTRY_FIELDS = (
    "harness",
    "component",
    "method",
    "version",
    "source_path",
    "install_path",
    "backup_refs",
    "installed_at",
)


def _observe_home() -> Path:
    """Return the configured home, including runtime env overrides."""
    configured_home = os.environ.get("KYBER_OBSERVE_HOME")
    if configured_home:
        return Path(configured_home).expanduser()
    return KYBER_OBSERVE_HOME


class Manifest:
    """Manage the installed-component manifest.

    Entries are keyed by ``harness``, ``component``, and ``method``.  A
    manifest is stored as ``{"entries": [...]}`` beneath the Kyber Observe
    home.  Missing manifests are treated as an empty manifest.
    """

    def __init__(self, path: Optional[PathLike] = None) -> None:
        """Create a manifest backed by ``path`` or the configured home."""
        self.path = (
            Path(path).expanduser()
            if path is not None
            else _observe_home() / MANIFEST_FILENAME
        )
        self._entries: List[Dict[str, Any]] = []

    @property
    def entries(self) -> List[Dict[str, Any]]:
        """Return a copy of all manifest entries."""
        return copy.deepcopy(self._entries)

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        """Iterate over copies of all manifest entries."""
        return iter(self.entries)

    def read(self) -> "Manifest":
        """Load entries from disk, tolerating a missing manifest file.

        Returns:
            This manifest instance, for convenient chaining.

        Raises:
            ValueError: If the manifest has an invalid top-level shape.
        """
        raw = load_json(self.path, {"entries": []})
        if isinstance(raw, list):
            entries = raw
        elif isinstance(raw, dict) and isinstance(raw.get("entries"), list):
            entries = raw["entries"]
        else:
            raise ValueError(f"Invalid manifest format in {self.path}")
        if not all(isinstance(entry, dict) for entry in entries):
            raise ValueError(f"Invalid manifest entries in {self.path}")
        self._entries = [self._validated_entry(entry) for entry in entries]
        return self

    def write(self) -> None:
        """Atomically write the manifest to disk."""
        payload = json.dumps(
            {"entries": self._entries}, indent=2, sort_keys=True
        )
        atomic_write(self.path, f"{payload}\n")

    def upsert(self, entry: Mapping[str, Any]) -> None:
        """Replace or add an entry keyed by harness, component, and method.

        Args:
            entry: Entry matching the manifest schema.

        Raises:
            ValueError: If a required schema field is missing or malformed.
        """
        validated = self._validated_entry(entry)
        key = self._key(validated)
        self._entries = [
            existing
            for existing in self._entries
            if self._key(existing) != key
        ]
        self._entries.append(validated)

    def has(self, harness: str, component: str) -> bool:
        """Return whether any method is installed for a harness component."""
        return any(
            entry["harness"] == harness and entry["component"] == component
            for entry in self._entries
        )

    def remove(self, harness: str, component: str) -> None:
        """Remove all methods recorded for a harness component."""
        self._entries = [
            entry
            for entry in self._entries
            if not (
                entry["harness"] == harness
                and entry["component"] == component
            )
        ]

    @staticmethod
    def _key(entry: Mapping[str, Any]) -> tuple[str, str, str]:
        """Return the idempotency key for an entry."""
        return (entry["harness"], entry["component"], entry["method"])

    @staticmethod
    def _validated_entry(entry: Mapping[str, Any]) -> Dict[str, Any]:
        """Validate and copy one manifest entry."""
        missing = [field for field in ENTRY_FIELDS if field not in entry]
        if missing:
            missing_fields = ", ".join(missing)
            message = f"Manifest entry missing fields: {missing_fields}"
            raise ValueError(message)
        identity_fields = ENTRY_FIELDS[:6]
        if not all(isinstance(entry[field], str) for field in identity_fields):
            raise ValueError(
                "Manifest identity and path fields must be strings"
            )
        if not isinstance(entry["backup_refs"], list):
            raise ValueError("Manifest backup_refs must be a list")
        if not isinstance(entry["installed_at"], str):
            raise ValueError("Manifest installed_at must be a string")
        return {field: copy.deepcopy(entry[field]) for field in ENTRY_FIELDS}
