import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError


ssm = boto3.client("ssm")
dynamodb = boto3.resource("dynamodb")

SSM_PREFIX = os.getenv("SSM_PARAMETER_PREFIX", "/ytshorts")
_CONFIG_CACHE: dict[str, str] = {}


def handler(event, context):
    table_name = _setting("CONTENT_TABLE_NAME", os.getenv("CONTENT_TABLE_NAME", ""))
    if not table_name:
        return {"status": "blocked", "reason": "content_table_missing"}

    items = _uploaded_items(table_name, limit=_int_setting("METRICS_MAX_VIDEOS", 50))
    if not items:
        return {"status": "noop", "reason": "no_uploaded_items"}

    youtube_ids = [item["youtube_id"] for item in items if item.get("youtube_id")]
    data_api_stats = {}
    analytics_rows = {}
    metric_errors = []
    youtube_config = _youtube_config()
    if youtube_config:
        access_token = ""
        try:
            access_token = _refresh_access_token(youtube_config)
        except Exception as exc:
            metric_errors.append(_metric_error("oauth_refresh", exc))
        if access_token:
            try:
                data_api_stats = _fetch_video_statistics(access_token, youtube_ids)
            except Exception as exc:
                metric_errors.append(_metric_error("data_api_oauth", exc))
            try:
                analytics_rows = _fetch_analytics(access_token, youtube_ids)
            except Exception as exc:
                metric_errors.append(_metric_error("analytics", exc))
    else:
        metric_errors.append("youtube_oauth_missing")

    if not data_api_stats:
        api_key = _setting("YOUTUBE_API_KEY", "")
        if api_key:
            try:
                data_api_stats = _fetch_video_statistics_with_key(api_key, youtube_ids)
                if not data_api_stats:
                    metric_errors.append("data_api_key:no_public_items")
            except Exception as exc:
                metric_errors.append(_metric_error("data_api_key", exc))
        else:
            metric_errors.append("data_api_key_missing")

    if not data_api_stats and not analytics_rows:
        reason = "; ".join(metric_errors)[:240] or "metrics_unavailable"
        _mark_metrics_blocked(table_name, items, reason)
        return {"status": "blocked", "reason": reason, "videos": len(youtube_ids)}

    try:
        updated = _store_metrics(table_name, items, data_api_stats, analytics_rows, "; ".join(metric_errors))
    except Exception as exc:
        raise
    return {"status": "ok", "videos": len(youtube_ids), "updated": updated, "partial": bool(metric_errors)}


def _uploaded_items(table_name: str, limit: int) -> list[dict[str, Any]]:
    table = dynamodb.Table(table_name)
    response = table.scan(Limit=max(1, limit * 2))
    items = []
    for item in response.get("Items", []):
        if item.get("upload_status") != "UPLOADED" and item.get("status") != "UPLOADED":
            continue
        platform_ids = item.get("platform_ids") or {}
        youtube_id = platform_ids.get("youtube")
        if not youtube_id:
            continue
        items.append(
            {
                "content_id": str(item.get("content_id")),
                "youtube_id": str(youtube_id),
                "uploaded_at": int(item.get("uploaded_at") or 0),
            }
        )
        if len(items) >= limit:
            break
    return items


def _fetch_video_statistics(access_token: str, youtube_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not youtube_ids:
        return {}
    params = urllib.parse.urlencode(
        {
            "part": "statistics,contentDetails,status",
            "id": ",".join(youtube_ids[:50]),
            "maxResults": "50",
        }
    )
    payload = _google_get(
        f"https://www.googleapis.com/youtube/v3/videos?{params}",
        access_token,
    )
    stats = {}
    for item in payload.get("items", []):
        video_id = item.get("id")
        if video_id:
            stats[video_id] = {
                "statistics": item.get("statistics") or {},
                "contentDetails": item.get("contentDetails") or {},
                "status": item.get("status") or {},
            }
    return stats


def _fetch_video_statistics_with_key(api_key: str, youtube_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not youtube_ids:
        return {}
    params = urllib.parse.urlencode(
        {
            "key": api_key,
            "part": "statistics,contentDetails,status",
            "id": ",".join(youtube_ids[:50]),
            "maxResults": "50",
        }
    )
    payload = _google_get_without_auth(f"https://www.googleapis.com/youtube/v3/videos?{params}")
    stats = {}
    for item in payload.get("items", []):
        video_id = item.get("id")
        if video_id:
            stats[video_id] = {
                "statistics": item.get("statistics") or {},
                "contentDetails": item.get("contentDetails") or {},
                "status": item.get("status") or {},
            }
    return stats


def _fetch_analytics(access_token: str, youtube_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not youtube_ids:
        return {}
    today = datetime.now(timezone.utc).date()
    end_date = today - timedelta(days=_int_setting("METRICS_ANALYTICS_LAG_DAYS", 2))
    start_date = end_date - timedelta(days=_int_setting("METRICS_LOOKBACK_DAYS", 14))
    metrics = ",".join(
        [
            "views",
            "likes",
            "comments",
            "shares",
            "estimatedMinutesWatched",
            "averageViewDuration",
            "averageViewPercentage",
        ]
    )
    params = urllib.parse.urlencode(
        {
            "ids": "channel==MINE",
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "metrics": metrics,
            "dimensions": "video",
            "filters": "video==" + ",".join(youtube_ids[:500]),
            "maxResults": "500",
        }
    )
    payload = _google_get(
        f"https://youtubeanalytics.googleapis.com/v2/reports?{params}",
        access_token,
    )
    headers = [header["name"] for header in payload.get("columnHeaders", [])]
    rows = {}
    for row in payload.get("rows") or []:
        values = dict(zip(headers, row))
        video_id = values.pop("video", "")
        if video_id:
            rows[video_id] = values
    return rows


def _store_metrics(
    table_name: str,
    items: list[dict[str, Any]],
    data_api_stats: dict[str, dict[str, Any]],
    analytics_rows: dict[str, dict[str, Any]],
    metrics_error: str = "",
) -> int:
    table = dynamodb.Table(table_name)
    collected_at = int(time.time())
    updated = 0
    for item in items:
        content_id = item["content_id"]
        youtube_id = item["youtube_id"]
        analytics = analytics_rows.get(youtube_id) or {}
        data_api = data_api_stats.get(youtube_id, {})
        status = "METRICS_COLLECTED" if analytics else ("METRICS_PARTIAL" if data_api else "METRICS_PENDING")
        metrics = {
            "youtube_id": youtube_id,
            "collected_at": collected_at,
            "status": status,
            "data_api": data_api,
            "analytics": analytics,
            "primary_kpi": "averageViewPercentage",
            "primary_kpi_value": analytics.get("averageViewPercentage", ""),
            "metrics_error": metrics_error[:240],
        }
        table.update_item(
            Key={"content_id": content_id},
            UpdateExpression=(
                "SET youtube_metrics = :metrics, metrics_status = :status, "
                "metrics_error = :error, updated_at = :updated_at"
            ),
            ExpressionAttributeValues={
                ":metrics": _clean(metrics),
                ":status": status,
                ":error": metrics_error[:240],
                ":updated_at": collected_at,
            },
        )
        updated += 1
    return updated


def _mark_metrics_blocked(table_name: str, items: list[dict[str, Any]], reason: str) -> None:
    table = dynamodb.Table(table_name)
    now = int(time.time())
    for item in items:
        table.update_item(
            Key={"content_id": item["content_id"]},
            UpdateExpression="SET metrics_status = :status, metrics_error = :error, updated_at = :updated_at",
            ExpressionAttributeValues={
                ":status": "METRICS_BLOCKED",
                ":error": reason,
                ":updated_at": now,
            },
        )


def _metric_error(stage: str, exc: Exception) -> str:
    text = str(exc).replace("\n", " ").replace("\r", " ")
    lower = text.lower()
    if "insufficient authentication scopes" in lower or "access_token_scope_insufficient" in lower:
        reason = "insufficient_scope"
    elif "quota" in lower:
        reason = "quota"
    elif "403" in lower or "forbidden" in lower:
        reason = "forbidden"
    elif "404" in lower or "not found" in lower:
        reason = "not_found"
    elif "400" in lower or "bad request" in lower:
        reason = "bad_request"
    else:
        reason = text[:120] or exc.__class__.__name__
    return f"{stage}:{reason}"


def _google_get(url: str, access_token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"google_api_error:{exc.code}:{body[:300]}") from exc


def _google_get_without_auth(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"google_api_error:{exc.code}:{body[:300]}") from exc


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
    request = urllib.request.Request(
        config["token_uri"],
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["access_token"]


def _setting(name: str, default: str) -> str:
    if name in _CONFIG_CACHE:
        return _CONFIG_CACHE[name]
    env_value = os.getenv(name)
    if env_value is not None:
        return env_value
    try:
        value = ssm.get_parameter(Name=f"{SSM_PREFIX}/{name}", WithDecryption=True)["Parameter"]["Value"]
    except ClientError:
        return default
    _CONFIG_CACHE[name] = value
    return value


def _int_setting(name: str, default: int) -> int:
    try:
        return int(_setting(name, str(default)))
    except ValueError:
        return default


def _clean(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_clean(v) for v in value if v is not None]
    if value is None:
        return ""
    return value
