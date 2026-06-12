import os


def target_tts_speed(original_duration: float) -> float:
    """Return a Shorts-friendly playback speed without pushing narration too far."""
    base_speed = _float_env("TTS_BASE_SPEED", 1.12)
    short_speed = _float_env("TTS_SHORT_SPEED", 1.06)
    medium_speed = _float_env("TTS_MEDIUM_SPEED", 1.16)
    long_speed = _float_env("TTS_LONG_SPEED", 1.20)
    very_long_speed = _float_env("TTS_VERY_LONG_SPEED", 1.24)
    max_speed = _float_env("TTS_MAX_SPEED", 1.24)

    if original_duration < _float_env("TTS_SHORT_DURATION_SECONDS", 42.0):
        speed = short_speed
    elif original_duration < _float_env("TTS_MEDIUM_DURATION_SECONDS", 55.0):
        speed = base_speed
    elif original_duration < _float_env("TTS_LONG_DURATION_SECONDS", 70.0):
        speed = medium_speed
    elif original_duration < _float_env("TTS_VERY_LONG_DURATION_SECONDS", 85.0):
        speed = long_speed
    else:
        speed = very_long_speed
    return min(max_speed, max(1.0, speed))


def adjusted_tts_duration(original_duration: float) -> float:
    return original_duration / target_tts_speed(original_duration)


def final_duration_in_range(original_duration: float) -> tuple[bool, float, float]:
    speed = target_tts_speed(original_duration)
    final_duration = original_duration / speed
    min_seconds = _float_env("TTS_MIN_FINAL_SECONDS", 35.0)
    max_seconds = _float_env("TTS_MAX_FINAL_SECONDS", 82.0)
    return min_seconds <= final_duration <= max_seconds, speed, final_duration


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default
