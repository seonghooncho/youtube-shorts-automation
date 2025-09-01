import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def upload_youtube(file_path, title, description, tags):
    api_key = os.getenv("YOUTUBE_API_KEY")
    youtube = build("youtube", "v3", developerKey=api_key)

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "description": description, "tags": tags},
            "status": {"privacyStatus": "public"}
        },
        media_body=MediaFileUpload(file_path)
    )
    response = request.execute()
    print(f"✅ YouTube 업로드 성공: {response['id']}")
    return response['id']
