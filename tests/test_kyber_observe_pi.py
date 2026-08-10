import pytest

from kyber_observe.config import DEFAULT_ENDPOINT
from kyber_observe.installers.base import InstallerContext
from kyber_observe.installers.pi import PiInstaller
import kyber_observe.installers.pi as pi_installer


def _context(**overrides):
    values = {
        "endpoint": DEFAULT_ENDPOINT,
        "method": "copy",
        "component": "extension",
        "dry_run": False,
        "force": False,
    }
    values.update(overrides)
    return InstallerContext(**values)


def _configure_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    observe_home = tmp_path / "kyber-observe"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KYBER_OBSERVE_HOME", str(observe_home))
    monkeypatch.setattr(pi_installer, "PI_AGENT_HOME", home / ".pi" / "agent")
    return home, observe_home


def test_install_extension_copy_excludes_node_modules_and_pycache(
    tmp_path, monkeypatch
):
    home, _ = _configure_home(tmp_path, monkeypatch)
    source = tmp_path / "staged" / "collectors" / "pi"
    source.mkdir(parents=True)
    (source / "extension.ts").write_text("export {}\n", encoding="utf-8")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "should-not-copy.js").write_text(
        "excluded\n", encoding="utf-8"
    )
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "should-not-copy.pyc").write_bytes(b"excluded")
    monkeypatch.setattr(pi_installer, "_extension_source", lambda: source)

    PiInstaller().install(_context())

    destination = home / ".pi" / "agent" / "extensions" / "pi-statusline"
    assert (destination / "extension.ts").read_text(encoding="utf-8") == "export {}\n"
    assert not (destination / "node_modules").exists()
    assert not (destination / "__pycache__").exists()


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        (DEFAULT_ENDPOINT, DEFAULT_ENDPOINT),
        ("http://collector.example:4318", "http://collector.example:4318"),
    ],
)
def test_install_observme_substitutes_endpoint(
    tmp_path, monkeypatch, endpoint, expected
):
    home, _ = _configure_home(tmp_path, monkeypatch)

    PiInstaller().install(
        _context(component="observme", endpoint=endpoint)
    )

    destination = home / ".pi" / "agent" / "observme.yaml"
    content = destination.read_text(encoding="utf-8")
    assert f"endpoint: {expected}" in content
    if expected != DEFAULT_ENDPOINT:
        assert "endpoint: http://localhost:4318" not in content


def test_install_extension_pi_install_uses_list_subprocess_args(
    tmp_path, monkeypatch
):
    _configure_home(tmp_path, monkeypatch)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(pi_installer.subprocess, "run", fake_run)

    PiInstaller().install(_context(method="pi-install"))

    assert calls == [
        (
            ["pi", "install", "npm:@dpalfery/pi-statusline"],
            {"check": True},
        )
    ]


def test_install_dry_run_writes_nothing_to_target_home(tmp_path, monkeypatch):
    home, observe_home = _configure_home(tmp_path, monkeypatch)

    PiInstaller().install(
        _context(component=None, dry_run=True)
    )

    assert not (home / ".pi").exists()
    assert not observe_home.exists()
