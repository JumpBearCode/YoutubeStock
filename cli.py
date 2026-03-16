import importlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer
from dotenv import load_dotenv

from src.models import VideoInfo
from src.sleep_strategy import inter_channel_sleep, random_sleep
from src.pipeline.downloader import download_audio
from src.pipeline.youtube import resolve_channel, get_latest_videos
from src.pipeline.storage import StorageManager
from src.agent.parser import parse_transcript
from src.agent.summarizer import summarize_transcript, summarize_batch, extract_info_from_path
from src.agent.transcriber import PROVIDERS, audio_path_to_transcript_path, transcribe
from src.emailer import send_report_email

app = typer.Typer(
    help="YoutubeStock - YouTube Channel Monitor & Analyst",
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)


@app.callback()
def _default(ctx: typer.Context):
    """If no subcommand is given, run the full pipeline."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)

LOG_DIR = ".logs"


# ── Shared helpers ────────────────────────────────────────────


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
        "history_max": getattr(config, "HISTORY_MAX_PER_CHANNEL", 0),
        "flat_fetch_n": getattr(config, "FLAT_FETCH_N", 15),
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


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _download_videos(cfg: dict, verbose: bool, mode: str = "download") -> None:
    """Shared logic for check and download commands.

    mode="check"    — incremental: only new videos, capped by check_max_new
    mode="download" — batch: latest last_n per channel
    """
    storage = StorageManager(cfg["storage_dir"], history_max=cfg["history_max"])
    channels = cfg["channels"]

    for i, channel in enumerate(channels):
        if mode == "check":
            max_n = cfg["check_max_new"]
            print(f"\n[{channel.channel_name}] Checking latest {max_n} videos...")
        else:
            max_n = channel.last_n
            print(f"\n[{channel.channel_name}] Fetching latest {max_n} videos...")

        history = storage.get_history(channel.channel_name)
        videos = get_latest_videos(channel, max_n, history=history, flat_fetch_n=cfg["flat_fetch_n"])
        to_download = [v for v in videos if v.video_id not in history]

        if not to_download:
            if mode == "check":
                print(f"  All up to date, no new videos.")
            else:
                print(f"  All videos already downloaded.")
            continue

        if mode == "download":
            print(f"  {len(to_download)} video(s) to download (skipping {len(videos) - len(to_download)} already downloaded)")
        else:
            print(f"  {len(to_download)} new video(s) to download")

        for j, video in enumerate(to_download):
            print(f"\n  Processing ({j+1}/{len(to_download)}): {video.title}")
            _process_entry(video, cfg, storage, verbose)

            if j < len(to_download) - 1:
                random_sleep(cfg["sleep_min"], cfg["sleep_max"])

        if i < len(channels) - 1:
            inter_channel_sleep(cfg["sleep_min"], cfg["sleep_max"])


# ── Commands ──────────────────────────────────────────────────


@app.command()
def check(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="详细输出")] = False,
):
    """Check for new videos and download them (incremental sync)."""
    cfg = _load_cfg()
    _download_videos(cfg, verbose, mode="check")
    print("\nDone.")


@app.command()
def download(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="详细输出")] = False,
):
    """Download latest N videos per channel."""
    cfg = _load_cfg()
    _download_videos(cfg, verbose, mode="download")
    print("\nDone.")


@app.command()
def transcript(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="详细输出")] = False,
    language: Annotated[Optional[str], typer.Option("--language", "-l", help="转录语言 (ISO-639-1: 'en', 'zh')，不指定自动检测")] = None,
    channel: Annotated[Optional[str], typer.Option("--channel", help="只转录该频道")] = None,
    path: Annotated[Optional[str], typer.Option("--path", help="转录单个音频文件")] = None,
):
    """Transcribe downloaded audio using Whisper API."""
    cfg = _load_cfg()
    provider, api_keys = _load_transcript_config()
    cost_per_minute = PROVIDERS[provider]["cost_per_minute"]
    fallback_info = f", fallback: paid key" if len(api_keys) > 1 else ""
    print(f"Using provider: {provider} (model: {PROVIDERS[provider]['model']}, ${cost_per_minute}/min{fallback_info})")

    total_duration = 0.0
    total_files = 0

    if path:
        audio_path = path
        if not Path(audio_path).exists():
            print(f"Error: File not found: {audio_path}", file=sys.stderr)
            sys.exit(1)
        output_path = str(Path(audio_path).with_suffix(".json"))
        if Path(output_path).exists():
            print(f"  Skipping (already exists): {output_path}")
            print("\nDone.")
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
        channels = cfg["channels"]

        for ch in channels:
            if channel and ch.channel_name != channel:
                continue

            history_file = Path(cfg["storage_dir"]) / ch.channel_name / "download_history.json"
            if not history_file.exists():
                if verbose:
                    print(f"\n[{ch.channel_name}] No download history, skipping.")
                continue

            history = json.loads(history_file.read_text())
            audio_entries = [
                (vid_id, entry["audio_path"])
                for vid_id, entry in history.items()
                if entry.get("audio_path")
            ]

            if not audio_entries:
                if verbose:
                    print(f"\n[{ch.channel_name}] No audio files found.")
                continue

            print(f"\n[{ch.channel_name}] {len(audio_entries)} audio file(s) to check")

            for video_id, audio_path in audio_entries:
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

    print("\nDone.")


@app.command()
def parse(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="详细输出")] = False,
    channel: Annotated[Optional[str], typer.Option("--channel", help="只整理该频道")] = None,
    path: Annotated[Optional[str], typer.Option("--path", help="整理单个 .txt 文件")] = None,
    model: Annotated[str, typer.Option("--model", help="GLM 模型")] = "glm-4.7-flash",
):
    """Parse transcripts: merge fragments into paragraphs and fix transliterations."""
    cfg = _load_cfg()

    from src.agent.parser import MODEL_PRICING
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

        for ch in channels:
            if channel and ch.channel_name != channel:
                continue

            transcript_dir = storage_dir / ch.channel_name / "transcript"
            if not transcript_dir.exists():
                if verbose:
                    print(f"\n[{ch.channel_name}] No transcript directory, skipping.")
                continue

            txt_files = [
                f for f in sorted(transcript_dir.rglob("*.txt"))
                if not f.name.endswith("_parsed.txt")
            ]

            if not txt_files:
                if verbose:
                    print(f"\n[{ch.channel_name}] No transcript files found.")
                continue

            print(f"\n[{ch.channel_name}] {len(txt_files)} transcript file(s) to check")

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

    print("\nDone.")


@app.command()
def summary(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="详细输出")] = False,
    channel: Annotated[Optional[str], typer.Option("--channel", help="只总结该频道")] = None,
    path: Annotated[Optional[str], typer.Option("--path", help="总结单个 _parsed.txt 文件")] = None,
):
    """Summarize parsed transcripts: extract market views and trade signals."""
    cfg = _load_cfg()

    from src.agent.summarizer import MODEL, MODEL_PRICING
    pricing = MODEL_PRICING[MODEL]
    pricing_info = f"${pricing['input']}/M in, ${pricing['output']}/M out"
    print(f"Using model: {MODEL} ({pricing_info})")

    if path:
        p = Path(path)
        if not p.exists():
            print(f"Error: File not found: {path}", file=sys.stderr)
            sys.exit(1)
        channel_name, date = extract_info_from_path(path)
        print(f"  Summarizing: {path}")
        result = summarize_transcript(str(p), channel_name, date, verbose)
        if result and result.success:
            print(f"  Summary OK: {result.output_path} (tokens: {_format_tokens(result.input_tokens)} in / {_format_tokens(result.output_tokens)} out, cost: ${result.cost:.4f}, Tavily calls: {result.search_calls})")
        elif result:
            print(f"  Summary FAILED: {result.error}")
        else:
            print(f"  Skipped (already exists or empty)")
    else:
        storage_dir = Path(cfg["storage_dir"])
        channels = cfg["channels"]
        all_parsed: list[tuple[str, str, str]] = []

        for ch in channels:
            if channel and ch.channel_name != channel:
                continue

            transcript_dir = storage_dir / ch.channel_name / "transcript"
            if not transcript_dir.exists():
                if verbose:
                    print(f"\n[{ch.channel_name}] No transcript directory, skipping.")
                continue

            parsed_files = sorted(transcript_dir.rglob("*_parsed.txt"))

            if not parsed_files:
                if verbose:
                    print(f"\n[{ch.channel_name}] No parsed transcript files found.")
                continue

            print(f"\n[{ch.channel_name}] {len(parsed_files)} parsed file(s) found")
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
            print(f"  Tokens: {_format_tokens(result.input_tokens)} in / {_format_tokens(result.output_tokens)} out, cost: ${result.cost:.4f}, Tavily calls: {result.search_calls}")
        else:
            print(f"  Summary FAILED: {result.error}")

    print("\nDone.")


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


@app.command()
def run(
    last_n: Annotated[int, typer.Option("--last_n", help="每频道拉取视频数")] = 1,
    language: Annotated[str, typer.Option("--language", "-l", help="转录语言")] = "zh",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="详细输出")] = False,
    force_summary: Annotated[bool, typer.Option("--force-summary", help="无新视频也跑 summary")] = False,
):
    """Run full pipeline: download -> transcribe -> parse -> summary -> email."""
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
        _run_pipeline(last_n, language, verbose, force_summary, log_file)
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()
        print(f"Log saved to: {log_path}")


def _run_pipeline(last_n: int, language: str, verbose: bool, force_summary: bool, log_file):
    cfg = _load_cfg()

    # Override last_n
    for ch in cfg["channels"]:
        ch.last_n = last_n

    storage = StorageManager(cfg["storage_dir"])
    channels = cfg["channels"]

    # ── Phase 1: Check for new videos ─────────────────────────────
    print(f"\n{'='*60}")
    print(f"Phase 1: Check & Process (last_n={last_n})")
    print(f"{'='*60}")

    channel_videos = []
    has_any_new = False

    for channel in channels:
        history = storage.get_history(channel.channel_name)
        videos = get_latest_videos(channel, last_n, history=history, flat_fetch_n=cfg.get("flat_fetch_n", 15))
        new_videos = [v for v in videos if v.video_id not in history]
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
                    _process_entry(video, cfg, storage, verbose)
                    did_download = True
                else:
                    if verbose:
                        print(f"  → Already downloaded")

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
    all_parsed_files = []
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

    # ── Phase 2: Batch Summary ────────────────────────────────────
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
            f"cost: ${summary_result.cost:.4f}, Tavily calls: {summary_result.search_calls}"
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

        search_calls = summary_result.search_calls
        token_section = (
            "\n---\n\n"
            "## Token Usage\n\n"
            "| Phase | Details | Cost |\n"
            "|-------|---------|------|\n"
            f"| Transcript | {_format_duration(transcript_duration)} | ${transcript_cost:.4f} |\n"
            f"| Parse | {_format_tokens(parse_input_tokens)} in / {_format_tokens(parse_output_tokens)} out | ${parse_cost:.4f} |\n"
            f"| Summary | {_format_tokens(summary_in)} in / {_format_tokens(summary_out)} out, Tavily: {search_calls} calls | ${summary_cost_val:.4f} |\n"
            f"| **Total** | | **${total_cost:.4f}** |\n"
        )

        with open(summary_result.output_path, "a", encoding="utf-8") as f:
            f.write(token_section)

        print(token_section)

        # ── Phase 3: Email report ─────────────────────────────────
        print(f"\n{'='*60}")
        print(f"Phase 3: Email Report")
        print(f"{'='*60}")

        email_result = send_report_email(
            summary_result.output_path, verbose=verbose
        )
        if email_result.success:
            print(f"\nEmail sent to: {email_result.recipient}")
        else:
            print(f"\nEmail skipped: {email_result.error}")
    else:
        print(f"\nSummary FAILED: {summary_result.error}")

    print("\nDone.")


def _load_cfg() -> dict:
    try:
        return load_config()
    except Exception as e:
        print(f"Error loading config.py: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    app()
