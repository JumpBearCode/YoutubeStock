import argparse
import importlib
import sys
from datetime import datetime

from .downloader import download_audio, download_video
from .models import ChannelConfig, VideoInfo
from .resolver import resolve_channel
from .rss import fetch_channel_feed, get_latest_videos
from .sleep_strategy import inter_channel_sleep, random_sleep
from .storage import StorageManager


def load_config() -> dict:
    spec = importlib.util.spec_from_file_location("config", "config.py")
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)

    print("Resolving channels...")
    channels = []
    for ch in config.CHANNELS:
        last_n = ch.get("last_n") or config.GLOBAL_LAST_N
        last_n = min(last_n, 15)
        channel = resolve_channel(ch["url"], last_n=last_n)
        channels.append(channel)

    return {
        "channels": channels,
        "sleep_min": config.SLEEP_MIN,
        "sleep_max": config.SLEEP_MAX,
        "storage_dir": config.STORAGE_DIR,
        "video_format": config.VIDEO_FORMAT,
        "audio_format": config.AUDIO_FORMAT,
        "audio_codec": config.AUDIO_CODEC,
        "audio_quality": config.AUDIO_QUALITY,
        "check_max_new": getattr(config, "CHECK_MAX_NEW", 5),
    }


def _process_video(
    video: VideoInfo, cfg: dict, storage: StorageManager, verbose: bool
) -> None:
    timestamp = video.published

    video_dir = storage.get_video_dir(video.channel_name, timestamp)
    if verbose:
        print(f"  Downloading video to {video_dir}")
    video_result = download_video(video, video_dir, cfg["video_format"])
    if video_result.success:
        print(f"  Video OK: {video_result.file_path}")
    else:
        print(f"  Video FAILED: {video_result.error}")

    random_sleep(cfg["sleep_min"], cfg["sleep_max"])

    audio_dir = storage.get_audio_dir(video.channel_name, timestamp)
    if verbose:
        print(f"  Downloading audio to {audio_dir}")
    audio_result = download_audio(
        video, audio_dir, cfg["audio_format"], cfg["audio_codec"], cfg["audio_quality"]
    )
    if audio_result.success:
        print(f"  Audio OK: {audio_result.file_path}")
    else:
        print(f"  Audio FAILED: {audio_result.error}")

    if video_result.success or audio_result.success:
        storage.mark_downloaded(
            video,
            video_result.file_path if video_result.success else None,
            audio_result.file_path if audio_result.success else None,
        )


def cmd_check(cfg: dict, verbose: bool) -> None:
    storage = StorageManager(cfg["storage_dir"])
    channels = cfg["channels"]
    max_new = cfg["check_max_new"]

    for i, channel in enumerate(channels):
        print(f"\n[{channel.channel_name}] Checking latest {max_new} videos...")
        videos = get_latest_videos(channel, max_new)
        new_videos = [v for v in videos if not storage.is_downloaded(channel.channel_name, v.video_id)]

        if not new_videos:
            print(f"  All up to date, no new videos.")
            continue

        print(f"  {len(new_videos)} new video(s) to download")

        for j, video in enumerate(new_videos):
            print(f"\n  Processing ({j+1}/{len(new_videos)}): {video.title}")
            _process_video(video, cfg, storage, verbose)

            if j < len(new_videos) - 1:
                random_sleep(cfg["sleep_min"], cfg["sleep_max"])

        if i < len(channels) - 1:
            inter_channel_sleep(cfg["sleep_min"], cfg["sleep_max"])


def cmd_download(cfg: dict, verbose: bool) -> None:
    storage = StorageManager(cfg["storage_dir"])
    channels = cfg["channels"]

    for i, channel in enumerate(channels):
        print(f"\n[{channel.channel_name}] Fetching latest {channel.last_n} videos...")
        videos = get_latest_videos(channel, channel.last_n)
        to_download = [v for v in videos if not storage.is_downloaded(channel.channel_name, v.video_id)]

        if not to_download:
            print(f"  All videos already downloaded.")
            continue

        print(f"  {len(to_download)} video(s) to download (skipping {len(videos) - len(to_download)} already downloaded)")
        for j, video in enumerate(to_download):
            print(f"\n  Processing ({j+1}/{len(to_download)}): {video.title}")
            _process_video(video, cfg, storage, verbose)

            if j < len(to_download) - 1:
                random_sleep(cfg["sleep_min"], cfg["sleep_max"])

        if i < len(channels) - 1:
            inter_channel_sleep(cfg["sleep_min"], cfg["sleep_max"])


def main():
    parser = argparse.ArgumentParser(description="YoutubeStock - YouTube Video/Audio Downloader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Check for new videos and download them")
    check_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    download_parser = subparsers.add_parser("download", help="Download latest N videos per channel")
    download_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    try:
        cfg = load_config()
    except Exception as e:
        print(f"Error loading config.py: {e}", file=sys.stderr)
        sys.exit(1)

    if args.command == "check":
        cmd_check(cfg, args.verbose)
    elif args.command == "download":
        cmd_download(cfg, args.verbose)

    print("\nDone.")


if __name__ == "__main__":
    main()
