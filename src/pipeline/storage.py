import json
import shutil
from datetime import datetime
from pathlib import Path

from ..models import VideoInfo

TIMESTAMP_FORMAT = "%Y-%m-%d-%H-%M-%S"
_DEPRECATED_FIELDS = {"video_path"}


class StorageManager:
    def __init__(self, storage_dir: str = ".storage", history_max: int = 0):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.history_max = history_max  # 0 = no limit
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

        # Strip deprecated fields
        for entry in history.values():
            for field in _DEPRECATED_FIELDS:
                entry.pop(field, None)

        # Prune old entries
        self._prune_history(channel_name, history)

        hfile = self._history_file(channel_name)
        hfile.parent.mkdir(parents=True, exist_ok=True)
        hfile.write_text(json.dumps(history, indent=2, default=str, ensure_ascii=False))

    def _prune_history(self, channel_name: str, history: dict) -> None:
        """Remove oldest entries beyond history_max, deleting their files."""
        if self.history_max <= 0 or len(history) <= self.history_max:
            return

        # Sort by downloaded_at descending, keep newest N
        sorted_ids = sorted(
            history.keys(),
            key=lambda vid: history[vid].get("downloaded_at", ""),
            reverse=True,
        )
        to_remove = sorted_ids[self.history_max:]

        for video_id in to_remove:
            entry = history[video_id]
            # Delete audio timestamp dir
            audio_path = entry.get("audio_path")
            if audio_path:
                audio_ts_dir = Path(audio_path).parent
                if audio_ts_dir.exists():
                    shutil.rmtree(audio_ts_dir, ignore_errors=True)
                    print(f"  Pruned: {audio_ts_dir}")

            # Delete corresponding transcript timestamp dir
            # audio: .storage/{ch}/audio/{ts}/...  → transcript: .storage/{ch}/transcript/{ts}/
            if audio_path:
                parts = Path(audio_path).parts
                for i, part in enumerate(parts):
                    if part == "audio" and i + 1 < len(parts):
                        ts_dir_name = parts[i + 1]
                        transcript_ts_dir = self.storage_dir / channel_name / "transcript" / ts_dir_name
                        if transcript_ts_dir.exists():
                            shutil.rmtree(transcript_ts_dir, ignore_errors=True)
                            print(f"  Pruned: {transcript_ts_dir}")
                        break

            del history[video_id]

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
        self, video: VideoInfo, audio_path: str | None
    ) -> None:
        history = self._load_history(video.channel_name)
        history[video.video_id] = {
            "title": video.title,
            "published": video.published.isoformat(),
            "link": video.link,
            "audio_path": audio_path,
            "downloaded_at": datetime.now().isoformat(),
        }
        self._save_history(video.channel_name)

    def update_entry(self, channel_name: str, video_id: str, **fields) -> None:
        """Update specific fields of an existing history entry."""
        history = self._load_history(channel_name)
        if video_id not in history:
            return
        history[video_id].update(fields)
        self._save_history(channel_name)
