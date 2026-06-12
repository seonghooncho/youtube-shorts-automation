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
