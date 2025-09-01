# uploader/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any


class SkipUpload(Exception):
    """안전하게 업로드를 건너뛰고 싶을 때 던지는 예외."""
    pass


class Uploader(ABC):
    @abstractmethod
    def upload(self, video_path: Path, item: Dict[str, Any]) -> str:
        """
        성공 시 플랫폼별 videoId/URL 반환.
        실패 시 예외 발생. 안전 스킵은 SkipUpload 예외 사용.
        """
        raise NotImplementedError
