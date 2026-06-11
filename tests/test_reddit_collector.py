from generator.text.scrape_reddit_and_store import (
    RedditScrapeConfig,
    _post_from_child,
    _post_from_pullpush_item,
)


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
