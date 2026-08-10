from kyber_observe.backup import backup_file, restore_file


def test_backup_file_copies_content_under_configured_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    observe_home = tmp_path / "kyber-observe"
    monkeypatch.setenv("KYBER_OBSERVE_HOME", str(observe_home))
    source = tmp_path / "settings.json"
    content = '{"mcpServers": {"example": {}}}\n'
    source.write_text(content, encoding="utf-8")

    backup_ref = backup_file(source)

    assert backup_ref is not None
    backup = observe_home / "backups" / backup_ref
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == content
    assert backup.parent.parent == observe_home / "backups"


def test_restore_file_round_trips_original_content(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KYBER_OBSERVE_HOME", str(tmp_path / "kyber-observe"))
    source = tmp_path / "settings.json"
    original = "original configuration\n"
    source.write_text(original, encoding="utf-8")

    backup_ref = backup_file(source)
    source.write_text("new configuration\n", encoding="utf-8")

    restore_file(source, backup_ref)

    assert source.read_text(encoding="utf-8") == original


def test_backup_file_missing_source_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KYBER_OBSERVE_HOME", str(tmp_path / "kyber-observe"))

    backup_ref = backup_file(tmp_path / "does-not-exist.json")

    assert backup_ref is None
