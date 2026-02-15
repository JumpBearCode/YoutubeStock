import argparse
import importlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .downloader import download_audio, download_video
from .models import ChannelConfig, VideoInfo
from .resolver import resolve_channel
from .rss import fetch_channel_feed, get_latest_videos
from .sleep_strategy import inter_channel_sleep, random_sleep
from .storage import StorageManager
from .transcriber import PROVIDERS, audio_path_to_transcript_path, transcribe


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


def _load_transcript_config() -> tuple[str, list[str]]:
    """Load provider and API keys from .env. Returns (provider, api_keys)."""
    load_dotenv()
    provider = os.getenv("TRANSCRIPTION_PROVIDER", "groq").lower()
    if provider not in PROVIDERS:
        print(f"Error: Unknown TRANSCRIPTION_PROVIDER '{provider}'. Use 'groq' or 'openai'.", file=sys.stderr)
        sys.exit(1)

    if provider == "groq":
        free_key = os.getenv("GROQ_API_KEY")
        paid_key = os.getenv("GROQ_API_KEY_PAID")
        api_keys = [k for k in [free_key, paid_key] if k]
        if not api_keys:
            print("Error: GROQ_API_KEY not set. Create a .env file with your key.", file=sys.stderr)
            print("See .env.example for reference.", file=sys.stderr)
            sys.exit(1)
    else:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("Error: OPENAI_API_KEY not set. Create a .env file with your key.", file=sys.stderr)
            print("See .env.example for reference.", file=sys.stderr)
            sys.exit(1)
        api_keys = [api_key]

    return provider, api_keys


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def cmd_transcript(cfg: dict, verbose: bool, language: str | None, channel_filter: str | None, path: str | None) -> None:
    provider, api_keys = _load_transcript_config()
    cost_per_minute = PROVIDERS[provider]["cost_per_minute"]
    fallback_info = f", fallback: paid key" if len(api_keys) > 1 else ""
    print(f"Using provider: {provider} (model: {PROVIDERS[provider]['model']}, ${cost_per_minute}/min{fallback_info})")

    total_duration = 0.0
    total_files = 0

    if path:
        # --path mode: single file, output in same directory
        audio_path = path
        if not Path(audio_path).exists():
            print(f"Error: File not found: {audio_path}", file=sys.stderr)
            sys.exit(1)
        output_path = str(Path(audio_path).with_suffix(".json"))
        if Path(output_path).exists():
            print(f"  Skipping (already exists): {output_path}")
            return
        print(f"  Transcribing: {audio_path}")
        result = transcribe(audio_path, output_path, api_keys, provider, language, verbose)
        if result.success:
            cost = result.duration_seconds / 60 * cost_per_minute
            print(f"  Transcript OK: {result.transcript_path} (duration: {_format_duration(result.duration_seconds)}, cost: ${cost:.3f})")
            total_duration += result.duration_seconds
            total_files += 1
        else:
            print(f"  Transcript FAILED: {result.error}")
    else:
        # Channel or all-channels mode
        storage_dir = Path(cfg["storage_dir"])
        channels = cfg["channels"]

        for channel in channels:
            if channel_filter and channel.channel_name != channel_filter:
                continue

            history_file = storage_dir / channel.channel_name / "download_history.json"
            if not history_file.exists():
                if verbose:
                    print(f"\n[{channel.channel_name}] No download history, skipping.")
                continue

            history = json.loads(history_file.read_text())
            audio_paths = [
                entry["audio_path"]
                for entry in history.values()
                if entry.get("audio_path")
            ]

            if not audio_paths:
                if verbose:
                    print(f"\n[{channel.channel_name}] No audio files found.")
                continue

            print(f"\n[{channel.channel_name}] {len(audio_paths)} audio file(s) to check")

            for audio_path in audio_paths:
                if not Path(audio_path).exists():
                    if verbose:
                        print(f"  Skipping (file missing): {audio_path}")
                    continue

                transcript_path = audio_path_to_transcript_path(audio_path)
                if Path(transcript_path).exists():
                    if verbose:
                        print(f"  Skipping (already exists): {transcript_path}")
                    continue

                print(f"  Transcribing: {audio_path}")
                result = transcribe(audio_path, transcript_path, api_keys, provider, language, verbose)
                if result.success:
                    cost = result.duration_seconds / 60 * cost_per_minute
                    print(f"  Transcript OK: {result.transcript_path} (duration: {_format_duration(result.duration_seconds)}, cost: ${cost:.3f})")
                    total_duration += result.duration_seconds
                    total_files += 1
                else:
                    print(f"  Transcript FAILED: {result.error}")

    if total_files > 0:
        total_cost = total_duration / 60 * cost_per_minute
        print(f"\nSummary: {total_files} file(s) transcribed, total duration: {_format_duration(total_duration)}, total cost: ${total_cost:.3f}")
    elif not path:
        print("\nNo new files to transcribe.")


def main():
    parser = argparse.ArgumentParser(description="YoutubeStock - YouTube Video/Audio Downloader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Check for new videos and download them")
    check_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    download_parser = subparsers.add_parser("download", help="Download latest N videos per channel")
    download_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    transcript_parser = subparsers.add_parser("transcript", help="Transcribe downloaded audio using OpenAI Whisper")
    transcript_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    transcript_parser.add_argument("--language", "-l", type=str, default=None, help="Force language (ISO-639-1: 'en', 'zh'). Auto-detects if omitted.")
    transcript_parser.add_argument("--channel", type=str, default=None, help="Transcribe only this channel")
    transcript_parser.add_argument("--path", type=str, default=None, help="Transcribe a specific audio file")

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
    elif args.command == "transcript":
        cmd_transcript(cfg, args.verbose, args.language, args.channel, args.path)

    print("\nDone.")


if __name__ == "__main__":
    main()
