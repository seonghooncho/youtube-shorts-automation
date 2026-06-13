import json

from shared.jobs import staged_generate


def test_render_target_ids_default_to_current_metadata(monkeypatch, tmp_path):
    metadata_path = tmp_path / "final_metadata.json"
    metadata_path.write_text(
        json.dumps([{"id": "new-a"}, {"id": "new-b"}, {"title": "missing id"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(staged_generate, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.delenv("TARGET_CONTENT_IDS", raising=False)
    monkeypatch.delenv("RENDER_SHARD_MODE", raising=False)

    assert staged_generate._render_target_ids() == ["new-a", "new-b"]


def test_render_target_ids_env_override_still_wins(monkeypatch, tmp_path):
    metadata_path = tmp_path / "final_metadata.json"
    metadata_path.write_text(json.dumps([{"id": "metadata-id"}]), encoding="utf-8")
    monkeypatch.setattr(staged_generate, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setenv("TARGET_CONTENT_IDS", "manual-a, manual-b")
    monkeypatch.delenv("RENDER_SHARD_MODE", raising=False)

    assert staged_generate._render_target_ids() == ["manual-a", "manual-b"]
