import requests
import os
import json
from pathlib import Path
from moviepy.editor import VideoFileClip, concatenate_videoclips
from shared.utils.slack_notify import send_slack_message
from shared.utils.config import  USED_PIXABAY_IDS_FILE, get_data_file, get_output_file, get_assets_file, get_video_source

PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")
STATE_PATH = get_data_file("used_pixabay_state.json")
TTS_RESULT_JSON = get_output_file("tts_check_result.json")
BG_PARTS_DIR = get_assets_file("bg_parts")

VIDEO_QUERY_CANDIDATES = [
    "nature", "landscape", "travel", "forest", "sky",
    "ocean", "background", "village", "tree", "clouds"
]

def load_used_ids():
    if USED_PIXABAY_IDS_FILE.exists():
        with open(USED_PIXABAY_IDS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_used_ids(used_ids):
    with open(USED_PIXABAY_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(used_ids), f, ensure_ascii=False, indent=2)
'''
def load_pixabay_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_page": 1}

def save_pixabay_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)'''

def fetch_pixabay_video_urls(query="nature", min_sec=10, max_sec=30, count=10, exclude_ids=None, page=1):
    url = "https://pixabay.com/api/videos/"
    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "per_page": count,
        "page": page,
        "safesearch": "true",
    }
    response = requests.get(url, params=params)
    data = response.json()
    results = []
    for hit in data.get("hits", []):
        video_id = hit.get("id")
        duration = hit.get("duration", 0)
        if min_sec <= duration <= max_sec and (exclude_ids is None or video_id not in exclude_ids):
            videos = hit["videos"]
            mp4_url = videos.get("large", {}).get("url") or videos.get("medium", {}).get("url")
            if mp4_url:
                results.append((video_id, mp4_url, duration))
    return results

def download_video_to_ebs(video_url: str, dest_path: Path):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        return str(dest_path)
    with requests.get(video_url, stream=True) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    return str(dest_path.resolve())

def prepare_bg_videos_for_tts(video_paths: list, tts_length: float, output_video_path: Path, margin: float = 2.0):
    target_len = tts_length + margin
    clips, cur_len = [], 0.0
    for vid_path in video_paths:
        video = VideoFileClip(str(vid_path))
        if video.duration is None or video.duration < 0.1:
            print(f"⚠️ 잘못된 클립 무시 (duration={video.duration:.2f}): {vid_path}")
            continue
        video = video.resize(height=1920)
        video = video.crop(x_center=video.w/2, y_center=video.h/2, width=1080, height=1920)
        remain = target_len - cur_len
        if remain <= 0:
            break
        if video.duration <= remain:
            clips.append(video)
            cur_len += video.duration
        else:
            if remain < 0.1:
                print(f"⚠️ remain 너무 짧아 생략: {remain:.2f}s")
                continue
            clips.append(video.subclip(0, remain))
            cur_len += remain
            break
    if not clips:
        raise Exception("영상 클립 부족")
    output_video_path.parent.mkdir(parents=True, exist_ok=True)
    final_video = concatenate_videoclips(clips)
    final_video.write_videofile(str(output_video_path), codec="libx264", audio=False, fps=24)
    print(f"✅ 영상 생성 완료: {output_video_path}")
    return str(output_video_path)

def batch_merge_videos_for_tts():
    used_ids = load_used_ids()
    # pixabay_state = load_pixabay_state()
    # last_page = pixabay_state.get("last_page", 1)
    with open(TTS_RESULT_JSON, "r", encoding="utf-8") as f:
        tts_results = json.load(f)
    for entry in tts_results:
        tts_filename = entry["filename"]
        tts_basename = Path(tts_filename).stem
        tts_length = entry["final_duration"]
        output_video_path = get_video_source(f"{tts_basename}.mp4")

        video_paths = []
        selected_ids = set()
        total_duration = 0.0
        margin = 2.0

        print(f"\n🎬 [ {tts_basename} ] 영상 병합 시작: 목표 {tts_length + margin:.1f}초")

        for query in VIDEO_QUERY_CANDIDATES:
            print(f"🔍 [{tts_basename}] 쿼리 '{query}'로 영상 시도 중...")
            page = 1
            while total_duration < tts_length + margin:
                candidates = fetch_pixabay_video_urls(
                    query=query, min_sec=10, max_sec=30, count=50,
                    exclude_ids=used_ids | selected_ids, page=page
                )
                if not candidates:
                    break
                for video_id, video_url, vid_duration in candidates:
                    if video_id in used_ids or video_id in selected_ids:
                        continue
                    part_path = BG_PARTS_DIR / f"{tts_basename}_bg_{video_id}.mp4"
                    download_video_to_ebs(video_url, part_path)
                    video_paths.append(str(part_path))
                    selected_ids.add(video_id)
                    total_duration += vid_duration
                    if total_duration >= tts_length + margin:
                        break
                page += 1
            if total_duration >= tts_length + margin:
                break

        if total_duration < tts_length + margin:
            send_slack_message(f"❌ [{tts_basename}] Pixabay 영상 부족! 마지막 query='{query}', 남은 길이={tts_length + margin - total_duration:.1f}s")
            print(f"❌ Slack 알림 전송됨: 영상 부족")
            continue

        # pixabay_state["last_page"] = page
        # save_pixabay_state(pixabay_state)

        prepare_bg_videos_for_tts(
            video_paths=video_paths,
            tts_length=tts_length,
            output_video_path=output_video_path,
            margin=margin,
        )
        used_ids.update(selected_ids)
        save_used_ids(used_ids)
        print(f"✅ used_pixabay_ids.json 갱신됨 (총 {len(used_ids)}개)")

if __name__ == "__main__":
    batch_merge_videos_for_tts()
