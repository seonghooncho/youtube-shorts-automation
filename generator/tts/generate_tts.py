import boto3
import json
import os
from dotenv import load_dotenv
from pathlib import Path
from tqdm import tqdm
import re
import random
from moviepy.editor import AudioFileClip
from generator.text.content_gate import ensure_content_gate, normalize_narration_fields, tts_text
from generator.text.generate_scripts_from_filtered import regenerate_post_by_id
from generator.tts.speed_policy import final_duration_in_range
from shared.utils.config import FINAL_METADATA_FILE, AUDIO_DIR, MARKS_DIR, FAILED_POSTS_FILE

load_dotenv()
MALE_VOICES = ["Matthew", "Justin", "Joey", "Kevin", "Stephen"]
FEMALE_VOICES = ["Joanna", "Kendra", "Kimberly", "Salli"]
# AWS Polly 설정
_polly_kwargs = {"region_name": os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-northeast-2"}
polly = boto3.client("polly", **_polly_kwargs)

def generate_tts_with_timestamps(text, filename, voice_id):
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    MARKS_DIR.mkdir(parents=True, exist_ok=True)
    # 1. 음성(mp3) 생성
    audio_response = polly.synthesize_speech(
        OutputFormat='mp3',
        Text=text,
        TextType='text',
        VoiceId=voice_id,
        Engine='neural',
    )

    audio_path = AUDIO_DIR / f"{filename}.mp3"
    with open(audio_path, 'wb') as f:
        f.write(audio_response['AudioStream'].read())

    # 2. 단어 타임스탬프 정보(json) 생성
    marks_response = polly.synthesize_speech(
        OutputFormat='json',
        Text=text,
        TextType='text',
        VoiceId=voice_id,
        SpeechMarkTypes=['word'],
        Engine='neural'
    )

    marks = []
    if "AudioStream" in marks_response:
        for line in marks_response["AudioStream"].iter_lines():
            if line:
                marks.append(json.loads(line))

    word_timestamps_path = MARKS_DIR / f"{filename}_marks.json"
    with open(word_timestamps_path, 'w', encoding='utf-8') as f:
        json.dump(marks, f, ensure_ascii=False, indent=2)

    return str(audio_path), str(word_timestamps_path)

def pick_voice_id(voice_type):
    # 남/여/neutral 분기, 랜덤 선택
    if voice_type == "female":
        return random.choice(FEMALE_VOICES)
    else:  # male, neutral, 또는 기타
        return random.choice(MALE_VOICES)


def run_batch_tts():
    with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
        items = json.load(f)

    ready_items = []
    failed_items = []

    for idx, item in enumerate(tqdm(items, desc="Generating TTS")):
        original_filename = item.get("id", None)
        if not original_filename:
            print("❌ 게시물 ID 없음, 건너뜀")
            failed_items.append(_tts_failure(item, "missing_id"))
            continue

        metadata = item
        try:
            ensure_content_gate(metadata, stage="tts")
            normalize_narration_fields(metadata)
        except ValueError as exc:
            print(f"🚫 [id={original_filename}] content gate rejected before TTS: {exc}")
            failed_items.append(_tts_failure(metadata, str(exc)))
            continue
        voice_type = metadata.get("voice", "male")
        voice_id = pick_voice_id(voice_type)
        script_text = tts_text(metadata)

        max_retries = 2
        try_count = 0
        success = False

        while try_count <= max_retries:
            try:
                audio_path, marks_path = generate_tts_with_timestamps(script_text, original_filename, voice_id)

                audio = AudioFileClip(audio_path)
                duration = audio.duration
                audio.close()
            except Exception as exc:
                print(f"🚫 [id={original_filename}] TTS generation failed: {exc}")
                failed_items.append(_tts_failure(metadata, f"tts_generation_failed:{exc}"))
                break

            duration_ok, speed, final_duration = final_duration_in_range(duration)
            wpm = _wpm(script_text, final_duration)
            if wpm > _max_tts_wpm():
                duration_ok = False
            if duration_ok:
                # 성공
                final_audio_path = AUDIO_DIR / f"{original_filename}.mp3"
                final_marks_path = MARKS_DIR / f"{original_filename}_marks.json"
                os.replace(audio_path, final_audio_path)
                os.replace(marks_path, final_marks_path)
                if not _valid_tts_artifacts(original_filename):
                    failed_items.append(_tts_failure(metadata, "tts_artifact_missing_after_success"))
                    break
                metadata["tts_status"] = "READY"
                metadata["tts_voice_id"] = voice_id
                metadata["tts_wpm"] = round(wpm, 2)
                metadata["tts_original_duration"] = round(duration, 3)
                metadata["tts_final_duration_estimate"] = round(final_duration, 3)
                ready_items.append(metadata)
                success = True
                break
            else:
                # 실패 → 삭제 및 regenerate 시도
                print(
                    f"⛔️ {audio_path}: original={duration:.2f}s "
                    f"speed={speed:.2f} final={final_duration:.2f}s "
                    f"wpm={wpm:.1f} (길이/속도 부적절, 재시도 {try_count+1}/{max_retries})"
                )
                os.remove(audio_path)
                os.remove(marks_path)
                try_count += 1

                if not _allow_tts_llm_regenerate():
                    reason = (
                        "tts_pacing_failed_llm_regenerate_disabled:"
                        f"original={duration:.2f}s final={final_duration:.2f}s wpm={wpm:.1f}"
                    )
                    metadata["tts_regenerate_blocked"] = True
                    metadata["tts_regenerate_blocked_reason"] = reason
                    failed_items.append(_tts_failure(metadata, reason))
                    break

                # regenerate 시도
                new_metadata = regenerate_post_by_id(
                    original_filename,
                    regenerate_reason=(
                        "The TTS narration was outside the target Shorts pacing. "
                        f"Original audio was {duration:.1f}s and would become {final_duration:.1f}s "
                        f"after {speed:.2f}x speed-up. Rewrite the script to land around "
                        "42 to 65 seconds after speed-up, with a fast hook and no filler."
                    ),
                )
                if not new_metadata:
                    print(f"❌ [id={original_filename}] regenerate_post_by_id 실패, skip")
                    break

                # script, voice 갱신
                try:
                    ensure_content_gate(new_metadata, stage="tts_regenerate")
                    normalize_narration_fields(new_metadata)
                except ValueError as exc:
                    print(f"🚫 [id={original_filename}] regenerated metadata rejected before TTS retry: {exc}")
                    failed_items.append(_tts_failure(new_metadata, str(exc)))
                    break
                metadata = new_metadata
                script_text = tts_text(new_metadata)
                voice_type = new_metadata.get("voice", "male")
                voice_id = pick_voice_id(voice_type)

        if not success:
            print(f"🚫 [id={original_filename}] 최종 실패, skip")
            if not any(failed.get("id") == original_filename for failed in failed_items):
                failed_items.append(_tts_failure(metadata, "tts_failed"))

    with open(FINAL_METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(ready_items, f, ensure_ascii=False, indent=2)
    _append_failed_posts(failed_items)


def _wpm(text: str, final_duration_seconds: float) -> float:
    words = re.findall(r"[A-Za-z0-9']+", str(text or ""))
    if final_duration_seconds <= 0:
        return 999.0
    return len(words) / (final_duration_seconds / 60)


def _max_tts_wpm() -> float:
    try:
        return float(os.getenv("TTS_MAX_WPM", "225"))
    except ValueError:
        return 225.0


def _allow_tts_llm_regenerate() -> bool:
    return os.getenv("TTS_ALLOW_LLM_REGENERATE", "0").strip().lower() in {"1", "true", "yes", "on"}


def _valid_tts_artifacts(content_id: str) -> bool:
    audio_path = AUDIO_DIR / f"{content_id}.mp3"
    marks_path = MARKS_DIR / f"{content_id}_marks.json"
    if not audio_path.exists() or audio_path.stat().st_size <= 0:
        return False
    if not marks_path.exists() or marks_path.stat().st_size <= 0:
        return False
    try:
        with open(marks_path, "r", encoding="utf-8") as f:
            marks = json.load(f)
        return isinstance(marks, list) and bool(marks)
    except Exception:
        return False


def _tts_failure(item: dict, reason: str) -> dict:
    return {
        "id": item.get("id"),
        "title": item.get("title") or item.get("public_title"),
        "stage": "tts",
        "error": reason,
    }


def _append_failed_posts(items: list[dict]) -> None:
    if not items:
        return
    FAILED_POSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if FAILED_POSTS_FILE.exists():
        try:
            with open(FAILED_POSTS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                existing = loaded
        except Exception:
            existing = []
    with open(FAILED_POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(existing + items, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    run_batch_tts()
