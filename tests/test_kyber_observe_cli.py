import json

from typer.testing import CliRunner

from kyber_observe.cli import app
import kyber_observe.installers.gemini as gemini_installer
import kyber_observe.installers.pi as pi_installer


runner = CliRunner()


def _configure_sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    observe_home = tmp_path / "kyber-observe"
    gemini_home = home / ".gemini" / "antigravity-cli"
    pi_home = home / ".pi" / "agent"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KYBER_OBSERVE_HOME", str(observe_home))
    monkeypatch.setattr(gemini_installer, "GEMINI_CLI_HOME", gemini_home)
    monkeypatch.setattr(
        gemini_installer, "GEMINI_PLUGINS_HOME", gemini_home / "plugins"
    )
    monkeypatch.setattr(pi_installer, "PI_AGENT_HOME", pi_home)
    return home, observe_home, gemini_home


def test_list_enumerates_harnesses_and_components():
    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "gemini:" in result.output
    assert "statusline" in result.output
    assert "plugin" in result.output
    assert "pi:" in result.output
    assert "extension" in result.output
    assert "observme" in result.output


def test_status_on_empty_manifest_succeeds_with_uninstalled_components(
    tmp_path, monkeypatch
):
    _configure_sandbox(tmp_path, monkeypatch)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "gemini:" in result.output
    assert "pi:" in result.output
    assert "not installed" in result.output


def test_install_gemini_dry_run_reports_actions_without_writing_to_sandbox(
    tmp_path, monkeypatch
):
    home, observe_home, _ = _configure_sandbox(tmp_path, monkeypatch)

    result = runner.invoke(app, ["install", "gemini", "--dry-run"])

    assert result.exit_code == 0
    assert "install statusline" in result.output
    assert "would merge statusLine" in result.output
    assert "install plugin" in result.output
    assert not home.exists()
    assert not observe_home.exists()


def test_uninstall_gemini_removes_file_and_restores_settings_backup(
    tmp_path, monkeypatch
):
    _, observe_home, gemini_home = _configure_sandbox(tmp_path, monkeypatch)
    gemini_home.mkdir(parents=True)
    settings_path = gemini_home / "settings.json"
    original_settings = {"mcpServers": {"example": {"command": "server"}}}
    settings_path.write_text(json.dumps(original_settings), encoding="utf-8")

    installed = runner.invoke(
        app, ["install", "gemini", "--component", "statusline"]
    )
    assert installed.exit_code == 0
    statusline_path = gemini_home / "statusline.py"
    assert statusline_path.is_file()
    assert json.loads(settings_path.read_text(encoding="utf-8"))["statusLine"]

    uninstalled = runner.invoke(
        app, ["uninstall", "gemini", "--component", "statusline"]
    )

    assert uninstalled.exit_code == 0
    assert not statusline_path.exists()
    assert json.loads(settings_path.read_text(encoding="utf-8")) == original_settings
    manifest = json.loads(
        (observe_home / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["entries"] == []


def test_install_unknown_harness_exits_nonzero_with_clear_message():
    result = runner.invoke(app, ["install", "unknown-harness"])

    assert result.exit_code != 0
    assert "Unknown harness" in result.output
    assert "gemini" in result.output
    assert "pi" in result.output


def test_install_unknown_component_lists_valid_components(tmp_path, monkeypatch):
    _configure_sandbox(tmp_path, monkeypatch)

    result = runner.invoke(
        app, ["install", "gemini", "--component", "unknown-component"]
    )

    assert result.exit_code != 0
    assert "Unknown component 'unknown-component' for gemini" in result.output
    assert "valid components: statusline, plugin" in result.output
