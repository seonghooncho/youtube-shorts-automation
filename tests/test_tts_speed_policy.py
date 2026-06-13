from generator.tts.speed_policy import adjusted_tts_duration, final_duration_in_range, target_tts_speed


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
