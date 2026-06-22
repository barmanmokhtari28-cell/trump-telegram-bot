import os
import re
import sys
import requests
import feedparser
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from playwright.sync_api import sync_playwright

# Constants
RSS_URL = "https://www.trumpstruth.org/feed"
SENT_POSTS_FILE = "sent_posts.txt"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

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
        translated = GoogleTranslator(source='en', target='fa').translate(text)
        return translated
    except Exception as e:
        print(f"Translation error: {e}")
        return ""

def get_video_url_from_page(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        video_tag = soup.find('video')
        if video_tag:
            if video_tag.get('src'):
                return video_tag.get('src')
            source_tag = video_tag.find('source')
            if source_tag and source_tag.get('src'):
                return source_tag.get('src')
        
        for link in soup.find_all('a', href=True):
            if '.mp4' in link['href']:
                return link['href']
    except Exception as e:
        print(f"Error checking video on page: {e}")
    return None

def download_video(video_url):
    local_filename = "temp_video.mp4"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        with requests.get(video_url, headers=headers, stream=True, timeout=90) as r:
            r.raise_for_status()
            with open(local_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return local_filename
    except Exception as e:
        print(f"Failed to download video: {e}")
        return None

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as photo:
        files = {"photo": photo}
        data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
        res = requests.post(url, files=files, data=data)
        if res.status_code != 200:
            print(f"Failed to send photo: {res.text}")
            return False
        return True

def send_telegram_video(video_path):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    with open(video_path, "rb") as video:
        files = {"video": video}
        data = {"chat_id": TELEGRAM_CHAT_ID}
        res = requests.post(url, files=files, data=data)
        if res.status_code != 200:
            print(f"Failed to send video: {res.text}")
            return False
        return True

def capture_screenshot(post_id, output_path):
    # Direct official Truth Social post URL
    official_url = f"https://truthsocial.com/@realDonaldTrump/posts/{post_id}"
    print(f"Navigating to official Truth Social URL: {official_url}")
    
    with sync_playwright() as p:
        # Launch browser mimicking normal desktop usage
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = browser.new_context(
            viewport={"width": 800, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        # Circumvent basic bot-detection scripts
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        try:
            page.goto(official_url, wait_until="load", timeout=30000)
            # Give the dynamic content and images some time to render
            page.wait_for_timeout(4000)
            
            # Hide the dynamic banners demanding login/sign-up which can cover the post
            page.add_style_tag(content="""
                div[role="dialog"], 
                div[class*="banner"], 
                div[class*="promo"], 
                div[class*="modal"], 
                .announcement-bar,
                .register-promo,
                .sign-up-banner { 
                    display: none !important; 
                }
            """)
            page.wait_for_timeout(1000)
            
            # Target the post card block containing his profile photo and post text
            selectors = [".detailed-status", "article", ".status", "main"]
            element = None
            for sel in selectors:
                try:
                    page.wait_for_selector(sel, timeout=3000)
                    element = page.locator(sel).first
                    if element:
                        element.screenshot(path=output_path)
                        print(f"Screenshotted official element: {sel}")
                        break
                except Exception:
                    continue
                    
            if not element:
                print("Element selectors failed, capturing full page fallback...")
                page.screenshot(path=output_path)
                
        except Exception as e:
            print(f"Could not load official page ({e}). Attempting fallback database screenshot...")
            # Fallback to the archive database if Cloudflare blocks the official page entirely
            fallback_url = f"https://trumpstruth.org/statuses/{post_id}"
            try:
                page.goto(fallback_url, wait_until="networkidle")
                page.wait_for_timeout(3000)
                # Capture the card which has his picture, title, and post content on the archive site
                page.locator(".detailed-status").first.screenshot(path=output_path)
                print("Fallback screenshot succeeded.")
            except Exception as e2:
                print(f"Fallback screenshot failed: {e2}")
                
        browser.close()

def clean_html_text(raw_html):
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text().strip()

def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Secrets missing in environment configuration.")
        sys.exit(1)

    sent_posts = get_sent_posts()
    feed = feedparser.parse(RSS_URL)
    
    items = feed.entries[:5]
    items.reverse()

    for item in items:
        guid = item.guid
        match = re.search(r"statuses/(\d+)", guid)
        if match:
            post_id = match.group(1)
        else:
            post_id = guid.split('/')[-1]

        if post_id in sent_posts:
            continue

        print(f"Processing post: {post_id}")
        
        raw_text = clean_html_text(item.description)
        translated_text = translate_to_persian(raw_text)
        
        # Matches your layout: "ترامپ:" on line 1, Persian translation on line 2
        caption = f"ترامپ:\n{translated_text}"
        
        screenshot_path = f"screenshot_{post_id}.png"
        
        # Capture from the official page instead of the archive site link
        capture_screenshot(post_id, screenshot_path)

        # Upload Screenshot with Caption
        success = send_telegram_photo(screenshot_path, caption)
        if not success:
            print(f"Upload failed. Skipping save track for ID: {post_id}")
            continue
            
        if os.path.exists(screenshot_path):
            os.remove(screenshot_path)

        # Find and upload video immediately after if it exists
        video_url = get_video_url_from_page(guid)
        if video_url:
            print(f"Downloading video from {video_url}")
            video_file = download_video(video_url)
            if video_file and os.path.exists(video_file):
                print("Uploading video...")
                send_telegram_video(video_file)
                os.remove(video_file)

        save_sent_post(post_id)

if __name__ == "__main__":
    main()
