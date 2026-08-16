"""Render riffs to audio and lay them onto a silent timeline.

The output is a commentary-only track: silence everywhere except the riffs,
timed to the movie so you can play the two together. The track is written by
streaming PCM to a temp file rather than holding a feature-length buffer in
memory, and re-encoded to mp3 once at the end.
"""

from __future__ import annotations

import concurrent.futures
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import media
from .config import Config
from .cues import timestamp
from .voice import synthesize
from .writer import PlacedRiff

SILENCE_CHUNK = 1 << 20  # 1 MiB of zeros at a time


@dataclass
class RenderedRiff:
    riff: PlacedRiff
    audio: Path
    duration: float
    tempo: float


def _render_one(riff: PlacedRiff, cfg: Config, cache: Path, work: Path) -> RenderedRiff | None:
    """Speak one line and make it fit. Returns None if it can't be made to fit."""
    raw = synthesize(riff.line, riff.speaker, cfg, cache)
    spoken = media.audio_duration(raw)
    if spoken <= 0:
        return None

    semitones = float(cfg.voices[riff.speaker].get("pitch_semitones", 0.0))
    budget = riff.budget_seconds
    tempo = 1.0

    if spoken > budget:
        needed = spoken / budget
        if needed > cfg.max_tempo_stretch:
            # Speeding this up enough would make it sound like a chipmunk.
            # Better a missing joke than one that tramples the next line.
            return None
        tempo = needed

    if abs(tempo - 1.0) > 0.001 or abs(semitones) > 0.01:
        adjusted = work / f"fit-{riff.gap_id}-{riff.speaker}.mp3"
        media.transform_audio(raw, adjusted, tempo=tempo, semitones=semitones)
        raw = adjusted
        spoken = media.audio_duration(raw)

    return RenderedRiff(riff=riff, audio=raw, duration=spoken, tempo=tempo)


def render_all(
    riffs: list[PlacedRiff],
    cfg: Config,
    cache: Path,
    work: Path,
    *,
    workers: int = 4,
) -> tuple[list[RenderedRiff], list[PlacedRiff]]:
    """Speak every riff. Returns (rendered, dropped)."""
    work.mkdir(parents=True, exist_ok=True)
    rendered: list[RenderedRiff] = []
    dropped: list[PlacedRiff] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_render_one, r, cfg, cache, work): r for r in riffs}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            riff = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:
                print(f"  ! {riff.speaker} @ {timestamp(riff.start, comma=False)}: {exc}")
                result = None
            if result is None:
                dropped.append(riff)
            else:
                rendered.append(result)
            if i % 25 == 0:
                print(f"    ...{i}/{len(riffs)} spoken")

    rendered.sort(key=lambda r: r.riff.start)
    return rendered, dropped


def _write_silence(fh, samples: int) -> None:
    remaining = samples * 2  # 16-bit mono
    zeros = b"\x00" * SILENCE_CHUNK
    while remaining > 0:
        fh.write(zeros[: min(remaining, SILENCE_CHUNK)])
        remaining -= SILENCE_CHUNK


def build_track(
    rendered: list[RenderedRiff],
    runtime: float,
    out_mp3: Path,
    cfg: Config,
    work: Path,
) -> tuple[Path, int]:
    """Lay the riffs onto silence at their timecodes. Returns (path, riffs placed)."""
    sr = cfg.sample_rate
    # mkstemp hands back an open OS handle; Windows won't let us unlink the file
    # later unless we close it before reopening the path ourselves.
    handle, raw_name = tempfile.mkstemp(suffix=".pcm", dir=str(work))
    os.close(handle)
    raw_path = Path(raw_name)
    cursor = 0  # samples written so far
    placed = 0

    try:
        with raw_path.open("wb") as fh:
            for item in rendered:
                target = int(item.riff.start * sr)
                if target < cursor:
                    # The previous riff is still talking. Skip rather than overlap —
                    # two bots on top of each other is unlistenable.
                    continue
                _write_silence(fh, target - cursor)
                pcm = media.decode_pcm(item.audio, sr)
                fh.write(pcm)
                cursor = target + len(pcm) // 2
                placed += 1

            total = int(runtime * sr) if runtime else cursor
            if total > cursor:
                _write_silence(fh, total - cursor)

        media.encode_mp3(raw_path, out_mp3, sr, cfg.mp3_bitrate)
    finally:
        raw_path.unlink(missing_ok=True)

    return out_mp3, placed


def write_srt(rendered: list[RenderedRiff], out_srt: Path) -> Path:
    """A subtitle file of the riffs — handy for reading along or checking timing."""
    out_srt.parent.mkdir(parents=True, exist_ok=True)
    with out_srt.open("w", encoding="utf-8") as fh:
        for i, item in enumerate(rendered, 1):
            start = item.riff.start
            end = start + item.duration
            fh.write(
                f"{i}\n{timestamp(start)} --> {timestamp(end)}\n"
                f"{item.riff.speaker.upper()}: {item.riff.line}\n\n"
            )
    return out_srt
