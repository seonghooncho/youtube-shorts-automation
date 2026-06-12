import json
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError


s3 = boto3.client("s3")

BUCKET_NAME = os.environ["BUCKET_NAME"]
PUBLISH_METADATA_KEY = os.getenv("PUBLISH_METADATA_KEY", "publish-ready/final_metadata.json")
LEGACY_METADATA_KEY = os.getenv("LEGACY_METADATA_KEY", "shorts/state/final_metadata.json")


def handler(event, context):
    event = event or {}
    days = _safe_int(event.get("days"), _safe_int(os.getenv("GENERATION_BATCH_DAYS"), 14))
    buffer_days = _safe_int(event.get("buffer_days"), _safe_int(os.getenv("GENERATION_BUFFER_DAYS"), 3))
    default_max = days + buffer_days
    max_new_items = _safe_int(
        event.get("max_new_items"),
        _safe_int(os.getenv("GENERATION_MAX_NEW_ITEMS"), default_max),
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
    for key in (PUBLISH_METADATA_KEY, LEGACY_METADATA_KEY):
        try:
            response = s3.get_object(Bucket=BUCKET_NAME, Key=key)
            return json.loads(response["Body"].read().decode("utf-8")), key
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404", "NotFound"}:
                continue
            raise
        except json.JSONDecodeError:
            return [], key
    return [], None


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
