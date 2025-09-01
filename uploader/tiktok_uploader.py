import os
from TikTokApi import TikTokApi

def upload_tiktok(file_path, caption):
    # TikTokApi는 현재 OAuth 토큰 필요
    # 환경 변수: TIKTOK_SESSION_ID
    session_id = os.getenv("TIKTOK_SESSION_ID")
    if not session_id:
        raise RuntimeError("❌ TikTok SESSION_ID 필요")

    with TikTokApi(custom_verify_fp=session_id, use_test_endpoints=True) as api:
        upload = api.video.upload(file_path, caption=caption)
        print(f"✅ TikTok 업로드 성공: {upload}")
        return upload
