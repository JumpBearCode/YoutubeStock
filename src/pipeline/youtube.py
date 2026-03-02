import json
import re
from datetime import datetime
from pathlib import Path

import yt_dlp

from ..models import ChannelConfig, VideoInfo

_CACHE_FILE = Path(".storage") / "channel_cache.json"


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        return json.loads(_CACHE_FILE.read_text())
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def _extract_channel_id_from_url(url: str) -> str | None:
    """Extract channel_id directly if URL is /channel/UCxxx format."""
    m = re.search(r"/channel/(UC[\w-]+)", url)
    return m.group(1) if m else None


def _resolve_via_ytdlp(url: str) -> tuple[str, str]:
    """Use yt-dlp to extract channel_id and channel name from any YouTube URL."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlist_items": "0",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    channel_id = info.get("channel_id") or info.get("id")
    channel_name = info.get("channel") or info.get("uploader") or info.get("title")

    if not channel_id:
        raise ValueError(f"Could not resolve channel_id from {url}")
    if not channel_name:
        channel_name = channel_id

    return channel_id, channel_name


def resolve_channel(url: str, last_n: int | None = None) -> ChannelConfig:
    """Resolve a YouTube channel URL to a ChannelConfig with id and name."""
    url = url.strip().rstrip("/")

    cache = _load_cache()
    if url in cache:
        entry = cache[url]
        print(f"  Cached: {entry['channel_name']} ({entry['channel_id']})")
        return ChannelConfig(
            channel_id=entry["channel_id"],
            channel_name=entry["channel_name"],
            last_n=last_n,
        )

    # Try direct extraction from /channel/UCxxx URLs
    channel_id = _extract_channel_id_from_url(url)

    if channel_id:
        # We have the ID, use yt-dlp just for the name
        _, channel_name = _resolve_via_ytdlp(url)
    else:
        print(f"  Resolving {url} ...")
        channel_id, channel_name = _resolve_via_ytdlp(url)

    print(f"  Resolved: {channel_name} ({channel_id})")

    cache[url] = {"channel_id": channel_id, "channel_name": channel_name}
    _save_cache(cache)

    return ChannelConfig(
        channel_id=channel_id,
        channel_name=channel_name,
        last_n=last_n,
    )


def _get_video_timestamp(video_id: str) -> datetime | None:
    """Fetch precise upload timestamp for a single video via yt-dlp."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        ts = info.get("timestamp") or info.get("release_timestamp")
        if ts:
            return datetime.fromtimestamp(ts)
        upload_date = info.get("upload_date")  # "YYYYMMDD"
        if upload_date:
            return datetime.strptime(upload_date, "%Y%m%d")
    except Exception as e:
        print(f"  Warning: failed to get timestamp for {video_id}: {e}")
    return None


def _flat_extract(channel_id: str, tab: str, n: int) -> list[tuple[str, str]]:
    """Flat-extract up to n entries from a channel tab, filtering upcoming/live."""
    url = f"https://www.youtube.com/channel/{channel_id}/{tab}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": n,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    candidates = []
    for entry in (info or {}).get("entries", []):
        video_id = entry.get("id")
        title = entry.get("title")
        if not video_id or not title:
            continue

        live_status = entry.get("live_status")
        if live_status in ("is_upcoming", "is_live"):
            print(f"  Skipping {live_status}: {title}")
            continue

        candidates.append((video_id, title))
    return candidates


def get_latest_videos(channel: ChannelConfig, n: int) -> list[VideoInfo]:
    """Fetch latest videos using two-step yt-dlp extraction.

    Step 1: Flat extraction from both /videos and /streams tabs.
    Step 2: Per-video extraction to get precise upload timestamp.
    Results are merged, deduplicated, sorted by time, and trimmed to n.
    """
    # Fetch extra to account for pinned entries
    fetch_n = n + 2

    # Step 1: flat-extract from both tabs
    vid_candidates = _flat_extract(channel.channel_id, "videos", fetch_n)
    stream_candidates = _flat_extract(channel.channel_id, "streams", fetch_n)

    # Deduplicate by video_id, preserving order
    seen = set()
    candidates = []
    for vid_id, title in vid_candidates + stream_candidates:
        if vid_id not in seen:
            seen.add(vid_id)
            candidates.append((vid_id, title))

    # Step 2: fetch precise timestamp per video
    videos = []
    for video_id, title in candidates:
        published = _get_video_timestamp(video_id) or datetime.now()
        videos.append(
            VideoInfo(
                video_id=video_id,
                title=title,
                channel_id=channel.channel_id,
                channel_name=channel.channel_name,
                published=published,
                link=f"https://www.youtube.com/watch?v={video_id}",
            )
        )

    # Sort by publish time (newest first), return top n
    videos.sort(key=lambda v: v.published, reverse=True)
    return videos[:n]
