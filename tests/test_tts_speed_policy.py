from generator.tts.speed_policy import adjusted_tts_duration, final_duration_in_range, target_tts_speed
import json


def test_target_tts_speed_is_moderately_fast_for_shorts():
    assert target_tts_speed(35.0) == 1.12
    assert target_tts_speed(50.0) == 1.18
    assert target_tts_speed(60.0) == 1.22
    assert target_tts_speed(80.0) == 1.26
    assert target_tts_speed(95.0) == 1.28


def test_adjusted_tts_duration_uses_target_speed():
    assert round(adjusted_tts_duration(60.0), 2) == 49.18


def test_final_duration_range_rejects_overlong_shorts():
    ok, speed, final_duration = final_duration_in_range(120.0)

    assert ok is False
    assert speed == 1.28
    assert round(final_duration, 2) == 93.75


def test_tts_batch_blocks_bad_metadata_before_polly(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    from generator.tts import generate_tts as tts_module

    metadata_path = tmp_path / "final_metadata.json"
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "id": "bad-for-tts",
                    "title": "AITA for testing #viral",
                    "script": ["A weak placeholder line."],
                    "source_provider": "synthetic",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(tts_module, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setattr(
        tts_module,
        "generate_tts_with_timestamps",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Polly should not be called")),
    )

    tts_module.run_batch_tts()

    assert json.loads(metadata_path.read_text(encoding="utf-8")) == []


def _tts_safe_item(content_id: str):
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
    return {
        "id": content_id,
        "title": "He Parked In My Driveway #shorts #story",
        "public_title": "He Parked In My Driveway",
        "script": lines,
        "voiceover_lines": lines,
        "tts_text": " ".join(lines),
        "caption_chunks": [
            "He parked in my driveway",
            "The door camera showed his car",
            "He replied in the building chat",
            "I posted the timestamp",
            "Was I wrong to post the clip?",
        ],
        "first_frame_text": "HE PARKED IN MY DRIVEWAY",
        "opening_visual_query": "parked car in driveway",
        "visual_beat_queries": [
            {"beat": "hook", "query": "parked car in driveway"},
            {"beat": "receipt", "query": "door camera car timestamp"},
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
    }


class _FakeAudioClip:
    duration = 50.0

    def __init__(self, path):
        self.path = path

    def close(self):
        return None


def test_tts_batch_removes_failed_generation(monkeypatch, tmp_path):
    from generator.tts import generate_tts as tts_module

    metadata_path = tmp_path / "final_metadata.json"
    failed_path = tmp_path / "failed_posts.json"
    metadata_path.write_text(json.dumps([_tts_safe_item("fail")]), encoding="utf-8")
    monkeypatch.setattr(tts_module, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setattr(tts_module, "FAILED_POSTS_FILE", failed_path)
    monkeypatch.setattr(tts_module, "AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(tts_module, "MARKS_DIR", tmp_path / "marks")
    monkeypatch.setattr(tts_module, "generate_tts_with_timestamps", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("polly down")))

    tts_module.run_batch_tts()

    assert json.loads(metadata_path.read_text(encoding="utf-8")) == []
    failed = json.loads(failed_path.read_text(encoding="utf-8"))
    assert failed[0]["stage"] == "tts"
    assert "tts_generation_failed" in failed[0]["error"]


def test_tts_batch_keeps_successful_item_with_artifacts(monkeypatch, tmp_path):
    from generator.tts import generate_tts as tts_module

    metadata_path = tmp_path / "final_metadata.json"
    failed_path = tmp_path / "failed_posts.json"
    audio_dir = tmp_path / "audio"
    marks_dir = tmp_path / "marks"
    metadata_path.write_text(json.dumps([_tts_safe_item("ok")]), encoding="utf-8")

    def fake_generate(text, filename, voice_id):
        audio_dir.mkdir(parents=True, exist_ok=True)
        marks_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / f"{filename}.tmp.mp3"
        marks_path = marks_dir / f"{filename}.tmp.json"
        audio_path.write_bytes(b"mp3")
        marks_path.write_text(json.dumps([{"time": 0, "type": "word", "value": "He"}]), encoding="utf-8")
        return str(audio_path), str(marks_path)

    monkeypatch.setattr(tts_module, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setattr(tts_module, "FAILED_POSTS_FILE", failed_path)
    monkeypatch.setattr(tts_module, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(tts_module, "MARKS_DIR", marks_dir)
    monkeypatch.setattr(tts_module, "AudioFileClip", _FakeAudioClip)
    monkeypatch.setattr(tts_module, "generate_tts_with_timestamps", fake_generate)
    monkeypatch.setattr(tts_module, "pick_voice_id", lambda voice_type: "Matthew")

    tts_module.run_batch_tts()

    items = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert [item["id"] for item in items] == ["ok"]
    assert items[0]["tts_status"] == "READY"
    assert items[0]["tts_voice_id"] == "Matthew"
    assert items[0]["tts_wpm"] > 0
    assert (audio_dir / "ok.mp3").exists()
    assert (marks_dir / "ok_marks.json").exists()
