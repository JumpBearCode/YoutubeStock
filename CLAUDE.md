# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YoutubeStock is a YouTube channel monitor, video/audio downloader, and audio transcriber. It detects new videos via YouTube RSS feeds, downloads both video (mp4) and audio (mp3) using yt-dlp, and transcribes audio to timestamped JSON + plain text using Whisper API (Groq or OpenAI).

## Commands

```bash
# Install dependencies (requires ffmpeg: brew install ffmpeg)
uv sync

# Run the CLI
uv run python -m src.cli download          # Download latest N videos per channel
uv run python -m src.cli check             # Check for new videos only
uv run python -m src.cli download --verbose # With verbose output

# Transcribe audio to JSON + TXT (requires API key in .env, see .env.example)
uv run python -m src.cli transcript                       # All channels
uv run python -m src.cli transcript --channel "老李玩钱"    # Specific channel
uv run python -m src.cli transcript --path path/to/file.mp3  # Single file
uv run python -m src.cli transcript --language zh -v       # Force language, verbose
```

There are no tests, linter, or type checker configured.

## Architecture

The CLI has three commands: `download` (batch pull recent N videos), `check` (incremental sync of new videos only), and `transcript` (convert downloaded audio to JSON + TXT via Whisper API). Download commands pull video+audio with sleep delays between downloads.

**Data flow:** `config.py` → `resolver` (URL → channel_id/name via yt-dlp, cached) → `rss` (channel_id → RSS feed → VideoInfo list) → `downloader` (yt-dlp download with retry) → `storage` (track download history as JSON).

Key modules in `src/`:
- **cli.py** — Entry point, argparse commands, orchestration loop for all commands
- **resolver.py** — Resolves channel URLs (@handle or /channel/UCxxx) to channel_id + name; caches results in `.storage/channel_cache.json`
- **rss.py** — Fetches YouTube RSS XML feeds via feedparser, parses into `VideoInfo`
- **downloader.py** — Wraps yt-dlp for video/audio downloads with 3-retry exponential backoff
- **storage.py** — `StorageManager` handles download history (per-channel JSON) and output directory structure
- **transcriber.py** — Audio compression (ffmpeg), chunking for large files, Whisper API transcription (Groq default, OpenAI fallback), JSON + TXT output. Supports multiple API keys with automatic fallback (free → paid)
- **models.py** — Dataclasses: `ChannelConfig`, `VideoInfo`, `DownloadResult`, `TranscriptResult`
- **sleep_strategy.py** — Triangular-distribution random delays between downloads

## Configuration

`config.py` is loaded dynamically at runtime via `importlib`. Channel URLs and global settings (sleep ranges, formats, quality) are defined there. The `CHANNELS` list entries have `url` (required) and `last_n` (optional, defaults to `GLOBAL_LAST_N`).

## Storage

All state lives in `.storage/` (gitignored). Per-channel subdirectories contain `download_history.json` and `video/` + `audio/` + `transcript/` folders organized by timestamp. Transcripts output both `.json` (array of `{timestamp, text}`) and `.txt` (plain text, one segment per line).

## Transcription

Configured via `.env` (see `.env.example`). Supports two providers:
- **Groq** (default) — `whisper-large-v3`, $0.03/hour. Supports dual API keys: `GROQ_API_KEY` (free tier, 8h/day) tried first, `GROQ_API_KEY_PAID` as fallback.
- **OpenAI** — `whisper-1`, $0.36/hour.

Set `TRANSCRIPTION_PROVIDER=groq` or `openai` in `.env`.
