import json
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from generator.text.filter_viable_posts import filter_viable_posts
from generator.text.generate_scripts_from_filtered import generate_scripts_from_filtered
from generator.text.scrape_reddit_and_store import scrape_reddit_and_store
from generator.tts.analyze_all_tts import analyze_all_tts
from generator.tts.generate_tts import run_batch_tts
from generator.video.convert_all_srt import convert_all_marks_to_srt
from generator.video.create_video import batch_render_all_videos
from generator.video.pixabay_video_merge import batch_merge_videos_for_tts
from shared.state import ContentRepository
from shared.storage import S3Store
from shared.utils.config import (
    AUDIO_DIR,
    DATA_DIR,
    FAILED_POSTS_FILE,
    FINAL_DIR,
    FINAL_METADATA_FILE,
    MARKS_DIR,
    OUTPUT_DIR,
    RAW_POSTS_FILE,
    SCRAPED_POST_LIST_FILE,
    SUBTITLES_DIR,
    USED_PIXABAY_IDS_FILE,
    VIABLE_POSTS_FILE,
    ensure_generator_directories,
    get_temp_file,
    get_output_file,
    get_video_source,
)
from shared.utils.s3_utils import update_metadata_after_video_creation


store = S3Store()
content_repo = ContentRepository()

PUBLISH_METADATA_KEY = "publish-ready/final_metadata.json"
LEGACY_METADATA_KEY = "shorts/state/final_metadata.json"


def run_generate_stage(stage: str) -> None:
    ensure_generator_directories()
    stage = stage.lower().strip()
    stages = {
        "collect": collect_stage,
        "filter": filter_stage,
        "script": script_stage,
        "tts": tts_stage,
        "subtitles": subtitles_stage,
        "render": render_stage,
        "finalize": finalize_stage,
    }
    if stage not in stages:
        raise ValueError(f"Unknown generate stage: {stage}")
    stages[stage]()


def collect_stage() -> None:
    if _desired_new_count() <= 0:
        print("✅ publish-ready 재고가 충분해 Reddit 수집을 건너뜁니다.")
        _write_json_file(RAW_POSTS_FILE, [])
        store.upload_file(RAW_POSTS_FILE, "raw/raw_posts.json")
        return

    _download_file("state/scraped_post_list.json", SCRAPED_POST_LIST_FILE)
    scrape_reddit_and_store()
    store.upload_file(RAW_POSTS_FILE, "raw/raw_posts.json")
    if SCRAPED_POST_LIST_FILE.exists():
        store.upload_file(SCRAPED_POST_LIST_FILE, "raw/scraped_post_list.json")
        store.upload_file(SCRAPED_POST_LIST_FILE, "state/scraped_post_list.json")


def filter_stage() -> None:
    needed = _desired_new_count()
    if needed <= 0:
        print("✅ publish-ready 재고가 충분해 이번 생성 배치를 건너뜁니다.")
        _write_json_file(VIABLE_POSTS_FILE, [])
        store.upload_file(VIABLE_POSTS_FILE, "scripts/viable_posts.json")
        return

    _download_required("raw/raw_posts.json", RAW_POSTS_FILE)
    filter_viable_posts()
    viable_posts = _read_json_file(VIABLE_POSTS_FILE)
    if not viable_posts:
        raise RuntimeError("No viable Reddit posts found after filtering.")
    store.upload_file(VIABLE_POSTS_FILE, "scripts/viable_posts.json")


def script_stage() -> None:
    _download_required("scripts/viable_posts.json", VIABLE_POSTS_FILE)
    _limit_viable_posts_for_batch()
    if not _read_json_file(VIABLE_POSTS_FILE):
        _write_metadata([])
        store.upload_file(FINAL_METADATA_FILE, "scripts/final_metadata.json")
        print("✅ 생성할 신규 항목이 없어 script stage를 no-op 처리합니다.")
        return

    generate_scripts_from_filtered()
    _prepare_publish_schedule()
    metadata = _read_json_file(FINAL_METADATA_FILE)
    if not metadata:
        raise RuntimeError("No final metadata generated from viable posts.")
    store.upload_file(FINAL_METADATA_FILE, "scripts/final_metadata.json")
    if FAILED_POSTS_FILE.exists():
        store.upload_file(FAILED_POSTS_FILE, "scripts/failed_posts.json")
    content_repo.upsert_items(metadata, "SCRIPTED")


def tts_stage() -> None:
    _download_required("scripts/final_metadata.json", FINAL_METADATA_FILE)
    if not _read_metadata():
        print("✅ 생성할 신규 항목이 없어 TTS stage를 no-op 처리합니다.")
        return

    run_batch_tts()
    uploaded_audio = store.upload_directory(AUDIO_DIR, "audio/mp3")
    uploaded_marks = store.upload_directory(MARKS_DIR, "audio/marks")
    if not uploaded_audio or not uploaded_marks:
        raise RuntimeError("TTS did not produce both audio and speech mark artifacts.")


def subtitles_stage() -> None:
    _download_required("scripts/final_metadata.json", FINAL_METADATA_FILE)
    if not _read_metadata():
        print("✅ 생성할 신규 항목이 없어 subtitles stage를 no-op 처리합니다.")
        return

    downloaded_marks = store.download_prefix("audio/marks", MARKS_DIR)
    if not downloaded_marks:
        raise RuntimeError("No speech mark artifacts found in S3 for subtitle generation.")
    convert_all_marks_to_srt()
    if not list(SUBTITLES_DIR.glob("*.srt")):
        raise RuntimeError("Subtitle generation produced no SRT files.")
    store.download_prefix("audio/mp3", AUDIO_DIR)
    analyze_all_tts()
    store.upload_directory(SUBTITLES_DIR, "audio/subtitles")
    store.upload_file(get_output_file("tts_check_result.json"), "audio/tts_check_result.json")


def render_stage() -> None:
    _download_required("scripts/final_metadata.json", FINAL_METADATA_FILE)
    if not _read_metadata():
        print("✅ 생성할 신규 항목이 없어 render stage를 no-op 처리합니다.")
        return

    _download_file("state/used_pixabay_ids.json", USED_PIXABAY_IDS_FILE)
    store.download_prefix("audio/mp3", AUDIO_DIR)
    store.download_prefix("audio/subtitles", SUBTITLES_DIR)
    _download_required("audio/tts_check_result.json", get_output_file("tts_check_result.json"))
    target_ids = _render_target_ids()
    if target_ids is not None and not target_ids:
        print("✅ render shard에 할당된 항목이 없어 no-op 처리합니다.")
        return

    batch_merge_videos_for_tts(target_ids=target_ids)
    batch_render_all_videos(target_ids=target_ids)
    rendered_files = _rendered_final_files(target_ids)
    if not rendered_files:
        raise RuntimeError("Video rendering produced no final MP4 files.")
    _attach_video_keys_from_local()
    _keep_rendered_metadata_only()
    store.upload_directory(FINAL_DIR, "videos/final")
    store.upload_directory(OUTPUT_DIR / "video-sources", "videos/sources")
    if os.getenv("RENDER_SHARD_MODE", "").lower().strip() == "array":
        if USED_PIXABAY_IDS_FILE.exists():
            store.upload_file(USED_PIXABAY_IDS_FILE, "state/used_pixabay_ids.json")
        return

    _finalize_publish_ready()


def finalize_stage() -> None:
    _download_required("scripts/final_metadata.json", FINAL_METADATA_FILE)
    if not _read_metadata():
        print("✅ 생성할 신규 항목이 없어 finalize stage를 no-op 처리합니다.")
        return
    _attach_video_keys_from_s3()
    _keep_rendered_metadata_only()
    if not _read_metadata():
        raise RuntimeError("No rendered final videos found during finalize stage.")
    _finalize_publish_ready()


def _finalize_publish_ready() -> None:
    update_metadata_after_video_creation()
    store.upload_file(FINAL_METADATA_FILE, "publish-ready/final_metadata.json")
    store.upload_file(FINAL_METADATA_FILE, "state/final_metadata.json")
    if USED_PIXABAY_IDS_FILE.exists():
        store.upload_file(USED_PIXABAY_IDS_FILE, "state/used_pixabay_ids.json")
    with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
        content_repo.upsert_items(json.load(f), "PUBLISH_READY")


def _download_required(key, path) -> None:
    if not store.download_file(key, path):
        raise FileNotFoundError(f"Required S3 object not found: {key}")


def _download_file(key, path) -> bool:
    return store.download_file(key, path)


def _prepare_publish_schedule() -> None:
    if not FINAL_METADATA_FILE.exists():
        return
    items = _read_metadata()
    batch_days = _desired_new_count()
    publish_hour = int(os.getenv("PUBLISH_HOUR_LOCAL", "8"))
    publish_minute = int(os.getenv("PUBLISH_MINUTE_LOCAL", "0"))
    timezone = ZoneInfo(os.getenv("SCHEDULE_TIMEZONE", "Asia/Seoul"))
    start_date = _next_publish_start_date(timezone)
    items = items[:batch_days]
    for index, item in enumerate(items):
        scheduled_dt = datetime.combine(
            start_date + timedelta(days=index),
            time(hour=publish_hour, minute=publish_minute),
            tzinfo=timezone,
        )
        item.setdefault("uploaded", False)
        item["status"] = "SCRIPTED"
        item["scheduled_publish_at"] = int(scheduled_dt.timestamp())
        item["scheduled_publish_date"] = scheduled_dt.date().isoformat()
    _write_metadata(items)


def _limit_viable_posts_for_batch() -> None:
    posts = _read_json_file(VIABLE_POSTS_FILE)
    batch_days = _desired_new_count()
    if batch_days <= 0:
        _write_json_file(VIABLE_POSTS_FILE, [])
        print("✂️ viable_posts 제한: 재고 충분 → 0")
        return
    if batch_days > 0 and len(posts) > batch_days:
        _write_json_file(VIABLE_POSTS_FILE, posts[:batch_days])
        print(f"✂️ viable_posts 제한: {len(posts)} -> {batch_days}")


def _attach_video_keys_from_local() -> None:
    if not FINAL_METADATA_FILE.exists():
        return
    items = _read_metadata()
    for item in items:
        content_id = item.get("id")
        if not content_id:
            continue
        video_path = FINAL_DIR / f"{content_id}.mp4"
        if video_path.exists():
            item["video_key"] = f"videos/final/{content_id}.mp4"
            if item.get("uploaded") or item.get("upload_status") == "UPLOADED":
                item["upload_status"] = "UPLOADED"
                item["status"] = "UPLOADED"
            else:
                item["upload_status"] = "PUBLISH_READY"
                item["status"] = "PUBLISH_READY"
    _write_metadata(items)


def _attach_video_keys_from_s3() -> None:
    if not FINAL_METADATA_FILE.exists():
        return
    items = _read_metadata()
    for item in items:
        content_id = item.get("id")
        if not content_id:
            continue
        video_key = f"videos/final/{content_id}.mp4"
        if store.object_exists(video_key):
            item["video_key"] = video_key
            item["upload_status"] = "PUBLISH_READY"
            item["status"] = "PUBLISH_READY"
    _write_metadata(items)


def _keep_rendered_metadata_only() -> None:
    items = [
        item
        for item in _read_metadata()
        if item.get("video_key") and item.get("upload_status") == "PUBLISH_READY"
    ]
    _write_metadata(items)


def _render_target_ids() -> list[str] | None:
    if os.getenv("RENDER_SHARD_MODE", "").lower().strip() != "array":
        env_ids = _target_ids_from_env()
        return env_ids if env_ids else None

    raw_index = os.getenv("AWS_BATCH_JOB_ARRAY_INDEX")
    if raw_index is None:
        return None
    try:
        shard_index = int(raw_index)
    except ValueError:
        raise ValueError(f"Invalid AWS_BATCH_JOB_ARRAY_INDEX: {raw_index}")

    items = _read_metadata()
    if shard_index >= len(items):
        return []
    content_id = str(items[shard_index].get("id") or "")
    return [content_id] if content_id else []


def _target_ids_from_env() -> list[str]:
    raw = os.getenv("TARGET_CONTENT_IDS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _rendered_final_files(target_ids: list[str] | None) -> list:
    if target_ids is None:
        return list(FINAL_DIR.glob("*.mp4"))
    return [FINAL_DIR / f"{content_id}.mp4" for content_id in target_ids if (FINAL_DIR / f"{content_id}.mp4").exists()]


def _desired_new_count() -> int:
    target_days = int(os.getenv("GENERATION_BATCH_DAYS", "14"))
    buffer_days = int(os.getenv("GENERATION_BUFFER_DAYS", "3"))
    max_new_items = int(os.getenv("GENERATION_MAX_NEW_ITEMS", str(target_days + buffer_days)))
    pending_count = len(_pending_publish_items())
    desired = target_days + buffer_days - pending_count
    desired = max(0, desired)
    return min(desired, max_new_items)


def _pending_publish_items() -> list[dict]:
    return [
        item
        for item in _load_existing_publish_metadata()
        if not item.get("uploaded")
        and item.get("upload_status") in (None, "", "PUBLISH_READY")
        and (item.get("video_key") or item.get("status") == "PUBLISH_READY")
    ]


def _next_publish_start_date(timezone: ZoneInfo):
    tomorrow = datetime.now(timezone).date() + timedelta(days=1)
    pending_dates = []
    for item in _pending_publish_items():
        raw_date = item.get("scheduled_publish_date")
        if raw_date:
            try:
                pending_dates.append(datetime.fromisoformat(raw_date).date())
                continue
            except ValueError:
                pass
        scheduled_at = int(item.get("scheduled_publish_at") or 0)
        if scheduled_at:
            pending_dates.append(datetime.fromtimestamp(scheduled_at, timezone).date())
    if not pending_dates:
        return tomorrow
    return max(tomorrow, max(pending_dates) + timedelta(days=1))


def _load_existing_publish_metadata() -> list[dict]:
    tmp_path = get_temp_file("existing_publish_metadata.json")
    for key in (PUBLISH_METADATA_KEY, LEGACY_METADATA_KEY):
        if not store.download_file(key, tmp_path):
            continue
        try:
            return _read_json_file(tmp_path)
        except Exception as e:
            print(f"⚠️ 기존 publish metadata 로드 실패: {key}: {e}")
            return []
    return []


def _read_metadata() -> list[dict]:
    return _read_json_file(FINAL_METADATA_FILE)


def _write_metadata(items: list[dict]) -> None:
    _write_json_file(FINAL_METADATA_FILE, items)


def _read_json_file(path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json_file(path, items: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
