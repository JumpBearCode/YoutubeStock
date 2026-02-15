# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YoutubeStock is a YouTube channel monitor, video/audio downloader, audio transcriber, and AI analyst. It detects new videos via YouTube RSS feeds, downloads both video (mp4) and audio (mp3) using yt-dlp, transcribes audio to timestamped JSON + plain text using Whisper API (Groq or OpenAI), parses fragmented transcripts into clean paragraphs, summarizes investment views with buy/sell signals using a ReAct agent, and emails the daily report via Gmail SMTP.

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

# Parse: merge Whisper fragments into paragraphs, fix transliterations (requires OPENAI_API_KEY)
uv run python -m src.cli parse                            # All channels
uv run python -m src.cli parse --channel "老李玩钱"         # Specific channel
uv run python -m src.cli parse --path path/to/file.txt     # Single file
uv run python -m src.cli parse --model gpt-5.1             # Use gpt-5.1 (default: gpt-5-mini)

# Summary: extract market views & trade signals via ReAct agent (requires OPENAI_API_KEY)
uv run python -m src.cli summary                           # All channels → .report/summary_{ts}.txt
uv run python -m src.cli summary --channel "老李玩钱"       # Single channel → .report/summary_{ts}.txt
uv run python -m src.cli summary --path path/to/_parsed.txt # Single file → _summary.txt in same dir
```

There are no tests, linter, or type checker configured.

## Architecture

The CLI has five commands: `download` (batch pull recent N videos), `check` (incremental sync of new videos only), `transcript` (convert downloaded audio to JSON + TXT via Whisper API), `parse` (merge Whisper fragments into paragraphs via OpenAI Agents SDK), and `summary` (extract market views & trade signals via LangChain ReAct agent with web search). Download commands pull video+audio with sleep delays between downloads. The full pipeline (`main.py`) runs all phases sequentially and emails the final report via Gmail SMTP.

**Data flow:**
- **Download pipeline:** `config.py` → `resolver` (URL → channel_id/name via yt-dlp, cached) → `rss` (channel_id → RSS feed → VideoInfo list) → `downloader` (yt-dlp download with retry) → `storage` (track download history as JSON)
- **Analysis pipeline:** `transcript` (audio → `.json` + `.txt`) → `parse` (`.txt` → `_parsed.txt` via OpenAI Agents SDK) → `summary` (`_parsed.txt` → `_summary.txt` or `.report/summary_{ts}.txt` via LangChain ReAct agent + DuckDuckGo search) → `emailer` (send report via Gmail SMTP)

Key modules in `src/`:
- **cli.py** — Entry point, argparse commands, orchestration loop for all commands
- **resolver.py** — Resolves channel URLs (@handle or /channel/UCxxx) to channel_id + name; caches results in `.storage/channel_cache.json`
- **rss.py** — Fetches YouTube RSS XML feeds via feedparser, parses into `VideoInfo`
- **downloader.py** — Wraps yt-dlp for video/audio downloads with 3-retry exponential backoff
- **storage.py** — `StorageManager` handles download history (per-channel JSON) and output directory structure
- **transcriber.py** — Audio compression (ffmpeg), chunking for large files, Whisper API transcription (Groq default, OpenAI fallback), JSON + TXT output. Supports multiple API keys with automatic fallback (free → paid)
- **parser.py** — Parse agent: merges Whisper fragmented lines into natural paragraphs, fixes stock name transliterations (e.g. "按摩店"→AMD). Uses OpenAI Agents SDK (gpt-5-mini default). Outputs `_parsed.txt`
- **summarizer.py** — Summary agent: extracts market views, buy/add/sell signals from `_parsed.txt` using LangChain ReAct agent (gpt-5.1) + DuckDuckGo search (capped at 5 calls). `--path` mode outputs `_summary.txt` per file; channel/all mode outputs one report to `.report/summary_{timestamp}.txt` with per-video sections
- **emailer.py** — Sends summary report via Gmail SMTP (App Password). Uses only Python built-in `smtplib` + `email`. Subject: `每日美股总结 {YYYY-MM-DD}`. Configured via `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `GMAIL_RECIPIENT` in `.env`
- **models.py** — Dataclasses: `ChannelConfig`, `VideoInfo`, `DownloadResult`, `TranscriptResult`
- **sleep_strategy.py** — Triangular-distribution random delays between downloads

## Configuration

`config.py` is loaded dynamically at runtime via `importlib`. Channel URLs and global settings (sleep ranges, formats, quality) are defined there. The `CHANNELS` list entries have `url` (required) and `last_n` (optional, defaults to `GLOBAL_LAST_N`).

## Storage

All state lives in `.storage/` (gitignored). Per-channel subdirectories contain `download_history.json` and `video/` + `audio/` + `transcript/` folders organized by timestamp. Transcripts output `.json` (array of `{timestamp, text}`), `.txt` (plain text), `_parsed.txt` (cleaned paragraphs), and `_summary.txt` (--path mode only). Batch summary reports go to `.report/summary_{timestamp}.txt`.

## Transcription

Configured via `.env` (see `.env.example`). Supports two providers:
- **Groq** (default) — `whisper-large-v3`, $0.03/hour. Supports dual API keys: `GROQ_API_KEY` (free tier, 8h/day) tried first, `GROQ_API_KEY_PAID` as fallback.
- **OpenAI** — `whisper-1`, $0.36/hour.

Set `TRANSCRIPTION_PROVIDER=groq` or `openai` in `.env`.

`OPENAI_API_KEY` is also required for the `parse` and `summary` commands. The `summary` command uses DuckDuckGo for web search (no extra API key needed).

## Email

After batch summary, `main.py` sends the report via Gmail SMTP (Phase 3). Configured via `.env`:
- `GMAIL_ADDRESS` — Gmail account
- `GMAIL_APP_PASSWORD` — 16-char App Password (Google Account → 2-Step Verification → App Passwords)
- `GMAIL_RECIPIENT` — Recipient email (defaults to self)

Zero external dependencies (Python built-in `smtplib`). If credentials are missing, email is silently skipped.
