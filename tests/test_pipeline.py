"""End-to-end checks that don't spend any API credits.

Audio is synthesized with ffmpeg instead of TTS, and the Claude request is
intercepted at the transport layer so the real SDK builds and serializes it
without a call going out. Run with:

    python tests/test_pipeline.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from moviesign import media, mixdown  # noqa: E402
from moviesign.config import Config  # noqa: E402
from moviesign.cues import Cue, Gap, find_gaps, parse_srt, thin_gaps  # noqa: E402
from moviesign.writer import PlacedRiff, RiffBatch, _gap_blocks, build_system  # noqa: E402

FAILS: list[str] = []


def check(label: str, got, want=True, tol: float | None = None) -> None:
    ok = abs(got - want) <= tol if tol is not None else got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        FAILS.append(label)


SRT = """\
1
00:00:35,000 --> 00:00:38,000
We should not have come to this planet.

2
00:00:41,500 --> 00:00:44,000
The readings are off the scale, Commander.

3
00:00:50,000 --> 00:00:52,000
Then we go in on foot.

4
00:00:54,500 --> 00:00:57,000
Sir, the fog is getting thicker.

5
00:01:09,000 --> 00:01:12,000
Nobody touch anything.
"""


def test_cues() -> None:
    print("\n[1] subtitle parsing and gap detection")
    cues = parse_srt(SRT)
    check("all cues parsed", len(cues), 5)
    check("timing decoded", cues[1].start, 41.5, tol=0.001)

    check("styling tags stripped",
          parse_srt("1\n00:00:01,000 --> 00:00:02,000\n<i>{\\an8}Hello</i>\n")[0].text, "Hello")
    check("overlapping cues merge into one silence",
          len(find_gaps(
              [Cue(0, 5, "a"), Cue(3, 9, "b"), Cue(20, 22, "c")],
              30.0, min_gap=2.0, margin=0.35, max_riff_seconds=9.0, head_skip=0,
          )), 2)

    cfg = Config()
    gaps = find_gaps(cues, 75.0, min_gap=cfg.min_gap, margin=cfg.margin,
                     max_riff_seconds=cfg.max_riff_seconds)
    # Silence before the first line, three interior gaps, and the tail after the
    # last line. The 2.5s gap between cues 3 and 4 is too tight once margins are
    # taken off both ends, so it must not appear.
    check("tight gaps rejected, roomy ones kept", len(gaps), 5)
    check("opening silence riffable once past the logos", gaps[0].start, 30.35, tol=0.001)
    check("margin keeps riffs off the dialogue", gaps[1].start, 38.35, tol=0.001)
    check("2.5s gap rejected as too tight",
          all(abs(g.start - 52.35) > 0.01 for g in gaps))
    check("long silence capped at max_riff_seconds", gaps[3].duration, 9.0, tol=0.001)
    check("trailing silence riffable", gaps[4].start, 72.35, tol=0.001)
    check("word budget scales with the gap", cfg.word_budget(gaps[3].duration), 23)

    thinned = thin_gaps(gaps, 75.0, max_riffs=2)
    check("thinning respects the cap", len(thinned), 2)
    check("thinning spreads across the runtime", thinned[0].start < 45 < thinned[1].start)
    check("thinned gaps are renumbered", [g.id for g in thinned], [0, 1])


def test_whisper_padding_regression() -> None:
    """Whisper pads each segment's end to the next segment's start.

    Real transcripts come back with nearly every cue butting against the next,
    and an occasional segment claiming a minute of runtime for a two-word line.
    Read naively, that says the movie is 93% dialogue and has nowhere to put a
    joke. This is the exact shape that the hand-written SRT above cannot catch,
    because authored subtitles have honest end times.
    """
    print()
    print("[1b] whisper-shaped transcript still yields gaps")
    from moviesign.cues import plausible_end

    check("two-word line can't claim a minute",
          round(plausible_end(0.0, 63.4, "Hey, man."), 1), 3.3, tol=0.05)
    check("an honest cue is left alone",
          plausible_end(0.0, 4.0, " ".join(["word"] * 10)), 4.0, tol=0.001)

    # Cue ends chained to the next cue's start, the way Whisper emits them.
    padded = [
        Cue(35.0, 41.5, "We should not have come to this planet."),
        Cue(41.5, 95.0, "Hey."),           # two chars, claims 53 seconds
        Cue(95.0, 99.0, "Then we go in on foot."),
    ]
    clamped = [Cue(c.start, plausible_end(c.start, c.end, c.text), c.text) for c in padded]
    gaps = find_gaps(clamped, 120.0, min_gap=2.0, margin=0.35,
                     max_riff_seconds=9.0, head_skip=0)
    check("padded transcript still exposes silence", len(gaps) >= 3)
    check("silence found inside the over-long cue",
          any(44 < g.start < 95 for g in gaps))


def tone(path: Path, seconds: float) -> Path:
    subprocess.run(
        [media.ffmpeg(), "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=300:duration={seconds}",
         "-c:a", "libmp3lame", "-b:a", "128k", str(path)],
        check=True,
    )
    return path


def test_audio(work: Path) -> None:
    print("\n[2] audio transforms")
    cfg = Config()
    src = tone(work / "t2.mp3", 2.0)
    check("decodes to the right sample count",
          round(len(media.decode_pcm(src, 44100)) / 2 / 44100, 1), 2.0, tol=0.15)

    media.transform_audio(src, work / "fast.mp3", tempo=1.12)
    check("tempo stretch shortens", round(media.audio_duration(work / "fast.mp3"), 2),
          1.79, tol=0.12)

    media.transform_audio(src, work / "high.mp3", semitones=3.0)
    check("pitch shift preserves duration",
          round(media.audio_duration(work / "high.mp3"), 1), 2.0, tol=0.2)

    print("\n[3] overrun policy: fit, stretch, or drop")
    riffs = [
        PlacedRiff(0, "crow", "fits fine", start=10.0, budget_seconds=3.0),
        PlacedRiff(1, "host", "needs a nudge", start=20.0, budget_seconds=1.85),
        PlacedRiff(2, "servo", "hopeless", start=30.0, budget_seconds=0.8),
    ]
    mixdown.synthesize = lambda text, speaker, c, cache: tone(work / f"{speaker}.mp3", 2.0)
    rendered, dropped = mixdown.render_all(riffs, cfg, work / "tts", work, workers=1)

    check("comfortable riff left alone", any(abs(r.tempo - 1.0) < 0.01 for r in rendered))
    check("tight riff sped up within limit",
          any(1.0 < r.tempo <= cfg.max_tempo_stretch for r in rendered))
    check("impossible riff dropped rather than clipping dialogue",
          [d.gap_id for d in dropped], [2])
    for r in rendered:
        check(f"riff {r.riff.gap_id} fits its budget",
              r.duration <= r.riff.budget_seconds + 0.05)

    print("\n[4] track assembly")
    mp3, placed = mixdown.build_track(rendered, 60.0, work / "track.mp3", cfg, work)
    check("every rendered riff placed", placed, len(rendered))
    check("track runs the length of the movie", round(media.audio_duration(mp3)), 60.0, tol=1.0)

    raw = media.decode_pcm(mp3, 44100)

    def peak(at: float, span: float = 0.4) -> int:
        a, b = int(at * 44100) * 2, int((at + span) * 44100) * 2
        chunk = raw[a:b]
        return max((abs(int.from_bytes(chunk[i:i + 2], "little", signed=True))
                    for i in range(0, len(chunk) - 1, 2)), default=0)

    check("silent before the first riff", peak(5.0) < 500)
    check("audible at the first riff", peak(10.5) > 1000)
    check("silent between riffs", peak(16.0) < 500)
    check("audible at the second riff", peak(20.5) > 1000)
    check("dropped riff left no audio behind", peak(45.0) < 500)

    srt = mixdown.write_srt(rendered, work / "track.srt")
    body = srt.read_text(encoding="utf-8")
    check("one subtitle per riff", body.count("-->"), len(rendered))
    check("subtitles name the speaker", "CROW:" in body)


def test_writer(work: Path) -> None:
    print("\n[5] structured-output schema is API-legal")
    schema = RiffBatch.model_json_schema()
    riff = schema.get("$defs", {}).get("Riff", {})
    check("top level is an object", schema.get("type"), "object")
    check("speaker constrained to the three bots",
          sorted(riff.get("properties", {}).get("speaker", {}).get("enum", [])),
          ["crow", "host", "servo"])
    check("no constraints the API rejects",
          not any(k in json.dumps(schema) for k in ("minimum", "maxLength", "multipleOf")))

    print("\n[6] prompt construction")
    cfg = Config()
    gap = Gap(id=7, start=100.0, end=104.0,
              before=[Cue(96, 99, "The creature is loose.")],
              after=[Cue(105, 107, "Run!")])
    frame = work / "frame.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 400)

    blocks = _gap_blocks(gap, cfg, frame)
    text = " ".join(b["text"] for b in blocks if b["type"] == "text")
    check("word budget stated", "at most 10 words" in text)
    check("silence length stated", "4.0s of silence" in text)
    check("preceding dialogue supplied", "The creature is loose." in text)
    check("upcoming dialogue supplied", "Run!" in text)
    check("frame attached as an image block",
          any(b["type"] == "image" for b in blocks))

    check("R rating licenses profanity", "profanity" in build_system(cfg).lower())
    pg = Config()
    pg.rating = "PG"
    check("PG rating asks for clean", "clean" in build_system(pg).lower())

    print("\n[7] the real SDK serializes the request correctly")
    import anthropic
    from moviesign.writer import write_riffs

    captured: dict = {}

    class Intercept(anthropic.Anthropic):
        def post(self, path, *, body=None, **kw):
            captured.update(path=path, body=body)
            raise RuntimeError("intercepted before the network")

    original = anthropic.Anthropic
    anthropic.Anthropic = lambda *a, **k: Intercept(api_key="sk-ant-test")
    try:
        write_riffs([gap], {7: frame}, cfg, workers=1)
    finally:
        anthropic.Anthropic = original

    body = captured.get("body") or {}
    check("posts to the messages endpoint", captured.get("path"), "/v1/messages")
    check("model carried through", body.get("model"), cfg.model)
    check("effort nested inside output_config",
          (body.get("output_config") or {}).get("effort"), cfg.effort)
    check("structured output requested",
          ((body.get("output_config") or {}).get("format") or {}).get("type"), "json_schema")
    check("system prompt marked for caching",
          any((b.get("cache_control") or {}).get("type") == "ephemeral"
              for b in (body.get("system") or [])))
    check("user turn carries both text and image",
          sorted({c["type"] for c in body["messages"][0]["content"]}), ["image", "text"])


def main() -> int:
    try:
        media.ffmpeg()
    except media.MediaError as exc:
        print(f"skipping: {exc}")
        return 0

    test_cues()
    test_whisper_padding_regression()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        test_audio(work)
        test_writer(work)

    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURE(S): {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
