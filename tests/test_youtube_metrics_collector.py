import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_metrics_module(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    spec = importlib.util.spec_from_file_location(
        "metrics_collector_test_module",
        ROOT / "infra/terraform/lambda/metrics_collector.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_fetch_analytics_maps_rows_by_video(monkeypatch):
    metrics = _load_metrics_module(monkeypatch)
    captured = {}

    def _fake_google_get(url, access_token):
        captured["url"] = url
        captured["access_token"] = access_token
        return {
            "columnHeaders": [
                {"name": "video"},
                {"name": "views"},
                {"name": "averageViewDuration"},
                {"name": "averageViewPercentage"},
            ],
            "rows": [["abc123", 100, 31.5, 72.4]],
        }

    monkeypatch.setattr(metrics, "_google_get", _fake_google_get)
    monkeypatch.setattr(metrics, "_int_setting", lambda name, default: default)

    rows = metrics._fetch_analytics("token", ["abc123"])

    assert rows["abc123"]["views"] == 100
    assert rows["abc123"]["averageViewPercentage"] == 72.4
    assert "youtubeanalytics.googleapis.com/v2/reports" in captured["url"]
    assert "dimensions=video" in captured["url"]
    assert captured["access_token"] == "token"


def test_store_metrics_updates_content_records(monkeypatch):
    metrics = _load_metrics_module(monkeypatch)
    calls = []

    class _Table:
        def update_item(self, **kwargs):
            calls.append(kwargs)

    class _Dynamo:
        def Table(self, table_name):
            assert table_name == "content"
            return _Table()

    monkeypatch.setattr(metrics, "dynamodb", _Dynamo())

    updated = metrics._store_metrics(
        "content",
        [{"content_id": "post1", "youtube_id": "abc123"}],
        {"abc123": {"statistics": {"viewCount": "101"}}},
        {"abc123": {"views": 100, "averageViewPercentage": 72.4}},
    )

    assert updated == 1
    values = calls[0]["ExpressionAttributeValues"]
    assert values[":status"] == "METRICS_COLLECTED"
    assert values[":metrics"]["primary_kpi"] == "averageViewPercentage"
    assert str(values[":metrics"]["analytics"]["averageViewPercentage"]) == "72.4"
