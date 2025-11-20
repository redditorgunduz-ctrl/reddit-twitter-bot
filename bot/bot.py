# bot/bot.py
import os
import time
import json
import random
import requests
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
USED_FILE = ROOT / "used_posts.json"
SUBREDDITS_FILE = ROOT / "subreddits.txt"
CAPTIONS_FILE = ROOT / "captions.txt"

# Ayarlar
MIN_UPVOTES = 0  # istersen popülerlik şartı koyabilirsin
SCREENSHOT_FILE = ROOT / "last_post.png"
TWITTER_URL = "https://twitter.com/"

# Helper - dosya yükleme / okuma
def read_lines(path):
    return [l.strip() for l in open(path, "r", encoding="utf-8").read().splitlines() if l.strip()]

def load_used():
    if not USED_FILE.exists():
        return []
    return json.loads(USED_FILE.read_text(encoding="utf-8"))

def save_used(used):
    USED_FILE.write_text(json.dumps(used, ensure_ascii=False, indent=2), encoding="utf-8")

def pick_caption():
    caps = read_lines(CAPTIONS_FILE)
    if not caps:
        return ""
    # shuffle on cycle
    return random.choice(caps)

# Reddit fetch
def fetch_candidates(subreddit):
    headers = {"User-Agent": "reddit-twitter-bot/0.1 by bot"}
    hot_url = f"https://www.reddit.com/{subreddit}/hot.json?limit=20"
    top_url = f"https://www.reddit.com/{subreddit}/top/.json?t=day&limit=20"
    cand = []
    try:
        r1 = requests.get(hot_url, headers=headers, timeout=15)
        r2 = requests.get(top_url, headers=headers, timeout=15)
        for r in (r1, r2):
            if r.status_code == 200:
                data = r.json()
                children = data.get("data", {}).get("children", [])
                for c in children:
                    d = c.get("data", {})
                    # Basit filtre: görsel değilse de post alınır; selftext veya title kullanacağız
                    post = {
                        "id": d.get("name"),
                        "title": d.get("title"),
                        "score": d.get("score", 0),
                        "permalink": d.get("permalink"),
                        "is_self": d.get("is_self", False),
                        "url": d.get("url"),
                        "num_comments": d.get("num_comments", 0),
                        "author": d.get("author")
                    }
                    cand.append(post)
    except Exception as e:
        print("Reddit fetch error:", e)
    return cand

# Seçim mantığı: hot ve top'tan gelenleri karıştırıyoruz, duplicate ve min_upvote kontrolü var
def choose_post(subreddits, used):
    random.shuffle(subreddits)
    headers = {"User-Agent": "reddit-twitter-bot/0.1 by bot"}
    for sub in subreddits:
        candidates = fetch_candidates(sub)
        if not candidates:
            continue
        # filtrele
        candidates = [c for c in candidates if c["id"] not in used and c["score"] >= MIN_UPVOTES]
        if not candidates:
            continue
        # rastgele seç
        post = random.choice(candidates)
        post["subreddit"] = sub
        return post
    return None

# Screenshot alma (Selenium headless)
def take_screenshot(reddit_permalink):
    url = f"https://www.reddit.com{reddit_permalink}"
    print("Opening", url)
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1200,1600")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(ChromeDriverManager().install(), options=chrome_options)
    try:
        driver.get(url)
        time.sleep(3)  # sayfanın yüklenmesi için bekle
        # Sayfanın tamamının screenshotunu al
        png = driver.get_screenshot_as_png()
        with open(SCREENSHOT_FILE, "wb") as f:
            f.write(png)
        print("Saved screenshot:", SCREENSHOT_FILE)
    finally:
        driver.quit()

    # Opsiyonel crop (kırpma) — şu an tam sayfa bırakıyoruz
    return SCREENSHOT_FILE

# Twitter'a web otomasyon ile gönderi atma
def twitter_post(caption_text, image_path):
    username = os.environ.get("TWITTER_USERNAME")
    password = os.environ.get("TWITTER_PASSWORD")
    if not username or not password:
        print("TWITTER_USERNAME/TWITTER_PASSWORD environment variables not set.")
        return False

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1200,1200")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(ChromeDriverManager().install(), options=chrome_options)
    try:
        driver.get("https://twitter.com/i/flow/login")
        time.sleep(4)
        # Basit login denemesi — Twitter ara yüz değişebilir, if durumunda manuel müdahale gerekebilir
        # Kullanıcı adı / telefon / email alanına yaz
        # HTML elementleri değişebildiği için burada birkaç deneme yapılır
        try:
            # username
            username_input = driver.find_element(By.NAME, "text")
            username_input.send_keys(username)
            username_input.send_keys("\n")
            time.sleep(2)
        except Exception:
            pass
        # password alanı
        try:
            password_input = driver.find_element(By.NAME, "password")
            password_input.send_keys(password)
            password_input.send_keys("\n")
            time.sleep(4)
        except Exception:
            # bazı durumlarda ek adımlar olabilir (email doğrulama vs)
            print("Could not auto-fill password - twitter flow may require extra steps.")
            # yine de devam etmeye çalış
            time.sleep(5)

        # artık logged-in mı kontrol et
        # tweet atma sayfasını aç
        driver.get("https://twitter.com/compose/tweet")
        time.sleep(3)

        # tweet kutusunu bulup yaz
        # Twitter DOM dinamik, alternatif seçiciler kullanılabilir
        try:
            tweetbox = driver.find_element(By.XPATH, "//div[@aria-label='Tweet text']")
            tweetbox.click()
            tweetbox.send_keys(caption_text)
        except Exception:
            try:
                tweetbox = driver.find_element(By.XPATH, "//div[contains(@class,'public-DraftStyleDefault-block')]")
                tweetbox.click()
                tweetbox.send_keys(caption_text)
            except Exception as e:
                print("Tweet box not found:", e)
                return False

        # görsel yükleme
        try:
            upload_input = driver.find_element(By.XPATH, "//input[@type='file']")
            upload_input.send_keys(str(image_path.resolve()))
            time.sleep(3)
        except Exception as e:
            print("Image upload failed:", e)

        # Tweet butonunu bul ve tıkla
        try:
            buttons = driver.find_elements(By.XPATH, "//div[@data-testid='tweetButtonInline']")
            if buttons:
                buttons[0].click()
            else:
                # fallback: başka buton
                tweet_btn = driver.find_element(By.XPATH, "//div[@role='button' and contains(., 'Tweet')]")
                tweet_btn.click()
            time.sleep(5)
            print("Tweet posted.")
            return True
        except Exception as e:
            print("Could not click tweet button:", e)
            return False
    finally:
        driver.quit()

# Main flow
def main():
    subreddits = read_lines(SUBREDDITS_FILE)
    captions = read_lines(CAPTIONS_FILE)
    used = load_used()
    post = choose_post(subreddits, used)
    if not post:
        print("No post found.")
        return

    print("Selected post:", post["title"], post["permalink"], post["score"])
    # screenshot
    img_path = take_screenshot(post["permalink"])

    # caption
    caption = pick_caption()
    # complement caption with subreddit + author
    caption_text = f"{caption}\n\n📌 r/{post['subreddit'].replace('r/','')}\n📝 {post.get('title')[:240]}"

    # tweet
    ok = twitter_post(caption_text, img_path)
    if ok:
        used.append(post["id"])
        save_used(used)
    else:
        print("Tweet failed; not marking as used.")

if __name__ == "__main__":
    main()
