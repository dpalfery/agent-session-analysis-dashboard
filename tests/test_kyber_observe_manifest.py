import json

from kyber_observe.manifest import Manifest


def _entry(**overrides):
    entry = {
        "harness": "gemini",
        "component": "statusline",
        "method": "copy",
        "version": "0.1.0",
        "source_path": "/repo/statusline.py",
        "install_path": "/home/test/.gemini/statusline.py",
        "backup_refs": ["20260810T120000000000Z/settings.json"],
        "installed_at": "2026-08-10T12:00:00+00:00",
    }
    entry.update(overrides)
    return entry


def test_upsert_same_harness_component_method_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KYBER_OBSERVE_HOME", str(tmp_path / "kyber-observe"))
    manifest = Manifest().read()

    manifest.upsert(_entry())
    manifest.upsert(_entry(version="0.2.0", backup_refs=[]))

    assert manifest.entries == [_entry(version="0.2.0", backup_refs=[])]


def test_read_missing_manifest_returns_empty_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KYBER_OBSERVE_HOME", str(tmp_path / "kyber-observe"))

    manifest = Manifest().read()

    assert manifest.entries == []


def test_remove_deletes_all_methods_for_harness_component(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KYBER_OBSERVE_HOME", str(tmp_path / "kyber-observe"))
    manifest = Manifest()
    manifest.upsert(_entry(method="copy"))
    manifest.upsert(_entry(method="agy"))
    manifest.upsert(_entry(component="plugin"))

    manifest.remove("gemini", "statusline")

    assert not manifest.has("gemini", "statusline")
    assert manifest.entries == [_entry(component="plugin")]


def test_write_persists_manifest_to_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / "kyber-observe"
    monkeypatch.setenv("KYBER_OBSERVE_HOME", str(home))
    manifest = Manifest()
    manifest.upsert(_entry())

    manifest.write()

    manifest_path = home / "manifest.json"
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == {
        "entries": [_entry()]
    }
    assert Manifest().read().entries == [_entry()]
