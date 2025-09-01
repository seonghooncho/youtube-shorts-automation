import json
from shared.utils.config import FINAL_METADATA_FILE, get_temp_file, clean_uploader_workspace
from shared.utils.s3_utils import download_from_s3, upload_to_s3
from shared.utils.slack_notify import send_slack_message
from uploader.youtube_uploader import upload_youtube
from uploader.instagram_uploader import upload_instagram
from uploader.tiktok_uploader import upload_tiktok

def upload_batch_pipeline():
    try:
        # 1. final_metadata.json 다운로드
        download_from_s3("shorts/state/final_metadata.json", str(FINAL_METADATA_FILE))

        with open(FINAL_METADATA_FILE, "r", encoding="utf-8") as f:
            metadata_list = json.load(f)

        # 2. 업로드 안 된 콘텐츠 찾기
        target = next((m for m in metadata_list if not m.get("uploaded")), None)
        if not target:
            send_slack_message("✅ 업로드할 영상이 없습니다")
            return

        local_path = get_temp_file(target["id"] + ".mp4")
        download_from_s3(f"shorts/videos/{target['id']}.mp4", str(local_path))

        title, description, tags = target["title"], target["description"], target["tags"]

        # 3. 플랫폼별 업로드
        youtube_id = upload_youtube(str(local_path), title, description, tags)
        insta_id = upload_instagram(str(local_path), f"{title}\n{description}")
        tiktok_id = upload_tiktok(str(local_path), f"{title} #shorts")

        # 4. 메타데이터 업데이트
        target["uploaded"] = True
        target["platform_ids"] = {
            "youtube": youtube_id,
            "instagram": insta_id,
            "tiktok": tiktok_id
        }

        with open(FINAL_METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata_list, f, ensure_ascii=False, indent=2)

        upload_to_s3(str(FINAL_METADATA_FILE), "shorts/state/final_metadata.json")

        send_slack_message(f"🎉 업로드 완료: {title}")

    except Exception as e:
        send_slack_message(f"🚨 업로드 파이프라인 실패: {e}")
        raise
    finally:
        clean_uploader_workspace()
