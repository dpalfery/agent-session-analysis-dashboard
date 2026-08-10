"""Backup and restore helpers for user configuration files."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
from typing import Optional, Union

from .config import KYBER_OBSERVE_HOME


PathLike = Union[str, os.PathLike[str]]


def _backup_home() -> Path:
    """Return the configured backup root, including runtime env overrides."""
    configured_home = os.environ.get("KYBER_OBSERVE_HOME")
    if configured_home:
        return Path(configured_home).expanduser() / "backups"
    return KYBER_OBSERVE_HOME / "backups"


def backup_file(path: PathLike) -> Optional[str]:
    """Copy a file into a timestamped backup directory.

    Missing source files are tolerated and return ``None`` because there is no
    prior user state to restore.  The returned reference is relative to the
    configured ``backups`` directory and can be passed to
    :func:`restore_file`.

    Args:
        path: File to back up.

    Returns:
        A relative backup reference, or ``None`` if ``path`` does not exist.

    Raises:
        IsADirectoryError: If ``path`` is a directory.
        OSError: If the file cannot be copied.
    """
    source = Path(path).expanduser()
    if not source.exists():
        return None
    if source.is_dir():
        raise IsADirectoryError(source)

    backups_home = _backup_home()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_directory = backups_home / timestamp
    suffix = 1
    while backup_directory.exists():
        backup_directory = backups_home / f"{timestamp}-{suffix}"
        suffix += 1
    backup_directory.mkdir(parents=True)

    destination = backup_directory / source.name
    shutil.copy2(source, destination)
    return str(destination.relative_to(backups_home))


def restore_file(path: PathLike, backup_ref: str) -> None:
    """Restore a file from a reference returned by :func:`backup_file`.

    Args:
        path: Original destination path.
        backup_ref: Relative path beneath the configured ``backups`` directory.

    Raises:
        ValueError: If ``backup_ref`` escapes the backup directory.
        FileNotFoundError: If the referenced backup does not exist.
        OSError: If the file cannot be restored.
    """
    backups_home = _backup_home().resolve()
    backup_path = (backups_home / backup_ref).resolve()
    if backup_path != backups_home and backups_home not in backup_path.parents:
        raise ValueError("backup_ref must remain under the backups directory")
    if not backup_path.is_file():
        raise FileNotFoundError(backup_path)

    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, destination)
