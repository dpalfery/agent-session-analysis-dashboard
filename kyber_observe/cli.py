"""Command-line interface for installing and managing Kyber Observe."""

from __future__ import annotations

from typing import Callable, Optional

import typer

from .config import DEFAULT_ENDPOINT
from .installers import REGISTRY, UnknownHarnessError, get_installer
from .installers.base import HarnessInstaller, InstallerContext
from .manifest import Manifest


app = typer.Typer(
    help="Install and manage Kyber Observe telemetry collectors.",
    no_args_is_help=True,
)

# Installer modules register lazily, so these are loaded when a command needs
# to enumerate the registry rather than relying on import order.
_BUILTIN_HARNESSES = ("gemini", "pi")


@app.callback()
def app_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Enable verbose command output.",
    ),
) -> None:
    """Configure global CLI options."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


def _load_registered_installers() -> dict[str, type[HarnessInstaller]]:
    """Load built-in installers and return all registered installers."""
    for harness in _BUILTIN_HARNESSES:
        try:
            get_installer(harness)
        except UnknownHarnessError:
            # A missing optional installer should not prevent listing any
            # other installer registered by an embedding application.
            continue
    return dict(REGISTRY)


def _installer_for(harness: str) -> type[HarnessInstaller]:
    """Resolve an installer or terminate with a user-facing error."""
    _load_registered_installers()
    try:
        return get_installer(harness)
    except UnknownHarnessError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


def _validate_component(
    harness: str, component: Optional[str], installer: type[HarnessInstaller]
) -> None:
    """Validate a component selection against an installer registry entry."""
    if component is None or component in installer.components:
        return
    valid = ", ".join(installer.components) or "none"
    typer.echo(
        f"Unknown component {component!r} for {harness}; "
        f"valid components: {valid}",
        err=True,
    )
    raise typer.Exit(code=1)


def _context(
    endpoint: str,
    method: str,
    component: str | None,
    dry_run: bool,
    force: bool,
) -> InstallerContext:
    """Build the common installer context from CLI options."""
    return InstallerContext(
        endpoint=endpoint,
        method=method,
        component=component,
        dry_run=dry_run,
        force=force,
    )


def _run_operation(operation: Callable[[], None], description: str) -> None:
    """Run an installer operation and present expected user errors clearly."""
    try:
        operation()
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(f"{description} failed: {error}", err=True)
        raise typer.Exit(code=1) from error


@app.command()
def install(
    harness: str = typer.Argument(..., help="Harness to install into."),
    component: Optional[str] = typer.Option(
        None,
        "--component",
        help="Component to install; omit to install all components.",
    ),
    method: str = typer.Option(
        "copy",
        "--method",
        help="Installation method (for example copy, agy, or pi-install).",
    ),
    endpoint: str = typer.Option(
        DEFAULT_ENDPOINT,
        "--endpoint",
        help="OTLP endpoint used by the installation.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Describe actions without changing the filesystem.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing installation.",
    ),
) -> None:
    """Install one or more components for a harness."""
    installer_type = _installer_for(harness)
    _validate_component(harness, component, installer_type)
    installer = installer_type()
    ctx = _context(endpoint, method, component, dry_run, force)
    _run_operation(lambda: installer.install(ctx), f"Install {harness}")


@app.command()
def uninstall(
    harness: str = typer.Argument(..., help="Harness to uninstall from."),
    component: Optional[str] = typer.Option(
        None,
        "--component",
        help="Component to uninstall; omit to uninstall all components.",
    ),
) -> None:
    """Uninstall harness components and restore backed-up configuration."""
    installer_type = _installer_for(harness)
    _validate_component(harness, component, installer_type)
    installer = installer_type()
    ctx = _context(DEFAULT_ENDPOINT, "copy", component, False, False)
    _run_operation(lambda: installer.uninstall(ctx), f"Uninstall {harness}")


@app.command("list")
def list_harnesses() -> None:
    """List registered harnesses and their supported components."""
    installers = _load_registered_installers()
    if not installers:
        typer.echo("No harnesses registered.")
        return
    for harness in sorted(installers):
        components = ", ".join(installers[harness].components) or "none"
        typer.echo(f"{harness}: {components}")


@app.command()
def status() -> None:
    """Show installation state and backup locations for every harness."""
    installers = _load_registered_installers()
    manifest = Manifest().read()
    entries = manifest.entries
    for harness in sorted(installers):
        installer = installers[harness]()
        try:
            states = installer.status()
        except (OSError, ValueError) as error:
            typer.echo(f"{harness}: unable to read status: {error}", err=True)
            continue
        typer.echo(f"{harness}:")
        for component in installers[harness].components:
            state = _component_state(states, component)
            component_entries = [
                entry
                for entry in entries
                if entry["harness"] == harness
                and entry["component"] == component
            ]
            installed = bool(state.get("installed", False))
            typer.echo(
                f"  {component}: {'installed' if installed else 'not installed'}"
            )
            path = state.get("path")
            if path is not None:
                typer.echo(f"    path: {path}")
            for entry in component_entries:
                backups = entry["backup_refs"] or ["none"]
                typer.echo(f"    backups: {', '.join(map(str, backups))}")


def _component_state(states: dict[str, object], component: str) -> dict[str, object]:
    """Extract a component status mapping from installer-specific shapes."""
    if component in states and isinstance(states[component], dict):
        return states[component]
    components = states.get("components")
    if isinstance(components, dict):
        state = components.get(component)
        if isinstance(state, dict):
            return state
    return {}


def main() -> None:
    """Run the Kyber Observe command-line application."""
    app()
