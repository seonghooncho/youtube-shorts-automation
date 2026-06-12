import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def validate_video_file(
    path: Path | str,
    *,
    min_duration_seconds: float | None = None,
    min_size_bytes: int | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    video_path = Path(path)
    if not video_path.exists():
        return False, "video_file_missing", {}

    min_duration_seconds = _float_env("MIN_RENDER_DURATION_SECONDS", 5.0, min_duration_seconds)
    min_size_bytes = _int_env("MIN_RENDER_VIDEO_BYTES", 1_048_576, min_size_bytes)
    min_width = _int_env("MIN_RENDER_WIDTH", 720, min_width)
    min_height = _int_env("MIN_RENDER_HEIGHT", 1280, min_height)

    size = video_path.stat().st_size
    if size < min_size_bytes:
        return False, f"video_too_small:{size}<{min_size_bytes}", {"size": size}

    try:
        probe = probe_video(video_path)
    except Exception as exc:
        return False, f"ffprobe_failed:{exc}", {"size": size}

    format_info = probe.get("format") or {}
    streams = probe.get("streams") or []
    video_stream = _first_stream(streams, "video")
    audio_stream = _first_stream(streams, "audio")
    duration = _duration_seconds(format_info, video_stream)

    details = {
        "size": size,
        "duration": duration,
        "width": int(video_stream.get("width") or 0) if video_stream else 0,
        "height": int(video_stream.get("height") or 0) if video_stream else 0,
        "fps": _fps(video_stream) if video_stream else 0.0,
        "video_bit_rate": _int_value((video_stream or {}).get("bit_rate")),
        "audio_sample_rate": _int_value((audio_stream or {}).get("sample_rate")),
        "audio_bit_rate": _int_value((audio_stream or {}).get("bit_rate")),
        "audio_channels": _int_value((audio_stream or {}).get("channels")),
        "has_video": bool(video_stream),
        "has_audio": bool(audio_stream),
    }

    if not video_stream:
        return False, "video_stream_missing", details
    if not audio_stream:
        return False, "audio_stream_missing", details
    if duration < min_duration_seconds:
        return False, f"duration_too_short:{duration:.3f}<{min_duration_seconds}", details
    if details["width"] < min_width or details["height"] < min_height:
        return False, f"resolution_too_small:{details['width']}x{details['height']}", details

    return True, "ok", details


def quality_warnings(path: Path | str) -> list[str]:
    probe = probe_video(path)
    format_info = probe.get("format") or {}
    streams = probe.get("streams") or []
    video_stream = _first_stream(streams, "video")
    audio_stream = _first_stream(streams, "audio")
    warnings = []
    duration = _duration_seconds(format_info, video_stream)
    fps = _fps(video_stream)
    video_bit_rate = _int_value((video_stream or {}).get("bit_rate")) or _int_value(format_info.get("bit_rate"))
    audio_sample_rate = _int_value((audio_stream or {}).get("sample_rate"))
    audio_bit_rate = _int_value((audio_stream or {}).get("bit_rate"))

    max_recommended_duration = _float_env("MAX_RECOMMENDED_SHORTS_DURATION_SECONDS", 85.0, None)
    min_recommended_fps = _float_env("MIN_RECOMMENDED_RENDER_FPS", 29.0, None)
    min_recommended_video_bit_rate = _int_env("MIN_RECOMMENDED_VIDEO_BITRATE", 3_000_000, None)
    min_recommended_audio_sample_rate = _int_env("MIN_RECOMMENDED_AUDIO_SAMPLE_RATE", 44_100, None)
    min_recommended_audio_bit_rate = _int_env("MIN_RECOMMENDED_AUDIO_BITRATE", 96_000, None)

    if duration > max_recommended_duration:
        warnings.append(f"duration_above_recommendation:{duration:.3f}>{max_recommended_duration:.3f}")
    if fps and fps < min_recommended_fps:
        warnings.append(f"fps_below_recommendation:{fps:.3f}<{min_recommended_fps:.3f}")
    if video_bit_rate and video_bit_rate < min_recommended_video_bit_rate:
        warnings.append(f"video_bitrate_below_recommendation:{video_bit_rate}<{min_recommended_video_bit_rate}")
    if audio_sample_rate and audio_sample_rate < min_recommended_audio_sample_rate:
        warnings.append(f"audio_sample_rate_below_recommendation:{audio_sample_rate}<{min_recommended_audio_sample_rate}")
    if audio_bit_rate and audio_bit_rate < min_recommended_audio_bit_rate:
        warnings.append(f"audio_bitrate_below_recommendation:{audio_bit_rate}<{min_recommended_audio_bit_rate}")
    return warnings


def probe_video(path: Path | str) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found")

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(result.stdout or "{}")


def _first_stream(streams: list[dict[str, Any]], codec_type: str) -> dict[str, Any] | None:
    return next((stream for stream in streams if stream.get("codec_type") == codec_type), None)


def _duration_seconds(format_info: dict[str, Any], video_stream: dict[str, Any] | None) -> float:
    for source in (format_info, video_stream or {}):
        raw_duration = source.get("duration")
        if raw_duration is None:
            continue
        try:
            return float(raw_duration)
        except (TypeError, ValueError):
            continue
    return 0.0


def _fps(video_stream: dict[str, Any] | None) -> float:
    if not video_stream:
        return 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = video_stream.get(key)
        if not raw or raw == "0/0":
            continue
        if "/" in str(raw):
            numerator, denominator = str(raw).split("/", 1)
            try:
                return float(numerator) / float(denominator)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return 0.0


def _int_value(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _int_env(name: str, default: int, override: int | None) -> int:
    if override is not None:
        return override
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float, override: float | None) -> float:
    if override is not None:
        return override
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default
