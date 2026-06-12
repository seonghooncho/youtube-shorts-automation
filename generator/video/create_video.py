# create_video.py (Pillow 기반 중앙 자막 + 굵은 테두리, ImageMagick 불필요)

import json
import os
import subprocess
import urllib.request
from pathlib import Path
import numpy as np

from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import VideoFileClip, CompositeVideoClip, ImageClip, vfx
from moviepy.video.tools.subtitles import SubtitlesClip
import pysrt

if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

from shared.utils.config import (
    AUDIO_DIR,
    SUBTITLES_DIR,
    FINAL_DIR,
    get_font_file,
    get_output_file,
    get_video_source,
)
import shutil
from imageio_ffmpeg import get_ffmpeg_exe
# ----------------------------
# 상태/설정
# ----------------------------
TTS_RESULT_JSON = get_output_file("tts_check_result.json")
ANTON_URL = "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"
DEFAULT_FONT_REL = "fonts/Anton-Regular.ttf"  # assets/fonts/Anton-Regular.ttf
DEFAULT_FONT_SIZE = 84
DEFAULT_STROKE_WIDTH = 24  # 가독성 충분히 두껍게
DEFAULT_PADDING = 40       # 텍스트 이미지 패딩
DEFAULT_LINE_SPACING_RATIO = 0.25  # 줄 간격 배수
CAPTION_PLAY_RES_X = 1080
CAPTION_PLAY_RES_Y = 1920
DEFAULT_CAPTION_FONT_SIZE = 76
DEFAULT_CAPTION_OUTLINE = 5
DEFAULT_CAPTION_SHADOW = 2
DEFAULT_CAPTION_CENTER_X = CAPTION_PLAY_RES_X // 2
DEFAULT_CAPTION_CENTER_Y = CAPTION_PLAY_RES_Y // 2
DEFAULT_CAPTION_MAX_WORDS = 2
DEFAULT_CAPTION_MAX_CHARS = 16
DEFAULT_CAPTION_MAX_DURATION = 1.05
DEFAULT_CAPTION_FADE_MS = 35
ASS_WHITE_STYLE = "&H00FFFFFF"
ASS_YELLOW_STYLE = "&H0000FFFF"
ASS_BLACK_STYLE = "&H00000000"
ASS_SHADOW_STYLE = "&H80000000"
ASS_WHITE_INLINE = "&HFFFFFF&"
ASS_YELLOW_INLINE = "&H00FFFF&"


# ----------------------------
# 유틸
# ----------------------------


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or get_ffmpeg_exe()


def get_tts_metadata(filename: str) -> dict:
    with open(TTS_RESULT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    return next((entry for entry in data if entry["filename"] == filename), None)


def adjust_subtitle_timings(srt_path: Path, speed: float):
    """SRT 타임코드에 배속(slow/fast) 보정."""
    subs = pysrt.open(str(srt_path))
    adjusted = []
    for sub in subs:
        start_sec = sub.start.ordinal / 1000 / speed
        end_sec = sub.end.ordinal / 1000 / speed
        adjusted.append(((start_sec, end_sec), sub.text))
    return adjusted


def ensure_anton_font() -> Path:
    """
    assets/fonts/Anton-Regular.ttf 이 없으면 자동 다운로드.
    실패하면 그대로 경로 반환하지만, 폰트 로드는 아래에서 기본폰트로 폴백됨.
    """
    font_path = get_font_file()
    font_path.parent.mkdir(parents=True, exist_ok=True)
    if not font_path.exists():
        try:
            print("⬇️ Anton-Regular.ttf not found. Downloading...")
            urllib.request.urlretrieve(ANTON_URL, str(font_path))
            print(f"✅ Downloaded: {font_path}")
        except Exception as e:
            print(f"⚠️ Failed to download Anton font: {e}")
    return font_path


def _ensure_ffmpeg_font_dir(font_path: Path) -> Path:
    """Keep FFmpeg's ASS font scan limited to actual font files."""
    font_dir = FINAL_DIR / "_fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    target = font_dir / font_path.name
    if not target.exists() or target.stat().st_size != font_path.stat().st_size:
        shutil.copy2(font_path, target)
    return font_dir


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font, stroke_width: int):
    # Pillow 최신버전 호환: textbbox 기준
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])


def _build_pil_text_image(
    text: str,
    font_path: Path,
    fontsize: int,
    fill: str,
    stroke_fill: str,
    stroke_width: int,
    max_width: int,
    padding: int = DEFAULT_PADDING,
    line_spacing_ratio: float = DEFAULT_LINE_SPACING_RATIO,
) -> Image.Image:
    """
    중앙 정렬 멀티라인 텍스트 이미지를 PIL로 생성.
    - 굵은 스트로크(stroke_width)로 가독성 강화.
    - max_width 기준으로 단어 단위 줄바꿈.
    """
    # 폰트 로드 (실패시 기본 폰트 폴백)
    try:
        font = ImageFont.truetype(str(font_path), fontsize)
    except Exception:
        font = ImageFont.load_default()

    # 단어 래핑
    words = (text or "").split()
    lines, cur = [], ""
    dummy = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(dummy)

    for w in words:
        trial = (cur + " " + w).strip()
        tw, th = _measure_text(d, trial, font, stroke_width)
        if tw <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    if not lines:
        lines = [" "]  # 빈 문자열 방어

    # 크기 계산
    sizes = [_measure_text(d, ln, font, stroke_width) for ln in lines]
    line_h = max(h for (_, h) in sizes)
    gap = int(line_h * line_spacing_ratio)
    text_w = min(max((w for (w, _) in sizes)), max_width)
    text_h = len(lines) * line_h + (len(lines) - 1) * gap

    # 캔버스 생성 (투명 배경)
    img = Image.new("RGBA", (text_w + padding * 2, text_h + padding * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 중앙 정렬로 줄 렌더링
    y = padding
    for ln in lines:
        lw, lh = _measure_text(draw, ln, font, stroke_width)
        x = (img.width - lw) // 2
        draw.text(
            (x, y),
            ln,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        y += lh + gap

    return img


def make_textclip_via_pil(
    text: str,
    fontsize: int,
    max_width: int,
    fill: str = "white",
    stroke_fill: str = "black",
    stroke_width: int = DEFAULT_STROKE_WIDTH,
) -> ImageClip:
    """
    Pillow로 생성한 텍스트 이미지를 ImageClip으로 감싸 반환.
    SubtitlesClip이 duration을 지정하므로 여기선 duration 설정 불필요.
    """
    font_path = ensure_anton_font()
    pil_img = _build_pil_text_image(
        text=text,
        font_path=font_path,
        fontsize=fontsize,
        fill=fill,
        stroke_fill=stroke_fill,
        stroke_width=stroke_width,
        max_width=max_width,
    )

    # ✅ PIL → NumPy 변환 (RGBA 지원)
    arr = np.array(pil_img)
    if arr.ndim == 3 and arr.shape[2] == 4:
        # RGBA → RGB + 알파마스크
        rgb = arr[:, :, :3]
        alpha = arr[:, :, 3].astype(float) / 255.0
        clip = ImageClip(rgb)
        mask = ImageClip(alpha, ismask=True)
        return clip.set_mask(mask)
    else:
        return ImageClip(arr)


# ----------------------------
# 메인 렌더
# ----------------------------
def render_video_with_ffmpeg(filename: str):
    audio_path = AUDIO_DIR / f"{filename}.mp3"
    subtitle_path = SUBTITLES_DIR / f"{filename}.srt"
    output_path = FINAL_DIR / f"{filename}.mp4"
    temp_caption_ass = FINAL_DIR / f"{filename}_captions.ass"

    # 메타 조회
    metadata = get_tts_metadata(filename)
    if not metadata:
        raise ValueError(f"'{filename}'에 대한 메타데이터를 찾을 수 없습니다.")

    speed = metadata.get("speed", 1.0)
    final_duration = metadata.get("final_duration")
    if final_duration is None:
        raise ValueError(f"'{filename}'에 final_duration 정보가 없습니다.")

    # 배경 영상
    margin = 1.0
    background_path = get_video_source(f"{filename}.mp4")
    if not background_path.exists():
        raise FileNotFoundError(f"배경 영상이 없습니다: {background_path}")

    # 자막 타이밍 보정
    if not subtitle_path.exists():
        raise FileNotFoundError(f"SRT가 없습니다: {subtitle_path}")
    adjusted_subs = adjust_subtitle_timings(subtitle_path, speed)
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    _write_centered_caption_ass(adjusted_subs, temp_caption_ass, offset_seconds=margin)

    print(f"🎬 FFmpeg: subtitles + audio merge → {output_path}")
    font_path = ensure_anton_font()
    ffmpeg_font_dir = _ensure_ffmpeg_font_dir(font_path)
    subtitle_filter = (
        f"subtitles='{_escape_filter_path(temp_caption_ass)}'"
        f":fontsdir='{_escape_filter_path(ffmpeg_font_dir)}'"
        f":original_size={CAPTION_PLAY_RES_X}x{CAPTION_PLAY_RES_Y}"
    )
    filter_complex = (
        f"[0:v]{subtitle_filter}[v];"
        f"[2:a]{_atempo_filter(speed)}[a1];"
        "[1:a][a1][3:a]concat=n=3:v=0:a=1[a]"
    )

    ffmpeg_cmd = [
        _ffmpeg_bin(),
        "-y",
        "-i", str(background_path),
        "-f", "lavfi", "-t", "1", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-i", str(audio_path),
        "-f", "lavfi", "-t", "1", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(output_path),
    ]
    subprocess.run(ffmpeg_cmd, check=True)

    # 임시파일 정리
    temp_caption_ass.unlink(missing_ok=True)


def _atempo_filter(speed: float) -> str:
    atempo_filters = []
    remaining_speed = speed
    while remaining_speed > 2.0:
        atempo_filters.append("atempo=2.0")
        remaining_speed /= 2.0
    while remaining_speed < 0.5:
        atempo_filters.append("atempo=0.5")
        remaining_speed /= 0.5
    atempo_filters.append(f"atempo={remaining_speed:.4f}")
    return ",".join(atempo_filters)


def _write_adjusted_srt(adjusted_subs, path: Path, offset_seconds: float) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for index, ((start, end), text) in enumerate(adjusted_subs, start=1):
            f.write(f"{index}\n")
            f.write(f"{_srt_time(start + offset_seconds)} --> {_srt_time(end + offset_seconds)}\n")
            f.write(f"{text}\n\n")


def _write_centered_caption_ass(adjusted_subs, path: Path, offset_seconds: float) -> None:
    """Write centered Shorts-style captions as ASS for deterministic FFmpeg rendering."""
    font_size = _env_int("CAPTION_FONT_SIZE", DEFAULT_CAPTION_FONT_SIZE, minimum=24)
    outline = _env_int("CAPTION_OUTLINE", DEFAULT_CAPTION_OUTLINE, minimum=0)
    shadow = _env_int("CAPTION_SHADOW", DEFAULT_CAPTION_SHADOW, minimum=0)
    center_x = _env_int("CAPTION_CENTER_X", DEFAULT_CAPTION_CENTER_X, minimum=0)
    center_y = _env_int("CAPTION_CENTER_Y", DEFAULT_CAPTION_CENTER_Y, minimum=0)
    fade_ms = _env_int("CAPTION_FADE_MS", DEFAULT_CAPTION_FADE_MS, minimum=0)
    events = _build_centered_caption_events(adjusted_subs)

    with open(path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\n")
        f.write("ScriptType: v4.00+\n")
        f.write(f"PlayResX: {CAPTION_PLAY_RES_X}\n")
        f.write(f"PlayResY: {CAPTION_PLAY_RES_Y}\n")
        f.write("WrapStyle: 2\n")
        f.write("ScaledBorderAndShadow: yes\n\n")
        f.write("[V4+ Styles]\n")
        f.write(
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\n"
        )
        f.write(
            "Style: Caption,Anton,"
            f"{font_size},{ASS_WHITE_STYLE},{ASS_YELLOW_STYLE},{ASS_BLACK_STYLE},"
            f"{ASS_SHADOW_STYLE},-1,0,0,0,100,100,0,0,1,{outline},{shadow},"
            "5,70,70,0,1\n\n"
        )
        f.write("[Events]\n")
        f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        for (start, end, words) in events:
            event_start = start + offset_seconds
            event_end = max(end + offset_seconds, event_start + 0.1)
            text = _format_centered_caption_text(words)
            prefix = f"{{\\an5\\pos({center_x},{center_y})\\fad({fade_ms},{fade_ms})}}"
            f.write(
                "Dialogue: 0,"
                f"{_ass_time(event_start)},{_ass_time(event_end)},"
                f"Caption,,0,0,0,,{prefix}{text}\n"
            )


def _build_centered_caption_events(
    adjusted_subs,
    *,
    max_words: int | None = None,
    max_chars: int | None = None,
    max_duration: float | None = None,
):
    max_words = max_words or _env_int("CAPTION_MAX_WORDS", DEFAULT_CAPTION_MAX_WORDS, minimum=1)
    max_chars = max_chars or _env_int("CAPTION_MAX_CHARS", DEFAULT_CAPTION_MAX_CHARS, minimum=4)
    max_duration = max_duration or _env_float(
        "CAPTION_MAX_DURATION",
        DEFAULT_CAPTION_MAX_DURATION,
        minimum=0.2,
    )

    words = []
    for (start, end), text in adjusted_subs:
        cleaned = _normalize_caption_word(text)
        if cleaned:
            words.append((float(start), float(end), cleaned))

    events = []
    group = []
    for item in words:
        if _should_start_new_caption_group(group, item, max_words, max_chars, max_duration):
            events.append(_caption_group_to_event(group))
            group = []
        group.append(item)
    if group:
        events.append(_caption_group_to_event(group))
    return events


def _should_start_new_caption_group(group, next_item, max_words, max_chars, max_duration) -> bool:
    if not group:
        return False
    if _ends_caption_phrase(group[-1][2]):
        return True
    candidate_words = [item[2] for item in group] + [next_item[2]]
    candidate_text = " ".join(candidate_words)
    candidate_duration = next_item[1] - group[0][0]
    return (
        len(group) >= max_words
        or len(candidate_text) > max_chars
        or candidate_duration > max_duration
    )


def _caption_group_to_event(group):
    start = group[0][0]
    end = group[-1][1]
    words = [item[2] for item in group]
    return (start, end, words)


def _format_centered_caption_text(words) -> str:
    uppercase = _env_bool("CAPTION_UPPERCASE", default=True)
    highlight_last = _env_bool("CAPTION_HIGHLIGHT_LAST_WORD", default=True)
    display_words = []
    for word in words:
        display = word.upper() if uppercase else word
        display_words.append(_escape_ass_text(display))

    if highlight_last and display_words:
        if len(display_words) == 1:
            return f"{{\\c{ASS_YELLOW_INLINE}}}{display_words[0]}{{\\c{ASS_WHITE_INLINE}}}"
        leading = " ".join(display_words[:-1])
        focus = display_words[-1]
        return f"{leading} {{\\c{ASS_YELLOW_INLINE}}}{focus}{{\\c{ASS_WHITE_INLINE}}}"
    return " ".join(display_words)


def _normalize_caption_word(text: str) -> str:
    return " ".join(str(text or "").replace("\n", " ").split()).strip()


def _ends_caption_phrase(text: str) -> bool:
    return text.rstrip().endswith((".", "?", "!", ";", ":"))


def _escape_ass_text(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
    )


def _ass_time(seconds: float) -> str:
    total_centis = max(0, int(round(seconds * 100)))
    hours, rem = divmod(total_centis, 3600 * 100)
    minutes, rem = divmod(rem, 60 * 100)
    secs, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{centis:02}"


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace("'", "\\'")


def batch_render_all_videos(target_ids: list[str] | None = None):
    with open(TTS_RESULT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    filenames = [entry["filename"] for entry in data]
    if target_ids:
        target_set = set(target_ids)
        filenames = [filename for filename in filenames if filename in target_set]

    for filename in filenames:
        try:
            print(f"\n🚀 시작: {filename}")
            render_video_with_ffmpeg(filename)
            print(f"✅ 완료: {filename}")
        except Exception as e:
            print(f"❌ 실패: {filename} → {e}")


if __name__ == "__main__":
    # 단건 테스트
    # render_video_with_ffmpeg("1mhz7zg")
    batch_render_all_videos()
