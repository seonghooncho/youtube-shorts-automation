import requests
import os
import json
import shutil
import subprocess
import hashlib
from pathlib import Path
from dataclasses import dataclass
from PIL import Image
from imageio_ffmpeg import get_ffmpeg_exe
from moviepy.editor import VideoFileClip
from shared.utils.slack_notify import send_slack_message
from shared.utils.config import FINAL_METADATA_FILE, USED_PIXABAY_IDS_FILE, get_data_file, get_output_file, get_assets_file, get_video_source

if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")
STATE_PATH = get_data_file("used_pixabay_state.json")
TTS_RESULT_JSON = get_output_file("tts_check_result.json")
BG_PARTS_DIR = get_assets_file("bg_parts")

VIDEO_QUERY_CANDIDATES = [
    "phone texting",
    "couple argument",
    "people talking",
    "person thinking",
    "apartment hallway",
    "coffee shop",
    "city street",
    "office conversation",
    "angry woman",
    "stressed man",
]

ASMR_VISUAL_QUERY_CANDIDATES = [
    "hands typing keyboard close up",
    "phone screen close up",
    "writing notebook close up",
    "coffee pouring close up",
    "rain window",
    "candle flame close up",
    "book pages turning",
    "cozy desk close up",
    "water pouring glass close up",
    "fabric texture close up",
]

GENERIC_LOW_SIGNAL_TERMS = {
    "background",
    "landscape",
    "nature",
    "sky",
    "cloud",
    "clouds",
    "sunset",
    "sunrise",
    "mountain",
    "forest",
    "lake",
    "ocean",
    "sea",
    "drone",
    "aerial",
    "timelapse",
    "time lapse",
}

CONCRETE_VISUAL_TERMS = {
    "phone",
    "texting",
    "hands",
    "typing",
    "keyboard",
    "writing",
    "notebook",
    "coffee",
    "pouring",
    "window",
    "candle",
    "book",
    "pages",
    "desk",
    "apartment",
    "hallway",
    "office",
    "people",
    "couple",
    "conversation",
    "argument",
    "camera",
    "neighbor",
}


@dataclass(frozen=True)
class PixabayCandidate:
    video_id: int | str
    url: str
    duration: float
    score: float

def load_used_ids():
    if USED_PIXABAY_IDS_FILE.exists():
        with open(USED_PIXABAY_IDS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_used_ids(used_ids):
    with open(USED_PIXABAY_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(used_ids), f, ensure_ascii=False, indent=2)

def _pixabay_page_spread() -> int:
    try:
        return max(1, int(os.getenv("PIXABAY_PAGE_SPREAD", "5")))
    except ValueError:
        return 5

def _start_page_for_content(content_id: str, query: str) -> int:
    digest = hashlib.sha256(f"{content_id}:{query}".encode("utf-8")).digest()
    return 1 + (int.from_bytes(digest[:2], "big") % _pixabay_page_spread())
'''
def load_pixabay_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_page": 1}

def save_pixabay_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)'''

def fetch_pixabay_video_urls(query="phone texting", min_sec=4, max_sec=30, count=10, exclude_ids=None, page=1):
    url = "https://pixabay.com/api/videos/"
    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "per_page": count,
        "page": page,
        "safesearch": "true",
    }
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    results = []
    for hit in data.get("hits", []):
        if _is_blocked_pixabay_hit(hit) or _is_low_signal_pixabay_hit(hit, query):
            continue
        video_id = hit.get("id")
        duration = float(hit.get("duration") or 0)
        if min_sec <= duration <= max_sec and (exclude_ids is None or video_id not in exclude_ids):
            mp4_url = _select_pixabay_video_url(hit.get("videos") or {})
            if mp4_url:
                results.append(PixabayCandidate(video_id, mp4_url, duration, _score_pixabay_hit(hit, query)))
    results.sort(key=lambda candidate: candidate.score, reverse=True)
    return [(candidate.video_id, candidate.url, candidate.duration) for candidate in results]

def download_video_to_ebs(video_url: str, dest_path: Path):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        if dest_path.stat().st_size >= _int_env("PIXABAY_MIN_DOWNLOAD_BYTES", 100_000):
            return str(dest_path)
        dest_path.unlink(missing_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    try:
        with requests.get(video_url, stream=True, timeout=(5, 60)) as r:
            r.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
        if tmp_path.stat().st_size < _int_env("PIXABAY_MIN_DOWNLOAD_BYTES", 100_000):
            raise RuntimeError(f"downloaded video too small: {tmp_path.stat().st_size} bytes")
        tmp_path.replace(dest_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        dest_path.unlink(missing_ok=True)
        raise
    return str(dest_path.resolve())

def prepare_bg_videos_for_tts(video_paths: list, tts_length: float, output_video_path: Path, margin: float = 2.0):
    target_len = tts_length + margin
    segment_paths, cur_len = [], 0.0
    segment_dir = output_video_path.parent / f"{output_video_path.stem}_segments"
    segment_dir.mkdir(parents=True, exist_ok=True)

    for vid_path in video_paths:
        remain = target_len - cur_len
        if remain <= 0:
            break

        video = None
        try:
            video = VideoFileClip(str(vid_path))
            if video.duration is None or video.duration < 0.1:
                print(f"⚠️ 잘못된 클립 무시 (duration={video.duration}): {vid_path}")
                continue

            segment_duration = _segment_duration_for_source(Path(vid_path), video.duration, target_length=target_len)
            clip_duration = min(segment_duration, video.duration, remain)
            start_time = _segment_start_for_source(Path(vid_path), video.duration, clip_duration)
        except Exception as exc:
            print(f"⚠️ 클립 분석 실패로 생략: {vid_path}: {exc}")
            continue
        finally:
            if video is not None:
                try:
                    video.close()
                except Exception:
                    pass

        if clip_duration < 0.5:
            print(f"⚠️ remain 너무 짧아 생략: {clip_duration:.2f}s")
            continue

        segment_path = segment_dir / f"segment_{len(segment_paths):03}.mp4"
        try:
            _write_vertical_segment(Path(vid_path), segment_path, clip_duration, start_time=start_time)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr)
            print(f"⚠️ 세그먼트 생성 실패로 생략: {vid_path}: {stderr[-300:]}")
            continue
        segment_paths.append(segment_path)
        cur_len += clip_duration

    if not segment_paths:
        raise Exception("영상 클립 부족")

    output_video_path.parent.mkdir(parents=True, exist_ok=True)
    _concat_segments(segment_paths, output_video_path)
    print(f"✅ 영상 생성 완료: {output_video_path}")
    return str(output_video_path)


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or get_ffmpeg_exe()


def _write_vertical_segment(input_path: Path, output_path: Path, duration: float, start_time: float = 0.0) -> None:
    fps = _int_env("SHORTS_RENDER_FPS", 30)
    video_filter = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "eq=contrast=1.06:saturation=1.12:brightness=0.01,"
        f"fps={fps},setsar=1"
    )
    cmd = [
        _ffmpeg_bin(),
        "-y",
    ]
    if start_time > 0:
        cmd.extend(["-ss", f"{start_time:.3f}"])
    cmd.extend([
        "-i",
        str(input_path),
        "-t",
        f"{duration:.3f}",
        "-vf",
        video_filter,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        os.getenv("BG_SEGMENT_CRF", "20"),
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ])
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _concat_segments(segment_paths: list[Path], output_path: Path) -> None:
    concat_file = output_path.parent / f"{output_path.stem}_concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for path in segment_paths:
            f.write(f"file '{path.resolve().as_posix()}'\n")

    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

def batch_merge_videos_for_tts(target_ids: list[str] | None = None):
    used_ids = load_used_ids()
    target_set = set(target_ids or [])
    # pixabay_state = load_pixabay_state()
    # last_page = pixabay_state.get("last_page", 1)
    with open(TTS_RESULT_JSON, "r", encoding="utf-8") as f:
        tts_results = json.load(f)
    metadata_by_id = _metadata_by_id()
    for entry in tts_results:
        tts_filename = entry["filename"]
        if target_set and tts_filename not in target_set:
            continue
        tts_basename = Path(tts_filename).stem
        story_metadata = metadata_by_id.get(tts_basename, {})
        tts_length = entry["final_duration"]
        output_video_path = get_video_source(f"{tts_basename}.mp4")

        video_paths = []
        selected_ids = set()
        margin = 2.0
        target_duration = tts_length + margin

        print(f"\n🎬 [ {tts_basename} ] 영상 병합 시작: 목표 {target_duration:.1f}초")

        queries = _queries_for_entry({**story_metadata, **entry})
        total_duration = _extend_video_selection(
            tts_basename=tts_basename,
            queries=queries,
            target_duration=target_duration,
            current_duration=0.0,
            video_paths=video_paths,
            selected_ids=selected_ids,
            excluded_ids=used_ids | selected_ids,
        )

        if total_duration < target_duration and used_ids and _env_bool("PIXABAY_ALLOW_USED_ID_FALLBACK", default=True):
            print(f"♻️ [{tts_basename}] 신규 Pixabay 후보 부족, used-id 재사용 fallback 시도")
            total_duration = _extend_video_selection(
                tts_basename=tts_basename,
                queries=queries,
                target_duration=target_duration,
                current_duration=total_duration,
                video_paths=video_paths,
                selected_ids=selected_ids,
                excluded_ids=selected_ids,
                page_salt="reuse",
            )

        if total_duration < target_duration:
            send_slack_message(f"❌ [{tts_basename}] Pixabay 영상 부족! 남은 길이={target_duration - total_duration:.1f}s")
            print(f"❌ Slack 알림 전송됨: 영상 부족")
            continue

        # pixabay_state["last_page"] = page
        # save_pixabay_state(pixabay_state)

        try:
            prepare_bg_videos_for_tts(
                video_paths=video_paths,
                tts_length=tts_length,
                output_video_path=output_video_path,
                margin=margin,
            )
        except Exception as exc:
            send_slack_message(f"❌ [{tts_basename}] 배경 영상 준비 실패: {exc}")
            print(f"❌ [{tts_basename}] 배경 영상 준비 실패: {exc}")
            continue
        used_ids.update(selected_ids)
        save_used_ids(used_ids)
        print(f"✅ used_pixabay_ids.json 갱신됨 (총 {len(used_ids)}개)")


def _extend_video_selection(
    *,
    tts_basename: str,
    queries: list[str],
    target_duration: float,
    current_duration: float,
    video_paths: list[str],
    selected_ids: set,
    excluded_ids: set,
    page_salt: str = "",
) -> float:
    total_duration = current_duration
    for query in queries:
        print(f"🔍 [{tts_basename}] 쿼리 '{query}'로 영상 시도 중...")
        page = _start_page_for_content(tts_basename, f"{page_salt}:{query}" if page_salt else query)
        pages_checked = 0
        while total_duration < target_duration and pages_checked < _max_pages_per_query():
            candidates = _fetch_pixabay_video_urls_safe(
                query=query,
                min_sec=4,
                max_sec=30,
                count=50,
                exclude_ids=excluded_ids | selected_ids,
                page=page,
            )
            if not candidates:
                break
            for video_id, video_url, vid_duration in candidates:
                if video_id in selected_ids or video_id in excluded_ids:
                    continue
                part_path = BG_PARTS_DIR / f"{tts_basename}_bg_{video_id}.mp4"
                if not _download_video_safe(video_url, part_path, tts_basename, query):
                    continue
                video_paths.append(str(part_path))
                selected_ids.add(video_id)
                total_duration += _segment_duration_for_source(
                    part_path,
                    vid_duration,
                    target_length=target_duration,
                )
                if total_duration >= target_duration:
                    break
            page += 1
            pages_checked += 1
        if total_duration >= target_duration:
            break
    return total_duration

def _select_pixabay_video_url(videos: dict) -> str | None:
    candidates = []
    for candidate in videos.values():
        if not isinstance(candidate, dict) or not candidate.get("url"):
            continue
        width = int(candidate.get("width") or 0)
        height = int(candidate.get("height") or 0)
        candidates.append((max(width, height), candidate["url"]))
    candidates.sort(reverse=True)
    min_long_edge = _int_env("PIXABAY_MIN_SOURCE_LONG_EDGE", 720)
    for long_edge, url in candidates:
        if long_edge >= min_long_edge:
            return url
    if candidates and _env_bool("PIXABAY_ALLOW_LOW_RES_FALLBACK", default=False):
        return candidates[0][1]
    return None


def _is_blocked_pixabay_hit(hit: dict) -> bool:
    tags = str(hit.get("tags") or "").lower()
    blocked_terms = {
        "green screen",
        "greenscreen",
        "chroma",
        "chroma key",
        "abstract",
        "animation",
        "animated",
        "anime",
        "cartoon",
        "game",
        "gaming",
        "logo",
        "vfx",
        "visual effect",
        "template",
        "intro",
        "outro",
        "slideshow",
    }
    return any(term in tags for term in blocked_terms)


def _is_low_signal_pixabay_hit(hit: dict, query: str) -> bool:
    if _tag_overlap_score(hit, query) > 0:
        return False
    tags = _tag_tokens(hit)
    if not tags:
        return True
    if tags & CONCRETE_VISUAL_TERMS:
        return False
    return bool(tags & GENERIC_LOW_SIGNAL_TERMS)


def _score_pixabay_hit(hit: dict, query: str) -> float:
    tags = _tag_tokens(hit)
    score = _tag_overlap_score(hit, query) * 10
    score += min(5, len(tags & CONCRETE_VISUAL_TERMS))
    score -= min(6, len(tags & GENERIC_LOW_SIGNAL_TERMS))
    duration = float(hit.get("duration") or 0)
    if 6 <= duration <= 18:
        score += 2
    elif duration > 24:
        score -= 1
    score += min(3, _int_like(hit.get("likes")) / 100)
    return score


def _tag_overlap_score(hit: dict, query: str) -> int:
    query_tokens = set(_query_tokens(query))
    if not query_tokens:
        return 0
    return len(query_tokens & _tag_tokens(hit))


def _tag_tokens(hit: dict) -> set[str]:
    return set(_query_tokens(str(hit.get("tags") or "")))


def _query_tokens(query: str) -> list[str]:
    return [
        token
        for token in "".join(ch.lower() if ch.isalnum() else " " for ch in str(query or "")).split()
        if len(token) >= 3
    ]


def _int_like(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _queries_for_entry(entry: dict) -> list[str]:
    queries = []
    for keyword in entry.get("visual_keywords") or []:
        normalized = _clean_query(keyword)
        if normalized and normalized not in queries:
            queries.append(normalized)
    primary_fallback_count = max(0, _int_env("PIXABAY_PRIMARY_FALLBACK_QUERIES", 4))
    for query in VIDEO_QUERY_CANDIDATES[:primary_fallback_count]:
        normalized = _clean_query(query)
        if normalized and normalized not in queries:
            queries.append(normalized)
    if _env_bool("PIXABAY_ENABLE_ASMR_FALLBACK", default=True):
        for query in _asmr_queries():
            normalized = _clean_query(query)
            if normalized and normalized not in queries:
                queries.append(normalized)
    for query in VIDEO_QUERY_CANDIDATES[primary_fallback_count:]:
        normalized = _clean_query(query)
        if normalized and normalized not in queries:
            queries.append(normalized)
    return queries[:max(1, _int_env("PIXABAY_MAX_QUERIES_PER_ITEM", 12))]


def _metadata_by_id() -> dict[str, dict]:
    if not FINAL_METADATA_FILE.exists():
        return {}
    try:
        with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception as exc:
        print(f"⚠️ final metadata load failed for visual keywords: {exc}")
        return {}
    return {
        str(item.get("id")): item
        for item in items
        if item.get("id")
    }


def _clean_query(query: str) -> str:
    normalized = " ".join(str(query or "").lower().split())
    if normalized in {"nature", "background", "landscape"}:
        return ""
    return normalized[:60]


def _segment_duration_for_source(path: Path, source_duration: float, target_length: float | None = None) -> float:
    min_seconds = _min_segment_seconds(target_length)
    max_seconds = min(_max_segment_seconds(target_length), max(min_seconds, source_duration))
    if max_seconds <= min_seconds:
        return max_seconds
    return _deterministic_float(f"{path.stem}:duration", min_seconds, max_seconds)


def _segment_start_for_source(path: Path, source_duration: float, segment_duration: float) -> float:
    max_start = max(0.0, source_duration - segment_duration - 0.25)
    if max_start <= 0:
        return 0.0
    return _deterministic_float(f"{path.stem}:start", 0.0, max_start)


def _deterministic_float(seed: str, minimum: float, maximum: float) -> float:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    ratio = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return minimum + (maximum - minimum) * ratio


def _min_segment_seconds(target_length: float | None = None) -> float:
    default = 3.4
    if target_length and target_length > 75:
        default = 4.0
    return _float_env("SHORTS_BG_MIN_CLIP_SECONDS", default)


def _max_segment_seconds(target_length: float | None = None) -> float:
    default = 5.6
    if target_length and target_length > 75:
        default = 6.6
    return max(_min_segment_seconds(target_length), _float_env("SHORTS_BG_MAX_CLIP_SECONDS", default))


def _fetch_pixabay_video_urls_safe(**kwargs) -> list[tuple[int | str, str, float]]:
    try:
        return fetch_pixabay_video_urls(**kwargs)
    except Exception as exc:
        query = kwargs.get("query", "")
        print(f"⚠️ Pixabay 조회 실패: query='{query}' page={kwargs.get('page')}: {exc}")
        return []


def _download_video_safe(video_url: str, part_path: Path, content_id: str, query: str) -> bool:
    try:
        download_video_to_ebs(video_url, part_path)
        return True
    except Exception as exc:
        print(f"⚠️ Pixabay 다운로드 실패로 후보 생략: [{content_id}] query='{query}' {video_url}: {exc}")
        return False


def _asmr_queries() -> list[str]:
    raw = os.getenv("PIXABAY_ASMR_FALLBACK_QUERIES", "").strip()
    if not raw:
        return ASMR_VISUAL_QUERY_CANDIDATES
    return [query.strip() for query in raw.split("|") if query.strip()]


def _max_pages_per_query() -> int:
    return max(1, _int_env("PIXABAY_MAX_PAGES_PER_QUERY", 3))


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


if __name__ == "__main__":
    batch_merge_videos_for_tts()
