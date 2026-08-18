# movie-sign

[![tests](https://github.com/davidtkunz/movie-sign/actions/workflows/tests.yml/badge.svg)](https://github.com/davidtkunz/movie-sign/actions/workflows/tests.yml)

Generate an MST3K-style riff track for a bad movie: three voices heckling from the
back of the theater, timed to land in the silences so they never talk over the film.

Output is a **commentary-only MP3** the length of the movie — silence everywhere
except the jokes. Play it alongside your own copy, both starting at 00:00. (This
is how RiffTrax ships their tracks, and it's the reason this repo doesn't touch
your video file.)

Real output from a 1934 John Wayne western:

```
[00:11:19]  HOST: Somebody bumped the lens cap.
[00:16:04] SERVO: The horse has read the script and is quietly leaving.
[00:21:53]  CROW: There's a radio tower on your 1880s hilltop, fellas.
[00:30:33]  CROW: The cinematographer has fully surrendered. That's a hat and a rumor.
[00:34:56]  CROW: That wig came off a mop, and the mop wants it back.
```

Those last three needed a frame, not a transcript. **[Hear it and read all 52
riffs](examples/star-packer/)** — including a 28-second audio sample of the free
Windows voices.

## How it works

1. **Find the dialogue.** Extracts the embedded subtitle track with ffmpeg, or
   transcribes with Whisper if there isn't one.
2. **Find the silence.** Every gap between lines longer than ~2s is a riff window.
   Each gap's length becomes a hard word budget (~2.6 words/sec).
3. **Write the jokes.** Batches of gaps go to Claude with the surrounding dialogue
   and a frame grabbed from the middle of the silence, so the riffs are about what's
   actually on screen — the boom mic, the matte painting, the hair.
4. **Speak them.** Windows' own voices for free, or ElevenLabs if you want
   real performances. One voice per bot either way.
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

setx ANTHROPIC_API_KEY "sk-ant-..."     # writes the jokes - required
```

That's the only account you need. Voices default to the ones already built into
Windows, which cost nothing. If the movie has no subtitle track, add
`pip install faster-whisper` and it transcribes the audio itself - also free.

For better voices, set `ELEVENLABS_API_KEY` too and pass `--backend elevenlabs`.

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
| `<movie>.riffs.sapi.mp3` | The commentary track. Play alongside the movie. |
| `<movie>.riffs.sapi.srt` | The riffs as subtitles, for reading along or checking timing. |
| `script.txt` | Human-readable script with timecodes. |
| `riffs.json` | The editable script. Change this, re-run `render`. |

Everything expensive is cached under `output/.cache/`, keyed by content — re-running
`render` after editing three lines only pays to re-speak those three lines.

## Voices

Two backends, and the script is saved separately from the audio so you can
switch whenever you like without rewriting a single joke.

| | `sapi` (default) | `elevenlabs` |
|---|---|---|
| Cost | free | credits per character |
| Account | none | required |
| Sounds like | a 1998 text-to-speech program | an actual performer |
| Platform | Windows only | anywhere |

The stock Windows voices are robotic, which suits two puppet robots better than
you'd think. Windows usually ships only two of them, so the three bots are
separated by `rate` and `pitch_semitones` as much as by voice - Servo is just
David pitched four semitones up.

```powershell
python -m moviesign voices                        # list both backends
python -m moviesign render "movie.mkv"                          # free voices
python -m moviesign render "movie.mkv" --backend elevenlabs     # same jokes, better voices
```

Re-voicing never costs a Claude call. `riffs.json` is the durable artifact; each
backend writes its own `.riffs.<backend>.mp3` so tracks don't overwrite each
other, and every spoken line is cached by content hash.

## Configuration

```powershell
python -m moviesign init      # writes moviesign.config.json
python -m moviesign voices    # lists Windows and ElevenLabs voices
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
| `tts_backend` | `sapi` | `sapi` for free Windows voices, `elevenlabs` for paid ones. |
| `sapi_voices` | David / Zira | Per bot: Windows `voice` name, `rate` (-10..10), `pitch_semitones`. |
| `voices` | Josh / Arnold / Antoni | ElevenLabs ids per bot, plus `pitch_semitones`. |

Useful flags: `--rating HARD-R`, `--min-gap 3`, `--max-riffs 150`, `--no-vision`
(skip frames — cheaper, but the jokes get more generic), `--subs movie.srt`.

## Notes

- **Cost.** A 90-minute feature runs roughly $2–4 of Claude for the writing.
  Voices are free on the default backend. The `gaps` command costs nothing at
  all and tells you how many riffs you're looking at before you commit.
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
