"""Contracts shared by harness installers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class InstallerContext:
    """Options supplied to an installer operation.

    Args:
        endpoint: OTLP endpoint to use for the installation.
        method: Installer-specific installation method.
        component: Component to operate on, or ``None`` for all components.
        dry_run: Whether the operation must avoid filesystem changes.
        force: Whether an existing installation should be replaced.
    """

    endpoint: str
    method: str
    component: str | None
    dry_run: bool
    force: bool


class HarnessInstaller(ABC):
    """Base interface for installers for a supported harness."""

    components: ClassVar[list[str]] = []

    @abstractmethod
    def install(self, ctx: InstallerContext) -> None:
        """Install the requested harness components.

        Args:
            ctx: Options controlling the installation.

        Raises:
            NotImplementedError: If a concrete installer does not implement
                this operation.
        """

    @abstractmethod
    def uninstall(self, ctx: InstallerContext) -> None:
        """Remove the requested harness components.

        Args:
            ctx: Options controlling the uninstall operation.

        Raises:
            NotImplementedError: If a concrete installer does not implement
                this operation.
        """

    @abstractmethod
    def status(self) -> dict[str, object]:
        """Return installation status for the harness components.

        Returns:
            A mapping describing the current installation state.

        Raises:
            NotImplementedError: If a concrete installer does not implement
                this operation.
        """
