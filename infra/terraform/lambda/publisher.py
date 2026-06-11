import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Any

import boto3
from botocore.exceptions import ClientError


s3 = boto3.client("s3")
ssm = boto3.client("ssm")
dynamodb = boto3.resource("dynamodb")

BUCKET_NAME = os.environ["BUCKET_NAME"]
CONTENT_TABLE_NAME = os.getenv("CONTENT_TABLE_NAME", "")
PUBLISH_METADATA_KEY = os.getenv("PUBLISH_METADATA_KEY", "publish-ready/final_metadata.json")
LEGACY_METADATA_KEY = os.getenv("LEGACY_METADATA_KEY", "shorts/state/final_metadata.json")
SSM_PREFIX = os.getenv("SSM_PARAMETER_PREFIX", "/ytshorts")
PRIVACY_STATUS = os.getenv("YOUTUBE_PRIVACY_STATUS", "private")
CATEGORY_ID = os.getenv("YOUTUBE_CATEGORY_ID", "22")


def handler(event, context):
    metadata, metadata_key = _load_metadata()
    if not metadata:
        return {"status": "noop", "reason": "metadata_not_found"}

    target = _next_due_item(metadata)
    if not target:
        return {"status": "noop", "reason": "no_due_publish_ready_item", "metadata_key": metadata_key}

    content_id = str(target.get("id") or target.get("content_id") or "")
    if not content_id:
        return {"status": "skipped", "reason": "target_missing_id"}

    youtube_config = _youtube_config()
    if not youtube_config:
        _mark_content(content_id, "UPLOAD_BLOCKED", {"upload_error": "youtube_oauth_missing"})
        return {"status": "blocked", "reason": "youtube_oauth_missing", "content_id": content_id}

    video_key = target.get("video_key") or f"videos/final/{content_id}.mp4"
    with tempfile.TemporaryDirectory() as tmp_dir:
        local_path = os.path.join(tmp_dir, f"{content_id}.mp4")
        resolved_key = _download_video(video_key, content_id, local_path)
        youtube_id = _upload_youtube(local_path, target, youtube_config)

    target["uploaded"] = True
    target["upload_status"] = "UPLOADED"
    target["uploaded_at"] = int(time.time())
    target["video_key"] = resolved_key
    platform_ids = target.get("platform_ids") or {}
    platform_ids["youtube"] = youtube_id
    target["platform_ids"] = platform_ids

    _save_metadata(metadata, metadata_key)
    _mark_content(
        content_id,
        "UPLOADED",
        {
            "platform_ids": platform_ids,
            "uploaded_at": target["uploaded_at"],
            "video_key": resolved_key,
            "upload_status": "UPLOADED",
        },
    )
    return {"status": "uploaded", "content_id": content_id, "youtube_id": youtube_id}


def _load_metadata() -> tuple[list[dict[str, Any]], str]:
    for key in (PUBLISH_METADATA_KEY, LEGACY_METADATA_KEY):
        try:
            response = s3.get_object(Bucket=BUCKET_NAME, Key=key)
            return json.loads(response["Body"].read().decode("utf-8")), key
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404", "NotFound"}:
                continue
            raise
    return [], PUBLISH_METADATA_KEY


def _save_metadata(metadata: list[dict[str, Any]], metadata_key: str) -> None:
    payload = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(Bucket=BUCKET_NAME, Key=metadata_key, Body=payload, ContentType="application/json")
    if metadata_key != LEGACY_METADATA_KEY:
        s3.put_object(Bucket=BUCKET_NAME, Key=LEGACY_METADATA_KEY, Body=payload, ContentType="application/json")


def _next_due_item(metadata: list[dict[str, Any]]) -> dict[str, Any] | None:
    now = int(time.time())
    candidates = sorted(metadata, key=lambda item: int(item.get("scheduled_publish_at") or 0))
    for item in candidates:
        if item.get("uploaded"):
            continue
        scheduled_at = int(item.get("scheduled_publish_at") or 0)
        if scheduled_at and scheduled_at > now:
            continue
        if item.get("upload_status") not in (None, "", "PUBLISH_READY"):
            continue
        return item
    return None


def _download_video(video_key: str, content_id: str, local_path: str) -> str:
    for key in (video_key, f"shorts/videos/{content_id}.mp4"):
        try:
            s3.download_file(BUCKET_NAME, key, local_path)
            return key
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404", "NotFound"}:
                continue
            raise
    raise FileNotFoundError(f"video object not found for content_id={content_id}")


def _youtube_config() -> dict[str, str] | None:
    names = {
        "client_id": f"{SSM_PREFIX}/YOUTUBE_CLIENT_ID",
        "client_secret": f"{SSM_PREFIX}/YOUTUBE_CLIENT_SECRET",
        "refresh_token": f"{SSM_PREFIX}/YOUTUBE_REFRESH_TOKEN",
        "token_uri": f"{SSM_PREFIX}/YOUTUBE_TOKEN_URI",
    }
    values: dict[str, str] = {}
    for key, name in names.items():
        try:
            value = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
        except ClientError:
            return None
        normalized = value.strip().upper()
        if key == "client_secret" and normalized in {"", "PENDING", "PUBLIC_CLIENT"}:
            values[key] = ""
            continue
        if not value or normalized == "PENDING":
            return None
        values[key] = value
    return values


def _refresh_access_token(config: dict[str, str]) -> str:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": config["refresh_token"],
        "client_id": config["client_id"],
    }
    if config.get("client_secret"):
        payload["client_secret"] = config["client_secret"]
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        config["token_uri"],
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["access_token"]


def _upload_youtube(file_path: str, item: dict[str, Any], config: dict[str, str]) -> str:
    access_token = _refresh_access_token(config)
    size = os.path.getsize(file_path)
    title = str(item.get("title") or "Untitled Short")[:100]
    description = str(item.get("description") or "")
    tags = item.get("tags") or []
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": CATEGORY_ID,
        },
        "status": {
            "privacyStatus": PRIVACY_STATUS,
            "selfDeclaredMadeForKids": os.getenv("YOUTUBE_MADE_FOR_KIDS", "0") == "1",
        },
    }
    init_request = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(size),
        },
    )
    with urllib.request.urlopen(init_request, timeout=60) as response:
        upload_url = response.headers["Location"]

    with open(file_path, "rb") as video:
        upload_request = urllib.request.Request(
            upload_url,
            data=video.read(),
            method="PUT",
            headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
        )
        with urllib.request.urlopen(upload_request, timeout=900) as response:
            payload = json.loads(response.read().decode("utf-8"))
    return payload["id"]


def _mark_content(content_id: str, status: str, extra: dict[str, Any] | None = None) -> None:
    if not CONTENT_TABLE_NAME:
        return
    table = dynamodb.Table(CONTENT_TABLE_NAME)
    names = {"#status": "status"}
    values = {":status": status, ":updated_at": int(time.time())}
    parts = ["#status = :status", "updated_at = :updated_at"]
    for idx, (key, value) in enumerate((extra or {}).items()):
        name_key = f"#k{idx}"
        value_key = f":v{idx}"
        names[name_key] = key
        values[value_key] = value
        parts.append(f"{name_key} = {value_key}")
    table.update_item(
        Key={"content_id": content_id},
        UpdateExpression="SET " + ", ".join(parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
