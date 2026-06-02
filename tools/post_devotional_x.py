"""
Posts the daily Scripture & Soul verse to X and optionally replies to mentions with verses.

Usage:
  python tools/post_devotional_x.py          # post daily verse
  python tools/post_devotional_x.py --reply  # post + process mentions (requires paid API tier)

X credentials: BIBLE_X_API_KEY etc. in .env (separate from JStoutHorse account)
"""

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

import tweepy
from anthropic import Anthropic
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

API_KEY      = os.getenv("BIBLE_X_API_KEY")
API_SECRET   = os.getenv("BIBLE_X_API_SECRET")
ACCESS_TOKEN = os.getenv("BIBLE_X_ACCESS_TOKEN")
ACCESS_SECRET= os.getenv("BIBLE_X_ACCESS_SECRET")

claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
SHOTS_DIR = Path(".tmp/screenshots")


def credentials_set():
    return all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET,
                "your_" not in (API_KEY or "")])


def get_clients():
    auth = tweepy.OAuth1UserHandler(API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET)
    v1 = tweepy.API(auth)
    v2 = tweepy.Client(
        consumer_key=API_KEY, consumer_secret=API_SECRET,
        access_token=ACCESS_TOKEN, access_token_secret=ACCESS_SECRET,
    )
    return v1, v2


def get_daily_verse() -> dict:
    today = date.today().strftime("%B %d, %Y")
    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": f"""Return a JSON devotional for {today}:
{{
  "verse_reference": "Book Chapter:Verse",
  "verse_text": "Full KJV verse (under 200 chars)",
  "theme": "One word",
  "tweet": "A warm 240-char tweet sharing this verse with hashtags #Scripture #BibleVerse #Faith #DailyVerse"
}}
JSON only."""}],
    )
    raw = resp.content[0].text.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part.startswith("json"): part = part[4:].strip()
            if part.startswith("{"): raw = part; break
    return json.loads(raw)


def screenshot_verse_card(verse: dict) -> Path | None:
    """Render a beautiful verse image for the tweet."""
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    out = SHOTS_DIR / "daily_verse.png"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;1,400&family=Cinzel:wght@600&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ width:800px; height:418px; background:#0f0b04;
    display:flex; align-items:center; justify-content:center; }}
  .card {{
    width:740px; background:linear-gradient(145deg,#1a1208,#141008);
    border:1px solid #2a1e0e; border-top:4px solid #8b6914;
    border-radius:4px; padding:40px 48px; text-align:center;
  }}
  .ornament {{ font-size:20px; color:#8b6914; letter-spacing:6px; margin-bottom:20px; }}
  .verse {{ font-family:'EB Garamond',serif; font-size:22px; font-style:italic;
    color:#e8d5b8; line-height:1.7; margin-bottom:20px; }}
  .ref {{ font-family:'Cinzel',serif; font-size:12px; letter-spacing:3px;
    color:#8b6914; margin-bottom:14px; }}
  .brand {{ font-family:'Cinzel',serif; font-size:11px; letter-spacing:4px;
    color:#3a2a14; text-transform:uppercase; }}
</style></head>
<body><div class="card">
  <div class="ornament">☩ ✦ ☩</div>
  <div class="verse">&ldquo;{verse['verse_text']}&rdquo;</div>
  <div class="ref">— {verse['verse_reference']} &nbsp;·&nbsp; KJV</div>
  <div class="brand">Scripture &amp; Soul &nbsp;·&nbsp; Daily Verse</div>
</div></body></html>"""

    tmp = SHOTS_DIR / "verse_card.html"
    tmp.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 800, "height": 418})
            page.goto(f"file:///{tmp.resolve().as_posix()}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(out), clip={"x":0,"y":0,"width":800,"height":418})
            browser.close()
        return out
    except Exception as e:
        print(f"  Screenshot failed: {e}")
        return None


def reply_to_mentions(v1, v2):
    """Read recent mentions and reply with relevant verses."""
    print("Checking mentions...")
    try:
        me = v2.get_me()
        user_id = me.data.id
        mentions = v2.get_users_mentions(id=user_id, max_results=10)
        if not mentions.data:
            print("  No new mentions.")
            return

        for mention in mentions.data:
            text = mention.text
            tweet_id = mention.id
            print(f"  Replying to: {text[:60]}...")

            resp = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=400,
                messages=[{"role": "user", "content": f"""Someone tweeted at a Bible verse account: "{text}"

Write a warm, brief reply (under 250 chars) with one relevant Bible verse reference and a short word of encouragement.
Format: [Encouragement]. [Book Chapter:Verse] — "[short verse quote]" #Scripture"""}],
            )
            reply_text = resp.content[0].text.strip()[:280]

            v2.create_tweet(text=reply_text, in_reply_to_tweet_id=tweet_id)
            print(f"  Replied.")
            time.sleep(3)

    except tweepy.errors.Forbidden:
        print("  Mention replies require paid X API tier.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reply", action="store_true", help="Also process and reply to mentions")
    parser.add_argument("--preview", action="store_true", help="Print tweet text only, don't post")
    args = parser.parse_args()

    verse = get_daily_verse()
    print(f"Today's verse: {verse.get('verse_reference')} — {verse.get('theme')}")

    tweet_text = verse.get("tweet", "")
    print(f"\nTweet:\n{tweet_text}\n")

    if args.preview:
        img = screenshot_verse_card(verse)
        if img:
            print(f"Image saved: {img}")
        return

    if not credentials_set():
        print("BIBLE_X_API_KEY not set in .env — saving preview only.")
        screenshot_verse_card(verse)
        # Save text for manual posting
        out = Path(f"posts/{date.today().strftime('%Y-%m-%d')}/Scripture_Soul")
        out.mkdir(parents=True, exist_ok=True)
        (out / "verse_tweet.txt").write_text(tweet_text, encoding="utf-8")
        print(f"Tweet text saved to {out}/verse_tweet.txt")
        return

    v1, v2 = get_clients()
    img = screenshot_verse_card(verse)

    media_ids = []
    if img and img.exists():
        try:
            media = v1.media_upload(filename=str(img))
            media_ids.append(media.media_id)
        except Exception as e:
            print(f"  Media upload failed: {e}")

    kwargs = {"text": tweet_text[:280]}
    if media_ids:
        kwargs["media_ids"] = media_ids

    resp = v2.create_tweet(**kwargs)
    print(f"Posted! Tweet ID: {resp.data['id']}")

    if args.reply:
        time.sleep(2)
        reply_to_mentions(v1, v2)


if __name__ == "__main__":
    main()
