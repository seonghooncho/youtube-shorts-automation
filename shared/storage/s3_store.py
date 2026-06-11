import os
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from shared.settings import AwsSettings


class S3Store:
    def __init__(self, settings: Optional[AwsSettings] = None):
        self.settings = settings or AwsSettings.from_env()
        self.client = self._build_client()

    def _build_client(self):
        kwargs = {"region_name": self.settings.region}
        access_key = os.getenv("AWS_S3_ACCESS_KEY")
        secret_key = os.getenv("AWS_S3_SECRET_ACCESS_KEY")
        if access_key and secret_key:
            kwargs.update(aws_access_key_id=access_key, aws_secret_access_key=secret_key)
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
