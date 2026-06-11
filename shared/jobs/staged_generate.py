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
    get_output_file,
    get_video_source,
)
from shared.utils.s3_utils import update_metadata_after_video_creation


store = S3Store()
content_repo = ContentRepository()


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
    }
    if stage not in stages:
        raise ValueError(f"Unknown generate stage: {stage}")
    stages[stage]()


def collect_stage() -> None:
    _download_file("state/scraped_post_list.json", SCRAPED_POST_LIST_FILE)
    scrape_reddit_and_store()
    store.upload_file(RAW_POSTS_FILE, "raw/raw_posts.json")
    if SCRAPED_POST_LIST_FILE.exists():
        store.upload_file(SCRAPED_POST_LIST_FILE, "raw/scraped_post_list.json")
        store.upload_file(SCRAPED_POST_LIST_FILE, "state/scraped_post_list.json")


def filter_stage() -> None:
    _download_required("raw/raw_posts.json", RAW_POSTS_FILE)
    filter_viable_posts()
    store.upload_file(VIABLE_POSTS_FILE, "scripts/viable_posts.json")


def script_stage() -> None:
    _download_required("scripts/viable_posts.json", VIABLE_POSTS_FILE)
    generate_scripts_from_filtered()
    _prepare_publish_schedule()
    store.upload_file(FINAL_METADATA_FILE, "scripts/final_metadata.json")
    if FAILED_POSTS_FILE.exists():
        store.upload_file(FAILED_POSTS_FILE, "scripts/failed_posts.json")
    with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
        content_repo.upsert_items(json.load(f), "SCRIPTED")


def tts_stage() -> None:
    _download_required("scripts/final_metadata.json", FINAL_METADATA_FILE)
    run_batch_tts()
    store.upload_directory(AUDIO_DIR, "audio/mp3")
    store.upload_directory(MARKS_DIR, "audio/marks")


def subtitles_stage() -> None:
    store.download_prefix("audio/marks", MARKS_DIR)
    convert_all_marks_to_srt()
    store.download_prefix("audio/mp3", AUDIO_DIR)
    analyze_all_tts()
    store.upload_directory(SUBTITLES_DIR, "audio/subtitles")
    store.upload_file(get_output_file("tts_check_result.json"), "audio/tts_check_result.json")


def render_stage() -> None:
    _download_required("scripts/final_metadata.json", FINAL_METADATA_FILE)
    _download_file("state/used_pixabay_ids.json", USED_PIXABAY_IDS_FILE)
    store.download_prefix("audio/mp3", AUDIO_DIR)
    store.download_prefix("audio/subtitles", SUBTITLES_DIR)
    _download_required("audio/tts_check_result.json", get_output_file("tts_check_result.json"))
    batch_merge_videos_for_tts()
    batch_render_all_videos()
    _attach_video_keys()
    update_metadata_after_video_creation()
    store.upload_directory(FINAL_DIR, "videos/final")
    store.upload_directory(OUTPUT_DIR / "video-sources", "videos/sources")
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
    batch_days = int(os.getenv("GENERATION_BATCH_DAYS", "14"))
    publish_hour = int(os.getenv("PUBLISH_HOUR_LOCAL", "8"))
    publish_minute = int(os.getenv("PUBLISH_MINUTE_LOCAL", "0"))
    timezone = ZoneInfo(os.getenv("SCHEDULE_TIMEZONE", "Asia/Seoul"))
    start_date = datetime.now(timezone).date() + timedelta(days=1)
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


def _attach_video_keys() -> None:
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
            item["upload_status"] = "PUBLISH_READY"
            item["status"] = "PUBLISH_READY"
    _write_metadata(items)


def _read_metadata() -> list[dict]:
    with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_metadata(items: list[dict]) -> None:
    with open(FINAL_METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
