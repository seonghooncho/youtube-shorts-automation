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
VIDEO_PREFIX = "videos/final"
LEGACY_VIDEO_PREFIX = "shorts/videos"

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
        due_items = [
            m for m in sorted(metadata_list, key=lambda item: item.get("scheduled_publish_at", 0))
            if not m.get("uploaded") and int(m.get("scheduled_publish_at") or 0) <= now
        ]
        target = None
        rejected_by_gate = False
        for candidate in due_items:
            try:
                ensure_content_gate(candidate, stage="upload")
                target = candidate
                break
            except ValueError as gate_error:
                rejected_by_gate = True
                candidate["upload_status"] = "REJECTED_BY_CONTENT_GATE"
                candidate["status"] = "REJECTED_BY_CONTENT_GATE"
                candidate["content_gate_upload_error"] = str(gate_error)
                candidate["content_gate_rejected_at"] = int(time.time())
                candidate["uploaded"] = False
                print(f"🚫 upload content gate rejected id={candidate.get('id')}: {gate_error}")
                try:
                    ContentRepository().mark_status(
                        str(candidate.get("id") or ""),
                        "REJECTED_BY_CONTENT_GATE",
                        {
                            "upload_status": "REJECTED_BY_CONTENT_GATE",
                            "content_gate_upload_error": str(gate_error),
                        },
                    )
                except Exception as repo_error:
                    print(f"⚠️ content gate rejection status update skipped: {repo_error}")
        if rejected_by_gate:
            with open(FINAL_METADATA_FILE, "w", encoding="utf-8") as f:
                json.dump(metadata_list, f, ensure_ascii=False, indent=2)
            upload_to_s3(str(FINAL_METADATA_FILE), metadata_key)
        if due_items and target is None:
            send_slack_message("🚫 업로드 가능한 due item이 없습니다. 모든 due item이 content gate에서 거절되었습니다.")
            return
        if not target:
            send_slack_message("✅ 업로드할 영상이 없습니다")
            return

        local_path = get_temp_file(target["id"] + ".mp4")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        video_key = target.get("video_key") or f"{VIDEO_PREFIX}/{target['id']}.mp4"
        if not download_from_s3(video_key, str(local_path)):
            legacy_video_key = f"{LEGACY_VIDEO_PREFIX}/{target['id']}.mp4"
            if not download_from_s3(legacy_video_key, str(local_path)):
                raise FileNotFoundError(f"S3 영상 파일을 찾을 수 없습니다: {video_key}")

        title, description, tags = target["title"], target["description"], target["tags"]
        platform_ids = {}

        # 3. 플랫폼별 업로드
        if "youtube" in settings.target_platforms:
            platform_ids["youtube"] = upload_youtube(str(local_path), title, description, tags)
        if "instagram" in settings.target_platforms and settings.instagram_enabled:
            from uploader.instagram_uploader import upload_instagram

            platform_ids["instagram"] = upload_instagram(str(local_path), f"{title}\n{description}")
        if "tiktok" in settings.target_platforms and settings.tiktok_enabled:
            from uploader.tiktok_uploader import upload_tiktok

            platform_ids["tiktok"] = upload_tiktok(str(local_path), f"{title} #shorts")

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
