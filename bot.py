import os
import re
import sys
import html
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from deep_translator import GoogleTranslator
from playwright.sync_api import sync_playwright

# ==========================================
# CONFIGURATION SETTINGS
# ==========================================
RSS_URL = "https://www.trumpstruth.org/feed"
SENT_POSTS_FILE = "sent_posts.txt"

# Your exact Telegram channel promotional username:
CHANNEL_USERNAME = "🤖 @secretollah" 

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
# ==========================================

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
        data = {
            "chat_id": TELEGRAM_CHAT_ID, 
            "caption": caption,
            "parse_mode": "HTML"
        }
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
    archive_url = f"https://trumpstruth.org/statuses/{post_id}"
    print(f"Navigating to clean archive URL: {archive_url}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox"]
        )
        context = browser.new_context(
            viewport={"width": 800, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        try:
            page.goto(archive_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(4000)
            
            selectors = [".detailed-status", "article", ".status", "main"]
            element = None
            for sel in selectors:
                try:
                    page.wait_for_selector(sel, timeout=4000)
                    element = page.locator(sel).first
                    if element:
                        element.screenshot(path=output_path)
                        print(f"Successfully screenshotted post block: {sel}")
                        break
                except Exception:
                    continue
                    
            if not element:
                print("Target block selector failed, capturing fallback viewport...")
                page.screenshot(path=output_path)
                
        except Exception as e:
            print(f"Failed to load or capture screenshot: {e}")
            
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
        
        # 1. Safe HTML escaping for Telegram formatting
        escaped_translation = html.escape(translated_text)
        escaped_original = html.escape(raw_text)
        escaped_username = html.escape(CHANNEL_USERNAME)
        escaped_guid = html.escape(guid)
        
        # 2. Convert Publication Time to Tehran Time (UTC + 3:30)
        if hasattr(item, 'published_parsed') and item.published_parsed:
            utc_dt = datetime(*item.published_parsed[:6])
        else:
            utc_dt = datetime.utcnow()
        tehran_dt = utc_dt + timedelta(hours=3, minutes=30)
        time_string = tehran_dt.strftime("%H:%M")
        
        # 3. Dynamic Hashtags
        lower_raw = raw_text.lower()
        hashtags = ["#ترامپ", "#آمریکا"]
        if "ایران" in translated_text or "iran" in lower_raw:
            hashtags.append("#ایران")
        if "چین" in translated_text or "china" in lower_raw:
            hashtags.append("#چین")
        if "روسیه" in translated_text or "russia" in lower_raw:
            hashtags.append("#روسیه")
        if "اسرائیل" in translated_text or "israel" in lower_raw:
            hashtags.append("#اسرائیل")
        if "انتخابات" in translated_text or "election" in lower_raw:
            hashtags.append("#انتخابات")
        if "فوری" in translated_text or "breaking" in lower_raw:
            hashtags.append("#فوری")
            
        hashtag_string = " ".join(hashtags)
        
        # Unicode Right-to-Left Mark (RLM) to ensure correct Persian typography
        RLM = "\u200f"
        
        # 4. Generate the Comprehensive Layout
        caption = (
            f"{RLM}🇺🇸 <b>دونــالـــد تـرامــپِ شـــیردل:</b>\n"
            f"<blockquote>{RLM}{escaped_translation}</blockquote>\n\n"
            f"{RLM}🇺🇸 <i>متن اصلی (جهت مشاهده ضربه بزنید):</i>\n"
            f"<tg-spoiler>{escaped_original}</tg-spoiler>\n\n"
            f"{RLM}⏰ ساعت انتشار (به وقت تهران): {time_string}\n"
            f"{RLM}🔗 <a href='{escaped_guid}'>مشاهده پست اصلی در Truth Social</a>\n\n"
            f"{RLM}{hashtag_string}\n"
            f"{RLM}{escaped_username}"
        )
        
        screenshot_path = f"screenshot_{post_id}.png"
        capture_screenshot(post_id, screenshot_path)

        success = send_telegram_photo(screenshot_path, caption)
        if not success:
            print(f"Upload failed. Skipping save track for ID: {post_id}")
            continue
            
        if os.path.exists(screenshot_path):
            os.remove(screenshot_path)

        # Download and send video if attached
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
