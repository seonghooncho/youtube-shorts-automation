from generator.text.scrape_reddit_and_store import scrape_reddit_and_store
from generator.text.filter_viable_posts import filter_viable_posts
from generator.text.generate_scripts_from_filtered import generate_scripts_from_filtered
from generator.tts.generate_tts import run_batch_tts
from generator.video.convert_all_srt import convert_all_marks_to_srt
from generator.tts.analyze_all_tts import analyze_all_tts
from generator.video.pixabay_video_merge import batch_merge_videos_for_tts
from generator.video.create_video import batch_render_all_videos
from shared.utils.s3_utils import upload_to_s3, download_from_s3, update_metadata_after_video_creation
from shared.utils.slack_notify import send_slack_message
from shared.utils.config import (
    ensure_generator_directories,
    FINAL_DIR,
    get_data_file,
    get_s3_video_key,
    get_s3_state_key,
    clean_generator_workspace
)

import json
from pathlib import Path

def run_batch_pipeline():
    ensure_generator_directories()
    # 1. S3에서 이전 상태 파일 다운로드 (final_metadata.json 제외)
    uploaded_keys = []

    try:
        print("🚀 배치 파이프라인 시작...")
        
        # 1. S3에서 이전 상태 파일 다운로드 (final_metadata.json 제외)
        state_files_to_sync = [
            "scraped_post_list.json",
            # "used_pixabay_state.json",
            "used_pixabay_ids.json",
        ]
        for file_name in state_files_to_sync:
            s3_key = get_s3_state_key(get_data_file(file_name))
            local_path = get_data_file(file_name)
            download_from_s3(s3_key, str(local_path)) 

    
        # 1. Reddit에서 게시물 수집
        scrape_reddit_and_store()

        # 2. 적합한 게시물 필터링
        filter_viable_posts()

        # 3. 필터링한 게시물로 스크립트 생성
        generate_scripts_from_filtered()

        # 4. TTS 생성
        run_batch_tts()

        # 5. SRT 파일 변환
        convert_all_marks_to_srt()

        # 6. TTS 분석(배속처리)
        analyze_all_tts()

        # 7. 영상 병합(영상소스 생성성)
        batch_merge_videos_for_tts()

        # 8. 최종 영상 생성
        batch_render_all_videos()

    
        # 3. final_metadata.json 병합 및 S3 업로드
        # 이 함수가 S3에서 이전 파일을 다운받아 새로운 파일과 병합 후 다시 S3에 업로드합니다.
        update_metadata_after_video_creation()
        
        # 4. 새로 생성된 영상 및 상태 파일 S3 업로드
        # 영상 업로드
        for video_path in FINAL_DIR.glob("*.mp4"):
            s3_key = get_s3_video_key(video_path)
            upload_to_s3(str(video_path), s3_key)
            uploaded_keys.append(s3_key)

        # 로그/상태 파일 업로드 (final_metadata.json은 위에서 이미 업로드됨)
        state_files = [
            get_data_file("scraped_post_list.json"),
            # get_data_file("used_pixabay_state.json"),
            get_data_file("used_pixabay_ids.json"),
        ]
        for path in state_files:
            s3_key = get_s3_state_key(path)
            upload_to_s3(str(path), s3_key)
            uploaded_keys.append(s3_key)

        # 5. Slack 알림
        slack_message = f"🎉 파이프라인 완료!\n업로드된 파일 수: {len(uploaded_keys)}\n"
        slack_message += "\n".join([f"- {key}" for key in uploaded_keys])
        send_slack_message(slack_message)

    except Exception as e:
        print(f"🚨 파이프라인 실행 중 오류 발생: {e}")
        send_slack_message(f"🚨 영상 생성 파이프라인 실패: {e}")
    

    

if __name__ == "__main__":
    run_batch_pipeline()
