import json
import os
import re
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
TITLE_HASHTAGS = ("#shorts", "#story")
DEFAULT_VIDEO_TAGS = (
    "shorts",
    "story",
    "storytime",
    "drama",
    "short story",
)
MAX_TITLE_CHARS = 100
MAX_TITLE_CONFLICT_CHARS = 68
MAX_DESCRIPTION_CHARS = 4800
MAX_TAGS = 15
_HASHTAG_RE = re.compile(r"#\w+")
_SPACE_RE = re.compile(r"\s+")
_BANNED_PUBLIC_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:aita\b|am\s+i\s+the\s+asshole\b|am\s+i\s+wrong\b|did\s+i\s+overreact\b)",
    re.IGNORECASE,
)
_AITA_CLEANUP_RE = re.compile(
    r"^\s*(?:aita|am\s+i\s+the\s+asshole|am\s+i\s+wrong|did\s+i\s+overreact)\s*(?:for|because|when|after|about)?\s*",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16}|hf_[A-Za-z0-9]{20,})"
)
_INTERNAL_MARKERS = (
    "OPENAI_API_KEY",
    "HF_TOKEN",
    "PIXABAY_API_KEY",
    "SLACK_WEBHOOK_URL",
    "AWS_ACCESS_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
    "YOUTUBE_TOKEN_URI",
    "SSM_PARAMETER",
    "PARAMETER STORE",
    "DYNAMODB",
    "TERRAFORM",
    "EVENTBRIDGE",
    "STEP FUNCTIONS",
    "LAMBDA",
    "PENDING",
    "UNDEFINED",
    "NULL",
    "NAN",
)


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

    metadata_error = _metadata_safety_error(target)
    if metadata_error:
        _block_upload(metadata, metadata_key, target, content_id, metadata_error)
        return {"status": "blocked", "reason": metadata_error, "content_id": content_id}

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
            target["video_key"] = resolved_key
            _block_upload(metadata, metadata_key, target, content_id, block_reason, {"video_key": resolved_key})
            return {"status": "blocked", "reason": block_reason, "content_id": content_id}
        youtube_id = _upload_youtube(local_path, target, youtube_config)

    uploaded_at = int(time.time())
    privacy_status = _setting("YOUTUBE_PRIVACY_STATUS", "public")
    platform_ids = _apply_upload_result(target, youtube_id, resolved_key, uploaded_at, privacy_status)

    _save_metadata(metadata, metadata_key)
    _mark_content(
        content_id,
        "UPLOADED",
        {
            "platform_ids": platform_ids,
            "youtube_id": youtube_id,
            "youtube_url": _youtube_url(youtube_id),
            "privacy_status": privacy_status,
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
        if key == LEGACY_METADATA_KEY and _is_production_env() and not _bool_setting("ALLOW_LEGACY_UPLOAD_METADATA", False):
            continue
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
    candidate_keys = [video_key]
    if not _is_production_env() or _bool_setting("ALLOW_LEGACY_VIDEO_FALLBACK", False):
        candidate_keys.append(f"shorts/videos/{content_id}.mp4")
    for key in candidate_keys:
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


def _apply_upload_result(
    target: dict[str, Any],
    youtube_id: str,
    resolved_key: str,
    uploaded_at: int,
    privacy_status: str,
) -> dict[str, str]:
    platform_ids = target.get("platform_ids") or {}
    platform_ids["youtube"] = youtube_id

    target["uploaded"] = True
    target["status"] = "UPLOADED"
    target["upload_status"] = "UPLOADED"
    target["uploaded_at"] = uploaded_at
    target["video_key"] = resolved_key
    target["platform_ids"] = platform_ids
    target["youtube_id"] = youtube_id
    target["youtube_url"] = _youtube_url(youtube_id)
    target["privacy_status"] = privacy_status
    return platform_ids


def _youtube_url(youtube_id: str) -> str:
    return f"https://www.youtube.com/watch?v={youtube_id}"


def _block_upload(
    metadata: list[dict[str, Any]],
    metadata_key: str,
    target: dict[str, Any],
    content_id: str,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> None:
    target["status"] = "UPLOAD_BLOCKED"
    target["upload_status"] = "UPLOAD_BLOCKED"
    target["upload_error"] = reason
    target["updated_at"] = int(time.time())
    _save_metadata(metadata, metadata_key)
    payload = {"upload_error": reason, "upload_status": "UPLOAD_BLOCKED"}
    payload.update(extra or {})
    _mark_content(content_id, "UPLOAD_BLOCKED", payload)


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
    metadata = _sanitize_upload_metadata(item)
    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata["tags"],
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


def _metadata_safety_error(item: dict[str, Any]) -> str:
    if item.get("dry_run") is True:
        return "unsafe_metadata:dry_run_item_not_allowed_downstream"
    source_provider = str(item.get("source_provider") or item.get("source_authenticity") or "").strip().lower()
    if _is_production_env() and source_provider not in {"reddit", "pullpush", "synthetic"} and not _bool_setting("ALLOW_UNKNOWN_SOURCE_PROVIDER", False):
        return "unsafe_metadata:source_provider:unknown"
    if source_provider == "synthetic" and not _bool_setting("ALLOW_SYNTHETIC_IN_PRODUCTION", False):
        return "unsafe_metadata:source_provider:synthetic_disabled"
    if source_provider in {"reddit", "pullpush"} and not str(item.get("source_url") or "").strip() and not _bool_setting("ALLOW_MISSING_SOURCE_URL", False):
        return "unsafe_metadata:source_url:missing"
    if (
        source_provider in {"reddit", "pullpush"}
        and not _source_context_text(item)
        and _is_production_env()
        and not _bool_setting("ALLOW_MISSING_SOURCE_CONTEXT", False)
    ):
        return "unsafe_metadata:source_context:missing"
    if item.get("generation_fallback") == "local_template" and not _bool_setting("ALLOW_LOCAL_TEMPLATE_UPLOAD", False):
        return "unsafe_metadata:generation_fallback:local_template_disabled"
    if not str(item.get("public_title") or "").strip():
        return "unsafe_metadata:public_title:missing"
    if _BANNED_PUBLIC_TITLE_PREFIX_RE.search(str(item.get("public_title") or item.get("title") or "")):
        return "unsafe_metadata:public_title:aita_prefix"
    if "#viral" in str(item.get("public_title") or "").lower() or "#viral" in str(item.get("title") or "").lower():
        return "unsafe_metadata:title:viral_hashtag"
    if not str(item.get("style_variant") or "").strip():
        return "unsafe_metadata:style_variant:missing"
    if not str(item.get("script_fingerprint") or "").strip():
        return "unsafe_metadata:script_fingerprint:missing"
    predicted_error = _predicted_safety_error(item)
    if predicted_error:
        return predicted_error
    critic_error = _critic_safety_error(item.get("critic_scores") or {})
    if critic_error:
        return critic_error
    caption_error = _caption_alignment_error(item)
    if caption_error:
        return caption_error
    fields = {
        "title": item.get("title", ""),
        "description": item.get("description", ""),
        "tags": " ".join(str(tag or "") for tag in item.get("tags") or []),
        "script": " ".join(str(line or "") for line in item.get("script") or []),
    }
    for field_name, value in fields.items():
        reason = _unsafe_public_text_reason(str(value or ""))
        if reason:
            return f"unsafe_metadata:{field_name}:{reason}"
    try:
        _sanitize_upload_metadata(item)
    except ValueError as exc:
        return str(exc)
    return ""


def _source_context_text(item: dict[str, Any]) -> str:
    return str(item.get("source_content_excerpt") or item.get("source_content") or "").strip()


def _caption_alignment_error(item: dict[str, Any]) -> str:
    chunks = [str(chunk or "").strip() for chunk in item.get("caption_chunks") or [] if str(chunk or "").strip()]
    if not chunks:
        return ""
    narration = str(item.get("tts_text") or " ".join(str(line or "") for line in item.get("voiceover_lines") or item.get("script") or [])).strip()
    narration_tokens = _word_tokens(narration)
    cursor = 0
    max_gap = _int_setting("CAPTION_CHUNK_MAX_TOKEN_GAP", 2)
    for index, chunk in enumerate(chunks, start=1):
        chunk_tokens = _word_tokens(chunk)
        if not chunk_tokens:
            continue
        span_start, span_end = _find_token_span(chunk_tokens, narration_tokens, cursor, max_gap)
        if span_start < 0:
            return f"unsafe_metadata:caption_chunks_not_in_tts_text:chunk_{index}"
        cursor = span_end + 1
    return ""


def _find_token_span(chunk_tokens: list[str], narration_tokens: list[str], start: int, max_gap: int) -> tuple[int, int]:
    for candidate_start in range(start, len(narration_tokens)):
        if narration_tokens[candidate_start] != chunk_tokens[0]:
            continue
        pos = candidate_start
        ok = True
        for token in chunk_tokens[1:]:
            found = -1
            search_end = min(len(narration_tokens), pos + max_gap + 2)
            for probe in range(pos + 1, search_end):
                if narration_tokens[probe] == token:
                    found = probe
                    break
            if found < 0:
                ok = False
                break
            pos = found
        if ok:
            return candidate_start, pos
    return -1, -1


def _word_tokens(text: str) -> list[str]:
    return [match.group(0).lower().strip("'") for match in re.finditer(r"[A-Za-z0-9']+", str(text or ""))]


def _predicted_safety_error(item: dict[str, Any]) -> str:
    if _float_like(item.get("predicted_retention_score")) < 8:
        return "unsafe_metadata:predicted_retention_score"
    if _float_like(item.get("predicted_clarity_score")) < 8:
        return "unsafe_metadata:predicted_clarity_score"
    if _float_like(item.get("predicted_ai_smell_score")) > 3:
        return "unsafe_metadata:predicted_ai_smell_score"
    if _float_like(item.get("predicted_comment_score")) < 7 and not _bool_setting("ALLOW_LOW_PREDICTED_COMMENT_SCORE", False):
        return "unsafe_metadata:predicted_comment_score"
    return ""


def _critic_safety_error(scores: dict[str, Any]) -> str:
    if not scores:
        return "unsafe_metadata:critic_scores:missing"
    if _float_like(scores.get("ai_smell_score")) > 3:
        return "unsafe_metadata:critic_ai_smell_score"
    if _float_like(scores.get("native_naturalness_score")) < 8:
        return "unsafe_metadata:critic_native_naturalness_score"
    if _float_like(scores.get("retention_score")) < 8:
        return "unsafe_metadata:critic_retention_score"
    if _float_like(scores.get("specificity_score")) < 8:
        return "unsafe_metadata:critic_specificity_score"
    return ""


def _sanitize_upload_metadata(item: dict[str, Any]) -> dict[str, Any]:
    title = _format_youtube_title(str(item.get("public_title") or item.get("title") or "Untitled Short"))
    description = _clean_public_text(str(item.get("description") or ""))[:MAX_DESCRIPTION_CHARS].strip()
    tags = _merge_youtube_tags(item.get("tags") or [])
    if not title.strip():
        raise ValueError("unsafe_metadata:title:empty")
    if not description:
        description = _format_youtube_description("A fast storytime Short about a relatable everyday conflict.")
    return {"title": title, "description": description, "tags": tags}


def _format_youtube_title(title: str) -> str:
    clean_title = _public_title(_strip_hashtags(_clean_public_text(title)).strip(" .,-") or "Story")
    suffix = " ".join(_normalize_hashtag(tag) for tag in TITLE_HASHTAGS if _normalize_hashtag(tag))
    budget = min(MAX_TITLE_CONFLICT_CHARS, MAX_TITLE_CHARS - len(suffix) - 1)
    if len(clean_title) > budget:
        clean_title = _truncate_at_word(clean_title, budget).strip(" .,-")
    return f"{clean_title} {suffix}".strip()[:MAX_TITLE_CHARS]


def _public_title(title: str) -> str:
    clean_title = _SPACE_RE.sub(" ", str(title or "")).strip(" .,-")
    if _BANNED_PUBLIC_TITLE_PREFIX_RE.search(clean_title):
        clean_title = _AITA_CLEANUP_RE.sub("", clean_title).strip(" .,-")
    if clean_title == clean_title.upper() or clean_title == clean_title.lower():
        clean_title = clean_title.title()
    else:
        clean_title = clean_title[:1].upper() + clean_title[1:]
    if not clean_title:
        clean_title = "A Family Bill Turned Into A Group Argument"
    if _BANNED_PUBLIC_TITLE_PREFIX_RE.search(clean_title):
        clean_title = "The Argument Started Before I Even Sat Down"
    return clean_title


def _format_youtube_description(description: str) -> str:
    clean_description = _strip_hashtags(_clean_public_text(description)).strip()
    hashtag_line = " ".join(_normalize_hashtag(tag) for tag in TITLE_HASHTAGS if _normalize_hashtag(tag))
    return "\n\n".join(part for part in (clean_description, hashtag_line) if part).strip()[:MAX_DESCRIPTION_CHARS]


def _merge_youtube_tags(tags: list[Any]) -> list[str]:
    merged: list[str] = []
    for tag in list(tags or []) + list(DEFAULT_VIDEO_TAGS):
        normalized = _normalize_tag(tag)
        if not normalized or normalized in merged:
            continue
        merged.append(normalized)
        if len(merged) >= MAX_TAGS:
            break
    return merged


def _strip_hashtags(text: str) -> str:
    return _SPACE_RE.sub(" ", _HASHTAG_RE.sub("", str(text or ""))).strip()


def _normalize_hashtag(tag: str) -> str:
    normalized = _normalize_tag(tag)
    if not normalized:
        return ""
    return "#" + normalized.replace(" ", "")


def _normalize_tag(tag: Any) -> str:
    normalized = _clean_public_text(tag).strip().lower().lstrip("#")
    normalized = _SPACE_RE.sub(" ", normalized)
    normalized = re.sub(r"[^a-z0-9 #_-]", "", normalized)
    return normalized[:100].strip()


def _float_like(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clean_public_text(text: Any) -> str:
    cleaned = str(text or "").replace("\x00", " ")
    cleaned = _SECRET_VALUE_RE.sub("[removed]", cleaned)
    cleaned = _SPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _unsafe_public_text_reason(text: str) -> str:
    if _SECRET_VALUE_RE.search(text):
        return "secret_like_value"
    upper = text.upper()
    for marker in _INTERNAL_MARKERS:
        if "_" in marker or " " in marker:
            matched = marker in upper
        else:
            matched = bool(re.search(rf"\b{re.escape(marker)}\b", upper))
        if matched:
            return marker.lower().replace(" ", "_")
    return ""


def _truncate_at_word(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    truncated = text[:limit].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated or text[:limit].rstrip()


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


def _is_production_env() -> bool:
    return any(_setting(name, os.getenv(name, "")).strip().lower() == "production" for name in ("APP_ENV", "YT_ENV"))
