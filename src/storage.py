import json
from datetime import datetime
from pathlib import Path

from .models import VideoInfo

TIMESTAMP_FORMAT = "%Y-%m-%d-%H-%M-%S"


class StorageManager:
    def __init__(self, storage_dir: str = ".storage"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._history_cache: dict[str, dict] = {}

    def _history_file(self, channel_name: str) -> Path:
        return self.storage_dir / channel_name / "download_history.json"

    def _load_history(self, channel_name: str) -> dict:
        if channel_name in self._history_cache:
            return self._history_cache[channel_name]
        hfile = self._history_file(channel_name)
        if hfile.exists():
            self._history_cache[channel_name] = json.loads(hfile.read_text())
        else:
            self._history_cache[channel_name] = {}
        return self._history_cache[channel_name]

    def _save_history(self, channel_name: str) -> None:
        history = self._load_history(channel_name)
        hfile = self._history_file(channel_name)
        hfile.parent.mkdir(parents=True, exist_ok=True)
        hfile.write_text(json.dumps(history, indent=2, default=str))

    def get_video_dir(self, channel_name: str, timestamp: datetime) -> Path:
        ts = timestamp.strftime(TIMESTAMP_FORMAT)
        path = self.storage_dir / channel_name / "video" / ts
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_audio_dir(self, channel_name: str, timestamp: datetime) -> Path:
        ts = timestamp.strftime(TIMESTAMP_FORMAT)
        path = self.storage_dir / channel_name / "audio" / ts
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_entry(self, channel_name: str, video_id: str) -> dict | None:
        history = self._load_history(channel_name)
        return history.get(video_id)

    def is_downloaded(self, channel_name: str, video_id: str) -> bool:
        history = self._load_history(channel_name)
        return video_id in history

    def mark_downloaded(
        self, video: VideoInfo, video_path: str | None, audio_path: str | None
    ) -> None:
        history = self._load_history(video.channel_name)
        history[video.video_id] = {
            "title": video.title,
            "published": video.published.isoformat(),
            "link": video.link,
            "video_path": video_path,
            "audio_path": audio_path,
            "downloaded_at": datetime.now().isoformat(),
        }
        self._save_history(video.channel_name)
