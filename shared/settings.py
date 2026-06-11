import os
from dataclasses import dataclass


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AwsSettings:
    region: str = "ap-northeast-2"
    s3_bucket_name: str = ""

    @classmethod
    def from_env(cls) -> "AwsSettings":
        return cls(
            region=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or cls.region,
            s3_bucket_name=os.getenv("S3_BUCKET_NAME", ""),
        )


@dataclass(frozen=True)
class UploadSettings:
    target_platforms: frozenset[str] = frozenset({"youtube"})
    youtube_privacy_status: str = "private"
    instagram_enabled: bool = False
    tiktok_enabled: bool = False

    @classmethod
    def from_env(cls) -> "UploadSettings":
        platforms = frozenset(
            item.strip().lower()
            for item in os.getenv("TARGET_PLATFORMS", "youtube").split(",")
            if item.strip()
        )
        return cls(
            target_platforms=platforms or frozenset({"youtube"}),
            youtube_privacy_status=os.getenv("YOUTUBE_PRIVACY_STATUS", cls.youtube_privacy_status),
            instagram_enabled=_bool_env("INSTAGRAM_UPLOAD_ENABLED"),
            tiktok_enabled=_bool_env("TIKTOK_UPLOAD_ENABLED"),
        )
