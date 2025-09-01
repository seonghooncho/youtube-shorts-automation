
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import os
from dotenv import load_dotenv

load_dotenv()

def upload_video(file_path, title, description, tags):
    api_key = os.getenv("YOUTUBE_API_KEY")
    youtube = build("youtube", "v3", developerKey=api_key)

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags
            },
            "status": {
                "privacyStatus": "public"
            }
        },
        media_body=MediaFileUpload(file_path)
    )

    response = request.execute()
    print(f"Video uploaded successfully: {response['id']}")