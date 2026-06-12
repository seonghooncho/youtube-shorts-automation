import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Protocol, Set
from urllib.parse import urlparse

import requests
from requests import HTTPError, Session

from generator.text.source_integrity import normalize_story_text, select_story_content, source_integrity_fields


@dataclass(frozen=True)
class RedditScrapeConfig:
    subreddit: str = "AmItheAsshole"
    sort: str = "hot"
    max_posts: int = 30
    min_needed: int = 15
    min_chars: int = 300
    max_pages: int = 10
    allow_over18: bool = False
    rescrape: bool = False
    user_agent: str = "youtube-shorts-automation/1.0"
    request_delay_seconds: float = 0.8
    detail_request_delay_seconds: float = 0.15
    fetch_post_details: bool = True
    fallback_provider: str = "pullpush"

    @classmethod
    def from_env(cls) -> "RedditScrapeConfig":
        return cls(
            subreddit=os.getenv("REDDIT_SUBREDDIT", cls.subreddit),
            sort=os.getenv("REDDIT_SORT", cls.sort),
            max_posts=int(os.getenv("REDDIT_MAX_POSTS", cls.max_posts)),
            min_needed=int(os.getenv("REDDIT_MIN_NEEDED", cls.min_needed)),
            min_chars=int(os.getenv("REDDIT_MIN_CHARS", cls.min_chars)),
            max_pages=int(os.getenv("REDDIT_MAX_PAGES", cls.max_pages)),
            allow_over18=os.getenv("REDDIT_ALLOW_OVER18", "0") == "1",
            rescrape=os.getenv("RESCRAPE", "0") == "1",
            user_agent=os.getenv("REDDIT_USER_AGENT", cls.user_agent),
            request_delay_seconds=float(os.getenv("REDDIT_REQUEST_DELAY_SECONDS", cls.request_delay_seconds)),
            detail_request_delay_seconds=float(
                os.getenv("REDDIT_DETAIL_REQUEST_DELAY_SECONDS", cls.detail_request_delay_seconds)
            ),
            fetch_post_details=os.getenv("REDDIT_FETCH_POST_DETAILS", "1") != "0",
            fallback_provider=os.getenv("REDDIT_FALLBACK_PROVIDER", cls.fallback_provider),
        )


class RedditSource(Protocol):
    def collect(self, scraped_ids: Set[str]) -> List[Dict[str, str]]:
        ...


class RedditApiSource:
    def __init__(self, config: RedditScrapeConfig, session: Optional[Session] = None):
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config.user_agent})
        self._access_token: Optional[str] = None

    def _oauth_token(self) -> Optional[str]:
        if self._access_token:
            return self._access_token

        client_id = os.getenv("REDDIT_CLIENT_ID")
        client_secret = os.getenv("REDDIT_CLIENT_SECRET")
        if not client_id or not client_secret:
            return None

        response = self.session.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": self.config.user_agent},
            timeout=20,
        )
        response.raise_for_status()
        self._access_token = response.json()["access_token"]
        return self._access_token

    def _listing(self, after: Optional[str]) -> Dict:
        token = self._oauth_token()
        params = {"limit": 100}
        if after:
            params["after"] = after

        if token:
            url = f"https://oauth.reddit.com/r/{self.config.subreddit}/{self.config.sort}"
            headers = {"Authorization": f"Bearer {token}", "User-Agent": self.config.user_agent}
        else:
            url = f"https://www.reddit.com/r/{self.config.subreddit}/{self.config.sort}.json"
            headers = {"User-Agent": self.config.user_agent}

        response = self.session.get(url, params=params, headers=headers, timeout=30)
        try:
            response.raise_for_status()
        except HTTPError as e:
            if response.status_code == 403 and not token:
                raise RuntimeError(
                    "Reddit public JSON endpoint returned 403. Set REDDIT_CLIENT_ID, "
                    "REDDIT_CLIENT_SECRET, and REDDIT_USER_AGENT for OAuth collection."
                ) from e
            raise
        return response.json()

    def _detail_listing(self, permalink: str) -> Dict:
        token = self._oauth_token()
        clean_path = permalink.rstrip("/")
        if not clean_path.startswith("/"):
            clean_path = f"/{clean_path}"

        if token:
            url = f"https://oauth.reddit.com{clean_path}"
            headers = {"Authorization": f"Bearer {token}", "User-Agent": self.config.user_agent}
        else:
            url = f"https://www.reddit.com{clean_path}.json"
            headers = {"User-Agent": self.config.user_agent}

        response = self.session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def _refresh_from_detail(self, post: Dict[str, str]) -> Dict[str, str]:
        permalink = _permalink_from_post(post)
        if not permalink:
            post.update({"source_detail_checked": False, "source_detail_error": "missing permalink"})
            return post

        try:
            payload = self._detail_listing(permalink)
            child = _first_post_child(payload)
            data = child.get("data") or {}
            detail_content = select_story_content(data)
            current_content = normalize_story_text(post.get("content", ""))
            if detail_content and len(detail_content) >= len(current_content):
                detail_improved = detail_content != current_content
                post["content"] = detail_content
                post.update(source_integrity_fields(detail_content, detail_checked=True, detail_improved=detail_improved))
            else:
                post.update(source_integrity_fields(current_content, detail_checked=True, detail_improved=False))
            post["source_detail_error"] = ""
        except Exception as e:
            current_content = normalize_story_text(post.get("content", ""))
            post.update(source_integrity_fields(current_content, detail_checked=False, detail_improved=False))
            post["source_detail_error"] = str(e)[:240]
        return post

    def collect(self, scraped_ids: Set[str]) -> List[Dict[str, str]]:
        posts: List[Dict[str, str]] = []
        seen_in_run: Set[str] = set()
        after: Optional[str] = None

        for page in range(1, self.config.max_pages + 1):
            payload = self._listing(after)
            listing = payload.get("data") or {}
            children: Iterable[Dict] = listing.get("children") or []
            page_ok = 0
            page_skipped = 0

            for child in children:
                post = post_from_reddit_child(child, self.config)
                if not post:
                    page_skipped += 1
                    continue

                if _should_skip(post["id"], scraped_ids, seen_in_run, self.config):
                    page_skipped += 1
                    continue

                if self.config.fetch_post_details:
                    post = self._refresh_from_detail(post)
                    _sleep_detail(self.config)

                posts.append(post)
                seen_in_run.add(post["id"])
                page_ok += 1
                if len(posts) >= self.config.max_posts:
                    break

            print(f"🔎 reddit page={page} accepted={page_ok} skipped={page_skipped} total={len(posts)}")
            if len(posts) >= self.config.max_posts:
                break

            after = listing.get("after")
            if not after or len(posts) >= self.config.min_needed:
                break
            _sleep(self.config)

        return posts


class PullPushSource:
    def __init__(self, config: RedditScrapeConfig, session: Optional[Session] = None):
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config.user_agent})

    def _search(self, before: Optional[int] = None) -> Dict:
        params = {
            "subreddit": self.config.subreddit,
            "size": 100,
            "sort": "desc",
        }
        if before:
            params["before"] = before
        response = self.session.get(
            "https://api.pullpush.io/reddit/search/submission/",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def collect(self, scraped_ids: Set[str]) -> List[Dict[str, str]]:
        posts: List[Dict[str, str]] = []
        seen_in_run: Set[str] = set()
        before: Optional[int] = None

        for page in range(1, self.config.max_pages + 1):
            payload = self._search(before=before)
            items = payload.get("data") or []
            page_ok = 0
            page_skipped = 0

            for item in items:
                post = post_from_pullpush_item(item, self.config)
                if not post:
                    page_skipped += 1
                    continue

                if _should_skip(post["id"], scraped_ids, seen_in_run, self.config):
                    page_skipped += 1
                    continue

                posts.append(post)
                seen_in_run.add(post["id"])
                page_ok += 1
                if len(posts) >= self.config.max_posts:
                    break

            print(f"🔎 pullpush page={page} accepted={page_ok} skipped={page_skipped} total={len(posts)}")
            if len(posts) >= self.config.max_posts or len(posts) >= self.config.min_needed:
                break

            timestamps = [item.get("created_utc") for item in items if item.get("created_utc")]
            if not timestamps:
                break
            before = int(min(timestamps)) - 1
            _sleep(self.config)

        return posts


def post_from_reddit_child(child: Dict, config: RedditScrapeConfig) -> Optional[Dict[str, str]]:
    data = child.get("data") or {}
    post_id = data.get("id")
    title = normalize_story_text(data.get("title"))
    content = select_story_content(data)

    if not _valid_story(data, post_id, title, content, config):
        return None

    permalink = data.get("permalink")
    source_url = f"https://www.reddit.com{permalink}" if permalink else data.get("url", "")
    return {
        "id": post_id,
        "title": title,
        "content": content,
        "source_url": source_url,
        "permalink": permalink or "",
        "subreddit": data.get("subreddit") or config.subreddit,
        "created_utc": data.get("created_utc"),
        "score": data.get("score"),
        "num_comments": data.get("num_comments"),
        "source_provider": "reddit",
        **source_integrity_fields(content, detail_checked=False),
    }


def post_from_pullpush_item(data: Dict, config: RedditScrapeConfig) -> Optional[Dict[str, str]]:
    post_id = data.get("id")
    title = normalize_story_text(data.get("title"))
    content = select_story_content(data)

    if not _valid_story(data, post_id, title, content, config):
        return None

    permalink = data.get("permalink") or f"/r/{config.subreddit}/comments/{post_id}/"
    source_url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink
    return {
        "id": post_id,
        "title": title,
        "content": content,
        "source_url": source_url,
        "permalink": permalink if permalink.startswith("/") else "",
        "subreddit": data.get("subreddit") or config.subreddit,
        "created_utc": data.get("created_utc"),
        "score": data.get("score"),
        "num_comments": data.get("num_comments"),
        "source_provider": "pullpush",
        **source_integrity_fields(content, detail_checked=False),
    }


def collect_with_fallback(config: RedditScrapeConfig, scraped_ids: Set[str]) -> List[Dict[str, str]]:
    try:
        return RedditApiSource(config).collect(scraped_ids)
    except Exception as e:
        if config.fallback_provider.lower().strip() != "pullpush":
            raise
        print(f"⚠️ Reddit 공식/API 수집 실패, PullPush fallback 사용: {e}")
        return PullPushSource(config).collect(scraped_ids)


def _valid_story(data: Dict, post_id: str, title: str, content: str, config: RedditScrapeConfig) -> bool:
    if not post_id or not title or not content:
        return False
    if data.get("stickied") or data.get("pinned") or data.get("distinguished"):
        return False
    if data.get("is_self") is False:
        return False
    if data.get("over_18") and not config.allow_over18:
        return False
    return len(content) >= config.min_chars


def _first_post_child(payload: Dict) -> Dict:
    if isinstance(payload, list) and payload:
        listing = payload[0].get("data") or {}
        children = listing.get("children") or []
        if children:
            return children[0]
    if isinstance(payload, dict):
        children = ((payload.get("data") or {}).get("children") or [])
        if children:
            return children[0]
    raise ValueError("detail response did not contain a post child")


def _permalink_from_post(post: Dict[str, str]) -> str:
    permalink = str(post.get("permalink") or "").strip()
    if permalink:
        return permalink
    source_url = str(post.get("source_url") or "").strip()
    if not source_url:
        return ""
    return urlparse(source_url).path


def _should_skip(post_id: str, scraped_ids: Set[str], seen_in_run: Set[str], config: RedditScrapeConfig) -> bool:
    return post_id in seen_in_run or (post_id in scraped_ids and not config.rescrape)


def _sleep(config: RedditScrapeConfig) -> None:
    if config.request_delay_seconds > 0:
        time.sleep(config.request_delay_seconds)


def _sleep_detail(config: RedditScrapeConfig) -> None:
    if config.detail_request_delay_seconds > 0:
        time.sleep(config.detail_request_delay_seconds)
