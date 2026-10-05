"""Haftalik trend sinyallerini toplar (sadece basliklar ve sayisal veriler)."""
import os
import requests
import feedparser

UA = {"User-Agent": "weekly-trends-bot/0.1"}


def hacker_news(n=30):
    base = "https://hacker-news.firebaseio.com/v0"
    ids = requests.get(f"{base}/topstories.json", headers=UA, timeout=20).json()[:n]
    out = []
    for i in ids:
        try:
            it = requests.get(f"{base}/item/{i}.json", headers=UA, timeout=20).json()
        except Exception:
            continue
        if it and it.get("title"):
            out.append({"source": "hackernews", "title": it["title"], "score": it.get("score", 0)})
    return out


def rss(feeds, per_feed=10):
    out = []
    for url in feeds:
        try:
            parsed = feedparser.parse(url, agent=UA["User-Agent"])
        except Exception:
            continue
        for e in parsed.entries[:per_feed]:
            out.append({"source": url.split("/")[2], "title": e.get("title", "").strip()})
    return out


def youtube_trending(region, category, api_key, n=15):
    if not api_key:
        return []
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "snippet,statistics", "chart": "mostPopular",
                "regionCode": region, "videoCategoryId": category,
                "maxResults": n, "key": api_key},
        timeout=20,
    )
    if r.status_code != 200:
        return []
    out = []
    for v in r.json().get("items", []):
        out.append({"source": "youtube", "title": v["snippet"]["title"],
                    "views": int(v["statistics"].get("viewCount", 0))})
    return out


def collect(cfg):
    t = cfg["trends"]
    signals = []
    signals += hacker_news(t["hackernews_top_n"])
    signals += rss(t["rss_feeds"], t["rss_per_feed"])
    signals += youtube_trending(t["youtube_region"], t["youtube_category_id"],
                                os.environ.get("YOUTUBE_API_KEY"))
    return signals
