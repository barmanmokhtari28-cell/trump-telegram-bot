import os
import re
import sys
import html
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from time import mktime
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from playwright.sync_api import sync_playwright

RSS_URL = "https://www.trumpstruth.org/feed"
SENT_POSTS_FILE = "sent_posts.txt"
CHANNEL_USERNAME = "@secretollah"
ARCHIVE_DOMAIN = "trumpstruth.org"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TEST_MODE = os.environ.get("TEST_MODE", "false").lower() == "true"
TEST_HOURS = float(os.environ.get("TEST_HOURS", "5"))

SELECTORS = [".detailed-status", "article", ".status", "main"]
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_MESSAGE_LIMIT = 4096


def get_sent_posts():
    if not os.path.exists(SENT_POSTS_FILE):
        return set()
    with open(SENT_POSTS_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())


def save_sent_post(post_id):
    with open(SENT_POSTS_FILE, "a") as f:
        f.write(f"{post_id}\n")


def translate_to_persian(text):
    if not text.strip():
        return ""
    try:
        return GoogleTranslator(source="en", target="fa").translate(text)
    except Exception as e:
        print(f"Translation error: {e}")
        return ""


def extract_post_id(entry):
    candidates = []
    for attr in ("id", "guid", "link"):
        val = getattr(entry, attr, None)
        if val:
            candidates.append(val)
    for link in getattr(entry, "links", []):
        href = link.get("href")
        if href:
            candidates.append(href)

    for val in candidates:
        m = re.search(rf"{re.escape(ARCHIVE_DOMAIN)}/statuses/(\d+)", val)
        if m:
            return m.group(1)
    return None


def get_entry_datetime(entry):
    if getattr(entry, "published_parsed", None):
        return datetime.fromtimestamp(mktime(entry.published_parsed), tz=timezone.utc)
    if getattr(entry, "updated_parsed", None):
        return datetime.fromtimestamp(mktime(entry.updated_parsed), tz=timezone.utc)
    return None


def clean_html_text(raw_html):
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text().strip()


def build_captions(raw_description):
    """
    Returns (photo_caption, extra_message_or_None).
    Telegram rejects any photo/video caption over 1024 characters. Long
    Trump posts + Persian translation regularly blow past that, so when the
    full caption is too long we send a short caption on the media itself and
    put the full translated text in a normal follow-up text message instead
    (which has a much higher 4096 char limit).
    """
    raw_text = clean_html_text(raw_description)
    translated_text = translate_to_persian(raw_text)
    escaped_translation = html.escape(translated_text)
    escaped_username = html.escape(CHANNEL_USERNAME)
    RLM = "\u200f"
    header = f"{RLM}🇺🇸 <b> دونــالـــد تـرامــپِ شـــیردل کـلــهِ سـکســـی:</b>"
    short_caption = f"{header}\n\n{RLM}{escaped_username}"

    if not escaped_translation.strip():
        return short_caption, None

    full_caption = (
        f"{header}\n"
        f"<blockquote>{RLM}{escaped_translation}</blockquote>\n\n"
        f"{RLM}{escaped_username}"
    )

    if len(full_caption) <= TELEGRAM_CAPTION_LIMIT:
        return full_caption, None

    return short_caption, full_caption


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Chunk defensively in case a translation ever exceeds the 4096 message limit.
    for i in range(0, len(text), TELEGRAM_MESSAGE_LIMIT):
        chunk = text[i:i + TELEGRAM_MESSAGE_LIMIT]
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"}
        res = requests.post(url, data=data, timeout=30)
        if res.status_code != 200:
            print(f"Failed to send message: {res.text}")
            return False
    return True


def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as photo:
        files = {"photo": photo}
        data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
        res = requests.post(url, files=files, data=data, timeout=60)
    if res.status_code != 200:
        print(f"Failed to send photo: {res.text}")
        return False
    return True


def send_telegram_video(video_path, caption=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    with open(video_path, "rb") as video:
        files = {"video": video}
        data = {"chat_id": TELEGRAM_CHAT_ID, "parse_mode": "HTML", "supports_streaming": True}
        if caption:
            data["caption"] = caption
        res = requests.post(url, files=files, data=data, timeout=180)
    if res.status_code != 200:
        print(f"Failed to send video: {res.text}")
        return False
    return True


def download_video(video_url):
    local_filename = "temp_video.mp4"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        with requests.get(video_url, headers=headers, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(local_filename, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return local_filename
    except Exception as e:
        print(f"Failed to download video: {e}")
        return None


def capture_post(page, post_id):
    archive_url = f"https://{ARCHIVE_DOMAIN}/statuses/{post_id}"
    screenshot_path = f"screenshot_{post_id}.png"
    print(f"Loading {archive_url}")

    page.goto(archive_url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(3000)

    element = None
    used_selector = None
    for sel in SELECTORS:
        try:
            page.wait_for_selector(sel, timeout=4000)
            loc = page.locator(sel).first
            if loc.count() > 0:
                element = loc
                used_selector = sel
                break
        except Exception:
            continue

    if element:
        element.screenshot(path=screenshot_path)
        print(f"Screenshotted post block via selector: {used_selector}")
    else:
        print("No specific post block found, falling back to full page screenshot")
        page.screenshot(path=screenshot_path)

    video_url = None
    try:
        scope = element if element else page
        video_el = scope.locator("video source, video").first
        if video_el.count() > 0:
            video_url = video_el.get_attribute("src")
        if not video_url:
            mp4_link = scope.locator("a[href*='.mp4']").first
            if mp4_link.count() > 0:
                video_url = mp4_link.get_attribute("href")
    except Exception as e:
        print(f"Error checking for video in post block: {e}")

    return screenshot_path, video_url


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Secrets missing in environment configuration.")
        sys.exit(1)

    sent_posts = get_sent_posts()
    feed = feedparser.parse(RSS_URL)
    items = feed.entries[:30]
    items.reverse()

    cutoff = None
    if TEST_MODE:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=TEST_HOURS)
        print(f"TEST_MODE on: only posts published after {cutoff.isoformat()} will be processed, "
              f"and the 'already sent' list will be ignored.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            viewport={"width": 800, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        for entry in items:
            post_id = extract_post_id(entry)
            if not post_id:
                print(f"Could not resolve a trumpstruth.org post ID for entry '{getattr(entry, 'title', '')}', skipping.")
                continue

            entry_dt = get_entry_datetime(entry)

            if TEST_MODE:
                if entry_dt is None or entry_dt < cutoff:
                    continue
            else:
                if post_id in sent_posts:
                    continue

            print(f"Processing post: {post_id}")
            photo_caption, extra_message = build_captions(getattr(entry, "description", ""))

            try:
                screenshot_path, video_url = capture_post(page, post_id)
            except Exception as e:
                print(f"Failed to load/capture post {post_id}: {e}")
                continue

            sent_ok = False
            if screenshot_path and os.path.exists(screenshot_path):
                sent_ok = send_telegram_photo(screenshot_path, photo_caption)
                os.remove(screenshot_path)

            if sent_ok and extra_message:
                send_telegram_message(extra_message)

            if sent_ok and video_url:
                print(f"Post has a video attachment, downloading: {video_url}")
                video_file = download_video(video_url)
                if video_file and os.path.exists(video_file):
                    size_mb = os.path.getsize(video_file) / (1024 * 1024)
                    print(f"Video size: {size_mb:.2f} MB")
                    if size_mb <= 49.5:
                        send_telegram_video(video_file)
                    else:
                        print("Video too large for a bot upload (Telegram bot API cap ~50MB), skipped.")
                    os.remove(video_file)

            if sent_ok:
                save_sent_post(post_id)

        browser.close()


if __name__ == "__main__":
    main()
