import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_planner_computes_inventory_shortfall(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("BUCKET_NAME", "test-bucket")
    planner = _load_module("planner_test_module", "infra/terraform/lambda/planner.py")
    metadata = [
        {"id": "uploaded", "uploaded": True, "upload_status": "UPLOADED", "video_key": "videos/final/a.mp4"},
        {"id": "ready-1", "uploaded": False, "upload_status": "PUBLISH_READY", "video_key": "videos/final/b.mp4"},
        {"id": "ready-2", "uploaded": False, "status": "PUBLISH_READY", "video_key": "videos/final/c.mp4"},
        {"id": "blocked", "uploaded": False, "upload_status": "UPLOAD_BLOCKED", "video_key": "videos/final/d.mp4"},
    ]
    monkeypatch.setattr(planner, "_load_metadata", lambda: (metadata, "publish-ready/final_metadata.json"))

    result = planner.handler({"days": 14, "buffer_days": 3, "max_new_items": 21}, None)

    assert result["pending_count"] == 2
    assert result["needed_new_items"] == 15
    assert result["should_generate"] is True


def test_planner_skips_when_inventory_is_full(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("BUCKET_NAME", "test-bucket")
    planner = _load_module("planner_full_test_module", "infra/terraform/lambda/planner.py")
    metadata = [
        {
            "id": f"ready-{index}",
            "uploaded": False,
            "upload_status": "PUBLISH_READY",
            "video_key": f"videos/final/{index}.mp4",
        }
        for index in range(17)
    ]
    monkeypatch.setattr(planner, "_load_metadata", lambda: (metadata, "publish-ready/final_metadata.json"))

    result = planner.handler({"days": 14, "buffer_days": 3, "max_new_items": 21}, None)

    assert result["pending_count"] == 17
    assert result["needed_new_items"] == 0
    assert result["should_generate"] is False


def test_planner_uses_ssm_config_defaults_when_event_omits_counts(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    planner = _load_module("planner_ssm_defaults_test_module", "infra/terraform/lambda/planner.py")
    monkeypatch.setattr(planner, "_load_metadata", lambda: ([], "publish-ready/final_metadata.json"))
    monkeypatch.setattr(
        planner,
        "_setting",
        lambda name, default: {
            "GENERATION_BATCH_DAYS": "14",
            "GENERATION_BUFFER_DAYS": "3",
            "GENERATION_MAX_NEW_ITEMS": "21",
        }.get(name, default),
    )

    result = planner.handler({"mode": "generate"}, None)

    assert result["days"] == 14
    assert result["buffer_days"] == 3
    assert result["max_new_items"] == 21
    assert result["needed_new_items"] == 17


def test_publisher_rejects_tiny_video_before_upload(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("YOUTUBE_MIN_UPLOAD_BYTES", "100")
    publisher = _load_module("publisher_test_module", "infra/terraform/lambda/publisher.py")
    video_path = tmp_path / "tiny.mp4"
    video_path.write_bytes(b"not a real mp4")

    valid, reason = publisher._validate_upload_candidate(str(video_path))

    assert valid is False
    assert reason == "video_too_small:14<100"


def test_publisher_uses_ssm_upload_threshold(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.delenv("YOUTUBE_MIN_UPLOAD_BYTES", raising=False)
    publisher = _load_module("publisher_ssm_test_module", "infra/terraform/lambda/publisher.py")
    monkeypatch.setattr(publisher, "_setting", lambda name, default: "100" if name == "YOUTUBE_MIN_UPLOAD_BYTES" else default)
    video_path = tmp_path / "tiny.mp4"
    video_path.write_bytes(b"not a real mp4")

    valid, reason = publisher._validate_upload_candidate(str(video_path))

    assert valid is False
    assert reason == "video_too_small:14<100"
