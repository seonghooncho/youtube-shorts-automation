import os
import json
from pathlib import Path
from shared.utils.config import MARKS_DIR, SUBTITLES_DIR

def ms_to_srt_time(ms):
    seconds, milliseconds = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

def convert_single_mark_file(json_path: Path, srt_path: Path):
    entries = []
    with open(json_path, 'r', encoding='utf-8') as f:
        first_line = f.readline().strip()
        # 배열로 시작하는 경우
        if first_line.startswith('['):
            f.seek(0)
            try:
                entries = json.load(f)
            except json.JSONDecodeError as e:
                print(f"⚠️ JSON decode error at {json_path.name} → {e}")
                return
        else:
            # LDJSON 처리
            if first_line:
                try:
                    entries.append(json.loads(first_line))
                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON decode error at {json_path.name}:1 → {e}")
            for line_num, line in enumerate(f, start=2):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON decode error at {json_path.name}:{line_num} → {e}")
                    continue

    if not entries:
        print(f"❌ No valid entries found in {json_path.name}")
        return

    with open(srt_path, 'w', encoding='utf-8') as out:
        for i in range(len(entries) - 1):
            start = ms_to_srt_time(entries[i]['time'])
            end = ms_to_srt_time(entries[i+1]['time'])
            word = entries[i]['value']

            out.write(f"{i + 1}\n")
            out.write(f"{start} --> {end}\n")
            out.write(f"{word}\n\n")

        if entries:
            last = entries[-1]
            out.write(f"{len(entries)}\n")
            out.write(f"{ms_to_srt_time(last['time'])} --> {ms_to_srt_time(last['time'] + 500)}\n")
            out.write(f"{last['value']}\n")


# def convert_all_marks_to_srt():
#     print("🔍 현재 경로:", MARKS_DIR.resolve())
    
#     # 모든 JSON 파일 중 '_marks.json'으로 끝나는 것만 추출
#     files = [f for f in MARKS_DIR.glob("*.json") if f.name.endswith("_marks.json")]

#     if not files:
#         print("❌ No marks files found in", MARKS_DIR)
#         return

#     for file in files:
#         print("✅ 발견:", file.name)
#         srt_name = file.stem.replace("_marks", "") + ".srt"
#         srt_path = SUBTITLE_DIR / srt_name
#         print(f"🎯 Converting: {file.name} -> {srt_name}")
#         convert_single_mark_file(file, srt_path)

#     print("✅ All files converted to SRT successfully!")


def convert_all_marks_to_srt():
    files = list(MARKS_DIR.glob("*_marks.json"))
    if not files:
        print("❌ No marks files found in", MARKS_DIR)
        return

    for file in files:
        srt_name = file.stem.replace("_marks", "") + ".srt"
        srt_path = SUBTITLES_DIR / srt_name
        print(f"🎯 Converting: {file.name} -> {srt_name}")
        convert_single_mark_file(file, srt_path)

    print("✅ All files converted to SRT successfully!")

if __name__ == "__main__":
    convert_all_marks_to_srt()
