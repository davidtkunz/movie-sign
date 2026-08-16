# movie-sign

[![tests](https://github.com/davidtkunz/movie-sign/actions/workflows/tests.yml/badge.svg)](https://github.com/davidtkunz/movie-sign/actions/workflows/tests.yml)

Generate an MST3K-style riff track for a bad movie: three voices heckling from the
back of the theater, timed to land in the silences so they never talk over the film.

Output is a **commentary-only MP3** the length of the movie — silence everywhere
except the jokes. Play it alongside your own copy, both starting at 00:00. (This
is how RiffTrax ships their tracks, and it's the reason this repo doesn't touch
your video file.)

```
[00:04:12]  CROW: That's not a spaceship, that's a hubcap with ambition.
[00:04:31] SERVO: I've made a huge mistake. — the actor, silently, forever
[00:05:03]  HOST: The fog machine is the only thing giving a performance.
```

## How it works

1. **Find the dialogue.** Extracts the embedded subtitle track with ffmpeg, or
   transcribes with Whisper if there isn't one.
2. **Find the silence.** Every gap between lines longer than ~2s is a riff window.
   Each gap's length becomes a hard word budget (~2.6 words/sec).
3. **Write the jokes.** Batches of gaps go to Claude with the surrounding dialogue
   and a frame grabbed from the middle of the silence, so the riffs are about what's
   actually on screen — the boom mic, the matte painting, the hair.
4. **Speak them.** ElevenLabs, one voice per bot.
5. **Lay them down.** Each riff is placed at its timecode on a silent timeline.
   Anything that runs long gets sped up slightly, or dropped if that isn't enough.

Step 5 is the part that makes or breaks it. A riff that overruns its gap steps on
the movie's next line, which is the one unforgivable sin — so the tool measures
every rendered line and throws away the ones that don't fit.

## Setup

**ffmpeg is required** — it does the subtitles, the frames, and the audio.

```powershell
winget install Gyan.FFmpeg     # Windows (reopen your terminal afterward)
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Linux
```

Then:

```powershell
git clone https://github.com/davidtkunz/movie-sign
cd movie-sign
pip install -r requirements.txt

$env:ANTHROPIC_API_KEY  = "sk-ant-..."     # writes the jokes
$env:ELEVENLABS_API_KEY = "..."            # speaks them
```

To make the keys stick across terminal sessions, use `setx ANTHROPIC_API_KEY "sk-ant-..."`.

## Usage

Work up in stages — each one costs more than the last, so there's no reason to pay
for voices before you know the jokes are good.

```powershell
# 1. Free. Where would the riffs land?
python -m moviesign gaps "D:\movies\Manos.mkv"

# 2. A couple of dollars. Write the riffs and read them.
python -m moviesign script "D:\movies\Manos.mkv"

# 3. Speak them and build the track.
python -m moviesign render "D:\movies\Manos.mkv"

# Or do 2 and 3 in one go:
python -m moviesign riff "D:\movies\Manos.mkv"
```

After `script`, `output/script.txt` is the whole riff track as text. Read it. If a
joke is weak, edit `output/riffs.json` directly and then run `render` — the writing
step won't re-run and you won't pay for it twice.

Outputs land in `output/`:

| File | What it is |
|---|---|
| `<movie>.riffs.mp3` | The commentary track. Play alongside the movie. |
| `<movie>.riffs.srt` | The riffs as subtitles, for reading along or checking timing. |
| `script.txt` | Human-readable script with timecodes. |
| `riffs.json` | The editable script. Change this, re-run `render`. |

Everything expensive is cached under `output/.cache/`, keyed by content — re-running
`render` after editing three lines only pays to re-speak those three lines.

## Configuration

```powershell
python -m moviesign init      # writes moviesign.config.json
python -m moviesign voices    # lists the voices on your ElevenLabs account
```

The settings worth knowing:

| Setting | Default | What it does |
|---|---|---|
| `rating` | `R` | `PG`, `R`, or `HARD-R`. Sets how filthy the writers' room gets. |
| `min_gap` | `2.0` | Shortest silence worth a joke. Raise it for a sparser track. |
| `riff_rate` | `0.55` | Roughly what fraction of gaps get a riff. Lower is more restrained. |
| `max_riffs` | `400` | Ceiling per movie. Gaps are sampled across the runtime, not front-loaded. |
| `words_per_second` | `2.6` | Assumed delivery speed. Lower it if riffs keep getting dropped. |
| `max_tempo_stretch` | `1.12` | How much a too-long riff may be sped up before it's cut instead. |
| `voices` | Josh / Arnold / Antoni | Voice ids per bot. Also `pitch_semitones`, if you want Servo higher. |

Useful flags: `--rating HARD-R`, `--min-gap 3`, `--max-riffs 150`, `--no-vision`
(skip frames — cheaper, but the jokes get more generic), `--subs movie.srt`.

## Notes

- **Cost.** A 90-minute feature runs roughly $2–4 of Claude for the writing and a
  few dollars of ElevenLabs credits for the voices, depending on riff count. The
  `gaps` command is free and tells you how many riffs you're looking at.
- **Bitmap subtitles.** Blu-ray rips often carry PGS subtitles, which are images,
  not text. movie-sign can't read those and will fall back to Whisper — or you can
  pass a `.srt` from elsewhere with `--subs`.
- **Forced subtitle tracks** only caption the foreign-language lines, which makes
  the whole movie look like silence. The tool prefers full tracks automatically,
  but `--sub-stream N` lets you pick.
- **Keep the output to yourself.** A riff track is commentary, but the movie isn't
  yours. That's exactly why this produces a separate audio file instead of muxing
  a new video.

## License

MIT
