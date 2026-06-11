import json
from shared.utils.config import FINAL_METADATA_FILE, get_temp_file, clean_uploader_workspace
from shared.utils.s3_utils import download_from_s3, upload_to_s3
from shared.utils.slack_notify import send_slack_message
from shared.settings import UploadSettings
from uploader.youtube_uploader import upload_youtube

def upload_batch_pipeline():
    try:
        settings = UploadSettings.from_env()
        # 1. final_metadata.json 다운로드
        if not download_from_s3("shorts/state/final_metadata.json", str(FINAL_METADATA_FILE)):
            send_slack_message("✅ 업로드할 메타데이터가 없습니다")
            return

        with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
            metadata_list = json.load(f)

        # 2. 업로드 안 된 콘텐츠 찾기
        target = next((m for m in metadata_list if not m.get("uploaded")), None)
        if not target:
            send_slack_message("✅ 업로드할 영상이 없습니다")
            return

        local_path = get_temp_file(target["id"] + ".mp4")
        if not download_from_s3(f"shorts/videos/{target['id']}.mp4", str(local_path)):
            raise FileNotFoundError(f"S3 영상 파일을 찾을 수 없습니다: shorts/videos/{target['id']}.mp4")

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

        with open(FINAL_METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata_list, f, ensure_ascii=False, indent=2)

        upload_to_s3(str(FINAL_METADATA_FILE), "shorts/state/final_metadata.json")

        send_slack_message(f"🎉 업로드 완료: {title}")

    except Exception as e:
        send_slack_message(f"🚨 업로드 파이프라인 실패: {e}")
        raise
    finally:
        clean_uploader_workspace()
