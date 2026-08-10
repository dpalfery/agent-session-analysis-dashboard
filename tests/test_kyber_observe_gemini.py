import json

from kyber_observe.config import DEFAULT_ENDPOINT
from kyber_observe.installers.base import InstallerContext
from kyber_observe.installers.gemini import GeminiInstaller
import kyber_observe.installers.gemini as gemini_installer


def _context(**overrides):
    values = {
        "endpoint": DEFAULT_ENDPOINT,
        "method": "copy",
        "component": "statusline",
        "dry_run": False,
        "force": False,
    }
    values.update(overrides)
    return InstallerContext(**values)


def _configure_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    observe_home = tmp_path / "kyber-observe"
    cli_home = home / ".gemini" / "antigravity-cli"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KYBER_OBSERVE_HOME", str(observe_home))
    monkeypatch.setattr(gemini_installer, "GEMINI_CLI_HOME", cli_home)
    monkeypatch.setattr(gemini_installer, "GEMINI_PLUGINS_HOME", cli_home / "plugins")
    return home, observe_home, cli_home


def test_install_statusline_preserves_existing_mcp_servers_and_unknown_settings(
    tmp_path, monkeypatch
):
    _, _, cli_home = _configure_home(tmp_path, monkeypatch)
    settings = {
        "mcpServers": {"example": {"command": "server"}},
        "unknownSetting": {"enabled": True},
    }
    cli_home.mkdir(parents=True)
    (cli_home / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    GeminiInstaller().install(_context())

    installed = json.loads(
        (cli_home / "settings.json").read_text(encoding="utf-8")
    )
    assert installed["mcpServers"] == settings["mcpServers"]
    assert installed["unknownSetting"] == settings["unknownSetting"]
    assert installed["statusLine"] == {
        "type": "command",
        "command": f"python3 {cli_home / 'statusline.py'}",
        "enabled": True,
    }
    assert (cli_home / "statusline.py").is_file()


def test_install_plugin_copytree_contains_schema_compliant_plugin_files(
    tmp_path, monkeypatch
):
    _, _, cli_home = _configure_home(tmp_path, monkeypatch)

    GeminiInstaller().install(_context(component="plugin"))

    plugin = cli_home / "plugins" / "agy-otel-telemetry"
    assert {path.name for path in plugin.iterdir()} >= {
        "plugin.json",
        "hooks.json",
        "telemetry.py",
    }
    plugin_manifest = json.loads(
        (plugin / "plugin.json").read_text(encoding="utf-8")
    )
    assert "$schema" in plugin_manifest


def test_install_plugin_agy_uses_list_subprocess_args(tmp_path, monkeypatch):
    _, _, cli_home = _configure_home(tmp_path, monkeypatch)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(gemini_installer.subprocess, "run", fake_run)

    GeminiInstaller().install(_context(component="plugin", method="agy"))

    source = gemini_installer.GeminiInstaller().plugin_source
    assert calls == [
        (
            ["agy", "plugin", "install", str(source)],
            {"check": True},
        )
    ]
    assert not (cli_home / "plugins").exists()


def test_install_dry_run_writes_nothing_to_target_home(tmp_path, monkeypatch):
    home, observe_home, _ = _configure_home(tmp_path, monkeypatch)

    GeminiInstaller().install(_context(component=None, dry_run=True))

    assert not home.exists()
    assert not observe_home.exists()
