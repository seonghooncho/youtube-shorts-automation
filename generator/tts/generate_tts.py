import boto3
import json
import os
from dotenv import load_dotenv
from pathlib import Path
from tqdm import tqdm
import re
import random
from moviepy.editor import AudioFileClip
from generator.text.generate_scripts_from_filtered import regenerate_post_by_id
from shared.utils.config import FINAL_METADATA_FILE, AUDIO_DIR, MARKS_DIR

load_dotenv()
MALE_VOICES = ["Matthew", "Justin", "Joey", "Kevin", "Stephen"]
FEMALE_VOICES = ["Joanna", "Kendra", "Kimberly", "Salli"]
# AWS Polly 설정
polly = boto3.client(
    "polly",
    aws_access_key_id=os.getenv("AWS_POLLY_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_POLLY_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION"),
)

def generate_tts_with_timestamps(text, filename, voice_id):
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

    for idx, item in enumerate(tqdm(items, desc="Generating TTS")):
        original_filename = item.get("id", None)
        if not original_filename:
            print("❌ 게시물 ID 없음, 건너뜀")
            continue

        metadata = item
        voice_type = metadata.get("voice", "male")
        voice_id = pick_voice_id(voice_type)
        script_text = " ".join(metadata["script"])

        max_retries = 2
        try_count = 0
        success = False

        while try_count <= max_retries:
            temp_filename = f"{original_filename}_try{try_count}"
            audio_path, marks_path = generate_tts_with_timestamps(script_text, original_filename, voice_id)

            audio = AudioFileClip(audio_path)
            duration = audio.duration
            audio.close()

            if 30 <= duration <= 170:
                # 성공
                final_audio_path = AUDIO_DIR / f"{original_filename}.mp3"
                final_marks_path = MARKS_DIR / f"{original_filename}_marks.json"
                os.replace(audio_path, final_audio_path)
                os.replace(marks_path, final_marks_path)
                success = True
                break
            else:
                # 실패 → 삭제 및 regenerate 시도
                print(f"⛔️ {audio_path}: {duration:.2f}s (길이 부적절, 재시도 {try_count+1}/{max_retries})")
                os.remove(audio_path)
                os.remove(marks_path)
                try_count += 1

                # regenerate 시도
                new_metadata = regenerate_post_by_id(original_filename)
                if not new_metadata:
                    print(f"❌ [id={original_filename}] regenerate_post_by_id 실패, skip")
                    break

                # script, voice 갱신
                script_text = " ".join(new_metadata["script"])
                voice_type = new_metadata.get("voice", "male")
                voice_id = pick_voice_id(voice_type)

        if not success:
            print(f"🚫 [id={original_filename}] 최종 실패, skip")



if __name__ == "__main__":
    run_batch_tts()
