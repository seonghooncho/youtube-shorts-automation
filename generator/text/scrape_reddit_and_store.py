import json
from typing import Set

from generator.text.reddit_sources import (
    RedditScrapeConfig,
    collect_with_fallback,
    post_from_pullpush_item as _post_from_pullpush_item,
    post_from_reddit_child as _post_from_child,
)
from shared.utils.config import RAW_POSTS_FILE, SCRAPED_POST_LIST_FILE


def _load_scraped_ids() -> Set[str]:
    if not SCRAPED_POST_LIST_FILE.exists():
        return set()
    with open(SCRAPED_POST_LIST_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return set(raw) if isinstance(raw, list) else set(raw.keys())


def scrape_reddit_and_store():
    config = RedditScrapeConfig.from_env()
    scraped_ids = _load_scraped_ids()
    new_posts = collect_with_fallback(config, scraped_ids)

    RAW_POSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RAW_POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(new_posts[: config.max_posts], f, ensure_ascii=False, indent=2)

    scraped_ids.update(post["id"] for post in new_posts)
    with open(SCRAPED_POST_LIST_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(scraped_ids), f, ensure_ascii=False, indent=2)

    print(f"✅ Reddit 수집 완료. {len(new_posts)}건 저장됨 → {RAW_POSTS_FILE}")
    if new_posts:
        print(f"📌 첫 번째 ID: {new_posts[0]['id']}")


if __name__ == "__main__":
    scrape_reddit_and_store()
