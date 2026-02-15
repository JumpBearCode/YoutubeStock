import argparse
import sys
from datetime import datetime
from pathlib import Path

from src.cli import (
    load_config,
    _load_transcript_config,
    _process_video,
    _format_duration,
    _format_tokens,
)
from src.rss import get_latest_videos
from src.storage import StorageManager
from src.transcriber import PROVIDERS, audio_path_to_transcript_path, transcribe
from src.parser import parse_transcript
from src.summarizer import summarize_batch, extract_info_from_path
from src.sleep_strategy import random_sleep, inter_channel_sleep

LOG_DIR = ".logs"


class TeeWriter:
    """Writes to both the original stream and a log file."""

    def __init__(self, original, log_file):
        self.original = original
        self.log_file = log_file

    def write(self, text):
        self.original.write(text)
        self.log_file.write(text)
        self.log_file.flush()

    def flush(self):
        self.original.flush()
        self.log_file.flush()

    def fileno(self):
        return self.original.fileno()

    def isatty(self):
        return self.original.isatty()


def main():
    parser = argparse.ArgumentParser(description="YoutubeStock - Full Pipeline")
    parser.add_argument(
        "--last_n", type=int, default=1,
        help="Number of latest videos per channel (default: 1)",
    )
    parser.add_argument(
        "--language", "-l", type=str, default="zh",
        help="Transcription language (default: zh)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--force-summary", action="store_true",
        help="Always run summary even if no new videos",
    )
    args = parser.parse_args()

    # Set up logging
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    log_path = log_dir / f"log_{timestamp}.txt"
    log_file = open(log_path, "w", encoding="utf-8")

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = TeeWriter(original_stdout, log_file)
    sys.stderr = TeeWriter(original_stderr, log_file)

    try:
        _run_pipeline(args.last_n, args.language, args.verbose, args.force_summary, log_file)
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()
        print(f"Log saved to: {log_path}")


def _run_pipeline(last_n: int, language: str, verbose: bool, force_summary: bool, log_file):
    # Load config
    try:
        cfg = load_config()
    except Exception as e:
        print(f"Error loading config.py: {e}", file=sys.stderr)
        sys.exit(1)

    # Override last_n
    for channel in cfg["channels"]:
        channel.last_n = last_n

    storage = StorageManager(cfg["storage_dir"])
    channels = cfg["channels"]

    # ── Phase 1: Check for new videos ─────────────────────────────
    print(f"\n{'='*60}")
    print(f"Phase 1: Check & Process (last_n={last_n})")
    print(f"{'='*60}")

    channel_videos = []  # (channel, videos, new_videos)
    has_any_new = False

    for channel in channels:
        videos = get_latest_videos(channel, last_n)
        new_videos = [
            v for v in videos
            if not storage.is_downloaded(channel.channel_name, v.video_id)
        ]
        channel_videos.append((channel, videos, new_videos))
        print(f"\n[{channel.channel_name}] {len(videos)} video(s), {len(new_videos)} new")
        if new_videos:
            has_any_new = True
            for v in new_videos:
                print(f"  NEW: {v.title}")

    # Cost tracking
    transcript_duration = 0.0
    transcript_cost = 0.0
    parse_input_tokens = 0
    parse_output_tokens = 0
    parse_cost = 0.0

    if not has_any_new and not force_summary:
        print("\nAll channels are up to date, nothing to do.")
        return

    if not has_any_new:
        print("\nAll channels are up to date, skipping to summary (--force-summary).")
    else:
        # Load transcript config
        provider, api_keys = _load_transcript_config()
        cost_per_minute = PROVIDERS[provider]["cost_per_minute"]
        print(f"\nTranscription provider: {provider} (model: {PROVIDERS[provider]['model']})")

        for ci, (channel, videos, new_videos) in enumerate(channel_videos):
            print(f"\n{'─'*60}")
            print(f"[{channel.channel_name}] Processing {len(videos)} video(s)...")
            print(f"{'─'*60}")

            did_download = False

            for vi, video in enumerate(videos):
                print(f"\n  ({vi+1}/{len(videos)}) {video.title}")

                # Step 1: Download if needed
                if not storage.is_downloaded(channel.channel_name, video.video_id):
                    if did_download:
                        random_sleep(cfg["sleep_min"], cfg["sleep_max"])
                    print(f"  → Downloading...")
                    _process_video(video, cfg, storage, verbose)
                    did_download = True
                else:
                    if verbose:
                        print(f"  → Already downloaded")

                # Get entry for audio path
                entry = storage.get_entry(channel.channel_name, video.video_id)
                if not entry or not entry.get("audio_path"):
                    print(f"  → No audio path, skipping transcript/parse")
                    continue

                audio_path = entry["audio_path"]

                # Step 2: Transcribe if needed
                transcript_json_path = audio_path_to_transcript_path(audio_path)
                if not Path(transcript_json_path).exists():
                    if not Path(audio_path).exists():
                        print(f"  → Audio file missing: {audio_path}")
                        continue
                    print(f"  → Transcribing...")
                    result = transcribe(
                        audio_path, transcript_json_path, api_keys,
                        provider, language, verbose,
                    )
                    if result.success:
                        cost = result.duration_seconds / 60 * cost_per_minute
                        print(f"  → Transcript OK ({_format_duration(result.duration_seconds)}, ${cost:.3f})")
                        transcript_duration += result.duration_seconds
                        transcript_cost += cost
                    else:
                        print(f"  → Transcript FAILED: {result.error}")
                        continue
                else:
                    if verbose:
                        print(f"  → Already transcribed")

                # Step 3: Parse if needed
                txt_path = transcript_json_path.rsplit(".", 1)[0] + ".txt"
                parsed_path = str(
                    Path(txt_path).with_name(Path(txt_path).stem + "_parsed.txt")
                )

                if not Path(parsed_path).exists():
                    if not Path(txt_path).exists():
                        print(f"  → Transcript .txt missing: {txt_path}")
                        continue
                    print(f"  → Parsing...")
                    parse_result = parse_transcript(txt_path, verbose=verbose)
                    if parse_result and parse_result.success:
                        print(
                            f"  → Parsed OK "
                            f"({_format_tokens(parse_result.input_tokens)} in / "
                            f"{_format_tokens(parse_result.output_tokens)} out, "
                            f"${parse_result.cost:.4f})"
                        )
                        parse_input_tokens += parse_result.input_tokens
                        parse_output_tokens += parse_result.output_tokens
                        parse_cost += parse_result.cost
                    elif parse_result:
                        print(f"  → Parse FAILED: {parse_result.error}")
                        continue
                else:
                    if verbose:
                        print(f"  → Already parsed")

            # Inter-channel sleep
            if ci < len(channel_videos) - 1 and did_download:
                inter_channel_sleep(cfg["sleep_min"], cfg["sleep_max"])

    # ── Collect all parsed files from last_n videos ───────────────
    all_parsed_files = []  # (parsed_path, channel_name, date)
    for channel, videos, _new_videos in channel_videos:
        for video in videos:
            entry = storage.get_entry(channel.channel_name, video.video_id)
            if not entry or not entry.get("audio_path"):
                continue
            audio_path = entry["audio_path"]
            transcript_json_path = audio_path_to_transcript_path(audio_path)
            txt_path = transcript_json_path.rsplit(".", 1)[0] + ".txt"
            parsed_path = str(
                Path(txt_path).with_name(Path(txt_path).stem + "_parsed.txt")
            )
            if Path(parsed_path).exists():
                channel_name, date = extract_info_from_path(parsed_path)
                all_parsed_files.append((parsed_path, channel_name, date))

    # ── Phase 2: Batch Summary (always runs) ──────────────────────
    print(f"\n{'='*60}")
    print(f"Phase 2: Batch Summary")
    print(f"{'='*60}")

    if not all_parsed_files:
        print("\nNo parsed files available for summary.")
        return

    print(f"\nSummarizing {len(all_parsed_files)} file(s) into one report...")
    for pf, cn, d in all_parsed_files:
        print(f"  • {cn} ({d}): {Path(pf).name}")

    summary_result = summarize_batch(all_parsed_files, verbose)

    if summary_result.success:
        print(f"\nReport OK: {summary_result.output_path}")
        print(
            f"Tokens: {_format_tokens(summary_result.input_tokens)} in / "
            f"{_format_tokens(summary_result.output_tokens)} out, "
            f"cost: ${summary_result.cost:.4f}"
        )

        # Write full prompt to log file only (not console)
        if summary_result.prompt:
            log_file.write(f"\n{'='*60}\n")
            log_file.write("Full Summary Prompt\n")
            log_file.write(f"{'='*60}\n")
            log_file.write(summary_result.prompt)
            log_file.write("\n")
            log_file.flush()

        # Build token usage section
        summary_in = summary_result.input_tokens
        summary_out = summary_result.output_tokens
        summary_cost_val = summary_result.cost
        total_cost = transcript_cost + parse_cost + summary_cost_val

        transcript_line = (
            f"Transcript: {_format_duration(transcript_duration)}  "
            f"(${transcript_cost:.4f})"
        )
        parse_line = (
            f"Parse:      {_format_tokens(parse_input_tokens)} in / "
            f"{_format_tokens(parse_output_tokens)} out  (${parse_cost:.4f})"
        )
        summary_line = (
            f"Summary:    {_format_tokens(summary_in)} in / "
            f"{_format_tokens(summary_out)} out  (${summary_cost_val:.4f})"
        )
        total_line = f"Total:      ${total_cost:.4f}"

        token_section = (
            "\n============================================================\n"
            "Token Usage\n"
            "============================================================\n"
            f"{transcript_line}\n"
            f"{parse_line}\n"
            f"{summary_line}\n"
            f"{total_line}\n"
        )

        # Append to report file
        with open(summary_result.output_path, "a", encoding="utf-8") as f:
            f.write(token_section)

        print(token_section)
    else:
        print(f"\nSummary FAILED: {summary_result.error}")

    print("\nDone.")


if __name__ == "__main__":
    main()
