import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, time as dt_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError


s3 = boto3.client("s3")
ssm = boto3.client("ssm")
dynamodb = boto3.resource("dynamodb")

PUBLISH_METADATA_KEY = os.getenv("PUBLISH_METADATA_KEY", "publish-ready/final_metadata.json")
LEGACY_METADATA_KEY = os.getenv("LEGACY_METADATA_KEY", "shorts/state/final_metadata.json")
SSM_PREFIX = os.getenv("SSM_PARAMETER_PREFIX", "/ytshorts")
_CONFIG_CACHE: dict[str, str] = {}


def handler(event, context):
    metadata, metadata_key = _load_metadata()
    if not metadata:
        return {"status": "noop", "reason": "metadata_not_found"}

    if _rebase_stale_queue(metadata):
        _save_metadata(metadata, metadata_key)

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
        valid_upload, block_reason = _validate_upload_candidate(local_path)
        if not valid_upload:
            target["status"] = "UPLOAD_BLOCKED"
            target["upload_status"] = "UPLOAD_BLOCKED"
            target["upload_error"] = block_reason
            target["video_key"] = resolved_key
            target["updated_at"] = int(time.time())
            _save_metadata(metadata, metadata_key)
            _mark_content(
                content_id,
                "UPLOAD_BLOCKED",
                {
                    "upload_error": block_reason,
                    "video_key": resolved_key,
                    "upload_status": "UPLOAD_BLOCKED",
                },
            )
            return {"status": "blocked", "reason": block_reason, "content_id": content_id}
        youtube_id = _upload_youtube(local_path, target, youtube_config)

    target["uploaded"] = True
    target["status"] = "UPLOADED"
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
    bucket_name = _setting("S3_BUCKET_NAME", os.getenv("BUCKET_NAME", ""))
    if not bucket_name:
        return [], PUBLISH_METADATA_KEY
    for key in (PUBLISH_METADATA_KEY, LEGACY_METADATA_KEY):
        try:
            response = s3.get_object(Bucket=bucket_name, Key=key)
            return json.loads(response["Body"].read().decode("utf-8")), key
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404", "NotFound"}:
                continue
            raise
    return [], PUBLISH_METADATA_KEY


def _save_metadata(metadata: list[dict[str, Any]], metadata_key: str) -> None:
    bucket_name = _setting("S3_BUCKET_NAME", os.getenv("BUCKET_NAME", ""))
    if not bucket_name:
        raise RuntimeError("S3_BUCKET_NAME is not configured")
    payload = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(Bucket=bucket_name, Key=metadata_key, Body=payload, ContentType="application/json")
    if metadata_key != LEGACY_METADATA_KEY:
        s3.put_object(Bucket=bucket_name, Key=LEGACY_METADATA_KEY, Body=payload, ContentType="application/json")


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


def _rebase_stale_queue(metadata: list[dict[str, Any]]) -> bool:
    rebase_stale_days = _int_setting("PUBLISH_REBASE_STALE_DAYS", 3)
    if rebase_stale_days <= 0:
        return False

    now = int(time.time())
    threshold_seconds = rebase_stale_days * 86400
    queue = [
        item
        for item in metadata
        if not item.get("uploaded") and item.get("upload_status") in (None, "", "PUBLISH_READY")
    ]
    queue.sort(key=lambda item: int(item.get("scheduled_publish_at") or 0))
    if not queue:
        return False

    oldest = int(queue[0].get("scheduled_publish_at") or 0)
    if not oldest or oldest >= now - threshold_seconds:
        return False

    timezone = ZoneInfo(_setting("SCHEDULE_TIMEZONE", "Asia/Seoul"))
    now_dt = datetime.fromtimestamp(now, timezone)
    publish_hour = _int_setting("PUBLISH_HOUR_LOCAL", 8)
    publish_minute = _int_setting("PUBLISH_MINUTE_LOCAL", 0)
    for index, item in enumerate(queue):
        if index == 0:
            scheduled_dt = now_dt
        else:
            scheduled_dt = datetime.combine(
                now_dt.date() + timedelta(days=index),
                dt_time(hour=publish_hour, minute=publish_minute),
                tzinfo=timezone,
            )
        item["scheduled_publish_at"] = int(scheduled_dt.timestamp())
        item["scheduled_publish_date"] = scheduled_dt.date().isoformat()
    return True


def _download_video(video_key: str, content_id: str, local_path: str) -> str:
    bucket_name = _setting("S3_BUCKET_NAME", os.getenv("BUCKET_NAME", ""))
    if not bucket_name:
        raise RuntimeError("S3_BUCKET_NAME is not configured")
    for key in (video_key, f"shorts/videos/{content_id}.mp4"):
        try:
            s3.download_file(bucket_name, key, local_path)
            return key
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404", "NotFound"}:
                continue
            raise
    raise FileNotFoundError(f"video object not found for content_id={content_id}")


def _validate_upload_candidate(local_path: str) -> tuple[bool, str]:
    size = os.path.getsize(local_path)
    min_upload_bytes = _int_setting("YOUTUBE_MIN_UPLOAD_BYTES", 1_048_576)
    if size < min_upload_bytes:
        return False, f"video_too_small:{size}<{min_upload_bytes}"
    return True, "ok"


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
            "categoryId": _setting("YOUTUBE_CATEGORY_ID", "22"),
        },
        "status": {
            "privacyStatus": _setting("YOUTUBE_PRIVACY_STATUS", "public"),
            "selfDeclaredMadeForKids": _bool_setting("YOUTUBE_MADE_FOR_KIDS", False),
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
    table_name = _setting("CONTENT_TABLE_NAME", os.getenv("CONTENT_TABLE_NAME", ""))
    if not table_name:
        return
    table = dynamodb.Table(table_name)
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


def _setting(name: str, default: str) -> str:
    env_value = os.getenv(name)
    if env_value is not None:
        return env_value
    if name in _CONFIG_CACHE:
        return _CONFIG_CACHE[name]
    parameter_name = f"{SSM_PREFIX}/{name}"
    try:
        value = ssm.get_parameter(Name=parameter_name, WithDecryption=True)["Parameter"]["Value"]
    except ClientError:
        value = default
    _CONFIG_CACHE[name] = value
    return value


def _int_setting(name: str, default: int) -> int:
    try:
        return int(_setting(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool_setting(name: str, default: bool) -> bool:
    raw = _setting(name, "1" if default else "0")
    return raw.strip().lower() in {"1", "true", "yes", "on"}
