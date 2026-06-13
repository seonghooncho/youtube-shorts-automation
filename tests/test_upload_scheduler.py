import json

from shared.jobs import upload_scheduler


def _upload_safe_item(content_id: str, *, scheduled_at: int = 1, **overrides):
    lines = [
        "He parked in my driveway before sunrise and left my guests on the street.",
        "I texted once because the door camera showed his car across the whole entrance.",
        "He replied in the building chat that I was being petty over empty pavement.",
        "Then I posted the camera timestamp and the message where he admitted it was his car.",
        "My neighbor said screenshots made him look bad in front of everyone on our block.",
        "I told him the driveway was not extra parking for his errands or deliveries.",
        "After that I put up a private parking sign and stopped answering late-night texts.",
        "Was I wrong to post the clip when he made it public first?",
    ]
    item = {
        "id": content_id,
        "title": "He Parked In My Driveway #shorts #story",
        "public_title": "He Parked In My Driveway",
        "description": "A neighbor parking conflict.",
        "tags": ["storytime"],
        "script": lines,
        "voiceover_lines": lines,
        "tts_text": " ".join(lines),
        "caption_chunks": [
            "He parked in my driveway",
            "The door camera showed his car",
            "He blamed me in the building chat",
            "I posted the timestamp",
            "Was I wrong to post the clip?",
        ],
        "style_variant": "neighbor_dispute",
        "script_fingerprint": f"fingerprint-{content_id}",
        "predicted_retention_score": 8,
        "predicted_rewatch_score": 8,
        "predicted_comment_score": 7,
        "predicted_clarity_score": 8,
        "predicted_ai_smell_score": 3,
        "critic_scores": {
            "ai_smell_score": 3,
            "native_naturalness_score": 8,
            "retention_score": 8,
            "specificity_score": 8,
        },
        "scheduled_publish_at": scheduled_at,
        "uploaded": False,
        "video_key": f"videos/final/{content_id}.mp4",
    }
    item.update(overrides)
    return item


def test_upload_scheduler_skips_bad_due_item_and_uploads_good(monkeypatch, tmp_path):
    metadata_path = tmp_path / "final_metadata.json"
    temp_dir = tmp_path / "temp"
    uploaded_metadata = {}
    metadata = [
        _upload_safe_item("bad", source_provider="synthetic"),
        _upload_safe_item("good"),
    ]

    def fake_download(key, path):
        if key == upload_scheduler.PUBLISH_METADATA_KEY:
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            return True
        if key == "videos/final/good.mp4":
            target = tmp_path / "good.mp4"
            target.write_bytes(b"video")
            with open(path, "wb") as f:
                f.write(b"video")
            return True
        return False

    def fake_upload(path, key):
        uploaded_metadata[key] = json.loads(metadata_path.read_text(encoding="utf-8"))

    monkeypatch.setattr(upload_scheduler, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setattr(upload_scheduler, "get_temp_file", lambda name: temp_dir / name)
    monkeypatch.setattr(upload_scheduler, "download_from_s3", fake_download)
    monkeypatch.setattr(upload_scheduler, "upload_to_s3", fake_upload)
    monkeypatch.setattr(upload_scheduler, "upload_youtube", lambda *args: "yt-good")
    monkeypatch.setattr(upload_scheduler, "send_slack_message", lambda message: None)
    monkeypatch.setattr(upload_scheduler, "clean_uploader_workspace", lambda: None)
    monkeypatch.setattr(upload_scheduler, "ContentRepository", lambda: type("Repo", (), {"mark_status": lambda *args, **kwargs: None})())

    upload_scheduler.upload_batch_pipeline()

    final_items = uploaded_metadata[upload_scheduler.PUBLISH_METADATA_KEY]
    assert final_items[0]["upload_status"] == "REJECTED_BY_CONTENT_GATE"
    assert final_items[1]["upload_status"] == "UPLOADED"
    assert final_items[1]["youtube_id"] == "yt-good"


def test_upload_scheduler_blocks_legacy_metadata_in_production(monkeypatch, tmp_path):
    messages = []
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ALLOW_LEGACY_UPLOAD_METADATA", raising=False)
    monkeypatch.setattr(upload_scheduler, "FINAL_METADATA_FILE", tmp_path / "final_metadata.json")
    monkeypatch.setattr(upload_scheduler, "download_from_s3", lambda key, path: False)
    monkeypatch.setattr(upload_scheduler, "send_slack_message", messages.append)
    monkeypatch.setattr(upload_scheduler, "clean_uploader_workspace", lambda: None)

    upload_scheduler.upload_batch_pipeline()

    assert any("legacy metadata fallback" in message for message in messages)


def test_upload_scheduler_legacy_metadata_still_uses_content_gate(monkeypatch, tmp_path):
    metadata_path = tmp_path / "final_metadata.json"
    uploaded = {}
    messages = []

    def fake_download(key, path):
        if key == upload_scheduler.PUBLISH_METADATA_KEY:
            return False
        if key == upload_scheduler.LEGACY_METADATA_KEY:
            metadata_path.write_text(json.dumps([_upload_safe_item("bad", source_provider="synthetic")]), encoding="utf-8")
            return True
        return False

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_LEGACY_UPLOAD_METADATA", "1")
    monkeypatch.setattr(upload_scheduler, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setattr(upload_scheduler, "download_from_s3", fake_download)
    monkeypatch.setattr(upload_scheduler, "upload_to_s3", lambda path, key: uploaded.setdefault(key, json.loads(metadata_path.read_text(encoding="utf-8"))))
    monkeypatch.setattr(upload_scheduler, "upload_youtube", lambda *args: (_ for _ in ()).throw(AssertionError("should not upload")))
    monkeypatch.setattr(upload_scheduler, "send_slack_message", messages.append)
    monkeypatch.setattr(upload_scheduler, "clean_uploader_workspace", lambda: None)
    monkeypatch.setattr(upload_scheduler, "ContentRepository", lambda: type("Repo", (), {"mark_status": lambda *args, **kwargs: None})())

    upload_scheduler.upload_batch_pipeline()

    assert uploaded[upload_scheduler.LEGACY_METADATA_KEY][0]["upload_status"] == "REJECTED_BY_CONTENT_GATE"
    assert any("legacy upload metadata" in message for message in messages)
