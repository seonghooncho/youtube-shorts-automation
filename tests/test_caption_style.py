import generator.video.create_video as create_video
from generator.video.create_video import (
    _audio_merge_filter,
    _build_centered_caption_events,
    _ensure_ffmpeg_font_dir,
    _format_centered_caption_text,
    _video_filter_with_subtitles,
    _write_centered_caption_ass,
)


def test_build_centered_caption_events_groups_short_phrases():
    adjusted_subs = [
        ((0.0, 0.2), "Okay"),
        ((0.2, 0.5), "quick"),
        ((0.5, 0.8), "backstory"),
        ((0.8, 1.1), "now"),
    ]

    events = _build_centered_caption_events(
        adjusted_subs,
        max_words=2,
        max_chars=20,
        max_duration=1.0,
    )

    assert events == [
        (0.0, 0.5, ["Okay", "quick"]),
        (0.5, 1.1, ["backstory", "now"]),
    ]


def test_build_centered_caption_events_breaks_after_sentence_end():
    adjusted_subs = [
        ((0.0, 0.2), "Wait."),
        ((0.2, 0.4), "Now"),
    ]

    events = _build_centered_caption_events(
        adjusted_subs,
        max_words=3,
        max_chars=20,
        max_duration=1.0,
    )

    assert events == [
        (0.0, 0.2, ["Wait."]),
        (0.2, 0.4, ["Now"]),
    ]


def test_format_centered_caption_text_uses_uppercase_and_focus_color(monkeypatch):
    monkeypatch.delenv("CAPTION_UPPERCASE", raising=False)
    monkeypatch.delenv("CAPTION_HIGHLIGHT_LAST_WORD", raising=False)

    text = _format_centered_caption_text(["hello", "world"])

    assert text == r"HELLO {\c&H00FFFF&}WORLD{\c&HFFFFFF&}"


def test_write_centered_caption_ass_sets_center_position_and_style(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTION_FONT_SIZE", "70")
    monkeypatch.setenv("CAPTION_CENTER_X", "540")
    monkeypatch.setenv("CAPTION_CENTER_Y", "960")
    monkeypatch.setenv("CAPTION_FADE_MS", "30")
    monkeypatch.setenv("CAPTION_MAX_WORDS", "2")
    monkeypatch.setenv("CAPTION_MAX_CHARS", "20")

    ass_path = tmp_path / "captions.ass"
    _write_centered_caption_ass(
        [
            ((0.0, 0.2), "okay"),
            ((0.2, 0.5), "quick"),
        ],
        ass_path,
        offset_seconds=1.0,
    )

    content = ass_path.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Style: Caption,Anton,70" in content
    assert r"{\an5\pos(540,960)\fad(30,30)}" in content
    assert "Dialogue: 0,0:00:01.00,0:00:01.50,Caption" in content
    assert r"OKAY {\c&H00FFFF&}QUICK{\c&HFFFFFF&}" in content


def test_write_centered_caption_ass_uses_larger_crisp_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("CAPTION_FONT_SIZE", raising=False)
    monkeypatch.delenv("CAPTION_OUTLINE", raising=False)
    monkeypatch.delenv("CAPTION_SHADOW", raising=False)
    monkeypatch.delenv("CAPTION_FADE_MS", raising=False)

    ass_path = tmp_path / "captions.ass"
    _write_centered_caption_ass([((0.0, 0.2), "blood")], ass_path, offset_seconds=0.0)

    content = ass_path.read_text(encoding="utf-8")
    assert "Style: Caption,Anton,114" in content
    assert ",1,7,0,5,70,70,0,1" in content
    assert r"\fad(" not in content or r"\fad(0,0)" in content


def test_ensure_ffmpeg_font_dir_allows_missing_font(tmp_path, monkeypatch):
    monkeypatch.setattr(create_video, "FINAL_DIR", tmp_path / "final")

    font_dir = _ensure_ffmpeg_font_dir(tmp_path / "missing.ttf")

    assert font_dir == tmp_path / "final" / "_fonts"
    assert font_dir.exists()


def test_audio_merge_filter_normalizes_loudness_and_sample_rate():
    audio_filter = _audio_merge_filter(1.16)

    assert "atempo=1.1600" in audio_filter
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in audio_filter
    assert "aformat=sample_rates=48000:channel_layouts=stereo" in audio_filter


def test_video_filter_normalizes_before_burning_subtitles(monkeypatch):
    monkeypatch.delenv("SHORTS_SCALE_FILTER", raising=False)

    video_filter = _video_filter_with_subtitles("subtitles='captions.ass'")

    assert video_filter.startswith("scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos")
    assert "crop=1080:1920" in video_filter
    assert "format=yuv444p,subtitles='captions.ass',format=yuv420p" in video_filter
