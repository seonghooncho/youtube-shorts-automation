# create_video.py (Pillow 기반 중앙 자막 + 굵은 테두리, ImageMagick 불필요)

import json
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
    temp_adjusted_srt = FINAL_DIR / f"{filename}_adjusted.srt"

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
    _write_adjusted_srt(adjusted_subs, temp_adjusted_srt, offset_seconds=margin)

    print(f"🎬 FFmpeg: subtitles + audio merge → {output_path}")
    font_path = ensure_anton_font()
    subtitle_filter = (
        f"subtitles='{_escape_filter_path(temp_adjusted_srt)}'"
        f":fontsdir='{_escape_filter_path(font_path.parent)}'"
        ":force_style='FontName=Anton,FontSize=84,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=8,Alignment=5'"
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
    temp_adjusted_srt.unlink(missing_ok=True)


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
