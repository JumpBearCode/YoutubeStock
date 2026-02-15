import json
import re
from pathlib import Path

import yt_dlp

from .models import ChannelConfig

_CACHE_FILE = Path(".storage") / "channel_cache.json"


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        return json.loads(_CACHE_FILE.read_text())
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2))


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
