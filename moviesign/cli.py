"""Command line entry point."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import sys
from pathlib import Path

from . import media, mixdown
from .config import CONFIG_NAME, Config
from .cues import find_gaps, parse_srt, thin_gaps, timestamp
from .writer import load_script, save_script, write_riffs


def _work_dir(video: Path, out: Path) -> Path:
    tag = hashlib.sha256(str(video.resolve()).encode()).hexdigest()[:10]
    d = out / ".cache" / tag
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_subtitles(video: Path, work: Path, args) -> list:
    """Find dialogue with timecodes: supplied file, embedded track, or Whisper."""
    if args.subs:
        path = Path(args.subs)
        if not path.exists():
            raise SystemExit(f"Subtitle file not found: {path}")
        print(f"  using supplied subtitles: {path.name}")
        return parse_srt(path.read_text(encoding="utf-8", errors="replace"))

    srt = work / "dialogue.srt"
    if srt.exists() and not args.force:
        print("  using cached dialogue track")
        return parse_srt(srt.read_text(encoding="utf-8", errors="replace"))

    streams = media.subtitle_streams(video)
    text_tracks = [s for s in streams if s["text"]]
    if streams and not text_tracks:
        print("  subtitle tracks are bitmap-only (PGS/VobSub) — can't read them as text")

    if args.sub_stream is not None:
        chosen = next((s for s in streams if s["sub_index"] == args.sub_stream), None)
        if chosen is None:
            raise SystemExit(f"No subtitle stream with index {args.sub_stream}")
    else:
        chosen = media.pick_subtitle_stream(streams, args.lang)

    if chosen is not None:
        label = f"{chosen['language']}/{chosen['codec']}"
        if chosen["title"]:
            label += f" ({chosen['title']})"
        print(f"  extracting embedded subtitles: stream {chosen['sub_index']} [{label}]")
        media.extract_subtitles(video, chosen["sub_index"], srt)
    else:
        print("  no usable subtitle track found — falling back to transcription")
        media.transcribe(video, srt, args.whisper_model)

    return parse_srt(srt.read_text(encoding="utf-8", errors="replace"))


def _prepare(video: Path, out: Path, cfg: Config, args):
    """Shared front half: subtitles -> gaps."""
    if not video.exists():
        raise SystemExit(f"Video not found: {video}")

    work = _work_dir(video, out)
    runtime = media.duration(video)
    print(f"\n{video.name}  ({timestamp(runtime, comma=False)})")

    cues = _get_subtitles(video, work, args)
    if not cues:
        raise SystemExit("No dialogue found. Supply subtitles with --subs.")
    print(f"  {len(cues)} lines of dialogue")

    gaps = find_gaps(
        cues,
        runtime,
        min_gap=cfg.min_gap,
        margin=cfg.margin,
        max_riff_seconds=cfg.max_riff_seconds,
        head_skip=getattr(args, "head_skip", 30.0),
    )
    kept = thin_gaps(gaps, runtime, cfg.max_riffs)
    print(f"  {len(gaps)} gaps of {cfg.min_gap}s or more; keeping {len(kept)}")
    return work, runtime, cues, kept


def _grab_frames(video: Path, gaps, work: Path, cfg: Config) -> dict:
    """One frame from the middle of each silence, for the model to look at."""
    frames_dir = work / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames: dict[int, Path] = {}

    def grab(gap):
        path = frames_dir / f"{gap.id:05d}.jpg"
        if not path.exists():
            media.grab_frame(video, gap.start + gap.duration / 2, path, cfg.frame_width)
        return gap.id, path

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for i, (gap_id, path) in enumerate(pool.map(grab, gaps), 1):
            frames[gap_id] = path
            if i % 50 == 0:
                print(f"    ...{i}/{len(gaps)} frames")
    return frames


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_gaps(args) -> int:
    """Free dry run: show where riffs would land, without calling any API."""
    cfg = Config.load()
    _apply_overrides(cfg, args)
    out = Path(args.out)
    _, runtime, _, gaps = _prepare(Path(args.video), out, cfg, args)

    total = sum(g.duration for g in gaps)
    print(f"\n  riffable silence: {total/60:.1f} min across {len(gaps)} gaps")
    print(f"  average gap: {total/max(1,len(gaps)):.1f}s "
          f"(~{cfg.word_budget(total/max(1,len(gaps)))} words)\n")
    for gap in gaps[:20]:
        after = gap.after[0].text[:52] if gap.after else "—"
        print(f"  {timestamp(gap.start, comma=False)}  {gap.duration:4.1f}s  -> {after}")
    if len(gaps) > 20:
        print(f"  ... and {len(gaps) - 20} more")
    return 0


def cmd_script(args) -> int:
    """Write the riffs with Claude. No audio, so it's cheap to iterate on."""
    cfg = Config.load()
    _apply_overrides(cfg, args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work, runtime, _, gaps = _prepare(Path(args.video), out, cfg, args)

    frames = {}
    if cfg.vision and not args.no_vision:
        print("\n  grabbing frames...")
        frames = _grab_frames(Path(args.video), gaps, work, cfg)

    print(f"\n  writing riffs with {cfg.model} (effort={cfg.effort}, rating={cfg.rating})...")
    riffs = write_riffs(gaps, frames, cfg, workers=args.workers)
    if not riffs:
        print("\n  No riffs were produced. Nothing to render.")
        return 1

    save_script(riffs, out / "riffs.json", out / "script.txt")
    rate = len(riffs) / max(1, len(gaps))
    print(f"\n  {len(riffs)} riffs ({rate:.0%} of gaps)")
    print(f"  script: {out / 'script.txt'}")
    print(f"\n  Read it, edit riffs.json if you like, then:  "
          f"python -m moviesign render \"{args.video}\"")
    return 0


def cmd_render(args) -> int:
    """Speak the riffs and lay them onto a silent, movie-length track."""
    cfg = Config.load()
    _apply_overrides(cfg, args)
    out = Path(args.out)
    video = Path(args.video)
    work = _work_dir(video, out)

    script = out / "riffs.json"
    if not script.exists():
        raise SystemExit(f"No script at {script}. Run `script` first.")
    riffs = load_script(script)
    runtime = media.duration(video)
    print(f"\n  {len(riffs)} riffs to speak")

    rendered, dropped = mixdown.render_all(
        riffs, cfg, work / "tts", work / "fitted", workers=args.workers
    )
    print(f"  {len(rendered)} spoken, {len(dropped)} dropped for overrunning their gap")

    stem = video.stem
    mp3 = out / f"{stem}.riffs.{cfg.tts_backend}.mp3"
    print("\n  building track...")
    mp3, placed = mixdown.build_track(rendered, runtime, mp3, cfg, work)
    srt = mixdown.write_srt(rendered, out / f"{stem}.riffs.{cfg.tts_backend}.srt")

    size_mb = mp3.stat().st_size / 1_048_576
    print(f"\n  {mp3}  ({size_mb:.1f} MB, {placed} riffs placed)")
    print(f"  {srt}")
    print(f"\n  Play it alongside your copy of the movie, both starting at 00:00.")
    return 0


def cmd_riff(args) -> int:
    """The whole pipeline: subtitles -> gaps -> jokes -> voices -> track."""
    rc = cmd_script(args)
    if rc != 0:
        return rc
    return cmd_render(args)


def cmd_voices(args) -> int:
    from . import sapi

    print()
    if sapi.available():
        names = sapi.installed_voices()
        print(f"  Windows SAPI (free, no account) - {len(names)} installed:")
        for name in names:
            print(f"    {name}")
        print("    Put these under \"sapi_voices\" in the config. Only two voices?")
        print("    That is normal - separate the bots with \"rate\" and \"pitch_semitones\".")
    else:
        print("  Windows SAPI: not available on this platform")

    print()
    try:
        from .voice import list_voices

        voices = list_voices()
        print(f"  ElevenLabs (paid) - {len(voices)} on this account:")
        for v in voices:
            print(f"    {v['voice_id']}  {v['name']}")
    except Exception as exc:
        print(f"  ElevenLabs: unavailable ({exc})")
    return 0


def cmd_init(args) -> int:
    cfg = Config.load()
    path = cfg.save()
    print(f"  wrote {path}")
    print("  Edit voice ids, timing, and the comedy rating there.")
    return 0


def _apply_overrides(cfg: Config, args) -> None:
    if getattr(args, "backend", None):
        cfg.tts_backend = args.backend
    for name in ("min_gap", "max_riffs", "rating", "model", "effort", "batch_size", "riff_rate"):
        value = getattr(args, name, None)
        if value is not None:
            setattr(cfg, name, value)
    if getattr(args, "no_vision", False):
        cfg.vision = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moviesign",
        description="Generate an MST3K-style riff track for a bad movie.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p, video=True):
        if video:
            p.add_argument("video", help="Path to the movie file")
        p.add_argument("-o", "--out", default="output", help="Output directory (default: output)")
        p.add_argument("--subs", help="Use this subtitle file instead of the embedded track")
        p.add_argument("--sub-stream", type=int, help="Which embedded subtitle stream to use")
        p.add_argument("--lang", default="eng", help="Preferred subtitle language (default: eng)")
        p.add_argument("--whisper-model", default="base",
                       help="Whisper size if transcribing: tiny/base/small/medium")
        p.add_argument("--force", action="store_true", help="Ignore cached subtitles")

    def add_timing(p):
        p.add_argument("--min-gap", type=float, help="Shortest silence worth a joke (seconds)")
        p.add_argument("--max-riffs", type=int, help="Cap on riffs for the whole movie")
        p.add_argument("--head-skip", type=float, default=30.0,
                       help="Leave the first N seconds alone — logos, studio cards (default: 30)")

    def add_writing(p):
        p.add_argument("--rating", choices=["PG", "R", "HARD-R"], help="How filthy (default: R)")
        p.add_argument("--riff-rate", type=float, help="Target fraction of gaps to riff, 0-1")
        p.add_argument("--model", help="Claude model id")
        p.add_argument("--effort", choices=["low", "medium", "high"], help="Thinking effort")
        p.add_argument("--batch-size", type=int, help="Gaps per API request")
        p.add_argument("--no-vision", action="store_true", help="Skip frames; riff on dialogue only")
        p.add_argument("--workers", type=int, default=4, help="Concurrent requests")

    p = sub.add_parser("gaps", help="Show where riffs would land (free, no API calls)")
    add_common(p)
    add_timing(p)
    p.set_defaults(func=cmd_gaps)

    p = sub.add_parser("script", help="Write the riffs (Claude only, no audio)")
    add_common(p)
    add_timing(p)
    add_writing(p)
    p.set_defaults(func=cmd_script)

    p = sub.add_parser("render", help="Speak an existing script and build the track")
    add_common(p)
    p.add_argument("--backend", choices=["sapi", "elevenlabs"],
                   help="Voice engine. Re-run with a different one to re-voice "
                        "the same script without rewriting a single joke.")
    p.add_argument("--workers", type=int, default=4, help="Concurrent TTS calls")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("riff", help="Full pipeline: script then render")
    add_common(p)
    add_timing(p)
    add_writing(p)
    p.add_argument("--backend", choices=["sapi", "elevenlabs"], help="Voice engine")
    p.set_defaults(func=cmd_riff)

    p = sub.add_parser("voices", help="List the ElevenLabs voices on your account")
    p.set_defaults(func=cmd_voices)

    p = sub.add_parser("init", help=f"Write a default {CONFIG_NAME}")
    p.set_defaults(func=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n  interrupted")
        return 130
    except (media.MediaError, RuntimeError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
