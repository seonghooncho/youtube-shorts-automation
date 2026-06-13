import json
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError


s3 = boto3.client("s3")
ssm = boto3.client("ssm")

SSM_PREFIX = os.getenv("SSM_PARAMETER_PREFIX", "/ytshorts")
PUBLISH_METADATA_KEY = os.getenv("PUBLISH_METADATA_KEY", "publish-ready/final_metadata.json")
LEGACY_METADATA_KEY = os.getenv("LEGACY_METADATA_KEY", "shorts/state/final_metadata.json")
_CONFIG_CACHE: dict[str, str] = {}


def handler(event, context):
    event = event or {}
    days = _safe_int(event.get("days"), _safe_int(_setting("GENERATION_BATCH_DAYS", "14"), 14))
    buffer_days = _safe_int(event.get("buffer_days"), _safe_int(_setting("GENERATION_BUFFER_DAYS", "3"), 3))
    default_max = days + buffer_days
    max_new_items = _safe_int(
        event.get("max_new_items"),
        _safe_int(_setting("GENERATION_MAX_NEW_ITEMS", str(default_max)), default_max),
    )

    metadata, metadata_key = _load_metadata()
    pending_items = _pending_publish_items(metadata)
    needed_new_items = min(max(0, max_new_items), max(0, days + buffer_days - len(pending_items)))

    return {
        "mode": "generate",
        "days": days,
        "buffer_days": buffer_days,
        "max_new_items": max_new_items,
        "needed_new_items": needed_new_items,
        "pending_count": len(pending_items),
        "metadata_key": metadata_key,
        "should_generate": needed_new_items > 0,
    }


def _load_metadata() -> tuple[list[dict[str, Any]], str | None]:
    bucket_name = _setting("S3_BUCKET_NAME", os.getenv("BUCKET_NAME", ""))
    if not bucket_name:
        return [], None
    for key in (PUBLISH_METADATA_KEY, LEGACY_METADATA_KEY):
        try:
            response = s3.get_object(Bucket=bucket_name, Key=key)
            return json.loads(response["Body"].read().decode("utf-8")), key
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404", "NotFound"}:
                continue
            raise
        except json.JSONDecodeError:
            return [], key
    return [], None


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
        value = os.getenv(name, default)
    _CONFIG_CACHE[name] = value
    return value


def _pending_publish_items(metadata: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in metadata
        if not item.get("uploaded")
        and item.get("upload_status") in (None, "", "PUBLISH_READY")
        and (item.get("video_key") or item.get("status") == "PUBLISH_READY")
    ]


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
