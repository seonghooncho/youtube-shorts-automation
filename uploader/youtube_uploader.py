import os
from typing import Iterable

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from generator.text.youtube_metadata import sanitize_upload_metadata
from uploader.youtube_oauth import build_youtube_credentials


def upload_youtube(file_path, title, description, tags: Iterable[str]):
    creds = build_youtube_credentials(interactive=False)
    youtube = build("youtube", "v3", credentials=creds)
    privacy_status = os.getenv("YOUTUBE_PRIVACY_STATUS", "public")
    metadata = sanitize_upload_metadata(title, description, tags)

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": metadata["title"],
                "description": metadata["description"],
                "tags": metadata["tags"],
                "categoryId": os.getenv("YOUTUBE_CATEGORY_ID", "22"),
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": os.getenv("YOUTUBE_MADE_FOR_KIDS", "0") == "1",
            },
        },
        media_body=MediaFileUpload(file_path, chunksize=-1, resumable=True),
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"⏫ YouTube upload progress: {int(status.progress() * 100)}%")

    print(f"✅ YouTube 업로드 성공: {response['id']} ({privacy_status})")
    return response["id"]
