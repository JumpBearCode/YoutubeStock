import re
import time
import random
from pathlib import Path

import yt_dlp

from .models import DownloadResult, VideoInfo

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = [30, 60, 120]


def _sanitize_title(title: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]', "", title)
    sanitized = sanitized.strip(". ")
    return sanitized[:200]


def download_audio(
    video: VideoInfo,
    output_dir: Path,
    audio_format: str = "bestaudio/best",
    audio_codec: str = "mp3",
    audio_quality: str = "192",
) -> DownloadResult:
    title = _sanitize_title(video.title)
    output_path = str(output_dir / f"{title}.mp3")

    ydl_opts = {
        "format": audio_format,
        "outtmpl": str(output_dir / f"{title}.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_codec,
                "preferredquality": audio_quality,
            }
        ],
        "quiet": True,
        "no_warnings": True,
        "sleep_interval": 3,
        "max_sleep_interval": 8,
    }

    for attempt in range(MAX_RETRIES):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video.link])
            return DownloadResult(
                video_info=video,
                success=True,
                file_path=output_path,
            )
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                backoff = BACKOFF_BASE_SECONDS[attempt] + random.uniform(0, 10)
                print(f"  Audio download failed (attempt {attempt + 1}), retrying in {backoff:.0f}s: {e}")
                time.sleep(backoff)
            else:
                return DownloadResult(
                    video_info=video,
                    success=False,
                    error=str(e),
                )
