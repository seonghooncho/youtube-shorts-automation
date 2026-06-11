import json
import time
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
            metadata_key = LEGACY_METADATA_KEY
        if metadata_key == LEGACY_METADATA_KEY and not download_from_s3(metadata_key, str(FINAL_METADATA_FILE)):
            send_slack_message("✅ 업로드할 메타데이터가 없습니다")
            return

        with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
            metadata_list = json.load(f)

        # 2. 예약 시간이 지난 업로드 대기 콘텐츠 찾기
        now = int(time.time())
        target = next(
            (
                m for m in sorted(metadata_list, key=lambda item: item.get("scheduled_publish_at", 0))
                if not m.get("uploaded") and int(m.get("scheduled_publish_at") or 0) <= now
            ),
            None,
        )
        if not target:
            send_slack_message("✅ 업로드할 영상이 없습니다")
            return

        local_path = get_temp_file(target["id"] + ".mp4")
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

        with open(FINAL_METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata_list, f, ensure_ascii=False, indent=2)

        upload_to_s3(str(FINAL_METADATA_FILE), metadata_key)
        if metadata_key != LEGACY_METADATA_KEY:
            upload_to_s3(str(FINAL_METADATA_FILE), LEGACY_METADATA_KEY)
        ContentRepository().mark_status(
            target["id"],
            "UPLOADED",
            {"platform_ids": platform_ids, "upload_status": "UPLOADED"},
        )

        send_slack_message(f"🎉 업로드 완료: {title}")

    except Exception as e:
        send_slack_message(f"🚨 업로드 파이프라인 실패: {e}")
        raise
    finally:
        clean_uploader_workspace()
