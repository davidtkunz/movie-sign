# Example: *The Star Packer* (1934)

Real output, not a mockup. A 54-minute John Wayne B-western went in; these 52
riffs came out.

**Listen:** [`sample-riffs.mp3`](sample-riffs.mp3) — 28 seconds, eight riffs back
to back, spoken by the free Windows voices so you can hear what the default
backend actually sounds like before installing anything.

**Read:** [`script.txt`](script.txt) — all 52 riffs with timecodes.
[`riffs.json`](riffs.json) — the same thing in the editable form `render` consumes.

## The movie

*The Star Packer*, Lone Star Productions, 1934. It's one of the John Wayne
"Lone Star" westerns that circulate as public domain and is freely available on
the Internet Archive, which is where this copy came from. That makes it a fair
test subject: genuinely bad, and nobody's rights are bruised by heckling it.

The video file is **not** in this repo. Bring your own copy; movie-sign only ever
produces a separate commentary track.

## How it was made

```powershell
python -m moviesign script "The_Star_Packer_512kb.mp4" --rating R
python -m moviesign render "The_Star_Packer_512kb.mp4"
```

The file had no subtitle track, so Whisper (`small`) transcribed it first — 270
lines of dialogue off a 1934 optical soundtrack, with the expected damage
("Cover my tray of the yak"). Gap detection found **65 stretches of silence**
long enough to hold a joke, 8.2 minutes in total. The writer took 52 of them and
left 13 alone.

Rendering dropped 2 more for overrunning their gap, so **50 riffs** made the
final track. That's the rule the whole tool is built around: a joke that would
step on the next line of dialogue gets thrown away.

| | |
|---|---|
| Cost | ~$2 of Claude, $0 of voices |
| Riffs | 52 written, 50 placed |
| Split | Crow 24, Servo 16, Host 12 |
| Length | median 10 words |

## What the frames bought

Each gap is sent to the model with a frame grabbed from the middle of the
silence. These are the riffs that could only exist because something looked at
the picture:

> **CROW:** There's a radio tower on your 1880s hilltop, fellas.
>
> **HOST:** Somebody bumped the lens cap.
>
> **SERVO:** Binoculars pointed at a gray bedsheet.
>
> **CROW:** The cinematographer has fully surrendered. That's a hat and a rumor.
>
> **HOST:** Eleven dollars of jail, and they're gonna shoot it from here for a while.

Run with `--no-vision` and the jokes get noticeably more generic — they can only
riff on what was said, not on what's on screen.

## Known rough edges in this run

- **A few repeated constructions.** "The horse is doing all the acting" and "his
  mustache is doing all the acting" both survived. Batches run in parallel, so a
  batch can only see riffs from earlier waves, not from its siblings in flight.
- **`--rating R` came out mild.** The model settled into witty contempt rather
  than profanity. `--rating HARD-R` pushes considerably harder.
