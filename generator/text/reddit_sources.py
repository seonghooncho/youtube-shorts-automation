import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Protocol, Set
from urllib.parse import urlparse

import requests
from requests import HTTPError, RequestException, Session

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
    request_timeout_seconds: float = 45.0
    request_max_attempts: int = 4
    request_backoff_seconds: float = 2.0
    pullpush_page_size: int = 50
    synthetic_fallback_enabled: bool = False
    synthetic_fallback_count: int = 60

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
            request_timeout_seconds=float(os.getenv("REDDIT_REQUEST_TIMEOUT_SECONDS", cls.request_timeout_seconds)),
            request_max_attempts=int(os.getenv("REDDIT_REQUEST_MAX_ATTEMPTS", cls.request_max_attempts)),
            request_backoff_seconds=float(os.getenv("REDDIT_REQUEST_BACKOFF_SECONDS", cls.request_backoff_seconds)),
            pullpush_page_size=int(os.getenv("PULLPUSH_PAGE_SIZE", cls.pullpush_page_size)),
            synthetic_fallback_enabled=_synthetic_fallback_enabled_from_env(),
            synthetic_fallback_count=int(os.getenv("REDDIT_SYNTHETIC_FALLBACK_COUNT", cls.synthetic_fallback_count)),
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
            "size": max(1, min(100, self.config.pullpush_page_size)),
            "sort": "desc",
        }
        if before:
            params["before"] = before
        return _get_json_with_retries(
            self.session,
            "https://api.pullpush.io/reddit/search/submission/",
            params=params,
            timeout=self.config.request_timeout_seconds,
            max_attempts=self.config.request_max_attempts,
            backoff_seconds=self.config.request_backoff_seconds,
            label="pullpush search",
        )

    def collect(self, scraped_ids: Set[str]) -> List[Dict[str, str]]:
        posts: List[Dict[str, str]] = []
        seen_in_run: Set[str] = set()
        before: Optional[int] = None

        for page in range(1, self.config.max_pages + 1):
            try:
                payload = self._search(before=before)
            except Exception as e:
                if posts:
                    print(f"⚠️ PullPush page fetch failed after partial collection; using {len(posts)} posts: {e}")
                    break
                raise
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


class SyntheticConflictSource:
    def __init__(self, config: RedditScrapeConfig):
        self.config = config

    def collect(self, scraped_ids: Set[str]) -> List[Dict[str, str]]:
        posts: List[Dict[str, str]] = []
        batch_id = os.getenv("SYNTHETIC_SOURCE_BATCH_ID") or time.strftime("%Y%m%d%H%M%S", time.gmtime())
        for index, scenario in enumerate(_SYNTHETIC_SCENARIOS, start=1):
            post_id = f"synthetic-{batch_id}-{scenario['slug']}"
            if _should_skip(post_id, scraped_ids, {post["id"] for post in posts}, self.config):
                continue
            content = _synthetic_story_content(scenario)
            posts.append(
                {
                    "id": post_id,
                    "title": scenario["title"],
                    "content": content,
                    "source_url": "",
                    "permalink": "",
                    "subreddit": "synthetic_conflict",
                    "created_utc": int(time.time()) - index,
                    "score": 0,
                    "num_comments": 0,
                    "source_provider": "synthetic",
                    "source_authenticity": "synthetic",
                    "source_collection_path": "synthetic",
                    "source_quality_status": "skipped",
                    "source_rejection_reason": "synthetic_fallback_source",
                    "source_generation_reason": "reddit_and_pullpush_unavailable",
                    **source_integrity_fields(content, detail_checked=True, detail_improved=False),
                }
            )
            if len(posts) >= min(self.config.synthetic_fallback_count, self.config.max_posts):
                break
        print(f"⚠️ synthetic conflict fallback generated {len(posts)} source seeds")
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
        "source_authenticity": "reddit",
        "source_collection_path": "reddit_oauth" if os.getenv("REDDIT_CLIENT_ID") and os.getenv("REDDIT_CLIENT_SECRET") else "reddit_public",
        "source_quality_status": "",
        "source_rejection_reason": "",
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
        "source_authenticity": "pullpush",
        "source_collection_path": "pullpush",
        "source_quality_status": "",
        "source_rejection_reason": "",
        **source_integrity_fields(content, detail_checked=False),
    }


def collect_with_fallback(config: RedditScrapeConfig, scraped_ids: Set[str]) -> List[Dict[str, str]]:
    config = _production_safe_config(config)
    try:
        return RedditApiSource(config).collect(scraped_ids)
    except Exception as e:
        if config.fallback_provider.lower().strip() != "pullpush":
            raise
        print(f"⚠️ Reddit 공식/API 수집 실패, PullPush fallback 사용: {e}")
        try:
            posts = PullPushSource(config).collect(scraped_ids)
        except Exception as pullpush_error:
            if not config.synthetic_fallback_enabled:
                print(f"⚠️ PullPush 수집 실패, synthetic fallback disabled: {pullpush_error}")
                return []
            print(f"⚠️ PullPush 수집 실패, synthetic conflict fallback 사용: {pullpush_error}")
            return SyntheticConflictSource(config).collect(scraped_ids)
        return _supplement_with_synthetic_if_needed(config, scraped_ids, posts)


def _production_safe_config(config: RedditScrapeConfig) -> RedditScrapeConfig:
    if _is_production_env() and config.synthetic_fallback_enabled and not _allow_synthetic_in_production():
        return RedditScrapeConfig(
            **{
                **config.__dict__,
                "synthetic_fallback_enabled": False,
            }
        )
    return config


def _synthetic_fallback_enabled_from_env() -> bool:
    enabled = os.getenv("REDDIT_SYNTHETIC_FALLBACK_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    if _is_production_env() and enabled and not _allow_synthetic_in_production():
        return False
    return enabled


def _is_production_env() -> bool:
    return any(os.getenv(name, "").strip().lower() == "production" for name in ("APP_ENV", "YT_ENV"))


def _allow_synthetic_in_production() -> bool:
    return os.getenv("ALLOW_SYNTHETIC_IN_PRODUCTION", "").strip().lower() in {"1", "true", "yes", "on"}


def _get_json_with_retries(
    session: Session,
    url: str,
    *,
    params: Dict,
    timeout: float,
    max_attempts: int,
    backoff_seconds: float,
    label: str,
) -> Dict:
    attempts = max(1, max_attempts)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except RequestException as e:
            last_error = e
            if attempt >= attempts:
                break
            wait = max(0.0, backoff_seconds) * attempt
            print(f"⚠️ {label} failed attempt {attempt}/{attempts}; retrying in {wait:.1f}s: {e}")
            if wait > 0:
                time.sleep(wait)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}") from last_error


def _supplement_with_synthetic_if_needed(
    config: RedditScrapeConfig,
    scraped_ids: Set[str],
    posts: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    if not config.synthetic_fallback_enabled:
        return posts
    target_count = min(config.max_posts, config.min_needed)
    if len(posts) >= target_count:
        return posts

    seen_ids = {str(post.get("id")) for post in posts if post.get("id")}
    synthetic_posts = SyntheticConflictSource(config).collect(scraped_ids | seen_ids)
    remaining = max(0, config.max_posts - len(posts))
    supplemented = posts + synthetic_posts[:remaining]
    print(
        "⚠️ PullPush accepted too few posts; "
        f"supplemented with synthetic seeds: {len(posts)} -> {len(supplemented)}"
    )
    return supplemented


def _synthetic_story_content(scenario: Dict[str, str]) -> str:
    return normalize_story_text(
        " ".join(
            [
                f"I had one clear boundary in this situation: {scenario['boundary']}.",
                f"{scenario['setup']}",
                f"Then {scenario['crossed_line']} without asking me first, and acted like I was the unreasonable one for noticing.",
                f"The part that made people take sides was {scenario['public_pressure']}.",
                f"I tried to keep it calm and said I was not paying for a problem I did not create, but {scenario['escalation']}.",
                f"What changed everything was {scenario['proof']}.",
                f"After that, {scenario['consequence']}.",
                f"Now half the people around us say I should have let it go to keep peace, and the other half say this was exactly when I needed to hold the boundary.",
                f"{scenario['debate']}",
            ]
        )
    )


_SYNTHETIC_SCENARIOS = [
    {
        "slug": "neighbor_driveway_camera",
        "title": "AITA for refusing to move my car after my neighbor used my driveway?",
        "boundary": "my driveway is not shared parking, even if I am not home",
        "setup": "My neighbor had been asking to use it for quick errands, and I said yes twice because it was only a few minutes.",
        "crossed_line": "he started treating it like his extra spot and told delivery drivers to leave packages by my side door",
        "public_pressure": "he complained in the neighborhood chat that I was being petty over empty pavement",
        "escalation": "he demanded I apologize for embarrassing him in front of the whole block",
        "proof": "my door camera showed his car there for six hours while guests had to park down the street",
        "consequence": "I put up a small private parking sign and stopped answering his texts",
        "debate": "Was I too strict, or did he turn a favor into a right?",
    },
    {
        "slug": "roommate_grocery_money",
        "title": "AITA for locking up my groceries after my roommate used the shared fund?",
        "boundary": "shared money is only for basics we both agree on",
        "setup": "My roommate and I kept a small grocery fund for rice, eggs, coffee, and cleaning supplies.",
        "crossed_line": "she spent most of it on snacks for her friends and said it still counted because everyone ate at our place",
        "public_pressure": "her friends joked that I was charging admission to my own kitchen",
        "escalation": "she pressured me to refill the fund before rent week because the apartment looked cheap without food out",
        "proof": "the receipt showed almost nothing we actually needed and several things I never eat",
        "consequence": "I bought my own groceries, labeled my shelf, and stopped contributing until she replaced the money",
        "debate": "Was that fair budgeting, or did I make the apartment feel hostile?",
    },
    {
        "slug": "family_birthday_bill",
        "title": "AITA for refusing to cover my mom's birthday dinner bill?",
        "boundary": "I would help plan the dinner, but I was not secretly covering everyone",
        "setup": "My family picked a restaurant that was already above what I said I could spend.",
        "crossed_line": "my aunt told the server to put the whole table on my card because I was the one who made the reservation",
        "public_pressure": "everyone got quiet and my mom looked hurt, like I had ruined her night",
        "escalation": "my cousin said I was cheap for discussing money at a birthday table",
        "proof": "the group chat showed I had said three times that everyone was paying their own share",
        "consequence": "I paid for my mom and myself, then left the rest of the table to split their orders",
        "debate": "Did I embarrass my family, or did they try to trap me into paying?",
    },
    {
        "slug": "coworker_lunch_accusation",
        "title": "AITA for proving my coworker lied about our team lunch order?",
        "boundary": "I was only collecting orders, not fronting money for changed meals",
        "setup": "Our small team orders lunch together once a week, and people transfer their share before I place it.",
        "crossed_line": "one coworker changed his order after the deadline and then accused me of pocketing the difference",
        "public_pressure": "he said it loudly in the break room while our manager was standing there",
        "escalation": "he demanded I refund him in cash because he claimed I had made the mistake",
        "proof": "the order app and payment notes showed exactly what he selected and when he edited it",
        "consequence": "I sent screenshots to the team chat and stopped coordinating lunch for everyone",
        "debate": "Was sending proof too much, or was that the only way to stop the accusation?",
    },
    {
        "slug": "airbnb_deposit_friends",
        "title": "AITA for keeping my friends' deposit after they trashed the rental?",
        "boundary": "the deposit was only refundable if we left the place the way we found it",
        "setup": "I booked a weekend rental because my account had the best rating and everyone promised to be careful.",
        "crossed_line": "two friends invited extra people, moved furniture outside, and left sticky spills on a white rug",
        "public_pressure": "they told the group I was acting like a landlord instead of a friend",
        "escalation": "they demanded their deposit back before the host even inspected the place",
        "proof": "the host sent photos of the rug, patio chairs, and broken lamp with a cleaning charge",
        "consequence": "I used the deposit for the fee and told them I would not book trips under my name again",
        "debate": "Should I have split the damage evenly, or should the people who caused it pay?",
    },
    {
        "slug": "cousin_borrowed_car",
        "title": "AITA for refusing to lend my cousin my car again?",
        "boundary": "my car can be borrowed only if it comes back clean, full, and on time",
        "setup": "My cousin needed it for one afternoon, and I agreed because he said it was for a simple errand.",
        "crossed_line": "he kept it overnight, returned it nearly empty, and acted like the scratches were already there",
        "public_pressure": "my relatives said I was making a family issue over a few marks",
        "escalation": "he pressured me to drop it because he had already done me the favor of bringing it back",
        "proof": "photos from the morning showed the side panel was clean before he took the keys",
        "consequence": "I asked him to pay for an estimate and refused to hand over the keys again",
        "debate": "Was I protecting my property, or overreacting because it was family?",
    },
    {
        "slug": "group_chat_package",
        "title": "AITA for posting proof after my neighbor accused me of taking a package?",
        "boundary": "I will help look for packages, but I will not accept blame without evidence",
        "setup": "A neighbor said her delivery disappeared and asked everyone in our building chat if they had seen it.",
        "crossed_line": "she named me directly because my door was closest to the mail shelf",
        "public_pressure": "people started sending awkward messages asking me to just return it quietly",
        "escalation": "she refused to delete the message and said innocent people do not get defensive",
        "proof": "my hallway camera showed the courier leaving with the box after scanning the wrong label",
        "consequence": "I posted the clip, asked her to correct herself, and stopped taking packages in for neighbors",
        "debate": "Was public proof necessary, or should I have handled it privately?",
    },
    {
        "slug": "shared_storage_unit",
        "title": "AITA for changing the storage unit code after my brother used my space?",
        "boundary": "the storage unit was for my furniture, not a free drop zone for everyone",
        "setup": "My brother asked to leave two boxes there for a week while he reorganized his apartment.",
        "crossed_line": "he filled half the unit with random bags and blocked the furniture I needed to move",
        "public_pressure": "my parents said I was being dramatic because storage is meant for storing things",
        "escalation": "he threatened to leave everything there longer if I kept rushing him",
        "proof": "the unit camera log showed he had visited four times after promising not to add more",
        "consequence": "I changed the access code and gave him one weekend to remove everything",
        "debate": "Was changing the code fair, or did I create unnecessary family drama?",
    },
    {
        "slug": "office_coffee_fund",
        "title": "AITA for refusing to restart the office coffee fund?",
        "boundary": "the coffee fund only works if people pay before using it",
        "setup": "I had been buying pods and milk for our corner of the office because everyone said they wanted a cheaper option.",
        "crossed_line": "two coworkers kept taking coffee without contributing and then complained when the supplies ran out",
        "public_pressure": "they joked in front of the team that I was running a tiny business from the break room",
        "escalation": "one of them demanded I keep buying supplies because people had gotten used to it",
        "proof": "the payment list showed the same four people had paid while twelve people used everything",
        "consequence": "I stopped managing the fund and brought only one travel mug from home",
        "debate": "Was I petty for ending it, or did they make the shared system impossible?",
    },
    {
        "slug": "apartment_laundry_queue",
        "title": "AITA for moving my neighbor's laundry after she ignored the timer?",
        "boundary": "shared machines need to be cleared when the cycle ends",
        "setup": "Our building has two washers for twenty apartments, so everyone uses a timer and moves fast.",
        "crossed_line": "a neighbor left her clothes in both machines for almost an hour and told me I should have waited",
        "public_pressure": "she complained in the building chat that I touched her things without permission",
        "escalation": "she threatened to report me to management for being disrespectful",
        "proof": "the machine app showed both cycles had ended fifty-two minutes before I moved anything",
        "consequence": "I posted the timestamp, put her clothes in a clean basket, and kept my laundry slot",
        "debate": "Did I cross a line, or was she blocking everyone else first?",
    },
    {
        "slug": "holiday_hosting_rooms",
        "title": "AITA for refusing to give up my bedroom during a family visit?",
        "boundary": "I would host dinner, but overnight guests needed to use the spare room or a hotel",
        "setup": "I live alone, and my family asked to gather at my apartment because it is central for everyone.",
        "crossed_line": "my uncle announced that an older relative would take my bedroom and I could sleep on the couch",
        "public_pressure": "everyone stared at me like saying no meant I did not care about family",
        "escalation": "my aunt pressured me to be gracious because I was already lucky to have my own place",
        "proof": "the messages showed I had offered the spare room weeks earlier and said my bedroom was not available",
        "consequence": "I kept my room, set up the spare room, and sent nearby hotel links for anyone unhappy",
        "debate": "Was that a selfish boundary, or reasonable hosting?",
    },
    {
        "slug": "wedding_centerpiece_invoice",
        "title": "AITA for refusing to pay for wedding decorations I never approved?",
        "boundary": "I agreed to help assemble decorations, not pay for new ones",
        "setup": "A close friend asked me to help with simple table pieces because I am good at organizing supplies.",
        "crossed_line": "another friend ordered expensive decorations in my name and told the group I had volunteered to cover it",
        "public_pressure": "people said canceling would make the event look unfinished and embarrass the couple",
        "escalation": "she demanded I pay first and settle it later because the invoice was already due",
        "proof": "the planning chat showed I had only agreed to bring tape, labels, and a few trays",
        "consequence": "I refused the invoice, helped with the original plan, and let the person who ordered extras explain it",
        "debate": "Should I have paid to avoid stress, or was that exactly why I had to refuse?",
    },
    {
        "slug": "friend_borrowed_outfit",
        "title": "AITA for asking my friend to replace the outfit she borrowed?",
        "boundary": "borrowed clothes come back clean, undamaged, and on time",
        "setup": "My friend needed something for a work event and asked to borrow an outfit I had saved for months to buy.",
        "crossed_line": "she returned it late with a stain and said it was not noticeable unless I looked for it",
        "public_pressure": "our friends said I was acting materialistic because it was just clothing",
        "escalation": "she pressured me to accept a small cleaning fee instead of replacement cost",
        "proof": "the cleaner wrote that the stain had set and the fabric was permanently marked",
        "consequence": "I asked her to replace it or pay the current resale price and stopped lending things out",
        "debate": "Was replacement too harsh, or is that the risk of borrowing someone's expensive item?",
    },
    {
        "slug": "work_credit_stolen",
        "title": "AITA for correcting my manager after a coworker took credit for my work?",
        "boundary": "team help is fine, but finished work should not be presented as someone else's",
        "setup": "I built a spreadsheet that solved a reporting problem our team had been stuck on for weeks.",
        "crossed_line": "a coworker presented it in a meeting as something she had put together over the weekend",
        "public_pressure": "she smiled at me across the table like I was supposed to stay quiet for team harmony",
        "escalation": "after the meeting she told me correcting her would make us both look unprofessional",
        "proof": "the file history showed my edits, notes, and timestamps from before she ever opened it",
        "consequence": "I sent a calm follow-up crediting everyone accurately and attached the file history",
        "debate": "Was that necessary self-advocacy, or did I make a workplace issue too public?",
    },
    {
        "slug": "neighbor_trash_bins",
        "title": "AITA for moving my neighbor's trash bins back onto his side?",
        "boundary": "trash bins cannot block my walkway every pickup day",
        "setup": "Our houses are close together, and the walkway to my door is narrow enough that a bin can block it.",
        "crossed_line": "my neighbor kept placing his bins in front of my gate because he said the truck reached that spot faster",
        "public_pressure": "he told nearby neighbors I was obsessing over a few feet of pavement",
        "escalation": "he threatened to keep doing it unless I could prove it was actually inconvenient",
        "proof": "photos showed delivery drivers leaving packages outside the gate because they could not get through",
        "consequence": "I moved the bins back to his side and sent him the photos with a clear message",
        "debate": "Was moving them passive aggressive, or was it the simplest way to protect access to my door?",
    },
    {
        "slug": "shared_streaming_account",
        "title": "AITA for changing the streaming password after my sister shared it?",
        "boundary": "the account was for our household, not every friend and coworker",
        "setup": "I paid for a family streaming plan and let my sister use one profile while she saved money.",
        "crossed_line": "she gave the password to multiple people and then blamed me when the account locked during my movie night",
        "public_pressure": "she told relatives I was being controlling over something that costs less than dinner",
        "escalation": "she demanded I upgrade the plan because her friends were already using it",
        "proof": "the login screen showed devices and names I did not recognize across several cities",
        "consequence": "I changed the password, removed the extra devices, and told her she could pay for her own plan",
        "debate": "Was I overprotective, or did she turn one favor into a free account for everyone?",
    },
    {
        "slug": "family_vacation_room",
        "title": "AITA for taking the room I paid extra for on a family trip?",
        "boundary": "I paid extra for a quiet room because I needed space to work early mornings",
        "setup": "My family split a rental, and I added money specifically for the room with the desk and door.",
        "crossed_line": "my cousin arrived first, unpacked in that room, and said rooms were first come first served",
        "public_pressure": "everyone said moving luggage would start the trip with bad energy",
        "escalation": "my cousin pressured me to be flexible because I was only using the desk for a few hours",
        "proof": "the payment sheet showed the extra amount next to my name and the room description",
        "consequence": "I asked for the room back or my extra payment refunded immediately",
        "debate": "Was I ruining the mood, or was the agreement clear before anyone arrived?",
    },
    {
        "slug": "volunteer_raffle_money",
        "title": "AITA for refusing to cover missing raffle money?",
        "boundary": "I would count the raffle tickets, but I was not responsible for cash I never handled",
        "setup": "At a community event, several volunteers were collecting money at different tables.",
        "crossed_line": "one organizer told everyone I must have miscounted because my table had the most tickets",
        "public_pressure": "people looked at me like I had quietly caused the shortage",
        "escalation": "she demanded I cover the difference so the final report would look clean",
        "proof": "the sign-in sheet showed another table kept taking cash after I had already turned mine in",
        "consequence": "I refused to pay, wrote down the timeline, and stepped back from handling money there again",
        "debate": "Should I have covered it to protect the event, or was that unfair blame?",
    },
    {
        "slug": "coworking_desk_booking",
        "title": "AITA for making someone leave the desk I reserved?",
        "boundary": "reserved desks are for the person who booked them, especially on busy days",
        "setup": "I paid extra for a coworking desk near an outlet because I had back-to-back calls.",
        "crossed_line": "someone sat there, spread out his things, and said reservations were more like suggestions",
        "public_pressure": "nearby people acted annoyed that I was interrupting a quiet workspace",
        "escalation": "he pressured me to take a smaller desk because he was already settled",
        "proof": "the booking app and the sticker on the desk both had my reservation window",
        "consequence": "I asked staff to enforce it and took the desk I paid for",
        "debate": "Was that rigid, or is a reservation meaningless if nobody enforces it?",
    },
    {
        "slug": "shared_printer_toner",
        "title": "AITA for hiding the toner after my neighbors kept using my printer?",
        "boundary": "I was fine printing occasional pages, not becoming the building print station",
        "setup": "A neighbor once asked to print a shipping label, and I said yes because it was quick.",
        "crossed_line": "more neighbors started sending files to print and one person used half a toner cartridge for flyers",
        "public_pressure": "they said I was creating conflict over a machine that was already sitting there",
        "escalation": "one neighbor demanded I keep the printer available until management bought one",
        "proof": "the print history showed dozens of pages that had nothing to do with me",
        "consequence": "I removed the toner, stopped sharing the network access, and told people to use a print shop",
        "debate": "Was that unfriendly, or did they take advantage of a small favor?",
    },
    {
        "slug": "dinner_reservation_late",
        "title": "AITA for sitting down without my friends after they were late again?",
        "boundary": "I would wait fifteen minutes, not lose another reservation",
        "setup": "My friends are often late, so I told everyone the restaurant would only hold our table briefly.",
        "crossed_line": "they showed up forty minutes late and expected me to keep standing outside",
        "public_pressure": "they said I made them look rude by ordering appetizers before they arrived",
        "escalation": "one friend pressured me to apologize to the server for making the table awkward",
        "proof": "the reservation text clearly said the table would be released after fifteen minutes",
        "consequence": "I sat down on time, ordered, and told them they could join when they got there",
        "debate": "Was I inconsiderate, or was this the only way to stop rewarding lateness?",
    },
    {
        "slug": "shared_balcony_plants",
        "title": "AITA for removing my roommate's plants from our shared balcony?",
        "boundary": "the balcony needed a clear path to the door and railing",
        "setup": "My roommate started with two plants, and I said they were fine as long as we could both use the space.",
        "crossed_line": "she added so many pots that I could not open the door without stepping sideways",
        "public_pressure": "she told friends I destroyed her peaceful corner because I hate nice things",
        "escalation": "she demanded I pay for a plant stand after I moved the pots to her side",
        "proof": "photos showed the blocked door and the original two-plant agreement in our messages",
        "consequence": "I kept a clear walkway and told her anything blocking the door had to move",
        "debate": "Was I too blunt, or did shared space need a hard limit?",
    },
]


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
