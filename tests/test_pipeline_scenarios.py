import json
import re
from pathlib import Path

from shared.jobs import staged_generate


class FakeStore:
    def __init__(self, initial=None):
        self.storage = dict(initial or {})

    def upload_file(self, local_path, key):
        self.storage[key] = Path(local_path).read_bytes()

    def download_file(self, key, local_path):
        if key not in self.storage:
            return False
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.storage[key])
        return True

    def upload_directory(self, local_dir, prefix):
        root = Path(local_dir)
        uploaded = []
        if not root.exists():
            return uploaded
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            key = f"{prefix.rstrip('/')}/{path.relative_to(root).as_posix()}"
            self.upload_file(path, key)
            uploaded.append(key)
        return uploaded

    def list_keys(self, prefix):
        normalized = prefix.rstrip("/") + "/"
        return [key for key in self.storage if key.startswith(normalized)]

    def download_prefix(self, prefix, local_dir):
        normalized = prefix.rstrip("/") + "/"
        downloaded = []
        for key in self.list_keys(prefix):
            relative = key[len(normalized):]
            if not relative:
                continue
            path = Path(local_dir) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.storage[key])
            downloaded.append(path)
        return downloaded

    def object_exists(self, key):
        return key in self.storage


class FakeRepo:
    def __init__(self):
        self.items = []

    def upsert_items(self, items, status):
        self.items.append((status, list(items)))


def _pipeline_safe_item(content_id: str, **overrides):
    lines = [
        "He parked in my driveway before sunrise and left my guests on the street.",
        "I texted once because the door camera showed his car across the whole entrance.",
        "He replied in the building chat that I was being petty over empty pavement.",
        "Then I posted the camera timestamp and the message where he admitted it was his car.",
        "My neighbor said screenshots made him look bad in front of everyone on our block.",
        "I told him the driveway was not extra parking for his errands or deliveries.",
        "The private parking sign went up after his second message, not before it.",
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
            "He replied in the building chat",
            "I posted the timestamp",
            "Was I wrong to post the clip?",
        ],
        "first_frame_text": "HE PARKED IN MY DRIVEWAY",
        "first_2_seconds": "He parked in my driveway",
        "opening_visual_query": "parked car in driveway",
        "visual_beat_queries": [
            {"beat": "hook", "query": "parked car in driveway"},
            {"beat": "receipt", "query": "door camera car timestamp"},
        ],
        "style_variant": "neighbor_dispute",
        "script_fingerprint": f"fingerprint-{content_id}",
        "source_provider": "pullpush",
        "source_url": "https://reddit.example/story",
        "source_title": "Neighbor parked in my driveway and argued in the building chat",
        "source_content_excerpt": " ".join(lines),
        "hook_type": "receipt_first_conflict",
        "source_summary": "A neighbor used the narrator's driveway, denied the problem, and was contradicted by door camera evidence in the building chat.",
        "story_beats": [
            "The neighbor parked in the driveway before sunrise.",
            "The narrator texted after checking the door camera.",
            "The neighbor argued in the building chat.",
            "The timestamp and message proved the car was his.",
        ],
        "adaptation_strategy": "The source was compressed into one morning, focused on the camera timestamp, and preserved without changing the core dispute.",
        "retention_angle": "A private driveway conflict turns public when the neighbor argues in the building chat and the camera receipt appears.",
        "turning_point": "The door camera timestamp showed his car blocking the driveway after he denied it.",
        "payoff_line": "The driveway stopped being a private favor once he argued about it in the building chat.",
        "viewer_question": "Was I wrong to post the clip when he made it public first?",
        "retention_risk": "The parking setup could feel ordinary, so the camera timestamp and public chat reply are surfaced early.",
        "marketability_score": 5,
        "visual_keywords": [
            "parked car in driveway",
            "door camera timestamp",
            "building chat messages",
            "private parking sign",
        ],
        "cut_plan": [
            "open on a parked car blocking a driveway",
            "cut to a phone text preview",
            "show a door camera timestamp",
            "end on a private parking sign",
        ],
        "bg_strategy": "hybrid",
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
        "scheduled_publish_at": 1,
        "uploaded": False,
    }
    item.update(overrides)
    return item


def _word_marks(text: str):
    words = re.findall(r"[A-Za-z0-9']+", text)
    return [{"time": index * 350, "type": "word", "value": word} for index, word in enumerate(words)]


def test_staged_tts_to_publish_ready_keeps_only_valid_artifacts(monkeypatch, tmp_path):
    metadata_path = tmp_path / "data" / "final_metadata.json"
    failed_path = tmp_path / "data" / "failed_posts.json"
    audio_dir = tmp_path / "audio"
    marks_dir = tmp_path / "marks"
    subtitles_dir = tmp_path / "subtitles"
    final_dir = tmp_path / "final"
    output_dir = tmp_path / "output"
    temp_dir = tmp_path / "temp"
    for path in (metadata_path.parent, audio_dir, marks_dir, subtitles_dir, final_dir, output_dir, temp_dir):
        path.mkdir(parents=True, exist_ok=True)

    initial_items = [
        _pipeline_safe_item("good"),
        _pipeline_safe_item("dry-run", dry_run=True),
    ]
    store = FakeStore({"scripts/final_metadata.json": json.dumps(initial_items).encode("utf-8")})
    repo = FakeRepo()

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("YT_ENV", raising=False)
    monkeypatch.setattr(staged_generate, "FINAL_METADATA_FILE", metadata_path)
    monkeypatch.setattr(staged_generate, "FAILED_POSTS_FILE", failed_path)
    monkeypatch.setattr(staged_generate, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(staged_generate, "MARKS_DIR", marks_dir)
    monkeypatch.setattr(staged_generate, "SUBTITLES_DIR", subtitles_dir)
    monkeypatch.setattr(staged_generate, "FINAL_DIR", final_dir)
    monkeypatch.setattr(staged_generate, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(staged_generate, "USED_PIXABAY_IDS_FILE", temp_dir / "used_pixabay_ids.json")
    monkeypatch.setattr(staged_generate, "get_output_file", lambda name: output_dir / name)
    monkeypatch.setattr(staged_generate, "get_temp_file", lambda name: temp_dir / name)
    monkeypatch.setattr(staged_generate, "store", store)
    monkeypatch.setattr(staged_generate, "content_repo", repo)
    monkeypatch.setattr(staged_generate, "send_slack_message", lambda message: None)
    monkeypatch.setattr(staged_generate, "quality_warnings", lambda path: [])
    monkeypatch.setattr(staged_generate, "validate_video_file", lambda path: (True, "", {}))
    monkeypatch.setattr(staged_generate, "update_metadata_after_video_creation", lambda: None)

    def fake_tts():
        items = json.loads(metadata_path.read_text(encoding="utf-8"))
        for item in items:
            content_id = item["id"]
            (audio_dir / f"{content_id}.mp3").write_bytes(b"mp3")
            (marks_dir / f"{content_id}_marks.json").write_text(
                json.dumps(_word_marks(item["tts_text"])),
                encoding="utf-8",
            )
            item["tts_status"] = "READY"
            item["tts_voice_id"] = "Matthew"
            item["tts_wpm"] = 180
            item["tts_original_duration"] = 40
            item["tts_final_duration_estimate"] = 34
        metadata_path.write_text(json.dumps(items), encoding="utf-8")

    def fake_convert_srt():
        items = json.loads(metadata_path.read_text(encoding="utf-8"))
        for item in items:
            content_id = item["id"]
            (subtitles_dir / f"{content_id}.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nHe parked in my driveway\n",
                encoding="utf-8",
            )
            item["caption_alignment_status"] = "aligned"
            item["caption_chunk_count"] = len(item["caption_chunks"])
            item["caption_timing_status"] = "ok"
        metadata_path.write_text(json.dumps(items), encoding="utf-8")

    def fake_analyze_tts():
        items = json.loads(metadata_path.read_text(encoding="utf-8"))
        (output_dir / "tts_check_result.json").write_text(
            json.dumps([{"filename": item["id"], "status": "ok"} for item in items]),
            encoding="utf-8",
        )

    def fake_render(target_ids=None):
        ids = target_ids or [item["id"] for item in json.loads(metadata_path.read_text(encoding="utf-8"))]
        for content_id in ids:
            (final_dir / f"{content_id}.mp4").write_bytes(b"video")

    monkeypatch.setattr(staged_generate, "run_batch_tts", fake_tts)
    monkeypatch.setattr(staged_generate, "convert_all_marks_to_srt", fake_convert_srt)
    monkeypatch.setattr(staged_generate, "analyze_all_tts", fake_analyze_tts)
    monkeypatch.setattr(staged_generate, "batch_merge_videos_for_tts", lambda target_ids=None: None)
    monkeypatch.setattr(staged_generate, "batch_render_all_videos", fake_render)

    staged_generate.tts_stage()
    staged_generate.subtitles_stage()
    staged_generate.render_stage()

    publish_ready = json.loads(store.storage["publish-ready/final_metadata.json"].decode("utf-8"))
    failed_posts = json.loads(store.storage["scripts/failed_posts.json"].decode("utf-8"))

    assert [item["id"] for item in publish_ready] == ["good"]
    assert publish_ready[0]["upload_status"] == "PUBLISH_READY"
    assert publish_ready[0]["video_key"] == "videos/final/good.mp4"
    assert "videos/final/good.mp4" in store.storage
    assert failed_posts[0]["stage"] == "tts"
    assert "dry_run_item_not_allowed_downstream" in failed_posts[0]["hard_errors"]
    assert repo.items[-1][0] == "PUBLISH_READY"
