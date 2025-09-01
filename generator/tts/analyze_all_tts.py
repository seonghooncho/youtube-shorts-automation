import json
from pathlib import Path
from moviepy.editor import AudioFileClip
from tqdm import tqdm
from shared.utils.config import AUDIO_DIR, get_output_file

def analyze_all_tts():
    """
    output/audio 내 모든 mp3 파일에 대해:
    - 파일명, 배속 수준, 배속 적용 후 길이만 기록
    - set이 아니라, 실제론 list of dict 구조가 직관적(파일 저장 및 추후 활용에 더 유리)
    """
    result_set = set()
    result_list = []

    for mp3_path in tqdm(sorted(AUDIO_DIR.glob("*.mp3")), desc="Analyzing TTS audio"):
        filename = mp3_path.stem
        audio = AudioFileClip(str(mp3_path))
        ori_duration = audio.duration

        # 배속 결정
        speed = 1.0
        if ori_duration > 65:
            speed = 1.2
        elif ori_duration > 59:
            speed = 1.1
        final_duration = ori_duration / speed

        # 기록(여기서는 set도 가능, 하지만 json으로 저장하려면 list of dict가 더 나음)
        entry = (filename, speed, final_duration)
        result_set.add(entry)
        result_list.append({
            "filename": filename,
            "speed": speed,
            "final_duration": final_duration
        })
        print(f"✅ {filename} | speed: {speed} | length: {final_duration:.2f}s")

    # 결과 저장(json은 list가 적합)
    result_json_path = get_output_file("tts_check_result.json")
    with open(result_json_path, "w", encoding="utf-8") as f:
        json.dump(result_list, f, ensure_ascii=False, indent=2)
    print(f"📦 전체 결과 저장 완료 → {result_json_path}")

if __name__ == "__main__":
    analyze_all_tts()
