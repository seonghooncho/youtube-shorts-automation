import os
import json
import re
import time
from typing import List, Dict, Tuple
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from shared.utils.config import RAW_POSTS_FILE, SCRAPED_POST_LIST_FILE

# ----------------------------
# 설정
# ----------------------------
MAX_POSTS = 30              # 최대 수집
MIN_NEEDED = 15             # 최소 보장 수
MIN_CHARS = 300
MAX_PAGES = 10              # next-button으로 넘길 최대 페이지 수
LIST_URL = "https://old.reddit.com/r/AmItheAsshole/hot/"

RESCRAPE = os.getenv("RESCRAPE", "0") == "1"  # 1이면 과거 scraped_ids 무시

POST_ID_RE = re.compile(r"/comments/([a-z0-9]+)/", re.IGNORECASE)

def sleep(a=0.6, b=1.6):
    import random
    time.sleep(random.uniform(a, b))

def force_old(url: str) -> str:
    if not url:
        return url
    return re.sub(r"https?://(www\.)?reddit\.com", "https://old.reddit.com", url)

def extract_post_id(url: str) -> str:
    if not url:
        return ""
    m = POST_ID_RE.search(url)
    return m.group(1) if m else ""

def build_driver() -> webdriver.Chrome:
    opts = Options()
    # ✅ 화면 없이(headless) 실행
    opts.add_argument("--headless=new")

    # 기존 옵션 유지 + 자동화 플래그 최소화
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option('useAutomationExtension', False)

    # ✅ 서버/CI에서 안정(권한/SHM)
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")

    # ✅ headless에서도 레이아웃 안정
    opts.add_argument("--window-size=1366,768")

    # ❌ UI 필요 없음
    # opts.add_argument("--start-maximized")

    # UA 지정
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=opts)

    # ✅ navigator.webdriver 숨김 (간단한 봇 감지 회피)
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"}
        )
    except Exception:
        pass

    return driver

def set_over18_cookie(driver):
    # 도메인 쿠키를 넣으려면 해당 도메인 열린 상태여야 함
    driver.get("https://old.reddit.com/")
    sleep(0.5, 1.0)
    try:
        driver.delete_all_cookies()
        driver.add_cookie({"name": "over18", "value": "1", "domain": ".reddit.com", "path": "/"})
    except Exception:
        pass

# 리스트에서 후보 추출 (광고/외부링크 제외)
def collect_listing_items(driver) -> List[Dict]:
    items = []
    cards = driver.find_elements(
        By.XPATH,
        "//div[starts-with(@id,'thing_t3_') and not(@data-promoted='true') and not(contains(@class,'promotedlink'))]"
    )
    for c in cards:
        # 외부링크/스티키 제외
        dom = (c.get_attribute("data-domain") or "").lower()
        if dom and not dom.startswith("self."):
            continue
        if (c.get_attribute("data-stickied") or "").lower() == "true":
            continue

        # 제목/링크
        href, title = "", ""
        try:
            a = c.find_element(By.XPATH, "./div[2]/div[1]/p[1]/a")
            href = a.get_attribute("href") or ""
            title = (a.text or "").strip()
        except Exception:
            try:
                a = c.find_element(By.XPATH, ".//a[contains(@href, '/comments/')]")
                href = a.get_attribute("href") or ""
                title = (a.text or "").strip()
            except Exception:
                continue

        if not href:
            continue
        # 트래킹/리다이렉트 제외
        bad = any(s in href.lower() for s in ["out.reddit.com", "utm_", "adserver", "promo", "tracking"])
        if bad:
            continue

        pid = extract_post_id(href)
        if not pid:
            continue
        items.append({"id": pid, "href": href, "title": title})
    return items

def parse_detail(driver, href: str, expected_id: str) -> Tuple[str, str, str]:
    url = force_old(href)
    driver.get(url)
    sleep(0.6, 1.2)

    cur = driver.current_url.lower()
    if "/comments/" not in cur or "/r/amitheasshole/" not in cur:
        return "", "", expected_id

    title = ""
    try:
        WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.title")))
        title = (driver.find_element(By.CSS_SELECTOR, "a.title").text or "").strip()
    except TimeoutException:
        try:
            WebDriverWait(driver, 4).until(EC.presence_of_element_located(
                (By.XPATH, "//*[starts-with(@id,'post-title')]")))
            title = (driver.find_element(By.XPATH, "//*[starts-with(@id,'post-title')]").text or "").strip()
        except Exception:
            title = ""

    content = ""
    # 1) form-t3_… 경로
    try:
        paras = driver.find_elements(By.XPATH, "//form[starts-with(@id,'form-t3_')]/div/div//*[self::p or self::li or self::blockquote]")
        if paras:
            content = "\n".join([p.text.strip() for p in paras if p.text and p.text.strip()])
    except Exception:
        pass
    # 2) old.reddit expando
    if not content:
        try:
            paras = driver.find_elements(By.CSS_SELECTOR, "div.expando div.usertext-body div.md p, "
                                                          "div.expando div.usertext-body div.md li, "
                                                          "div.expando div.usertext-body div.md blockquote")
            if paras:
                content = "\n".join([p.text.strip() for p in paras if p.text and p.text.strip()])
        except Exception:
            pass
    # 3) 새 UI rtjson
    if not content:
        try:
            paras = driver.find_elements(By.XPATH, "//div[contains(@id,'-post-rtjson-content')]//*[self::p or self::li or self::blockquote]")
            if paras:
                content = "\n".join([p.text.strip() for p in paras if p.text and p.text.strip()])
        except Exception:
            pass

    # 확정 post id
    final_id = expected_id
    try:
        f = driver.find_element(By.XPATH, "//form[starts-with(@id,'form-t3_')]")
        fid = f.get_attribute("id") or ""
        if "form-t3_" in fid:
            final_id = fid.split("form-t3_")[1]
    except Exception:
        pid2 = extract_post_id(driver.current_url)
        if pid2:
            final_id = pid2

    return title, content, final_id

def scrape_reddit_and_store():
    # scraped_ids 로드
    if SCRAPED_POST_LIST_FILE.exists():
        with open(SCRAPED_POST_LIST_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            scraped_ids = set(raw) if isinstance(raw, list) else set(raw.keys())
    else:
        scraped_ids = set()

    driver = build_driver()
    try:
        set_over18_cookie(driver)

        new_posts, new_ids = [], []
        pages = 0
        next_url = LIST_URL

        while len(new_posts) < MAX_POSTS and pages < MAX_PAGES and next_url:
            driver.get(next_url)
            # old.reddit은 서버 렌더라 스크롤 거의 불필요하지만 1회 정도만
            sleep(0.6, 1.0)

            items = collect_listing_items(driver)
            print(f"🔎 list items: {len(items)} on page {pages+1}")

            # 중복 제거
            pruned = []
            dup_cnt = 0
            for it in items:
                pid, href, title = it["id"], it["href"], it.get("title", "")
                if not pid or not href:
                    continue
                if (pid in scraped_ids) and not RESCRAPE:
                    dup_cnt += 1
                    continue
                pruned.append((pid, href, title))
            print(f"🚮 after de-dup: {len(pruned)} (dups: {dup_cnt}, scraped_ids: {len(scraped_ids)})")

            ok, short, empty = 0, 0, 0
            for pid, href, list_title in pruned:
                if len(new_posts) >= MAX_POSTS:
                    break
                title, content, fid = parse_detail(driver, href, pid)
                if not content:
                    empty += 1
                    continue
                if len(content) < MIN_CHARS:
                    short += 1
                    continue

                new_posts.append({"id": fid, "title": title or list_title, "content": content})
                new_ids.append(fid)
                scraped_ids.add(fid)
                ok += 1
            print(f"📊 parse stats — ok:{ok}, empty:{empty}, short:{short}")

            # 다음 페이지
            pages += 1
            if len(new_posts) >= MIN_NEEDED:
                # 최소 개수 도달하면 조기 종료 가능
                pass
            try:
                nxt = driver.find_element(By.CSS_SELECTOR, "span.next-button > a")
                next_url = nxt.get_attribute("href")
            except NoSuchElementException:
                next_url = None

            # 최소 개수 못 채우면 다음 페이지로 계속
            if len(new_posts) < MIN_NEEDED and next_url:
                continue
            # 최소 개수 채웠고 더 이상 진행 원치 않으면 break
            if len(new_posts) >= MIN_NEEDED:
                break

    finally:
        # ✅ 에러가 나도 프로세스/세션 누수 없이 정리
        try:
            driver.quit()
        except Exception:
            pass

    with open(RAW_POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(new_posts[:MAX_POSTS], f, ensure_ascii=False, indent=2)

    # scraped_ids 저장 (list 방식 유지)
    with open(SCRAPED_POST_LIST_FILE, "w", encoding="utf-8") as f:
        json.dump(list(scraped_ids), f, ensure_ascii=False, indent=2)

    print(f"✅ 크롤링 완료. {len(new_posts)}건 저장됨 → {RAW_POSTS_FILE}")
    if new_ids:
        print(f"📌 첫 번째 ID: {new_ids[0]}")

if __name__ == "__main__":
    scrape_reddit_and_store()
