import json
from pathlib import Path

import generator.video.pixabay_video_merge as pixabay_video_merge
from generator.video.pixabay_video_merge import (
    _is_blocked_pixabay_hit,
    _metadata_by_id,
    _queries_for_entry,
    _segment_duration_for_source,
    _segment_start_for_source,
    _select_pixabay_video_url,
)


def test_queries_for_entry_prefers_story_visual_keywords(monkeypatch):
    monkeypatch.setenv("PIXABAY_MAX_QUERIES_PER_ITEM", "5")
    queries = _queries_for_entry(
        {
            "visual_keywords": [
                "Phone Texting",
                "nature",
                "couple argument",
                "phone texting",
            ]
        }
    )

    assert queries[:2] == ["phone texting", "couple argument"]
    assert "nature" not in queries


def test_segment_duration_stays_in_shorts_cut_range(monkeypatch):
    monkeypatch.setenv("SHORTS_BG_MIN_CLIP_SECONDS", "2.8")
    monkeypatch.setenv("SHORTS_BG_MAX_CLIP_SECONDS", "4.2")

    duration = _segment_duration_for_source(Path("clip-1.mp4"), 20.0)

    assert 2.8 <= duration <= 4.2


def test_segment_duration_is_deterministic_for_candidate_accounting(monkeypatch):
    monkeypatch.setenv("SHORTS_BG_MIN_CLIP_SECONDS", "2.8")
    monkeypatch.setenv("SHORTS_BG_MAX_CLIP_SECONDS", "4.2")
    path = Path("clip-1.mp4")

    assert _segment_duration_for_source(path, 20.0) == _segment_duration_for_source(path, 20.0)


def test_segment_start_is_inside_source_duration():
    duration = 3.5
    start = _segment_start_for_source(Path("clip-1.mp4"), 20.0, duration)

    assert 0.0 <= start <= 16.25


def test_select_pixabay_video_url_prefers_large_enough_variant():
    url = _select_pixabay_video_url(
        {
            "large": {"url": "https://example.com/large.mp4", "width": 1920, "height": 1080},
            "medium": {"url": "https://example.com/medium.mp4", "width": 640, "height": 360},
        }
    )

    assert url == "https://example.com/large.mp4"


def test_blocked_pixabay_hit_rejects_green_screen_and_abstract():
    assert _is_blocked_pixabay_hit({"tags": "phone, green screen, texting"}) is True
    assert _is_blocked_pixabay_hit({"tags": "abstract, background, spiral"}) is True
    assert _is_blocked_pixabay_hit({"tags": "phone, texting, woman"}) is False


def test_metadata_by_id_loads_visual_keywords(tmp_path, monkeypatch):
    metadata_path = tmp_path / "final_metadata.json"
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "id": "abc123",
                    "visual_keywords": ["phone texting"],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pixabay_video_merge, "FINAL_METADATA_FILE", metadata_path)

    assert _metadata_by_id()["abc123"]["visual_keywords"] == ["phone texting"]
