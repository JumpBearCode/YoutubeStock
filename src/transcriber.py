import json
import math
import subprocess
import tempfile
from pathlib import Path

from openai import OpenAI

from .models import TranscriptResult

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB
CHUNK_DURATION_MINUTES = 20

PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "whisper-large-v3",
        "cost_per_minute": 0.0005,  # $0.03/hour
    },
    "openai": {
        "base_url": None,
        "model": "whisper-1",
        "cost_per_minute": 0.006,  # $0.36/hour
    },
}


def audio_path_to_transcript_path(audio_path: str) -> str:
    """Replace /audio/ with /transcript/ and .mp3 with .json."""
    return audio_path.replace("/audio/", "/transcript/").replace(".mp3", ".json")


def compress_audio(input_path: str, output_path: str) -> None:
    """Compress audio to mono 16kHz 32kbps for Whisper."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-ac", "1", "-ar", "16000", "-b:a", "32k",
            output_path,
        ],
        capture_output=True,
        check=True,
    )


def get_audio_duration(file_path: str) -> float:
    """Get audio duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def split_audio_chunks(input_path: str, chunk_dir: str) -> list[tuple[str, float]]:
    """Split audio into 20-min chunks if file > 25MB.

    Returns list of (chunk_path, offset_seconds).
    If file is under the limit, returns [(input_path, 0.0)].
    """
    if Path(input_path).stat().st_size <= MAX_FILE_SIZE:
        return [(input_path, 0.0)]

    duration = get_audio_duration(input_path)
    chunk_seconds = CHUNK_DURATION_MINUTES * 60
    num_chunks = math.ceil(duration / chunk_seconds)
    chunks = []

    for i in range(num_chunks):
        offset = i * chunk_seconds
        chunk_path = str(Path(chunk_dir) / f"chunk_{i:03d}.mp3")
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-ss", str(offset), "-t", str(chunk_seconds),
                "-c", "copy", chunk_path,
            ],
            capture_output=True,
            check=True,
        )
        chunks.append((chunk_path, offset))

    return chunks


def transcribe_audio_file(
    client: OpenAI,
    audio_path: str,
    model: str,
    language: str | None = None,
) -> tuple[list[dict], float]:
    """Transcribe a single audio file via Whisper API.

    Returns (segments, duration_seconds).
    """
    kwargs = {
        "model": model,
        "response_format": "verbose_json",
        "timestamp_granularities": ["segment"],
    }
    if language:
        kwargs["language"] = language

    with open(audio_path, "rb") as f:
        kwargs["file"] = f
        transcription = client.audio.transcriptions.create(**kwargs)

    segments = []
    for seg in transcription.segments:
        seconds = int(seg.start)
        h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
        timestamp = f"{h:02d}:{m:02d}:{s:02d}"
        segments.append({"timestamp": timestamp, "text": seg.text.strip()})

    return segments, transcription.duration


def _make_client(api_key: str, provider_cfg: dict) -> OpenAI:
    kwargs = {"api_key": api_key}
    if provider_cfg["base_url"]:
        kwargs["base_url"] = provider_cfg["base_url"]
    return OpenAI(**kwargs)


def transcribe(
    audio_path: str,
    output_path: str,
    api_keys: list[str],
    provider: str = "groq",
    language: str | None = None,
    verbose: bool = False,
) -> TranscriptResult:
    """Full transcription pipeline: compress → chunk → transcribe → merge → write JSON + TXT.

    api_keys: list of keys to try in order (e.g. [free_key, paid_key]).
    Falls back to next key on API failure.
    """
    try:
        provider_cfg = PROVIDERS[provider]
        model = provider_cfg["model"]
        key_idx = 0
        client = _make_client(api_keys[key_idx], provider_cfg)

        with tempfile.TemporaryDirectory() as tmp_dir:
            # Compress
            compressed_path = str(Path(tmp_dir) / "compressed.mp3")
            if verbose:
                print(f"    Compressing audio...")
            compress_audio(audio_path, compressed_path)

            # Chunk if needed
            chunks = split_audio_chunks(compressed_path, tmp_dir)
            if verbose and len(chunks) > 1:
                print(f"    Split into {len(chunks)} chunks")

            # Transcribe each chunk
            all_segments = []
            total_duration = 0.0

            for chunk_path, offset in chunks:
                if verbose:
                    print(f"    Transcribing{f' chunk (offset {offset:.0f}s)' if offset > 0 else ''}...")

                # Try current key, fallback to next on failure
                segments = None
                duration = 0.0
                while key_idx < len(api_keys):
                    try:
                        segments, duration = transcribe_audio_file(client, chunk_path, model, language)
                        break
                    except Exception as e:
                        if key_idx + 1 < len(api_keys):
                            key_idx += 1
                            print(f"    Free key failed ({e}), switching to paid key...")
                            client = _make_client(api_keys[key_idx], provider_cfg)
                        else:
                            raise

                # Adjust timestamps by chunk offset
                if offset > 0:
                    for seg in segments:
                        parts = seg["timestamp"].split(":")
                        total_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + int(offset)
                        h, m, s = total_sec // 3600, (total_sec % 3600) // 60, total_sec % 60
                        seg["timestamp"] = f"{h:02d}:{m:02d}:{s:02d}"

                all_segments.extend(segments)
                total_duration += duration

        # Write JSON
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(all_segments, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Write TXT
        txt_path = output_path.rsplit(".", 1)[0] + ".txt"
        lines = [seg["text"] for seg in all_segments]
        Path(txt_path).write_text("\n".join(lines) + "\n", encoding="utf-8")

        return TranscriptResult(
            success=True,
            transcript_path=output_path,
            duration_seconds=total_duration,
        )

    except Exception as e:
        return TranscriptResult(success=False, error=str(e))
