from service.lls.build_identity import source_identity


def test_source_identity_detects_content_and_path_changes_but_ignores_runtime_data(tmp_path):
    source = tmp_path / "module.py"
    source.write_text("value = 1\n")
    initial = source_identity(tmp_path)
    assert initial["packaged_build"] is False
    assert initial["build_commit"] is None
    (tmp_path / "runtime.sqlite3").write_bytes(b"private runtime data")
    assert source_identity(tmp_path) == initial
    source.write_text("value = 2\n")
    edited = source_identity(tmp_path)
    assert edited["source_sha256"] != initial["source_sha256"]
    source.rename(tmp_path / "renamed.py")
    assert source_identity(tmp_path)["source_sha256"] != edited["source_sha256"]
