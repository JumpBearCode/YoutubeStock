import time
from datetime import datetime

import feedparser

from .models import ChannelConfig, VideoInfo

RSS_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def fetch_channel_feed(channel: ChannelConfig) -> list[VideoInfo]:
    url = RSS_URL_TEMPLATE.format(channel_id=channel.channel_id)
    feed = feedparser.parse(url)

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


def get_latest_videos(channel: ChannelConfig, n: int) -> list[VideoInfo]:
    videos = fetch_channel_feed(channel)
    return videos[:n]
