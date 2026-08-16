"""The writers' room: turn silent gaps into riffs, using Claude.

Each request carries a batch of gaps. For every gap the model gets the dialogue
on either side, a frame grabbed from the middle of the silence, and a hard word
budget derived from the gap's length. It answers with structured output so
there's nothing to parse out of prose.
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import Config
from .cues import Gap, timestamp

Speaker = Literal["host", "crow", "servo"]


class Riff(BaseModel):
    gap_id: int = Field(description="Which gap this riff belongs in.")
    speaker: Speaker = Field(description="Who says it.")
    line: str = Field(description="The riff itself. Spoken words only, no stage direction.")


class RiffBatch(BaseModel):
    riffs: list[Riff]


@dataclass
class PlacedRiff:
    gap_id: int
    speaker: str
    line: str
    start: float
    budget_seconds: float


PERSONA = """\
You are the writers' room for a riff track — three voices heckling a bad movie \
from the back of the theater, in the Mystery Science Theater 3000 tradition.

THE VOICES
- HOST — the human being. Dry, weary, deadpan. Does the setup lines and the flat \
observations. Talks like somebody who has seen a great many bad movies and has \
made his peace with it.
- CROW — sardonic and fast. Goes after production values, continuity, and anyone \
who made a choice on this film. Cruel, and enjoying it.
- SERVO — theatrical, committed to the bit. Will speak AS a character on screen, \
will narrate, will burst into song. Never underplays anything.

THE RULES OF THE ROOM
1. A riff lands in SILENCE. Every gap comes with a hard time budget. Talking over \
the movie's dialogue is the one unforgivable sin — go over budget and the riff \
gets thrown away.
2. Ride the movie. Riff what is actually on screen or what was just said: the boom \
mic in frame, the matte painting, the man's hair, the line reading, the fact that \
this shot has now gone on for ninety seconds. Specific beats clever.
3. Short is funny. Four to twelve words is the pocket. If it takes a sentence and \
a half, you don't have a joke yet — you have an observation.
4. Don't explain it. No "wow," no "geez," no throat-clearing before the punchline. \
Land and get out.
5. Not every gap deserves a riff. Silence is a rhythm, and a forced joke costs more \
than a quiet moment. Omit any gap you don't have something good for — leave it out \
of your output entirely.
6. Never repeat a joke, a construction, or a target you've already used.
7. You are heckling, not narrating. If a line would work as a caption, cut it.
"""

RATINGS = {
    "PG": (
        "REGISTER: PG. Keep it clean. The comedy comes from timing and specificity, "
        "not from language."
    ),
    "R": (
        "REGISTER: R. Profanity is welcome when it lands — shit, fuck, asshole, "
        "goddamn, crude comparisons, the scatological. Swear like a comedy writer at "
        "two in the morning, not like someone who just learned they're allowed to. "
        "The profanity is punctuation, not the joke: one well-placed 'fuck' is funny, "
        "a 'fuck' in every line is noise. Punch at the movie — the budget, the "
        "choices, the people who signed off on this. Not at anybody's race, gender, "
        "or the like; that isn't edgy, it's just a worse joke."
    ),
    "HARD-R": (
        "REGISTER: hard R, no brakes. Filthy, mean, and specific. Sexual and "
        "scatological material is fair game, and so is genuine contempt for the "
        "filmmakers. Still: the profanity serves the joke, never substitutes for it, "
        "and the target is always the movie — never anybody's race, gender, or the "
        "like, which is just a worse joke wearing a leather jacket."
    ),
}


def build_system(cfg: Config) -> str:
    rating = RATINGS.get(cfg.rating.upper(), RATINGS["R"])
    return (
        f"{PERSONA}\n{rating}\n\n"
        f"Roughly {int(cfg.riff_rate * 100)}% of the gaps you're offered should get a "
        "riff. Spend your jokes on the gaps that deserve them."
    )


def _image_block(path: Path) -> dict[str, Any]:
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def _gap_blocks(gap: Gap, cfg: Config, frame: Path | None) -> list[dict[str, Any]]:
    budget = gap.duration
    words = cfg.word_budget(budget)
    before = "\n".join(f"  {c.text}" for c in gap.before) or "  (nothing — silence before this)"
    after = "\n".join(f"  {c.text}" for c in gap.after) or "  (nothing — silence after this)"

    header = (
        f"=== GAP {gap.id} @ {timestamp(gap.start, comma=False)} ===\n"
        f"BUDGET: {budget:.1f}s of silence — at most {words} words.\n"
        f"Just said:\n{before}\n"
        f"Said next (you must be finished before this):\n{after}"
    )
    blocks: list[dict[str, Any]] = [{"type": "text", "text": header}]
    if frame is not None and frame.exists():
        blocks.append({"type": "text", "text": "On screen during this silence:"})
        blocks.append(_image_block(frame))
    return blocks


def _write_batch(
    client,
    cfg: Config,
    batch: list[Gap],
    frames: dict[int, Path],
    recent: list[str],
) -> list[Riff]:
    content: list[dict[str, Any]] = []

    intro = f"Here are {len(batch)} silences from the movie, in order."
    if recent:
        already = "\n".join(f"  - {line}" for line in recent[-12:])
        intro += f"\n\nRiffs you have already used (do not repeat these or their shape):\n{already}"
    content.append({"type": "text", "text": intro})

    for gap in batch:
        content.extend(_gap_blocks(gap, cfg, frames.get(gap.id)))

    content.append({
        "type": "text",
        "text": (
            "Write the riffs now. One riff per gap at most, and skip any gap you "
            "don't have a real joke for — omitting it is a valid and often correct "
            "answer. Respect every word budget."
        ),
    })

    response = client.messages.parse(
        model=cfg.model,
        max_tokens=8000,
        output_config={"effort": cfg.effort},
        system=[{
            "type": "text",
            "text": build_system(cfg),
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": content}],
        output_format=RiffBatch,
    )

    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        category = getattr(detail, "category", None) if detail else None
        print(f"  ! batch declined by safety classifiers ({category or 'unspecified'}) — skipped")
        return []

    parsed = response.parsed_output
    if parsed is None:
        # Structured output should make this unreachable; recover rather than crash.
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            parsed = RiffBatch.model_validate_json(text)
        except Exception:
            print("  ! batch returned unparseable output — skipped")
            return []

    valid_ids = {g.id for g in batch}
    return [r for r in parsed.riffs if r.gap_id in valid_ids and r.line.strip()]


def write_riffs(
    gaps: list[Gap],
    frames: dict[int, Path],
    cfg: Config,
    *,
    workers: int = 4,
) -> list[PlacedRiff]:
    """Generate riffs for every gap. Batches run concurrently; order is restored after."""
    import anthropic

    client = anthropic.Anthropic()
    batches = [gaps[i : i + cfg.batch_size] for i in range(0, len(gaps), cfg.batch_size)]
    by_batch: dict[int, list[Riff]] = {}
    recent: list[str] = []

    print(f"  {len(gaps)} gaps in {len(batches)} batches, {workers} at a time")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        # Seed each batch with the riffs written so far. Batches launched together
        # can't see each other, but later waves can see earlier ones.
        futures = {}
        for i, batch in enumerate(batches):
            futures[pool.submit(_write_batch, client, cfg, batch, frames, list(recent))] = i
            if (i + 1) % workers == 0:
                done, _ = concurrent.futures.wait(
                    futures, return_when=concurrent.futures.ALL_COMPLETED
                )
                for fut in done:
                    idx = futures[fut]
                    try:
                        result = fut.result()
                    except Exception as exc:  # keep going; one bad batch isn't fatal
                        print(f"  ! batch {idx} failed: {exc}")
                        result = []
                    by_batch[idx] = result
                    recent.extend(r.line for r in result)
                futures = {}
                print(f"    ...{sum(len(v) for v in by_batch.values())} riffs so far")

        for fut in concurrent.futures.as_completed(futures):
            idx = futures[fut]
            try:
                by_batch[idx] = fut.result()
            except Exception as exc:
                print(f"  ! batch {idx} failed: {exc}")
                by_batch[idx] = []

    gap_by_id = {g.id: g for g in gaps}
    placed: list[PlacedRiff] = []
    for idx in sorted(by_batch):
        for riff in by_batch[idx]:
            gap = gap_by_id[riff.gap_id]
            placed.append(PlacedRiff(
                gap_id=gap.id,
                speaker=riff.speaker,
                line=riff.line.strip(),
                start=gap.start,
                budget_seconds=gap.duration,
            ))

    placed.sort(key=lambda r: r.start)
    return placed


def save_script(riffs: list[PlacedRiff], out_json: Path, out_txt: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps([r.__dict__ for r in riffs], indent=2),
        encoding="utf-8",
    )
    lines = [
        f"[{timestamp(r.start, comma=False)}] {r.speaker.upper():>5}: {r.line}"
        for r in riffs
    ]
    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_script(path: Path) -> list[PlacedRiff]:
    return [PlacedRiff(**row) for row in json.loads(path.read_text(encoding="utf-8"))]
