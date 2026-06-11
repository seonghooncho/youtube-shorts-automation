import os
import time
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
                batch.put_item(
                    Item={
                        "content_id": str(content_id),
                        "status": item_status,
                        "title": item.get("title", ""),
                        "source_url": item.get("source_url", ""),
                        "updated_at": now,
                        "scheduled_publish_at": item.get("scheduled_publish_at", 0),
                        "platform_ids": item.get("platform_ids", {}),
                        "video_key": item.get("video_key", ""),
                        "upload_status": item.get("upload_status", ""),
                    }
                )

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
            values[value_key] = value
            update_parts.append(f"{name_key} = {value_key}")
        self.table.update_item(
            Key={"content_id": str(content_id)},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
