from pathlib import Path
import shutil

# 프로젝트 루트 경로
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 주요 디렉토리 경로들
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
ASSETS_DIR = PROJECT_ROOT / "assets"
TEMP_DIR = PROJECT_ROOT / "temp"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
JOBS_DIR = PROJECT_ROOT / "jobs"
SERVICES_DIR = PROJECT_ROOT / "services"

# 주요 파일 경로들
RAW_POSTS_FILE = DATA_DIR / "raw_posts.json"
VIABLE_POSTS_FILE = DATA_DIR / "viable_posts.json"
FINAL_METADATA_FILE = DATA_DIR / "final_metadata.json"
FAILED_POSTS_FILE = DATA_DIR / "failed_posts.json"
SCRAPED_POST_LIST_FILE = DATA_DIR / "scraped_post_list.json"
USED_PIXABAY_IDS_FILE = DATA_DIR / "used_pixabay_ids.json"

# 출력 디렉토리들
AUDIO_DIR = OUTPUT_DIR / "audio"
FINAL_DIR = OUTPUT_DIR / "final"
MARKS_DIR = OUTPUT_DIR / "marks"
SUBTITLES_DIR = OUTPUT_DIR / "subtitles"

# 임시 디렉토리들
TMP_DIR = ASSETS_DIR / "tmp"
OUTPUT_TMP_DIR = ASSETS_DIR / "output"

# 디렉토리 생성 함수
def ensure_generator_directories():
    """영상 생성 배치 작업에 필요한 디렉토리를 생성합니다."""
    directories = [
        DATA_DIR,
        OUTPUT_DIR,
        ASSETS_DIR,
        TEMP_DIR,
        AUDIO_DIR,
        FINAL_DIR,
        MARKS_DIR,
        SUBTITLES_DIR,
        OUTPUT_TMP_DIR
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

def ensure_uploader_directories():
    """영상 업로드 배치 작업에 필요한 디렉토리를 생성합니다."""
    directories = [
        DATA_DIR,
        TEMP_DIR
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

# 경로 유틸리티 함수들
def get_data_file(filename: str) -> Path:
    """data 디렉토리의 파일 경로를 반환합니다."""
    return DATA_DIR / filename

def get_output_file(filename: str) -> Path:
    """output 디렉토리의 파일 경로를 반환합니다."""
    return OUTPUT_DIR / filename

def get_assets_file(filename: str) -> Path:
    """assets 디렉토리의 파일 경로를 반환합니다."""
    return ASSETS_DIR / filename

def get_font_file() -> Path:
    """자막 렌더링에 사용할 기본 폰트 경로를 반환합니다."""
    bundled_font = PROJECT_ROOT / "shared" / "utils" / "Anton-Regular.ttf"
    if bundled_font.exists():
        return bundled_font
    return ASSETS_DIR / "fonts" / "Anton-Regular.ttf"

def get_video_source(name: str) -> Path:
    """유튜브 업로드용 병합 영상 소스 경로를 반환합니다."""
    return OUTPUT_DIR / "video-sources" / name

# S3 경로 관리
S3_VIDEO_PREFIX = "shorts/videos"
S3_STATE_PREFIX = "shorts/state"

def get_s3_video_key(local_path: Path) -> str:
    return f"{S3_VIDEO_PREFIX}/{local_path.name}"

def get_s3_state_key(local_path: Path) -> str:
    return f"{S3_STATE_PREFIX}/{local_path.name}"

#업로드
def get_temp_file(filename: str) -> Path:
    """temp 디렉토리의 파일 경로를 반환합니다.(업로드 시 s3에서 가져온 완성결과물 임시저장소소)"""
    return TEMP_DIR / filename



def clean_generator_workspace():
    """영상 생성 작업 후 중간/임시 파일을 정리합니다."""
    directories_to_clean = [
        OUTPUT_DIR,
        ASSETS_DIR / "tmp"
    ]
    for directory in directories_to_clean:
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
            print(f"🧹 디렉토리 정리 완료: {directory}")

    # 데이터 디렉토리 내 특정 파일만 삭제 (상태 파일은 유지)
    files_to_remove = [
        RAW_POSTS_FILE,
        VIABLE_POSTS_FILE,
        FAILED_POSTS_FILE,
    ]
    for file_path in files_to_remove:
        if file_path.exists():
            file_path.unlink()
            print(f"🧹 파일 삭제 완료: {file_path}")

def clean_uploader_workspace():
    """영상 업로드 작업 후 임시 파일을 정리합니다."""
    directories_to_clean = [
        TEMP_DIR
    ]
    for directory in directories_to_clean:
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
            print(f"🧹 디렉토리 정리 완료: {directory}")
