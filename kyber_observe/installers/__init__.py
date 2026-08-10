"""Installer registry and public installer lookup API."""

from __future__ import annotations

import importlib

from .base import HarnessInstaller, InstallerContext

InstallerType = type[HarnessInstaller]


class UnknownHarnessError(LookupError):
    """Raised when an installer is not registered for a harness name."""


REGISTRY: dict[str, InstallerType] = {}

_INSTALLER_MODULES: dict[str, str] = {
    "gemini": ".gemini",
    "pi": ".pi",
}


def register_installer(name: str, installer: InstallerType) -> None:
    """Register an installer class under a harness name.

    Installer modules should call this once at module scope.  Keeping the
    registration function here makes adding a future installer a one-line
    change while allowing this package to import before optional modules land.

    Args:
        name: Stable, user-facing harness name.
        installer: Concrete installer class for the harness.

    Raises:
        TypeError: If ``installer`` is not a ``HarnessInstaller`` subclass.
        ValueError: If ``name`` is empty.
    """
    if not name:
        raise ValueError("Installer name must not be empty")
    if not isinstance(installer, type) or not issubclass(
        installer, HarnessInstaller
    ):
        raise TypeError("installer must be a HarnessInstaller subclass")
    REGISTRY[name] = installer


def get_installer(name: str) -> InstallerType:
    """Return the installer class registered for ``name``.

    Known installer modules are imported on demand so the registry remains
    importable while installers are developed independently.

    Args:
        name: User-facing harness name.

    Returns:
        The concrete installer class registered for the harness.

    Raises:
        UnknownHarnessError: If no installer is registered for ``name``.
    """
    if name not in REGISTRY and name in _INSTALLER_MODULES:
        importlib.import_module(_INSTALLER_MODULES[name], __name__)

    try:
        return REGISTRY[name]
    except KeyError as exc:
        raise UnknownHarnessError(
            f"Unknown harness {name!r}; available harnesses: "
            f"{', '.join(sorted(REGISTRY)) or 'none'}"
        ) from exc


__all__ = [
    "HarnessInstaller",
    "InstallerContext",
    "REGISTRY",
    "UnknownHarnessError",
    "get_installer",
    "register_installer",
]
