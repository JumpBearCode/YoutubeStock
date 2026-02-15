# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YoutubeStock is a YouTube channel monitor and video/audio downloader. It detects new videos via YouTube RSS feeds, then downloads both video (mp4) and audio (mp3) using yt-dlp, with built-in anti-ban strategies (randomized sleep, exponential backoff retries).

## Commands

```bash
# Install dependencies (requires ffmpeg: brew install ffmpeg)
uv sync

# Run the CLI
uv run python -m src.cli download          # Download latest N videos per channel
uv run python -m src.cli check             # Check for new videos only
uv run python -m src.cli download --verbose # With verbose output
```

There are no tests, linter, or type checker configured.

## Architecture

The CLI has two commands: `download` (batch pull recent N videos) and `check` (incremental sync of new videos only). Both download video+audio for each video with sleep delays between downloads.

**Data flow:** `config.py` → `resolver` (URL → channel_id/name via yt-dlp, cached) → `rss` (channel_id → RSS feed → VideoInfo list) → `downloader` (yt-dlp download with retry) → `storage` (track download history as JSON).

Key modules in `src/`:
- **cli.py** — Entry point, argparse commands, orchestration loop for both commands
- **resolver.py** — Resolves channel URLs (@handle or /channel/UCxxx) to channel_id + name; caches results in `.storage/channel_cache.json`
- **rss.py** — Fetches YouTube RSS XML feeds via feedparser, parses into `VideoInfo`
- **downloader.py** — Wraps yt-dlp for video/audio downloads with 3-retry exponential backoff
- **storage.py** — `StorageManager` handles download history (per-channel JSON) and output directory structure
- **models.py** — Dataclasses: `ChannelConfig`, `VideoInfo`, `DownloadResult`
- **sleep_strategy.py** — Triangular-distribution random delays between downloads

## Configuration

`config.py` is loaded dynamically at runtime via `importlib`. Channel URLs and global settings (sleep ranges, formats, quality) are defined there. The `CHANNELS` list entries have `url` (required) and `last_n` (optional, defaults to `GLOBAL_LAST_N`).

## Storage

All state lives in `.storage/` (gitignored). Per-channel subdirectories contain `download_history.json` and `video/` + `audio/` folders organized by timestamp.
