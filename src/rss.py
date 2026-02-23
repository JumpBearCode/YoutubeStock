import time
from datetime import datetime

import feedparser
import yt_dlp

from .models import ChannelConfig, VideoInfo

RSS_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


def _fetch_via_rss(channel: ChannelConfig) -> list[VideoInfo]:
    """Fetch videos via YouTube RSS feed."""
    url = RSS_URL_TEMPLATE.format(channel_id=channel.channel_id)

    feed = None
    for attempt in range(MAX_RETRIES):
        feed = feedparser.parse(url, agent=USER_AGENT)
        if feed.entries or (hasattr(feed, 'status') and feed.status == 200):
            break
        if attempt < MAX_RETRIES - 1:
            print(f"  RSS attempt {attempt + 1} failed (status={getattr(feed, 'status', 'N/A')}), retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

    if not feed or not feed.entries:
        return []

    videos = []
    for entry in feed.entries:
        published = datetime(*entry.published_parsed[:6])
        videos.append(
            VideoInfo(
                video_id=entry.yt_videoid,
                title=entry.title,
                channel_id=channel.channel_id,
                channel_name=channel.channel_name,
                published=published,
                link=entry.link,
            )
        )
    return videos


def _fetch_via_ytdlp(channel: ChannelConfig, n: int) -> list[VideoInfo]:
    """Fallback: fetch videos via yt-dlp channel listing."""
    channel_url = f"https://www.youtube.com/channel/{channel.channel_id}/videos"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": n,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    videos = []
    for entry in info.get("entries", []):
        video_id = entry.get("id")
        title = entry.get("title")
        if not video_id or not title:
            continue
        videos.append(
            VideoInfo(
                video_id=video_id,
                title=title,
                channel_id=channel.channel_id,
                channel_name=channel.channel_name,
                published=datetime.now(),
                link=f"https://www.youtube.com/watch?v={video_id}",
            )
        )
    return videos


def fetch_channel_feed(channel: ChannelConfig, n: int = 15) -> list[VideoInfo]:
    """Fetch channel videos. Tries RSS first, falls back to yt-dlp."""
    videos = _fetch_via_rss(channel)
    if videos:
        return videos

    print(f"  RSS feed failed for {channel.channel_name}, falling back to yt-dlp...")
    return _fetch_via_ytdlp(channel, n)


def get_latest_videos(channel: ChannelConfig, n: int) -> list[VideoInfo]:
    videos = fetch_channel_feed(channel, n)
    return videos[:n]
