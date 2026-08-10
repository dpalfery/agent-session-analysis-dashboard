"""Install the Pi statusline extension and ObservMe configuration.

The repository copy of the extension is the reliable, offline installation
method.  ``pi-install`` is provided for users who have the published npm
package available, but ``@dpalfery/pi-statusline`` may not yet be published.
Consequently, ``copy`` is the default method for the extension.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess

from ..backup import backup_file, restore_file
from ..config import DEFAULT_ENDPOINT, PI_AGENT_HOME
from ..io import atomic_write, copytree_excluding
from ..manifest import Manifest
from . import register_installer
from .base import HarnessInstaller, InstallerContext


_HARNESS = "pi"
_EXTENSION = "extension"
_OBSERVME = "observme"
_DEFAULT_METHOD = "copy"
_EXTENSION_NAME = "pi-statusline"


def _repository_root() -> Path:
    """Return the repository root containing this package."""
    return Path(__file__).resolve().parents[2]


def _extension_source() -> Path:
    """Return the in-repository Pi extension source directory."""
    return _repository_root() / "collectors" / "pi"


def _observme_template() -> Path:
    """Return the in-repository ObservMe configuration template."""
    return _repository_root() / ".pi" / "observme.yaml"


def _components(component: str | None) -> list[str]:
    """Expand an optional component selection and validate its name."""
    selected = [_EXTENSION, _OBSERVME] if component is None else [component]
    invalid = [item for item in selected if item not in PiInstaller.components]
    if invalid:
        raise ValueError(f"Unknown Pi component(s): {', '.join(invalid)}")
    return selected


def _manifest_entry(
    component: str,
    method: str,
    source_path: Path,
    install_path: Path,
    backup_refs: list[str],
) -> dict[str, object]:
    """Build a manifest entry for one installed Pi component."""
    return {
        "harness": _HARNESS,
        "component": component,
        "method": method,
        "version": "0.1.0",
        "source_path": str(source_path),
        "install_path": str(install_path),
        "backup_refs": backup_refs,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }


class PiInstaller(HarnessInstaller):
    """Install and manage Pi telemetry components."""

    components = [_EXTENSION, _OBSERVME]

    def install(self, ctx: InstallerContext) -> None:
        """Install selected Pi components.

        Args:
            ctx: Installation options.

        Raises:
            FileNotFoundError: If a repository source file is absent.
            ValueError: If a component or installation method is invalid.
            OSError: If a filesystem operation fails.
        """
        manifest = Manifest().read()
        for component in _components(ctx.component):
            method = ctx.method or _DEFAULT_METHOD
            if component == _OBSERVME:
                method = "copy"
            if manifest.has(_HARNESS, component) and not ctx.force:
                self._announce(
                    ctx,
                    f"skip {component}: already installed (use --force to replace)",
                )
                continue
            if component == _EXTENSION:
                self._install_extension(ctx, manifest, method)
            else:
                self._install_observme(ctx, manifest)

    def uninstall(self, ctx: InstallerContext) -> None:
        """Remove selected Pi components and restore backed-up files.

        Args:
            ctx: Uninstallation options.

        Raises:
            ValueError: If the component selection is invalid.
            OSError: If removal or restoration fails.
        """
        manifest = Manifest().read()
        selected = set(_components(ctx.component))
        entries = [
            entry
            for entry in manifest
            if entry["harness"] == _HARNESS
            and entry["component"] in selected
        ]
        for entry in entries:
            install_path = Path(entry["install_path"])
            self._announce(ctx, f"remove {install_path}")
            if ctx.dry_run:
                continue
            if install_path.is_dir():
                shutil.rmtree(install_path)
            elif install_path.exists():
                install_path.unlink()
            for backup_ref in entry["backup_refs"]:
                restore_file(install_path, backup_ref)
            manifest.remove(_HARNESS, entry["component"])
        if entries and not ctx.dry_run:
            manifest.write()

    def status(self) -> dict[str, object]:
        """Return installation state for each Pi component.

        Returns:
            A mapping containing per-component installed state and manifest
            details.
        """
        manifest = Manifest().read()
        result: dict[str, object] = {"harness": _HARNESS, "components": {}}
        states = result["components"]
        assert isinstance(states, dict)
        for component in self.components:
            entries = [
                entry
                for entry in manifest
                if entry["harness"] == _HARNESS
                and entry["component"] == component
            ]
            entry = entries[-1] if entries else None
            installed = bool(entry and Path(entry["install_path"]).exists())
            states[component] = {"installed": installed, "entry": entry}
        return result

    def _install_extension(
        self, ctx: InstallerContext, manifest: Manifest, method: str
    ) -> None:
        """Install the Pi extension by copy or native Pi command."""
        destination = PI_AGENT_HOME / "extensions" / _EXTENSION_NAME
        source = _extension_source()
        if method not in {_DEFAULT_METHOD, "pi-install"}:
            raise ValueError(f"Unsupported Pi extension method: {method}")
        self._announce(ctx, f"install extension {source} -> {destination}")
        if ctx.dry_run:
            if method == "pi-install":
                self._announce(ctx, "run: pi install npm:@dpalfery/pi-statusline")
            return
        if method == "pi-install":
            subprocess.run(
                ["pi", "install", "npm:@dpalfery/pi-statusline"],
                check=True,
            )
        else:
            if not source.is_dir():
                raise FileNotFoundError(source)
            if ctx.force and destination.exists():
                shutil.rmtree(destination)
            copytree_excluding(
                source,
                destination,
                exclude=("node_modules", "__pycache__", ".gitignore"),
            )
        manifest.upsert(
            _manifest_entry(_EXTENSION, method, source, destination, [])
        )
        manifest.write()

    def _install_observme(self, ctx: InstallerContext, manifest: Manifest) -> None:
        """Render and install the ObservMe YAML configuration."""
        source = _observme_template()
        destination = PI_AGENT_HOME / "observme.yaml"
        self._announce(ctx, f"render {source} -> {destination}")
        if ctx.dry_run:
            return
        if not source.is_file():
            raise FileNotFoundError(source)
        backup_ref = backup_file(destination)
        content = source.read_text(encoding="utf-8").replace(
            DEFAULT_ENDPOINT, ctx.endpoint
        )
        atomic_write(destination, content)
        manifest.upsert(
            _manifest_entry(
                _OBSERVME,
                "copy",
                source,
                destination,
                [backup_ref] if backup_ref is not None else [],
            )
        )
        manifest.write()

    @staticmethod
    def _announce(ctx: InstallerContext, message: str) -> None:
        """Print an operation, marking it when it is a dry run."""
        prefix = "DRY-RUN: " if ctx.dry_run else ""
        print(f"{prefix}{message}")


register_installer("pi", PiInstaller)
