import json
from dotenv import load_dotenv

from shared.utils.config import (
    FINAL_METADATA_FILE,
    get_temp_file,
    get_s3_state_key,
    get_data_file,
    S3_LEGACY_STATE_PREFIX,
    S3_PUBLISH_READY_PREFIX,
)
from shared.storage import S3Store

load_dotenv()

s3_store = S3Store()

def upload_to_s3(file_path: str, s3_key: str):
    try:
        s3_store.upload_file(file_path, s3_key)
        print(f"✅ Uploaded to S3: {s3_key}")
    except Exception as e:
        print(f"🚨 S3 업로드 실패: {e}")

def download_from_s3(s3_key: str, file_path: str) -> bool:
    """
    S3에서 로컬 경로로 파일을 다운로드합니다.
    파일이 존재하지 않으면 예외를 발생시키지 않고 False를 반환합니다.
    """
    try:
        downloaded = s3_store.download_file(s3_key, file_path)
        if downloaded:
            print(f"⬇️ Downloaded from S3: {s3_key}")
            return True
        else:
            print(f"⚠️ S3에 파일이 없습니다: {s3_key}")
            return False
    except Exception as e:
        print(f"🚨 S3 다운로드 실패: {e}")
        return False

def update_metadata_after_video_creation():
    """
    영상 생성 후, 이전 메타데이터와 새로운 메타데이터를 병합하고 S3에 업로드합니다.
    """
    # 경로 구성
    s3_metadata_key = f"{S3_PUBLISH_READY_PREFIX}/{FINAL_METADATA_FILE.name}"
    legacy_metadata_key = f"{S3_LEGACY_STATE_PREFIX}/{FINAL_METADATA_FILE.name}"
    tmp_old_metadata_path = get_temp_file("old_final_metadata.json") # temp/old_final_metadata.json
    new_metadata_path = get_data_file("final_metadata.json")    # output/final_metadata.json
    merged_output_path = FINAL_METADATA_FILE                       # data/final_metadata.json

    # 1. S3에서 기존 metadata 다운로드 (파일이 없을 경우 빈 리스트)
    old_data = []
    if download_from_s3(s3_metadata_key, str(tmp_old_metadata_path)) or download_from_s3(legacy_metadata_key, str(tmp_old_metadata_path)):
        try:
            with open(tmp_old_metadata_path, "r", encoding="utf-8") as f:
                old_data = json.load(f)
        except Exception as e:
            print(f"🚨 기존 metadata 로드 실패: {e}")
            old_data = []
    else:
        print("⚠️ S3에 기존 metadata가 없어 새로운 파일로 시작합니다.")

    # 2. 새로 생성된 metadata 불러오기
    if not new_metadata_path.exists():
        print(f"🚨 새로운 metadata 파일이 없습니다: {new_metadata_path}")
        return

    with open(new_metadata_path, "r", encoding="utf-8") as f:
        new_data = json.load(f)
    
    # 3. 병합 (새로운 데이터가 기존 데이터를 덮어쓰도록 ID 기준 중복 제거)
    if not isinstance(old_data, list):
        old_data = []
    if not isinstance(new_data, list):
        new_data = []
    new_ids = {item["id"] for item in new_data if item.get("id")}
    filtered_old = [item for item in old_data if item.get("id") not in new_ids]
    merged_data = filtered_old + new_data
    merged_data.sort(key=lambda item: int(item.get("scheduled_publish_at") or 0))
    
    # 4. 병합된 메타데이터를 최종 경로에 저장
    with open(merged_output_path, "w", encoding="utf-8") as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=2)
    print(f"✅ 병합된 metadata가 저장되었습니다: {merged_output_path}")

    # 5. 병합 결과를 S3에 업로드
    upload_to_s3(str(merged_output_path), s3_metadata_key)
    upload_to_s3(str(merged_output_path), legacy_metadata_key)
