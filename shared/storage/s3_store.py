import os
from pathlib import Path
from typing import Iterable, Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from shared.settings import AwsSettings


class S3Store:
    def __init__(self, settings: Optional[AwsSettings] = None):
        self.settings = settings or AwsSettings.from_env()
        self.client = self._build_client()

    def _build_client(self):
        kwargs = {"region_name": self.settings.region}
        try:
            return boto3.client("s3", **kwargs)
        except (NoCredentialsError, ClientError) as e:
            print(f"⚠️ AWS S3 설정 오류: {e}")
            return None

    @property
    def bucket_name(self) -> str:
        bucket = os.getenv("S3_BUCKET_NAME") or self.settings.s3_bucket_name
        if not bucket:
            raise RuntimeError("S3_BUCKET_NAME이 설정되지 않았습니다.")
        return bucket

    def upload_file(self, local_path: Path | str, key: str) -> None:
        if not self.client:
            raise RuntimeError("S3 클라이언트가 초기화되지 않았습니다.")
        self.client.upload_file(str(local_path), self.bucket_name, key)

    def download_file(self, key: str, local_path: Path | str) -> bool:
        if not self.client:
            raise RuntimeError("S3 클라이언트가 초기화되지 않았습니다.")
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.download_file(self.bucket_name, key, str(path))
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def list_keys(self, prefix: str) -> list[str]:
        if not self.client:
            raise RuntimeError("S3 클라이언트가 초기화되지 않았습니다.")
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        return keys

    def object_exists(self, key: str) -> bool:
        if not self.client:
            raise RuntimeError("S3 클라이언트가 초기화되지 않았습니다.")
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def upload_files(self, files: Iterable[tuple[Path, str]]) -> list[str]:
        uploaded: list[str] = []
        for local_path, key in files:
            if not local_path.exists() or not local_path.is_file():
                continue
            self.upload_file(local_path, key)
            uploaded.append(key)
        return uploaded

    def upload_directory(self, local_dir: Path | str, prefix: str) -> list[str]:
        root = Path(local_dir)
        if not root.exists():
            return []
        files = [
            (path, f"{prefix.rstrip('/')}/{path.relative_to(root).as_posix()}")
            for path in root.rglob("*")
            if path.is_file()
        ]
        return self.upload_files(files)

    def download_prefix(self, prefix: str, local_dir: Path | str) -> list[Path]:
        root = Path(local_dir)
        downloaded: list[Path] = []
        normalized = prefix.rstrip("/") + "/"
        for key in self.list_keys(normalized):
            relative = key[len(normalized):]
            if not relative:
                continue
            local_path = root / relative
            if self.download_file(key, local_path):
                downloaded.append(local_path)
        return downloaded
