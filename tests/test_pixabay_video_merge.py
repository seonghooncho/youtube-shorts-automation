import json
from pathlib import Path

from PIL import Image, ImageFilter

import generator.video.pixabay_video_merge as pixabay_video_merge
from generator.video.pixabay_video_merge import (
    _concat_segments,
    _download_video_safe,
    _fetch_pixabay_video_urls_safe,
    _image_sharpness_score,
    _is_blocked_pixabay_hit,
    _is_low_signal_pixabay_hit,
    _metadata_by_id,
    _passes_video_quality_gate,
    _queries_for_entry,
    _score_pixabay_hit,
    _segment_duration_for_source,
    _segment_start_for_source,
    _select_pixabay_video_url,
    fetch_pixabay_video_urls,
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


def test_queries_for_entry_uses_opening_visual_query_first(monkeypatch):
    monkeypatch.setenv("PIXABAY_MAX_QUERIES_PER_ITEM", "6")

    queries = _queries_for_entry(
        {
            "opening_visual_query": "door camera driveway car",
            "visual_beat_queries": [
                {"beat": "receipt", "query": "phone timestamp screenshot"},
                {"beat": "decision", "query": "private parking sign"},
            ],
            "visual_keywords": ["phone texting", "suburban driveway"],
        }
    )

    assert queries[:4] == [
        "door camera driveway car",
        "phone timestamp screenshot",
        "private parking sign",
        "phone texting",
    ]


def test_queries_for_entry_includes_asmr_visual_fallback(monkeypatch):
    monkeypatch.setenv("PIXABAY_MAX_QUERIES_PER_ITEM", "12")
    queries = _queries_for_entry(
        {
            "visual_keywords": [
                "phone texting",
                "apartment hallway",
                "office conversation",
                "angry neighbor",
                "security camera",
                "rental house",
            ]
        }
    )

    assert "hands typing keyboard close up" in queries
    assert queries.index("hands typing keyboard close up") > queries.index("phone texting")


def test_segment_duration_uses_fast_initial_cut_range(monkeypatch):
    monkeypatch.delenv("SHORTS_BG_MIN_CLIP_SECONDS", raising=False)
    monkeypatch.delenv("SHORTS_BG_MAX_CLIP_SECONDS", raising=False)
    monkeypatch.delenv("SHORTS_BG_FAST_MIN_CLIP_SECONDS", raising=False)
    monkeypatch.delenv("SHORTS_BG_FAST_MAX_CLIP_SECONDS", raising=False)

    duration = _segment_duration_for_source(Path("clip-1.mp4"), 20.0)

    assert 2.2 <= duration <= 3.5


def test_segment_duration_stays_in_regular_shorts_cut_range(monkeypatch):
    monkeypatch.delenv("SHORTS_BG_MIN_CLIP_SECONDS", raising=False)
    monkeypatch.delenv("SHORTS_BG_MAX_CLIP_SECONDS", raising=False)

    duration = _segment_duration_for_source(Path("clip-1.mp4"), 20.0, current_duration=12.0)

    assert 3.4 <= duration <= 5.6


def test_segment_duration_breathes_more_for_longer_narration(monkeypatch):
    monkeypatch.delenv("SHORTS_BG_MIN_CLIP_SECONDS", raising=False)
    monkeypatch.delenv("SHORTS_BG_MAX_CLIP_SECONDS", raising=False)

    duration = _segment_duration_for_source(Path("clip-1.mp4"), 20.0, target_length=80.0, current_duration=12.0)

    assert 4.0 <= duration <= 6.6


def test_segment_duration_is_deterministic_for_candidate_accounting(monkeypatch):
    monkeypatch.setenv("SHORTS_BG_MIN_CLIP_SECONDS", "3.4")
    monkeypatch.setenv("SHORTS_BG_MAX_CLIP_SECONDS", "5.6")
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


def test_select_pixabay_video_url_prefers_highest_resolution_regardless_of_label():
    url = _select_pixabay_video_url(
        {
            "large": {"url": "https://example.com/large.mp4", "width": 1920, "height": 1080},
            "medium": {"url": "https://example.com/medium.mp4", "width": 3840, "height": 2160},
        }
    )

    assert url == "https://example.com/medium.mp4"


def test_select_pixabay_video_url_rejects_low_res_by_default(monkeypatch):
    monkeypatch.delenv("PIXABAY_ALLOW_LOW_RES_FALLBACK", raising=False)
    url = _select_pixabay_video_url(
        {
            "small": {"url": "https://example.com/small.mp4", "width": 1280, "height": 720},
        }
    )

    assert url is None


def test_select_pixabay_video_url_rejects_short_edge_below_1080(monkeypatch):
    monkeypatch.delenv("PIXABAY_ALLOW_LOW_RES_FALLBACK", raising=False)

    url = _select_pixabay_video_url(
        {
            "wide": {"url": "https://example.com/wide.mp4", "width": 1920, "height": 720},
        }
    )

    assert url is None


def test_blocked_pixabay_hit_rejects_green_screen_and_abstract():
    assert _is_blocked_pixabay_hit({"tags": "phone, green screen, texting"}) is True
    assert _is_blocked_pixabay_hit({"tags": "abstract, background, spiral"}) is True
    assert _is_blocked_pixabay_hit({"tags": "phone, texting, woman"}) is False


def test_low_signal_pixabay_hit_rejects_generic_landscape_without_overlap():
    assert _is_low_signal_pixabay_hit({"tags": "nature, landscape, sunset, sky"}, "phone texting") is True
    assert _is_low_signal_pixabay_hit({"tags": "phone, woman, texting"}, "phone texting") is False


def test_pixabay_hit_score_prefers_query_overlap():
    matching = _score_pixabay_hit({"tags": "phone, texting, woman", "duration": 10, "likes": 100}, "phone texting")
    generic = _score_pixabay_hit({"tags": "nature, landscape, sky", "duration": 10, "likes": 100}, "phone texting")

    assert matching > generic


def test_fetch_pixabay_video_urls_sorts_by_score(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "hits": [
                    {
                        "id": 1,
                        "tags": "nature, landscape, sky",
                        "duration": 10,
                        "videos": {"large": {"url": "https://example.com/1.mp4", "width": 1920, "height": 1080}},
                    },
                    {
                        "id": 2,
                        "tags": "phone, texting, woman",
                        "duration": 10,
                        "videos": {"large": {"url": "https://example.com/2.mp4", "width": 1920, "height": 1080}},
                    },
                ]
            }

    monkeypatch.setattr(pixabay_video_merge.requests, "get", lambda *args, **kwargs: _Response())

    results = fetch_pixabay_video_urls(query="phone texting", min_sec=4, max_sec=30)

    assert results == [(2, "https://example.com/2.mp4", 10.0)]


def test_fetch_pixabay_video_urls_safe_returns_empty_on_error(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(pixabay_video_merge, "fetch_pixabay_video_urls", _raise)

    assert _fetch_pixabay_video_urls_safe(query="phone texting") == []


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


def test_image_sharpness_score_detects_blurry_frames():
    sharp = Image.new("L", (120, 120), 255)
    pixels = sharp.load()
    for y in range(120):
        for x in range(120):
            if (x // 6 + y // 6) % 2:
                pixels[x, y] = 0
    blurry = sharp.filter(ImageFilter.GaussianBlur(radius=4))

    assert _image_sharpness_score(sharp) > _image_sharpness_score(blurry) * 10


def test_video_quality_gate_rejects_low_sharpness(monkeypatch, tmp_path):
    video_path = tmp_path / "candidate.mp4"
    video_path.write_bytes(b"placeholder")
    monkeypatch.setenv("PIXABAY_MIN_SHARPNESS_SCORE", "60")
    monkeypatch.setattr(pixabay_video_merge, "_video_sharpness_score", lambda path: 12.5)

    assert _passes_video_quality_gate(video_path) is False


def test_download_video_safe_removes_blurry_candidate(monkeypatch, tmp_path):
    def _fake_download(url, path):
        path.write_bytes(b"placeholder")
        return str(path)

    part_path = tmp_path / "part.mp4"
    monkeypatch.setattr(pixabay_video_merge, "download_video_to_ebs", _fake_download)
    monkeypatch.setattr(pixabay_video_merge, "_video_sharpness_score", lambda path: 12.5)
    monkeypatch.setenv("PIXABAY_MIN_SHARPNESS_SCORE", "60")

    assert _download_video_safe("https://example.com/video.mp4", part_path, "abc", "phone") is False
    assert not part_path.exists()


def test_concat_segments_reencodes_normalized_background(monkeypatch, tmp_path):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

    segment_a = tmp_path / "a.mp4"
    segment_b = tmp_path / "b.mp4"
    segment_a.write_bytes(b"a")
    segment_b.write_bytes(b"b")
    output_path = tmp_path / "out.mp4"

    monkeypatch.setenv("SHORTS_RENDER_FPS", "30")
    monkeypatch.setenv("BG_CONCAT_PRESET", "fast")
    monkeypatch.setenv("BG_CONCAT_CRF", "18")
    monkeypatch.setattr(pixabay_video_merge, "_ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(pixabay_video_merge.subprocess, "run", _fake_run)

    _concat_segments([segment_a, segment_b], output_path)

    cmd = captured["cmd"]
    assert "-fflags" in cmd
    assert "+genpts" in cmd
    assert "-c:v" in cmd
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert ("-c" not in cmd) or (cmd[cmd.index("-c") + 1] != "copy")
    assert "fps=30,setsar=1,format=yuv420p" in cmd[cmd.index("-vf") + 1]
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"
