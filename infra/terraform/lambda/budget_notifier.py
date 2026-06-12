import json
import os
import urllib.request

import boto3
from botocore.exceptions import ClientError


ssm = boto3.client("ssm")

SSM_PREFIX = os.getenv("SSM_PARAMETER_PREFIX", "/ytshorts")
SLACK_WEBHOOK_PARAMETER = os.getenv("SLACK_WEBHOOK_PARAMETER", f"{SSM_PREFIX}/SLACK_WEBHOOK_URL")


def handler(event, context):
    webhook = _slack_webhook()
    if not webhook:
        return {"status": "skipped", "reason": "slack_webhook_missing"}

    message = _format_event(event)
    body = json.dumps({"text": message}).encode("utf-8")
    request = urllib.request.Request(
        webhook,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()
    return {"status": "sent"}


def _slack_webhook() -> str | None:
    try:
        value = ssm.get_parameter(Name=SLACK_WEBHOOK_PARAMETER, WithDecryption=True)["Parameter"]["Value"]
    except ClientError:
        return None
    if not value or value.strip().upper() == "PENDING":
        return None
    return value


def _format_event(event: dict) -> str:
    records = event.get("Records") or []
    if records and records[0].get("Sns"):
        sns = records[0]["Sns"]
        subject = sns.get("Subject") or "AWS alert"
        message = sns.get("Message") or ""
        return f":warning: {subject}\n```{message[:3000]}```"

    detail_type = event.get("detail-type", "AWS event")
    detail = event.get("detail", {})
    return f":warning: {detail_type}\n```{json.dumps(detail, ensure_ascii=False, indent=2)[:3000]}```"
