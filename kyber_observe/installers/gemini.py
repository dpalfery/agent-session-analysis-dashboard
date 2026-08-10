"""Installer for Gemini/Antigravity collectors."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import stat
import subprocess

from ..backup import backup_file, restore_file
from ..config import GEMINI_CLI_HOME, GEMINI_PLUGINS_HOME
from ..io import atomic_write, copytree_excluding, load_json, merge_json_preserving
from ..manifest import Manifest
from . import register_installer
from .base import HarnessInstaller, InstallerContext


_HARNESS = "gemini"
_STATUSLINE = "statusline"
_PLUGIN = "plugin"
_VERSION = "0.1.0"


class GeminiInstaller(HarnessInstaller):
    """Install and manage Gemini statusline and AGY telemetry components."""

    components = [_STATUSLINE, _PLUGIN]

    def __init__(self) -> None:
        """Create an installer using the repository's collector sources."""
        self.source_root = Path(__file__).resolve().parents[2] / "collectors" / "gemini"
        self.statusline_source = self.source_root / "statusline.py"
        self.plugin_source = self.source_root / "agy-otel-telemetry"
        self.statusline_target = GEMINI_CLI_HOME / "statusline.py"
        self.settings_target = GEMINI_CLI_HOME / "settings.json"
        self.plugin_target = GEMINI_PLUGINS_HOME / "agy-otel-telemetry"

    def install(self, ctx: InstallerContext) -> None:
        """Install the selected Gemini components.

        Args:
            ctx: Options controlling component, method, and safety behavior.

        Raises:
            ValueError: If the component or method is unsupported.
            FileNotFoundError: If a repository collector source is missing.
            subprocess.CalledProcessError: If ``agy`` installation fails.
        """
        components = self._selected_components(ctx.component)
        manifest = Manifest().read()
        for component in components:
            if manifest.has(_HARNESS, component) and not ctx.force:
                self._announce(f"skip {component}: already installed")
                continue
            if component == _STATUSLINE:
                self._install_statusline(ctx, manifest)
            else:
                self._install_plugin(ctx, manifest)

    def uninstall(self, ctx: InstallerContext) -> None:
        """Remove selected components and restore backed-up configuration."""
        components = self._selected_components(ctx.component)
        manifest = Manifest().read()
        for component in components:
            entries = [
                entry for entry in manifest.entries
                if entry["harness"] == _HARNESS and entry["component"] == component
            ]
            if ctx.dry_run:
                self._announce(f"would uninstall {component}")
                continue
            if component == _STATUSLINE:
                self._remove_statusline(entries)
            else:
                shutil.rmtree(self.plugin_target, ignore_errors=True)
            manifest.remove(_HARNESS, component)
            if entries:
                self._announce(f"uninstalled {component}")
        if not ctx.dry_run:
            manifest.write()

    def status(self) -> dict[str, object]:
        """Return filesystem and manifest state for each Gemini component."""
        manifest = Manifest().read()
        result: dict[str, object] = {}
        for component in self.components:
            installed = manifest.has(_HARNESS, component)
            if component == _STATUSLINE:
                present = self.statusline_target.is_file() and self.settings_target.is_file()
                path = self.statusline_target
            else:
                present = self.plugin_target.is_dir()
                path = self.plugin_target
            result[component] = {
                "installed": installed and present,
                "manifest": installed,
                "path": str(path),
            }
        return result

    def _install_statusline(self, ctx: InstallerContext, manifest: Manifest) -> None:
        self._require_source(self.statusline_source)
        self._announce(f"install statusline: {self.statusline_source} -> {self.statusline_target}")
        if ctx.dry_run:
            self._announce(f"would merge statusLine into {self.settings_target}")
            return

        backup_refs: list[str] = []
        reference = backup_file(self.settings_target)
        if reference is not None:
            backup_refs.append(reference)
        self.statusline_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.statusline_source, self.statusline_target)
        self.statusline_target.chmod(
            self.statusline_target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        existing = load_json(self.settings_target, {})
        if not isinstance(existing, dict):
            raise ValueError(f"Expected a JSON object in {self.settings_target}")
        incoming = {
            "statusLine": {
                "type": "command",
                "command": f"python3 {self.statusline_target}",
                "enabled": True,
            }
        }
        merged = merge_json_preserving(existing, incoming)
        atomic_write(self.settings_target, json.dumps(merged, indent=2) + "\n")
        self._record(manifest, _STATUSLINE, "copy", self.statusline_source,
                     self.statusline_target, backup_refs)

    def _install_plugin(self, ctx: InstallerContext, manifest: Manifest) -> None:
        self._require_source(self.plugin_source)
        method = ctx.method or "copy"
        if method not in {"copy", "agy"}:
            raise ValueError(f"Unsupported Gemini plugin method: {method}")
        self._announce(f"install plugin with method {method}: {self.plugin_source}")
        if ctx.dry_run:
            action = "copytree" if method == "copy" else "agy plugin install"
            self._announce(f"would {action} {self.plugin_source}")
            return

        if method == "copy":
            if ctx.force and self.plugin_target.exists():
                shutil.rmtree(self.plugin_target)
            self.plugin_target.parent.mkdir(parents=True, exist_ok=True)
            copytree_excluding(self.plugin_source, self.plugin_target)
        else:
            subprocess.run(
                ["agy", "plugin", "install", str(self.plugin_source)],
                check=True,
            )
        self._record(manifest, _PLUGIN, method, self.plugin_source,
                     self.plugin_target, [])

    def _remove_statusline(self, entries: list[dict[str, object]]) -> None:
        self.statusline_target.unlink(missing_ok=True)
        restored = False
        for entry in reversed(entries):
            for reference in entry["backup_refs"]:
                restore_file(self.settings_target, str(reference))
                restored = True
                break
            if restored:
                break
        if not restored and self.settings_target.is_file():
            settings = load_json(self.settings_target, {})
            if isinstance(settings, dict) and "statusLine" in settings:
                settings.pop("statusLine")
                if settings:
                    atomic_write(self.settings_target, json.dumps(settings, indent=2) + "\n")
                else:
                    self.settings_target.unlink()

    def _record(
        self,
        manifest: Manifest,
        component: str,
        method: str,
        source: Path,
        target: Path,
        backup_refs: list[str],
    ) -> None:
        manifest.upsert({
            "harness": _HARNESS,
            "component": component,
            "method": method,
            "version": _VERSION,
            "source_path": str(source),
            "install_path": str(target),
            "backup_refs": backup_refs,
            "installed_at": datetime.now(timezone.utc).isoformat(),
        })
        manifest.write()

    @staticmethod
    def _require_source(path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"Gemini collector source not found: {path}")

    @classmethod
    def _selected_components(cls, component: str | None) -> list[str]:
        if component is None:
            return list(cls.components)
        if component not in cls.components:
            raise ValueError(f"Unsupported Gemini component: {component}")
        return [component]

    @staticmethod
    def _announce(message: str) -> None:
        print(message)


register_installer(_HARNESS, GeminiInstaller)


__all__ = ["GeminiInstaller"]
