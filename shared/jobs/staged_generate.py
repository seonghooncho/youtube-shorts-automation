import json
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from generator.text.filter_viable_posts import filter_viable_posts
from generator.text.content_gate import filter_content_gate_items
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
from shared.utils.slack_notify import send_slack_message
from shared.utils.video_validation import quality_warnings, validate_video_file


store = S3Store()
content_repo = ContentRepository()

PUBLISH_METADATA_KEY = "publish-ready/final_metadata.json"
LEGACY_METADATA_KEY = "shorts/state/final_metadata.json"
RENDER_USED_PIXABAY_PREFIX = "state/render-used-pixabay"


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
    content_repo.upsert_sources(_read_json_file(RAW_POSTS_FILE))
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
        print("✅ 품질 기준을 통과한 source가 없어 이번 생성 배치를 no-op 처리합니다.")
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
    _log_generation_acceptance()
    _prepare_publish_schedule()
    metadata = _read_json_file(FINAL_METADATA_FILE)
    if not metadata:
        print("✅ 품질 기준을 통과한 script가 없어 이후 단계를 no-op 처리합니다.")
    store.upload_file(FINAL_METADATA_FILE, "scripts/final_metadata.json")
    if FAILED_POSTS_FILE.exists():
        store.upload_file(FAILED_POSTS_FILE, "scripts/failed_posts.json")
    content_repo.upsert_items(metadata, "SCRIPTED")


def tts_stage() -> None:
    _download_required("scripts/final_metadata.json", FINAL_METADATA_FILE)
    if not _read_metadata():
        print("✅ 생성할 신규 항목이 없어 TTS stage를 no-op 처리합니다.")
        return
    _filter_metadata_by_content_gate("tts")
    if not _read_metadata():
        print("✅ content gate 통과 항목이 없어 TTS stage를 no-op 처리합니다.")
        return

    run_batch_tts()
    store.upload_file(FINAL_METADATA_FILE, "scripts/final_metadata.json")
    if FAILED_POSTS_FILE.exists():
        store.upload_file(FAILED_POSTS_FILE, "scripts/failed_posts.json")
    if not _read_metadata():
        print("✅ TTS 성공 항목이 없어 이후 단계를 no-op 처리합니다.")
        return
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
    store.download_prefix("audio/mp3", AUDIO_DIR)
    if not downloaded_marks:
        raise RuntimeError("No speech mark artifacts found in S3 for subtitle generation.")
    _filter_metadata_by_artifacts("subtitles", require_audio=True, require_marks=True)
    if not _read_metadata():
        print("✅ audio/marks artifact gate 통과 항목이 없어 subtitles stage를 no-op 처리합니다.")
        return
    convert_all_marks_to_srt()
    if not list(SUBTITLES_DIR.glob("*.srt")):
        raise RuntimeError("Subtitle generation produced no SRT files.")
    analyze_all_tts()
    store.upload_file(FINAL_METADATA_FILE, "scripts/final_metadata.json")
    if FAILED_POSTS_FILE.exists():
        store.upload_file(FAILED_POSTS_FILE, "scripts/failed_posts.json")
    store.upload_directory(SUBTITLES_DIR, "audio/subtitles")
    store.upload_file(get_output_file("tts_check_result.json"), "audio/tts_check_result.json")


def render_stage() -> None:
    _download_required("scripts/final_metadata.json", FINAL_METADATA_FILE)
    if not _read_metadata():
        print("✅ 생성할 신규 항목이 없어 render stage를 no-op 처리합니다.")
        return
    _filter_metadata_by_content_gate("render")
    if not _read_metadata():
        print("✅ content gate 통과 항목이 없어 render stage를 no-op 처리합니다.")
        return

    _download_file("state/used_pixabay_ids.json", USED_PIXABAY_IDS_FILE)
    store.download_prefix("audio/mp3", AUDIO_DIR)
    store.download_prefix("audio/subtitles", SUBTITLES_DIR)
    _download_required("audio/tts_check_result.json", get_output_file("tts_check_result.json"))
    _filter_metadata_by_artifacts("render", require_audio=True, require_srt=True, require_tts_result=True)
    if not _read_metadata():
        print("✅ render artifact gate 통과 항목이 없어 render stage를 no-op 처리합니다.")
        return
    target_ids = _render_target_ids()
    if target_ids is not None and not target_ids:
        print("✅ render shard에 할당된 항목이 없어 no-op 처리합니다.")
        return

    batch_merge_videos_for_tts(target_ids=target_ids)
    batch_render_all_videos(target_ids=target_ids)
    rendered_files = _rendered_final_files(target_ids)
    rendered_files = _validated_rendered_files(rendered_files)
    if not rendered_files:
        raise RuntimeError("Video rendering produced no final MP4 files.")
    _attach_video_keys_from_local()
    _keep_rendered_metadata_only()
    store.upload_directory(FINAL_DIR, "videos/final")
    store.upload_directory(OUTPUT_DIR / "video-sources", "videos/sources")
    if os.getenv("RENDER_SHARD_MODE", "").lower().strip() == "array":
        _upload_render_shard_pixabay_usage(target_ids)
        return

    _finalize_publish_ready()


def finalize_stage() -> None:
    _download_required("scripts/final_metadata.json", FINAL_METADATA_FILE)
    if not _read_metadata():
        print("✅ 생성할 신규 항목이 없어 finalize stage를 no-op 처리합니다.")
        return
    _attach_video_keys_from_s3()
    _keep_rendered_metadata_only()
    _filter_metadata_by_content_gate("finalize")
    if not _read_metadata():
        print("✅ publish-ready로 보낼 content gate 통과 항목이 없습니다.")
        return
    _finalize_publish_ready()


def _finalize_publish_ready() -> None:
    _merge_render_shard_pixabay_usage()
    update_metadata_after_video_creation()
    _filter_metadata_by_artifacts(
        "publish_ready",
        require_video_key=True,
        require_final_video=True,
        allow_s3_video=True,
    )
    if not _read_metadata():
        print("✅ video artifact gate 통과 항목이 없어 publish-ready 업로드를 건너뜁니다.")
        return
    _filter_metadata_by_content_gate("publish_ready")
    if not _read_metadata():
        print("✅ content gate 통과 항목이 없어 publish-ready 업로드를 건너뜁니다.")
        return
    store.upload_file(FINAL_METADATA_FILE, "publish-ready/final_metadata.json")
    store.upload_file(FINAL_METADATA_FILE, "state/final_metadata.json")
    if USED_PIXABAY_IDS_FILE.exists():
        store.upload_file(USED_PIXABAY_IDS_FILE, "state/used_pixabay_ids.json")
    with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
        content_repo.upsert_items(json.load(f), "PUBLISH_READY")


def _filter_metadata_by_content_gate(stage: str) -> None:
    items = _read_metadata()
    accepted, rejected = filter_content_gate_items(items, stage=stage)
    if rejected:
        print(f"🚫 content gate rejected {len(rejected)} item(s) at {stage}: {rejected[:3]}")
        _append_failed_items(rejected)
        try:
            store.upload_file(FAILED_POSTS_FILE, "scripts/failed_posts.json")
        except Exception as exc:
            print(f"⚠️ failed_posts upload skipped: {exc}")
    _write_metadata(accepted)


def _filter_metadata_by_artifacts(
    stage: str,
    *,
    require_audio: bool = False,
    require_marks: bool = False,
    require_srt: bool = False,
    require_tts_result: bool = False,
    require_video_key: bool = False,
    require_final_video: bool = False,
    allow_s3_video: bool = False,
) -> None:
    tts_result_ids = _tts_result_ids() if require_tts_result else set()
    accepted = []
    rejected = []
    for item in _read_metadata():
        content_id = str(item.get("id") or "")
        errors = []
        if not content_id:
            errors.append("missing_id")
        if require_audio and not _valid_file(AUDIO_DIR / f"{content_id}.mp3"):
            errors.append("missing_audio_mp3")
        if require_marks and not _valid_file(MARKS_DIR / f"{content_id}_marks.json"):
            errors.append("missing_speech_marks")
        if require_srt and not _valid_file(SUBTITLES_DIR / f"{content_id}.srt"):
            errors.append("missing_subtitle_srt")
        if require_tts_result and content_id not in tts_result_ids:
            errors.append("missing_tts_check_result")
        if require_video_key and not str(item.get("video_key") or "").strip():
            errors.append("missing_video_key")
        if require_final_video:
            local_video = FINAL_DIR / f"{content_id}.mp4"
            video_key = str(item.get("video_key") or f"videos/final/{content_id}.mp4")
            has_video = _valid_file(local_video)
            if not has_video and allow_s3_video and video_key:
                try:
                    has_video = store.object_exists(video_key)
                except Exception as exc:
                    print(f"⚠️ video artifact S3 check skipped for {content_id}: {exc}")
            if not has_video:
                errors.append("missing_final_mp4")
        if errors:
            item["artifact_gate_status"] = "rejected"
            item["artifact_gate_stage"] = stage
            item["artifact_gate_errors"] = errors
            rejected.append(
                {
                    "id": content_id,
                    "title": item.get("title") or item.get("public_title"),
                    "stage": stage,
                    "error": "artifact_gate_failed:" + ",".join(errors),
                }
            )
            continue
        item["artifact_gate_status"] = "passed"
        item["artifact_gate_stage"] = stage
        accepted.append(item)
    if rejected:
        print(f"🚫 artifact gate rejected {len(rejected)} item(s) at {stage}: {rejected[:3]}")
        _append_failed_items(rejected)
        try:
            store.upload_file(FAILED_POSTS_FILE, "scripts/failed_posts.json")
        except Exception as exc:
            print(f"⚠️ failed_posts upload skipped: {exc}")
    _write_metadata(accepted)


def _valid_file(path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def _tts_result_ids() -> set[str]:
    path = get_output_file("tts_check_result.json")
    if not path.exists():
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            items = json.load(f)
        if not isinstance(items, list):
            return set()
        return {str(item.get("filename")) for item in items if item.get("filename")}
    except Exception as exc:
        print(f"⚠️ tts_check_result read failed: {exc}")
        return set()


def _append_failed_items(items: list[dict]) -> None:
    if not items:
        return
    existing = _read_json_file(FAILED_POSTS_FILE)
    if not isinstance(existing, list):
        existing = []
    _write_json_file(FAILED_POSTS_FILE, existing + items)


def _log_generation_acceptance() -> None:
    desired = _desired_new_count()
    accepted = len(_read_metadata())
    rejected = len(_read_json_file(FAILED_POSTS_FILE))
    print(f"📊 generation quality summary desired={desired} accepted={accepted} rejected={rejected}")
    if accepted < desired:
        try:
            send_slack_message(
                f"ytshorts generation accepted fewer items than desired: desired={desired}, accepted={accepted}, rejected={rejected}"
            )
        except Exception as exc:
            print(f"⚠️ Slack notify skipped: {exc}")


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
    env_ids = _target_ids_from_env()
    if env_ids:
        return env_ids

    items = _read_metadata()
    metadata_ids = [str(item.get("id")) for item in items if item.get("id")]

    if os.getenv("RENDER_SHARD_MODE", "").lower().strip() != "array":
        return metadata_ids

    raw_index = os.getenv("AWS_BATCH_JOB_ARRAY_INDEX")
    if raw_index is None:
        return metadata_ids
    try:
        shard_index = int(raw_index)
    except ValueError:
        raise ValueError(f"Invalid AWS_BATCH_JOB_ARRAY_INDEX: {raw_index}")

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


def _validated_rendered_files(files: list) -> list:
    valid_files = []
    for video_path in files:
        valid, reason, details = validate_video_file(video_path)
        if valid:
            warnings = quality_warnings(video_path)
            if warnings:
                print(f"⚠️ rendered video quality warnings: {video_path} warnings={warnings} details={details}")
            valid_files.append(video_path)
            continue
        print(f"⚠️ invalid rendered video skipped: {video_path} reason={reason} details={details}")
        try:
            video_path.unlink(missing_ok=True)
        except OSError as exc:
            print(f"⚠️ invalid video cleanup failed: {video_path}: {exc}")
    return valid_files


def _upload_render_shard_pixabay_usage(target_ids: list[str] | None) -> None:
    if not USED_PIXABAY_IDS_FILE.exists():
        return
    shard_id = "all"
    if target_ids:
        shard_id = "-".join(str(item) for item in target_ids[:3])
    store.upload_file(USED_PIXABAY_IDS_FILE, f"{RENDER_USED_PIXABAY_PREFIX}/{shard_id}.json")


def _merge_render_shard_pixabay_usage() -> None:
    used_ids = set(_read_json_file(USED_PIXABAY_IDS_FILE))
    existing_used_path = get_temp_file("existing_used_pixabay_ids.json")
    if _download_file("state/used_pixabay_ids.json", existing_used_path):
        used_ids.update(_read_json_file(existing_used_path))
    shard_dir = get_temp_file("render-used-pixabay")
    downloaded = store.download_prefix(RENDER_USED_PIXABAY_PREFIX, shard_dir)
    for path in downloaded:
        try:
            used_ids.update(_read_json_file(path))
        except Exception as exc:
            print(f"⚠️ render shard Pixabay usage merge failed: {path}: {exc}")
    if downloaded or used_ids:
        _write_json_file(USED_PIXABAY_IDS_FILE, sorted(used_ids, key=str))


def _desired_new_count() -> int:
    target_new_items = os.getenv("GENERATION_TARGET_NEW_ITEMS")
    if target_new_items is not None and target_new_items.strip() != "":
        return max(0, _safe_int(target_new_items, 0))

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


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
