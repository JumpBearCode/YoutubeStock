from dataclasses import dataclass
from datetime import datetime


@dataclass
class ChannelConfig:
    channel_id: str
    channel_name: str
    last_n: int | None = None


@dataclass
class VideoInfo:
    video_id: str
    title: str
    channel_id: str
    channel_name: str
    published: datetime
    link: str


@dataclass
class DownloadResult:
    video_info: VideoInfo
    success: bool
    file_path: str | None = None
    error: str | None = None


@dataclass
class TranscriptResult:
    success: bool
    transcript_path: str | None = None
    duration_seconds: float = 0.0
    error: str | None = None
