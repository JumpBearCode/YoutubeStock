import argparse
import importlib
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .models import VideoInfo
from .sleep_strategy import inter_channel_sleep, random_sleep
from .pipeline.downloader import download_audio
from .pipeline.youtube import resolve_channel, get_latest_videos
from .pipeline.storage import StorageManager
from .agent.parser import parse_transcript
from .agent.summarizer import summarize_transcript, summarize_batch, extract_info_from_path
from .agent.transcriber import PROVIDERS, audio_path_to_transcript_path, transcribe


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
        "audio_format": config.AUDIO_FORMAT,
        "audio_codec": config.AUDIO_CODEC,
        "audio_quality": config.AUDIO_QUALITY,
        "check_max_new": getattr(config, "CHECK_MAX_NEW", 5),
    }


def _process_entry(
    video: VideoInfo, cfg: dict, storage: StorageManager, verbose: bool
) -> None:
    timestamp = video.published

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

    if audio_result.success:
        storage.mark_downloaded(video, audio_result.file_path)


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
            _process_entry(video, cfg, storage, verbose)

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
            _process_entry(video, cfg, storage, verbose)

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


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def cmd_parse(cfg: dict, verbose: bool, model: str, channel_filter: str | None, path: str | None) -> None:
    from .agent.parser import MODEL_PRICING
    pricing = MODEL_PRICING.get(model, {})
    pricing_info = f"${pricing.get('input', '?')}/M in, ${pricing.get('output', '?')}/M out" if pricing else "unknown pricing"
    print(f"Using model: {model} ({pricing_info})")

    total_files = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    skipped = 0

    if path:
        p = Path(path)
        if not p.exists():
            print(f"Error: File not found: {path}", file=sys.stderr)
            sys.exit(1)
        print(f"  Parsing: {path}")
        result = parse_transcript(str(p), model=model, verbose=verbose)
        if result and result.success:
            print(f"  Parsed OK: {result.output_path} (tokens: {_format_tokens(result.input_tokens)} in / {_format_tokens(result.output_tokens)} out, cost: ${result.cost:.4f})")
            total_files += 1
            total_input_tokens += result.input_tokens
            total_output_tokens += result.output_tokens
            total_cost += result.cost
        elif result:
            print(f"  Parse FAILED: {result.error}")
    else:
        storage_dir = Path(cfg["storage_dir"])
        channels = cfg["channels"]

        for channel in channels:
            if channel_filter and channel.channel_name != channel_filter:
                continue

            transcript_dir = storage_dir / channel.channel_name / "transcript"
            if not transcript_dir.exists():
                if verbose:
                    print(f"\n[{channel.channel_name}] No transcript directory, skipping.")
                continue

            txt_files = [
                f for f in sorted(transcript_dir.rglob("*.txt"))
                if not f.name.endswith("_parsed.txt")
            ]

            if not txt_files:
                if verbose:
                    print(f"\n[{channel.channel_name}] No transcript files found.")
                continue

            print(f"\n[{channel.channel_name}] {len(txt_files)} transcript file(s) to check")

            for txt_file in txt_files:
                parsed_path = txt_file.with_name(txt_file.stem + "_parsed.txt")
                if parsed_path.exists():
                    skipped += 1
                    if verbose:
                        print(f"  Skipping (already exists): {parsed_path}")
                    continue

                print(f"  Parsing: {txt_file}")
                result = parse_transcript(str(txt_file), model=model, verbose=verbose)
                if result and result.success:
                    print(f"  Parsed OK: {result.output_path} (tokens: {_format_tokens(result.input_tokens)} in / {_format_tokens(result.output_tokens)} out, cost: ${result.cost:.4f})")
                    total_files += 1
                    total_input_tokens += result.input_tokens
                    total_output_tokens += result.output_tokens
                    total_cost += result.cost
                elif result:
                    print(f"  Parse FAILED: {result.error}")

    if total_files > 0:
        print(f"\nSummary: {total_files} file(s) parsed, tokens: {_format_tokens(total_input_tokens)} in / {_format_tokens(total_output_tokens)} out, total cost: ${total_cost:.4f}")
    elif not path:
        skipped_msg = f" ({skipped} already parsed)" if skipped else ""
        print(f"\nNo new files to parse.{skipped_msg}")


def cmd_summary(cfg: dict, verbose: bool, channel_filter: str | None, path: str | None) -> None:
    from .agent.summarizer import MODEL, MODEL_PRICING
    pricing = MODEL_PRICING[MODEL]
    pricing_info = f"${pricing['input']}/M in, ${pricing['output']}/M out"
    print(f"Using model: {MODEL} ({pricing_info})")

    if path:
        # --path mode: single _parsed.txt -> single _summary.txt in same directory
        p = Path(path)
        if not p.exists():
            print(f"Error: File not found: {path}", file=sys.stderr)
            sys.exit(1)
        channel_name, date = extract_info_from_path(path)
        print(f"  Summarizing: {path}")
        result = summarize_transcript(str(p), channel_name, date, verbose)
        if result and result.success:
            print(f"  Summary OK: {result.output_path} (tokens: {_format_tokens(result.input_tokens)} in / {_format_tokens(result.output_tokens)} out, cost: ${result.cost:.4f})")
        elif result:
            print(f"  Summary FAILED: {result.error}")
        else:
            print(f"  Skipped (already exists or empty)")
    else:
        # Channel/all mode: collect all _parsed.txt -> one report in .report/
        storage_dir = Path(cfg["storage_dir"])
        channels = cfg["channels"]
        all_parsed: list[tuple[str, str, str]] = []  # (path, channel_name, date)

        for channel in channels:
            if channel_filter and channel.channel_name != channel_filter:
                continue

            transcript_dir = storage_dir / channel.channel_name / "transcript"
            if not transcript_dir.exists():
                if verbose:
                    print(f"\n[{channel.channel_name}] No transcript directory, skipping.")
                continue

            parsed_files = sorted(transcript_dir.rglob("*_parsed.txt"))

            if not parsed_files:
                if verbose:
                    print(f"\n[{channel.channel_name}] No parsed transcript files found.")
                continue

            print(f"\n[{channel.channel_name}] {len(parsed_files)} parsed file(s) found")
            for pf in parsed_files:
                channel_name, date = extract_info_from_path(str(pf))
                all_parsed.append((str(pf), channel_name, date))
                if verbose:
                    print(f"  Including: {pf}")

        if not all_parsed:
            print("\nNo parsed files found.")
            return

        print(f"\nSummarizing {len(all_parsed)} file(s) into one report...")
        result = summarize_batch(all_parsed, verbose)
        if result.success:
            print(f"  Report OK: {result.output_path}")
            print(f"  Tokens: {_format_tokens(result.input_tokens)} in / {_format_tokens(result.output_tokens)} out, cost: ${result.cost:.4f}")
        else:
            print(f"  Summary FAILED: {result.error}")


def main():
    parser = argparse.ArgumentParser(description="YoutubeStock - YouTube Audio Downloader & Analyst")
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

    parse_parser = subparsers.add_parser("parse", help="Parse transcripts: merge fragments into paragraphs and fix transliterations")
    parse_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parse_parser.add_argument("--channel", type=str, default=None, help="Parse only this channel's transcripts")
    parse_parser.add_argument("--path", type=str, default=None, help="Parse a specific transcript .txt file")
    parse_parser.add_argument("--model", type=str, default="gpt-5-mini", choices=["gpt-5-mini", "gpt-5.1"], help="OpenAI model (default: gpt-5-mini)")

    summary_parser = subparsers.add_parser("summary", help="Summarize parsed transcripts: extract market views and trade signals")
    summary_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    summary_parser.add_argument("--channel", type=str, default=None, help="Summarize only this channel")
    summary_parser.add_argument("--path", type=str, default=None, help="Summarize a specific _parsed.txt file")

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
    elif args.command == "parse":
        cmd_parse(cfg, args.verbose, args.model, args.channel, args.path)
    elif args.command == "summary":
        cmd_summary(cfg, args.verbose, args.channel, args.path)

    print("\nDone.")


if __name__ == "__main__":
    main()
