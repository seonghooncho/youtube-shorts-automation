from pathlib import Path
from typing import Iterable, List

from shared.utils.config import FINAL_DIR, get_data_file, get_s3_state_key, get_s3_video_key
from shared.utils.s3_utils import download_from_s3, upload_to_s3


STATE_FILES_TO_SYNC = [
    "scraped_post_list.json",
    "used_pixabay_ids.json",
]


def download_state_files(file_names: Iterable[str] = STATE_FILES_TO_SYNC) -> None:
    for file_name in file_names:
        local_path = get_data_file(file_name)
        s3_key = get_s3_state_key(local_path)
        download_from_s3(s3_key, str(local_path))


def upload_state_files(file_names: Iterable[str] = STATE_FILES_TO_SYNC) -> List[str]:
    uploaded_keys: List[str] = []
    for file_name in file_names:
        path = get_data_file(file_name)
        if not path.exists():
            continue
        s3_key = get_s3_state_key(path)
        upload_to_s3(str(path), s3_key)
        uploaded_keys.append(s3_key)
    return uploaded_keys


def upload_final_videos(final_dir: Path = FINAL_DIR) -> List[str]:
    uploaded_keys: List[str] = []
    for video_path in final_dir.glob("*.mp4"):
        s3_key = get_s3_video_key(video_path)
        upload_to_s3(str(video_path), s3_key)
        uploaded_keys.append(s3_key)
    return uploaded_keys
