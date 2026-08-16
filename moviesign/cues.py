"""Subtitle parsing and the search for silence.

A riff lands in a gap between lines of dialogue. Everything downstream depends
on finding those gaps accurately, so this module is deliberately fussy about
merging overlapping cues before it measures the space between them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# "00:01:23,456 --> 00:01:25,789"  (SRT uses a comma, VTT a period)
_TIMING = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
_TAGS = re.compile(r"<[^>]+>|\{\\[^}]*\}")


@dataclass
class Cue:
    start: float
    end: float
    text: str


@dataclass
class Gap:
    id: int
    start: float
    """When the riff may begin (dialogue end + margin)."""
    end: float
    """When the riff must be finished (next dialogue start - margin)."""
    before: list[Cue] = field(default_factory=list)
    after: list[Cue] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(text: str) -> list[Cue]:
    """Parse SRT or WebVTT. Tolerant of BOMs, CRLF, missing indices, and styling tags."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []

    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        timing_idx = next((i for i, ln in enumerate(lines) if _TIMING.search(ln)), None)
        if timing_idx is None:
            continue
        m = _TIMING.search(lines[timing_idx])
        start = _to_seconds(*m.group(1, 2, 3, 4))
        end = _to_seconds(*m.group(5, 6, 7, 8))

        body = " ".join(lines[timing_idx + 1 :])
        body = _TAGS.sub("", body)
        body = re.sub(r"\s+", " ", body).strip()
        # Drop caption artifacts that aren't spoken dialogue.
        if not body or body.upper() in {"[MUSIC]", "[SILENCE]", "♪"}:
            continue
        if end <= start:
            continue
        cues.append(Cue(start, end, body))

    cues.sort(key=lambda c: c.start)
    return cues


def _speech_intervals(cues: list[Cue]) -> list[tuple[float, float]]:
    """Collapse overlapping/adjacent cues so a gap is real silence, not a cue boundary."""
    merged: list[list[float]] = []
    for cue in cues:
        if merged and cue.start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], cue.end)
        else:
            merged.append([cue.start, cue.end])
    return [(a, b) for a, b in merged]


def _context(cues: list[Cue], before_t: float, after_t: float, n: int = 3) -> tuple[list[Cue], list[Cue]]:
    before = [c for c in cues if c.end <= before_t + 0.01][-n:]
    after = [c for c in cues if c.start >= after_t - 0.01][: max(1, n - 1)]
    return before, after


def find_gaps(
    cues: list[Cue],
    runtime: float,
    *,
    min_gap: float,
    margin: float,
    max_riff_seconds: float,
    head_skip: float = 30.0,
) -> list[Gap]:
    """Find every stretch of silence long enough to hold a joke.

    `head_skip` leaves the opening logos alone; riffing over a studio card
    before the movie has started reads as a bug, not a bit.
    """
    if not cues:
        return []

    intervals = _speech_intervals(cues)
    windows: list[tuple[float, float]] = []

    # Silence before the first line.
    if intervals[0][0] > head_skip:
        windows.append((head_skip, intervals[0][0]))

    for (_, end), (next_start, _) in zip(intervals, intervals[1:]):
        windows.append((end, next_start))

    # Silence after the last line, if we know the runtime.
    if runtime and runtime > intervals[-1][1]:
        windows.append((intervals[-1][1], runtime))

    gaps: list[Gap] = []
    for raw_start, raw_end in windows:
        if raw_end - raw_start < min_gap + 2 * margin:
            continue
        start = raw_start + margin
        end = min(raw_end - margin, start + max_riff_seconds)
        if end - start < min_gap:
            continue
        before, after = _context(cues, raw_start, raw_end)
        gaps.append(Gap(id=len(gaps), start=start, end=end, before=before, after=after))

    return gaps


def thin_gaps(gaps: list[Gap], runtime: float, max_riffs: int) -> list[Gap]:
    """Trim to `max_riffs` while keeping riffs spread across the runtime.

    Bucket the timeline evenly and keep the roomiest gap in each bucket, so a
    talky first act can't eat the whole budget and leave act three silent.
    """
    if len(gaps) <= max_riffs or max_riffs <= 0:
        return gaps

    span = runtime or (gaps[-1].end + 1.0)
    bucket_size = span / max_riffs
    best: dict[int, Gap] = {}
    for gap in gaps:
        idx = min(int(gap.start / bucket_size), max_riffs - 1)
        incumbent = best.get(idx)
        if incumbent is None or gap.duration > incumbent.duration:
            best[idx] = gap

    kept = sorted(best.values(), key=lambda g: g.start)
    for i, gap in enumerate(kept):
        gap.id = i
    return kept


def timestamp(seconds: float, comma: bool = True) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"
