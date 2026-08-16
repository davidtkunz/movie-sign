"""Everything that shells out to ffmpeg: probing, subtitles, frames, audio."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# Subtitle codecs that are bitmaps, not text. We can't convert these to SRT.
IMAGE_SUB_CODECS = {"dvd_subtitle", "hdmv_pgs_subtitle", "dvb_subtitle", "xsub"}


class MediaError(RuntimeError):
    pass


def _find(name: str) -> str:
    override = os.environ.get(f"MOVIESIGN_{name.upper()}")
    if override and Path(override).exists():
        return override
    found = shutil.which(name)
    if not found:
        raise MediaError(
            f"{name} not found on PATH.\n"
            "movie-sign needs ffmpeg for everything: subtitles, frames, and audio.\n"
            "  Windows:  winget install Gyan.FFmpeg     (then reopen your terminal)\n"
            "  macOS:    brew install ffmpeg\n"
            "  Linux:    sudo apt install ffmpeg\n"
            f"Or point MOVIESIGN_{name.upper()} at the executable."
        )
    return found


def ffmpeg() -> str:
    return _find("ffmpeg")


def ffprobe() -> str:
    return _find("ffprobe")


def _run(cmd: list[str], *, capture_stdout: bool = False) -> bytes:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        raise MediaError(f"{Path(cmd[0]).name} failed:\n" + "\n".join(tail))
    return proc.stdout or b""


def probe(path: Path) -> dict:
    out = _run(
        [ffprobe(), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_stdout=True,
    )
    return json.loads(out)


def duration(path: Path) -> float:
    info = probe(path)
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def subtitle_streams(path: Path) -> list[dict]:
    """Text subtitle tracks, in file order, with their relative subtitle index."""
    streams = []
    sub_index = 0
    for stream in probe(path).get("streams", []):
        if stream.get("codec_type") != "subtitle":
            continue
        tags = stream.get("tags") or {}
        streams.append({
            "sub_index": sub_index,
            "codec": stream.get("codec_name", "?"),
            "language": tags.get("language", "und"),
            "title": tags.get("title", ""),
            "forced": bool(stream.get("disposition", {}).get("forced")),
            "text": stream.get("codec_name") not in IMAGE_SUB_CODECS,
        })
        sub_index += 1
    return streams


def pick_subtitle_stream(streams: list[dict], language: str | None) -> dict | None:
    """Prefer a full text track in the requested language over a forced one.

    Forced tracks only caption foreign-language lines, so they leave most of the
    movie looking like silence — which would carpet the whole runtime in riffs.
    """
    usable = [s for s in streams if s["text"]]
    if not usable:
        return None
    if language:
        matches = [s for s in usable if s["language"].startswith(language)]
        usable = matches or usable
    full = [s for s in usable if not s["forced"]]
    return (full or usable)[0]


def extract_subtitles(video: Path, sub_index: int, out_srt: Path) -> Path:
    out_srt.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg(), "-y", "-v", "error", "-i", str(video),
          "-map", f"0:s:{sub_index}", "-c:s", "srt", str(out_srt)])
    return out_srt


def transcribe(video: Path, out_srt: Path, model_size: str = "base") -> Path:
    """Fall back to Whisper when the file carries no text subtitle track."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise MediaError(
            "No text subtitle track in this file, and faster-whisper isn't installed.\n"
            "  pip install faster-whisper\n"
            "Or supply your own subtitles with --subs path/to/movie.srt"
        ) from exc

    from .cues import timestamp

    print(f"  transcribing with Whisper ({model_size}) — this takes a while...")
    model = WhisperModel(model_size, device="auto", compute_type="int8")
    segments, _ = model.transcribe(str(video), vad_filter=True)

    out_srt.parent.mkdir(parents=True, exist_ok=True)
    with out_srt.open("w", encoding="utf-8") as fh:
        for i, seg in enumerate(segments, 1):
            text = seg.text.strip()
            if not text:
                continue
            fh.write(f"{i}\n{timestamp(seg.start)} --> {timestamp(seg.end)}\n{text}\n\n")
    return out_srt


def grab_frame(video: Path, at: float, out_jpg: Path, width: int = 768) -> Path:
    """Single frame at `at` seconds. Seek goes before -i so it's a keyframe jump."""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg(), "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video),
          "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "5", str(out_jpg)])
    return out_jpg


def audio_duration(path: Path) -> float:
    return duration(path)


def pitch_filter(semitones: float) -> str | None:
    """Shift pitch without changing duration — resample up, then slow back down."""
    if abs(semitones) < 0.01:
        return None
    ratio = 2 ** (semitones / 12.0)
    return f"asetrate=44100*{ratio:.6f},aresample=44100,atempo={1/ratio:.6f}"


def transform_audio(
    src: Path,
    dst: Path,
    *,
    tempo: float = 1.0,
    semitones: float = 0.0,
) -> Path:
    """Apply speed-up and/or pitch shift, writing a new file."""
    filters = []
    pitch = pitch_filter(semitones)
    if pitch:
        filters.append(pitch)
    if abs(tempo - 1.0) > 0.001:
        filters.append(f"atempo={tempo:.4f}")

    cmd = [ffmpeg(), "-y", "-v", "error", "-i", str(src)]
    if filters:
        cmd += ["-filter:a", ",".join(filters)]
    cmd += [str(dst)]
    _run(cmd)
    return dst


def decode_pcm(path: Path, sample_rate: int) -> bytes:
    """Decode to raw signed 16-bit mono PCM."""
    return _run(
        [ffmpeg(), "-v", "error", "-i", str(path),
         "-f", "s16le", "-acodec", "pcm_s16le",
         "-ar", str(sample_rate), "-ac", "1", "-"],
        capture_stdout=True,
    )


def encode_mp3(raw_pcm: Path, out_mp3: Path, sample_rate: int, bitrate: str) -> Path:
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg(), "-y", "-v", "error",
          "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", str(raw_pcm),
          "-c:a", "libmp3lame", "-b:a", bitrate, str(out_mp3)])
    return out_mp3
