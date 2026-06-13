import os
import time
from decimal import Decimal
from typing import Any, Dict, Iterable, Optional

import boto3

from shared.settings import AwsSettings


class ContentRepository:
    def __init__(self, table_name: Optional[str] = None, settings: Optional[AwsSettings] = None):
        self.table_name = table_name or os.getenv("CONTENT_TABLE_NAME", "")
        self.settings = settings or AwsSettings.from_env()
        self.table = None
        if self.table_name:
            self.table = boto3.resource("dynamodb", region_name=self.settings.region).Table(self.table_name)

    def enabled(self) -> bool:
        return self.table is not None

    def upsert_sources(self, posts: Iterable[Dict[str, Any]]) -> None:
        if not self.table:
            return
        now = int(time.time())
        with self.table.batch_writer() as batch:
            for post in posts:
                source_id = post.get("id")
                if not source_id:
                    continue
                record = {
                    "content_id": f"source#{source_id}",
                    "status": "SOURCE_SEEN",
                    "source_id": str(source_id),
                    "title": post.get("title", ""),
                    "source_url": post.get("source_url", ""),
                    "source_provider": post.get("source_provider", ""),
                    "source_subreddit": post.get("subreddit", ""),
                    "source_hash": post.get("content_hash", ""),
                    "source_score": post.get("score"),
                    "content_char_count": post.get("content_char_count"),
                    "content_word_count": post.get("content_word_count"),
                    "source_is_truncated": post.get("source_is_truncated", False),
                    "source_detail_checked": post.get("source_detail_checked", False),
                    "updated_at": now,
                    "scheduled_publish_at": 0,
                }
                batch.put_item(Item=_clean_for_dynamodb(record))

    def upsert_items(self, items: Iterable[Dict[str, Any]], status: str) -> None:
        if not self.table:
            return
        now = int(time.time())
        with self.table.batch_writer() as batch:
            for item in items:
                content_id = item.get("id")
                if not content_id:
                    continue
                item_status = "UPLOADED" if item.get("uploaded") or item.get("upload_status") == "UPLOADED" else status
                record = {
                    "content_id": str(content_id),
                    "status": item_status,
                    "title": item.get("title", ""),
                    "source_url": item.get("source_url", ""),
                    "updated_at": now,
                    "scheduled_publish_at": item.get("scheduled_publish_at", 0),
                    "platform_ids": item.get("platform_ids", {}),
                    "uploaded_at": item.get("uploaded_at", 0),
                    "video_key": item.get("video_key", ""),
                    "upload_status": item.get("upload_status", ""),
                    "source_provider": item.get("source_provider", ""),
                    "source_subreddit": item.get("source_subreddit", ""),
                    "source_hash": item.get("source_hash", ""),
                    "source_archetype": item.get("source_archetype", ""),
                    "source_score": item.get("source_score"),
                    "source_scorecard": item.get("source_scorecard", {}),
                    "hook_type": item.get("hook_type", ""),
                    "first_2_seconds": item.get("first_2_seconds", ""),
                    "turning_point": item.get("turning_point", ""),
                    "payoff_line": item.get("payoff_line", ""),
                    "viewer_question": item.get("viewer_question", ""),
                    "marketability_score": item.get("marketability_score"),
                    "script_char_count": item.get("script_char_count"),
                    "quality_warnings": item.get("quality_warnings", []),
                    "bg_strategy": item.get("bg_strategy", ""),
                    "bg_queries": item.get("bg_queries", []),
                    "pixabay_ids": item.get("pixabay_ids", []),
                    "caption_style_version": item.get("caption_style_version", "centered-anton-v2"),
                    "youtube_metrics": item.get("youtube_metrics", {}),
                }
                batch.put_item(Item=_clean_for_dynamodb(record))

    def mark_status(self, content_id: str, status: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if not self.table:
            return
        names = {"#status": "status"}
        values = {":status": status, ":updated_at": int(time.time())}
        update_parts = ["#status = :status", "updated_at = :updated_at"]
        for idx, (key, value) in enumerate((extra or {}).items()):
            name_key = f"#k{idx}"
            value_key = f":v{idx}"
            names[name_key] = key
            values[value_key] = _clean_for_dynamodb(value)
            update_parts.append(f"{name_key} = {value_key}")
        self.table.update_item(
            Key={"content_id": str(content_id)},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def winning_patterns(self, limit: int = 100) -> Dict[str, Any]:
        if not self.table:
            return {}
        response = self.table.scan(Limit=max(1, limit))
        buckets: dict[str, dict[str, list[float]]] = {
            "source_archetype": {},
            "hook_type": {},
            "bg_strategy": {},
        }
        for item in response.get("Items", []):
            metrics = item.get("youtube_metrics") or {}
            analytics = metrics.get("analytics") or {}
            kpi = _float_like(metrics.get("primary_kpi_value") or analytics.get("averageViewPercentage"))
            if kpi <= 0:
                continue
            for field_name in buckets:
                key = str(item.get(field_name) or "").strip()
                if not key:
                    continue
                buckets[field_name].setdefault(key, []).append(kpi)
        return {
            field_name: _rank_pattern_bucket(values)
            for field_name, values in buckets.items()
        }


def _clean_for_dynamodb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {str(k): _clean_for_dynamodb(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_clean_for_dynamodb(item) for item in value if item is not None]
    if value is None:
        return ""
    return value


def _float_like(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _rank_pattern_bucket(values: dict[str, list[float]]) -> list[dict[str, Any]]:
    ranked = []
    for key, scores in values.items():
        if not scores:
            continue
        ranked.append(
            {
                "value": key,
                "count": len(scores),
                "avg_average_view_percentage": round(sum(scores) / len(scores), 2),
            }
        )
    ranked.sort(key=lambda item: (item["avg_average_view_percentage"], item["count"]), reverse=True)
    return ranked[:5]
