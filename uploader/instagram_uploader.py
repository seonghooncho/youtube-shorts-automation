import os
from instagrapi import Client

def upload_instagram(file_path, caption):
    username = os.getenv("INSTAGRAM_USERNAME")
    password = os.getenv("INSTAGRAM_PASSWORD")

    cl = Client()
    cl.login(username, password)

    media = cl.clip_upload(file_path, caption)
    print(f"✅ Instagram 업로드 성공: {media.pk}")
    return media.pk
