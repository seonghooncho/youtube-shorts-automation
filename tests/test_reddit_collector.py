from generator.text.scrape_reddit_and_store import (
    RedditScrapeConfig,
    _post_from_child,
    _post_from_pullpush_item,
)
from generator.text import reddit_sources
from generator.text.reddit_sources import PullPushSource, SyntheticConflictSource, collect_with_fallback
from requests import ReadTimeout


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FlakyPullPushSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _Response(response)


def test_post_from_child_accepts_self_post():
    config = RedditScrapeConfig(min_chars=10)
    child = {
        "data": {
            "id": "abc123",
            "title": "AITA for testing?",
            "selftext": "This is long enough for a useful story.",
            "is_self": True,
            "permalink": "/r/AmItheAsshole/comments/abc123/test/",
            "subreddit": "AmItheAsshole",
        }
    }

    post = _post_from_child(child, config)

    assert post["id"] == "abc123"
    assert post["source_url"].endswith("/abc123/test/")
    assert post["content_char_count"] == len("This is long enough for a useful story.")
    assert post["source_detail_checked"] is False


def test_post_from_child_rejects_external_link():
    config = RedditScrapeConfig(min_chars=10)
    child = {
        "data": {
            "id": "abc123",
            "title": "AITA for testing?",
            "selftext": "This is long enough for a useful story.",
            "is_self": False,
        }
    }

    assert _post_from_child(child, config) is None


def test_post_from_pullpush_item_accepts_story():
    config = RedditScrapeConfig(min_chars=10)
    item = {
        "id": "def456",
        "title": "AITA for fallback?",
        "selftext": "This fallback story has enough text.",
        "permalink": "/r/AmItheAsshole/comments/def456/fallback/",
        "subreddit": "AmItheAsshole",
    }

    post = _post_from_pullpush_item(item, config)

    assert post["id"] == "def456"
    assert post["source_provider"] == "pullpush"


def test_post_from_pullpush_item_uses_body_fallback():
    config = RedditScrapeConfig(min_chars=10)
    item = {
        "id": "ghi789",
        "title": "AITA for fallback body?",
        "selftext": "[removed]",
        "body": "This archived body field has enough text for collection.",
        "permalink": "/r/AmItheAsshole/comments/ghi789/fallback/",
        "subreddit": "AmItheAsshole",
    }

    post = _post_from_pullpush_item(item, config)

    assert post["content"] == "This archived body field has enough text for collection."
    assert post["content_word_count"] == 9
    assert post["source_is_truncated"] is False


def test_pullpush_retries_transient_timeout():
    config = RedditScrapeConfig(
        min_chars=10,
        max_posts=1,
        min_needed=1,
        request_max_attempts=2,
        request_backoff_seconds=0,
    )
    session = _FlakyPullPushSession(
        [
            ReadTimeout("slow"),
            {
                "data": [
                    {
                        "id": "retry1",
                        "title": "AITA for retrying?",
                        "selftext": "This retry story has enough text.",
                        "permalink": "/r/AmItheAsshole/comments/retry1/retry/",
                        "subreddit": "AmItheAsshole",
                    }
                ]
            },
        ]
    )

    posts = PullPushSource(config, session=session).collect(set())

    assert session.calls == 2
    assert posts[0]["id"] == "retry1"


def test_pullpush_keeps_partial_collection_when_later_page_times_out():
    config = RedditScrapeConfig(
        min_chars=10,
        max_posts=2,
        min_needed=2,
        max_pages=2,
        request_max_attempts=1,
        request_backoff_seconds=0,
    )
    session = _FlakyPullPushSession(
        [
            {
                "data": [
                    {
                        "id": "partial1",
                        "title": "AITA for partial results?",
                        "selftext": "This partial story has enough text.",
                        "created_utc": 1000,
                        "permalink": "/r/AmItheAsshole/comments/partial1/partial/",
                        "subreddit": "AmItheAsshole",
                    }
                ]
            },
            ReadTimeout("still slow"),
        ]
    )

    posts = PullPushSource(config, session=session).collect(set())

    assert session.calls == 2
    assert [post["id"] for post in posts] == ["partial1"]


def test_synthetic_conflict_source_generates_viable_seed_shape():
    config = RedditScrapeConfig(max_posts=3, synthetic_fallback_count=3)

    posts = SyntheticConflictSource(config).collect(set())

    assert len(posts) == 3
    assert posts[0]["source_provider"] == "synthetic"
    assert posts[0]["content_word_count"] >= 90
    assert posts[0]["content_char_count"] >= 550
    assert posts[0]["source_is_truncated"] is False


def test_synthetic_conflict_source_uses_run_batch_id(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_SOURCE_BATCH_ID", "run-a")
    config = RedditScrapeConfig(max_posts=1, synthetic_fallback_count=1)
    first = SyntheticConflictSource(config).collect(set())
    assert first[0]["id"].startswith("synthetic-run-a-")

    monkeypatch.setenv("SYNTHETIC_SOURCE_BATCH_ID", "run-b")
    second = SyntheticConflictSource(config).collect({first[0]["id"]})

    assert len(second) == 1
    assert second[0]["id"].startswith("synthetic-run-b-")


def test_collect_with_fallback_uses_synthetic_when_external_sources_fail(monkeypatch):
    class _BrokenSource:
        def __init__(self, config):
            self.config = config

        def collect(self, scraped_ids):
            raise RuntimeError("source down")

    monkeypatch.setattr(reddit_sources, "RedditApiSource", _BrokenSource)
    monkeypatch.setattr(reddit_sources, "PullPushSource", _BrokenSource)
    config = RedditScrapeConfig(max_posts=2, synthetic_fallback_count=2)

    posts = collect_with_fallback(config, set())

    assert len(posts) == 2
    assert all(post["source_provider"] == "synthetic" for post in posts)


def test_collect_with_fallback_supplements_when_pullpush_has_too_few_posts(monkeypatch):
    class _BrokenReddit:
        def __init__(self, config):
            self.config = config

        def collect(self, scraped_ids):
            raise RuntimeError("reddit down")

    class _EmptyPullPush:
        def __init__(self, config):
            self.config = config

        def collect(self, scraped_ids):
            return []

    monkeypatch.setattr(reddit_sources, "RedditApiSource", _BrokenReddit)
    monkeypatch.setattr(reddit_sources, "PullPushSource", _EmptyPullPush)
    config = RedditScrapeConfig(max_posts=4, min_needed=3, synthetic_fallback_count=4)

    posts = collect_with_fallback(config, set())

    assert len(posts) == 4
    assert all(post["source_provider"] == "synthetic" for post in posts)
