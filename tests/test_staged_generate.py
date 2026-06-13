import json

from shared.jobs import staged_generate


def test_publish_ready_gate_removes_bad_metadata(monkeypatch, tmp_path):
    metadata_path = tmp_path / "final_metadata.json"
    failed_path = tmp_path / "failed_posts.json"
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "id": "bad-synthetic",
                    "title": "AITA for testing #viral",
                    "script": ["A weak placeholder line."],
                    "source_provider": "synthetic",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(staged_generate, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setattr(staged_generate, "FAILED_POSTS_FILE", failed_path)
    monkeypatch.setattr(staged_generate.store, "upload_file", lambda *args, **kwargs: None)

    staged_generate._filter_metadata_by_content_gate("publish_ready")

    assert json.loads(metadata_path.read_text(encoding="utf-8")) == []
    rejected = json.loads(failed_path.read_text(encoding="utf-8"))
    assert rejected[0]["stage"] == "publish_ready"
    assert "synthetic_source_not_allowed" in rejected[0]["hard_errors"]


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


def test_artifact_gate_removes_item_without_mp3_before_subtitles(monkeypatch, tmp_path):
    metadata_path = tmp_path / "final_metadata.json"
    failed_path = tmp_path / "failed_posts.json"
    audio_dir = tmp_path / "audio"
    marks_dir = tmp_path / "marks"
    marks_dir.mkdir()
    (marks_dir / "missing-audio_marks.json").write_text("[]", encoding="utf-8")
    metadata_path.write_text(json.dumps([{"id": "missing-audio", "title": "Missing Audio"}]), encoding="utf-8")
    monkeypatch.setattr(staged_generate, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setattr(staged_generate, "FAILED_POSTS_FILE", failed_path)
    monkeypatch.setattr(staged_generate, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(staged_generate, "MARKS_DIR", marks_dir)
    monkeypatch.setattr(staged_generate.store, "upload_file", lambda *args, **kwargs: None)

    staged_generate._filter_metadata_by_artifacts("subtitles", require_audio=True, require_marks=True)

    assert json.loads(metadata_path.read_text(encoding="utf-8")) == []
    failed = json.loads(failed_path.read_text(encoding="utf-8"))
    assert "missing_audio_mp3" in failed[0]["error"]


def test_artifact_gate_removes_item_without_srt_before_render(monkeypatch, tmp_path):
    metadata_path = tmp_path / "final_metadata.json"
    failed_path = tmp_path / "failed_posts.json"
    audio_dir = tmp_path / "audio"
    subtitles_dir = tmp_path / "subtitles"
    output_dir = tmp_path / "output"
    audio_dir.mkdir()
    subtitles_dir.mkdir()
    output_dir.mkdir()
    (audio_dir / "missing-srt.mp3").write_bytes(b"mp3")
    (output_dir / "tts_check_result.json").write_text('[{"filename":"missing-srt"}]', encoding="utf-8")
    metadata_path.write_text(json.dumps([{"id": "missing-srt", "title": "Missing SRT"}]), encoding="utf-8")
    monkeypatch.setattr(staged_generate, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setattr(staged_generate, "FAILED_POSTS_FILE", failed_path)
    monkeypatch.setattr(staged_generate, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(staged_generate, "SUBTITLES_DIR", subtitles_dir)
    monkeypatch.setattr(staged_generate, "get_output_file", lambda name: output_dir / name)
    monkeypatch.setattr(staged_generate.store, "upload_file", lambda *args, **kwargs: None)

    staged_generate._filter_metadata_by_artifacts("render", require_audio=True, require_srt=True, require_tts_result=True)

    assert json.loads(metadata_path.read_text(encoding="utf-8")) == []
    failed = json.loads(failed_path.read_text(encoding="utf-8"))
    assert "missing_subtitle_srt" in failed[0]["error"]


def test_artifact_gate_removes_item_without_final_mp4(monkeypatch, tmp_path):
    metadata_path = tmp_path / "final_metadata.json"
    failed_path = tmp_path / "failed_posts.json"
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    metadata_path.write_text(
        json.dumps([{"id": "missing-video", "title": "Missing Video", "video_key": "videos/final/missing-video.mp4"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(staged_generate, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setattr(staged_generate, "FAILED_POSTS_FILE", failed_path)
    monkeypatch.setattr(staged_generate, "FINAL_DIR", final_dir)
    monkeypatch.setattr(staged_generate.store, "object_exists", lambda key: False)
    monkeypatch.setattr(staged_generate.store, "upload_file", lambda *args, **kwargs: None)

    staged_generate._filter_metadata_by_artifacts(
        "publish_ready",
        require_video_key=True,
        require_final_video=True,
        allow_s3_video=True,
    )

    assert json.loads(metadata_path.read_text(encoding="utf-8")) == []
    failed = json.loads(failed_path.read_text(encoding="utf-8"))
    assert "missing_final_mp4" in failed[0]["error"]
