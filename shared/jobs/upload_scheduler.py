import json
import os
import time
from generator.text.content_gate import ensure_content_gate
from shared.utils.config import FINAL_METADATA_FILE, get_temp_file, clean_uploader_workspace
from shared.utils.s3_utils import download_from_s3, upload_to_s3
from shared.utils.slack_notify import send_slack_message
from shared.settings import UploadSettings
from uploader.youtube_uploader import upload_youtube
from shared.state import ContentRepository

PUBLISH_METADATA_KEY = "publish-ready/final_metadata.json"
LEGACY_METADATA_KEY = "shorts/state/final_metadata.json"
REJECTED_METADATA_KEY = "state/rejected_publish_metadata.json"
VIDEO_PREFIX = "videos/final"
LEGACY_VIDEO_PREFIX = "shorts/videos"
BLOCKED_UPLOAD_STATUSES = {
    "REJECTED_BY_CONTENT_GATE",
    "VIDEO_MISSING",
    "UPLOAD_SKIPPED",
    "UPLOAD_FAILED_PERMANENT",
    "PUBLISH_REJECTED",
}

def upload_batch_pipeline():
    try:
        settings = UploadSettings.from_env()
        # 1. final_metadata.json 다운로드
        metadata_key = PUBLISH_METADATA_KEY
        if not download_from_s3(metadata_key, str(FINAL_METADATA_FILE)):
            if _is_production_env() and not _allow_legacy_upload_metadata():
                send_slack_message("✅ publish-ready metadata가 없고 production legacy metadata fallback이 비활성화되어 업로드를 건너뜁니다")
                return
            metadata_key = LEGACY_METADATA_KEY
        if metadata_key == LEGACY_METADATA_KEY and not download_from_s3(metadata_key, str(FINAL_METADATA_FILE)):
            send_slack_message("✅ 업로드할 메타데이터가 없습니다")
            return
        if metadata_key == LEGACY_METADATA_KEY:
            send_slack_message("⚠️ legacy upload metadata를 사용합니다. 모든 항목은 content gate를 통과해야 업로드됩니다.")

        with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
            metadata_list = json.load(f)

        # 2. 예약 시간이 지난 업로드 대기 콘텐츠 찾기
        now = int(time.time())
        due_items = [m for m in sorted(metadata_list, key=_scheduled_at) if _is_upload_candidate(m, now)]
        target = None
        target_local_path = None
        skipped_items = []
        for candidate in due_items:
            try:
                ensure_content_gate(candidate, stage="upload")
            except ValueError as gate_error:
                _mark_upload_skip(candidate, "REJECTED_BY_CONTENT_GATE")
                candidate["content_gate_upload_error"] = str(gate_error)
                candidate["content_gate_rejected_at"] = int(time.time())
                skipped_items.append(candidate)
                print(f"🚫 upload content gate rejected id={candidate.get('id')}: {gate_error}")
                _mark_repo_status(
                    candidate,
                    "REJECTED_BY_CONTENT_GATE",
                    {
                        "upload_status": "REJECTED_BY_CONTENT_GATE",
                        "content_gate_upload_error": str(gate_error),
                    },
                )
                continue

            local_path = get_temp_file(str(candidate["id"]) + ".mp4")
            local_path.parent.mkdir(parents=True, exist_ok=True)
            video_key = candidate.get("video_key") or f"{VIDEO_PREFIX}/{candidate['id']}.mp4"
            if not _download_video(candidate, local_path, video_key):
                _mark_upload_skip(candidate, "VIDEO_MISSING")
                candidate["video_missing_at"] = int(time.time())
                candidate["video_key_attempted"] = video_key
                skipped_items.append(candidate)
                print(f"🚫 upload video missing id={candidate.get('id')}: {video_key}")
                _mark_repo_status(
                    candidate,
                    "VIDEO_MISSING",
                    {
                        "upload_status": "VIDEO_MISSING",
                        "video_key_attempted": video_key,
                    },
                )
                continue

            target = candidate
            target_local_path = local_path
            break

        if skipped_items:
            metadata_list = _move_skipped_items_out_of_active(metadata_list, skipped_items, metadata_key)
        if due_items and target is None:
            send_slack_message("🚫 업로드 가능한 due item이 없습니다. due item이 content gate 또는 artifact gate에서 제외되었습니다.")
            return
        if not target:
            send_slack_message("✅ 업로드할 영상이 없습니다")
            return

        title, description, tags = target["title"], target["description"], target["tags"]
        platform_ids = {}

        # 3. 플랫폼별 업로드
        if "youtube" in settings.target_platforms:
            platform_ids["youtube"] = upload_youtube(str(target_local_path), title, description, tags)
        if "instagram" in settings.target_platforms and settings.instagram_enabled:
            from uploader.instagram_uploader import upload_instagram

            platform_ids["instagram"] = upload_instagram(str(target_local_path), f"{title}\n{description}")
        if "tiktok" in settings.target_platforms and settings.tiktok_enabled:
            from uploader.tiktok_uploader import upload_tiktok

            platform_ids["tiktok"] = upload_tiktok(str(target_local_path), f"{title} #shorts")

        if not platform_ids:
            send_slack_message(f"✅ 활성화된 업로드 플랫폼이 없어 스킵: {title}")
            return

        # 4. 메타데이터 업데이트
        target["uploaded"] = True
        target["platform_ids"] = platform_ids
        target["upload_status"] = "UPLOADED"
        target["uploaded_at"] = int(time.time())
        youtube_id = platform_ids.get("youtube")
        if youtube_id:
            target["youtube_id"] = youtube_id
            target["youtube_url"] = f"https://www.youtube.com/watch?v={youtube_id}"
            target["privacy_status"] = settings.youtube_privacy_status

        with open(FINAL_METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata_list, f, ensure_ascii=False, indent=2)

        upload_to_s3(str(FINAL_METADATA_FILE), metadata_key)
        if metadata_key != LEGACY_METADATA_KEY:
            upload_to_s3(str(FINAL_METADATA_FILE), LEGACY_METADATA_KEY)
        ContentRepository().mark_status(
            target["id"],
            "UPLOADED",
            {
                "platform_ids": platform_ids,
                "youtube_id": youtube_id or "",
                "youtube_url": target.get("youtube_url", ""),
                "privacy_status": target.get("privacy_status", ""),
                "upload_status": "UPLOADED",
                "uploaded_at": target["uploaded_at"],
            },
        )

        send_slack_message(f"🎉 업로드 완료: {title}")

    except Exception as e:
        send_slack_message(f"🚨 업로드 파이프라인 실패: {e}")
        raise
    finally:
        clean_uploader_workspace()


def _is_production_env() -> bool:
    return any(os.getenv(name, "").strip().lower() == "production" for name in ("APP_ENV", "YT_ENV"))


def _allow_legacy_upload_metadata() -> bool:
    return os.getenv("ALLOW_LEGACY_UPLOAD_METADATA", "").strip().lower() in {"1", "true", "yes", "on"}


def _is_upload_candidate(item: dict, now: int) -> bool:
    if item.get("uploaded") or item.get("upload_skip"):
        return False
    status_values = {
        str(item.get("status") or "").strip().upper(),
        str(item.get("upload_status") or "").strip().upper(),
    }
    if status_values & BLOCKED_UPLOAD_STATUSES:
        return False
    return _scheduled_at(item) <= now


def _scheduled_at(item: dict) -> int:
    try:
        return int(item.get("scheduled_publish_at") or 0)
    except (TypeError, ValueError):
        return 0


def _mark_upload_skip(item: dict, status: str) -> None:
    item["upload_skip"] = True
    item["upload_status"] = status
    item["status"] = status
    item["uploaded"] = False


def _download_video(item: dict, local_path, video_key: str) -> bool:
    if download_from_s3(video_key, str(local_path)):
        return True
    if _is_production_env() and not _allow_legacy_video_fallback():
        return False
    legacy_video_key = f"{LEGACY_VIDEO_PREFIX}/{item['id']}.mp4"
    if legacy_video_key != video_key and download_from_s3(legacy_video_key, str(local_path)):
        item["legacy_video_key_used"] = legacy_video_key
        send_slack_message(f"⚠️ legacy video fallback used for upload: {legacy_video_key}")
        return True
    return False


def _move_skipped_items_out_of_active(metadata_list: list[dict], skipped_items: list[dict], metadata_key: str) -> list[dict]:
    skipped_ids = {str(item.get("id")) for item in skipped_items if item.get("id") is not None}
    active = [item for item in metadata_list if str(item.get("id")) not in skipped_ids]
    _write_json(FINAL_METADATA_FILE, active)
    upload_to_s3(str(FINAL_METADATA_FILE), metadata_key)

    rejected_path = get_temp_file("rejected_publish_metadata.json")
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_items = _load_rejected_metadata(rejected_path)
    rejected_items.extend(skipped_items)
    _write_json(rejected_path, rejected_items)
    upload_to_s3(str(rejected_path), REJECTED_METADATA_KEY)
    return active


def _load_rejected_metadata(path) -> list[dict]:
    if download_from_s3(REJECTED_METADATA_KEY, str(path)):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as exc:
            print(f"⚠️ rejected publish metadata load skipped: {exc}")
    return []


def _write_json(path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _mark_repo_status(item: dict, status: str, attrs: dict) -> None:
    try:
        ContentRepository().mark_status(str(item.get("id") or ""), status, attrs)
    except Exception as repo_error:
        print(f"⚠️ upload status update skipped: {repo_error}")


def _allow_legacy_video_fallback() -> bool:
    return os.getenv("ALLOW_LEGACY_VIDEO_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}
