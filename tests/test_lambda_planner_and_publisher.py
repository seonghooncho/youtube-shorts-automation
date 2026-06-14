import importlib.util
from io import BytesIO
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


def test_publisher_blocks_internal_upload_metadata(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    publisher = _load_module("publisher_metadata_safety_test_module", "infra/terraform/lambda/publisher.py")
    monkeypatch.setattr(publisher, "_setting", lambda name, default: default)

    reason = publisher._metadata_safety_error(
        {
            "title": "PENDING",
            "public_title": "PENDING",
            "description": "A normal story.",
            "tags": ["storytime"],
            "script": ["A normal caption line."],
            "style_variant": "neighbor_dispute",
            "script_fingerprint": "abc123",
            "predicted_retention_score": 8,
            "predicted_clarity_score": 8,
            "predicted_ai_smell_score": 3,
            "predicted_comment_score": 7,
            "critic_scores": {
                "ai_smell_score": 3,
                "native_naturalness_score": 8,
                "retention_score": 8,
                "specificity_score": 8,
            },
        }
    )

    assert reason == "unsafe_metadata:title:pending"


def test_publisher_blocks_dry_run_metadata(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    publisher = _load_module("publisher_dry_run_safety_test_module", "infra/terraform/lambda/publisher.py")

    reason = publisher._metadata_safety_error({"dry_run": True})

    assert reason == "unsafe_metadata:dry_run_item_not_allowed_downstream"


def test_publisher_skips_legacy_metadata_in_production_by_default(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("BUCKET_NAME", "test-bucket")
    publisher = _load_module("publisher_legacy_metadata_safety_test_module", "infra/terraform/lambda/publisher.py")
    requested_keys = []

    class FakeS3:
        def get_object(self, Bucket, Key):
            requested_keys.append(Key)
            if Key == publisher.PUBLISH_METADATA_KEY:
                raise publisher.ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
            return {"Body": BytesIO(b'[{"id": "legacy"}]')}

    def fake_setting(name, default):
        if name == "S3_BUCKET_NAME":
            return "test-bucket"
        if name in {"APP_ENV", "YT_ENV"}:
            return "production"
        return default

    monkeypatch.setattr(publisher, "s3", FakeS3())
    monkeypatch.setattr(publisher, "_setting", fake_setting)

    metadata, key = publisher._load_metadata()

    assert metadata == []
    assert key == publisher.PUBLISH_METADATA_KEY
    assert requested_keys == [publisher.PUBLISH_METADATA_KEY]


def test_publisher_blocks_synthetic_and_local_template_by_default(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.delenv("SCRIPT_ALLOW_SYNTHETIC_SOURCES", raising=False)
    monkeypatch.delenv("SCRIPT_LOCAL_FALLBACK_ENABLED", raising=False)
    publisher = _load_module("publisher_source_safety_test_module", "infra/terraform/lambda/publisher.py")
    monkeypatch.setattr(publisher, "_setting", lambda name, default: default)

    synthetic_reason = publisher._metadata_safety_error(
        {
            "title": "Neighbor Used My Driveway",
            "description": "A normal story.",
            "tags": ["storytime"],
            "script": ["A normal caption line."],
            "source_provider": "synthetic",
        }
    )
    fallback_reason = publisher._metadata_safety_error(
        {
            "title": "Neighbor Used My Driveway",
            "description": "A normal story.",
            "tags": ["storytime"],
            "script": ["A normal caption line."],
            "generation_fallback": "local_template",
        }
    )

    assert synthetic_reason == "unsafe_metadata:source_provider:synthetic_disabled"
    assert fallback_reason == "unsafe_metadata:generation_fallback:local_template_disabled"


def test_publisher_blocks_reddit_without_source_url(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    publisher = _load_module("publisher_source_url_safety_test_module", "infra/terraform/lambda/publisher.py")

    reason = publisher._metadata_safety_error(
        {
            "title": "He Parked In My Driveway #shorts #story",
            "public_title": "He Parked In My Driveway",
            "description": "A normal story.",
            "tags": ["storytime"],
            "script": ["A normal caption line."],
            "source_provider": "reddit",
            "style_variant": "neighbor_dispute",
            "script_fingerprint": "abc123",
            "predicted_retention_score": 8,
            "predicted_clarity_score": 8,
            "predicted_ai_smell_score": 3,
            "predicted_comment_score": 7,
            "critic_scores": {
                "ai_smell_score": 3,
                "native_naturalness_score": 8,
                "retention_score": 8,
                "specificity_score": 8,
            },
        }
    )

    assert reason == "unsafe_metadata:source_url:missing"


def test_publisher_sanitizes_upload_metadata(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    publisher = _load_module("publisher_metadata_sanitize_test_module", "infra/terraform/lambda/publisher.py")

    metadata = publisher._sanitize_upload_metadata(
        {
            "title": "My neighbor used my driveway #shorts",
            "description": "A boundary conflict.",
            "tags": ["Neighbor!", "#Storytime"],
        }
    )

    assert metadata["title"].endswith("#shorts #story")
    assert "#viral" not in metadata["title"]
    assert metadata["description"] == "A boundary conflict."
    assert metadata["tags"][:2] == ["neighbor", "storytime"]


def test_publisher_records_youtube_upload_result(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    publisher = _load_module("publisher_upload_result_test_module", "infra/terraform/lambda/publisher.py")
    target = {"id": "post1"}

    platform_ids = publisher._apply_upload_result(
        target,
        youtube_id="abc123",
        resolved_key="videos/final/post1.mp4",
        uploaded_at=123456,
        privacy_status="public",
    )

    assert platform_ids == {"youtube": "abc123"}
    assert target["uploaded"] is True
    assert target["upload_status"] == "UPLOADED"
    assert target["youtube_id"] == "abc123"
    assert target["youtube_url"] == "https://www.youtube.com/watch?v=abc123"
    assert target["privacy_status"] == "public"
