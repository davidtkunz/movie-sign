"""Configuration: riff timing, comedy register, and voice assignments."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

CONFIG_NAME = "moviesign.config.json"

# ElevenLabs pre-made voice IDs. These are the widely published stock voices;
# run `python -m moviesign voices` to list what your account actually has and
# replace these with whatever you like.
DEFAULT_VOICES: dict[str, dict[str, Any]] = {
    "host": {
        "name": "Josh",
        "voice_id": "TxGEqnHWrfWFTfGW9XjX",
        "stability": 0.45,
        "similarity_boost": 0.75,
        "style": 0.30,
        "pitch_semitones": 0.0,
    },
    "crow": {
        "name": "Arnold",
        "voice_id": "VR6AewLTigWG4xSOukaG",
        "stability": 0.35,
        "similarity_boost": 0.80,
        "style": 0.55,
        "pitch_semitones": 0.0,
    },
    "servo": {
        "name": "Antoni",
        "voice_id": "ErXwobaYiN019PkySvjV",
        "stability": 0.30,
        "similarity_boost": 0.80,
        "style": 0.70,
        "pitch_semitones": 0.0,
    },
}

# Windows usually ships only David and Zira, so the three bots are separated
# by rate and a post-hoc pitch shift as much as by voice. Host is the plain
# baseline; Crow is fast and pitched down to a rasp; Servo is pitched well up
# into puppet territory.
DEFAULT_SAPI_VOICES: dict[str, dict[str, Any]] = {
    "host":  {"voice": "Microsoft David Desktop", "rate": 0, "pitch_semitones": 0.0},
    "crow":  {"voice": "Microsoft Zira Desktop",  "rate": 3, "pitch_semitones": -1.5},
    "servo": {"voice": "Microsoft David Desktop", "rate": 1, "pitch_semitones": 4.0},
}

SPEAKERS = tuple(DEFAULT_VOICES)


@dataclass
class Config:
    # --- timing -------------------------------------------------------------
    min_gap: float = 2.0
    """Shortest silence (seconds) worth putting a joke in."""

    margin: float = 0.35
    """Dead air kept at each end of a gap so the riff never clips dialogue."""

    max_riff_seconds: float = 9.0
    """Cap on a single riff, even when the gap is a minute of nothing."""

    words_per_second: float = 2.6
    """Assumed delivery rate. Drives the word budget handed to the writer."""

    max_tempo_stretch: float = 1.12
    """A riff that overruns its gap gets sped up by at most this before it's cut."""

    max_riffs: int = 400
    """Ceiling on riffs per feature. Gaps are sampled across the runtime, not front-loaded."""

    riff_rate: float = 0.55
    """Roughly what fraction of offered gaps should get a joke. The rest stay silent."""

    # --- writing ------------------------------------------------------------
    model: str = "claude-opus-5"
    effort: str = "medium"
    rating: str = "R"
    batch_size: int = 8
    """Gaps per API request. Larger = cheaper, but the model sees less of each."""

    vision: bool = True
    frame_width: int = 768

    # --- voice --------------------------------------------------------------
    tts_backend: str = "sapi"
    """Which voice engine: "sapi" (free, Windows, no account) or "elevenlabs"."""

    tts_model: str = "eleven_turbo_v2_5"
    voices: dict[str, dict[str, Any]] = field(
        default_factory=lambda: json.loads(json.dumps(DEFAULT_VOICES))
    )
    sapi_voices: dict[str, dict[str, Any]] = field(
        default_factory=lambda: json.loads(json.dumps(DEFAULT_SAPI_VOICES))
    )

    # --- output -------------------------------------------------------------
    sample_rate: int = 44100
    mp3_bitrate: str = "96k"

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or Path.cwd() / CONFIG_NAME
        cfg = cls()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {f for f in cfg.__dataclass_fields__}
            for key, value in data.items():
                if key in known:
                    setattr(cfg, key, value)
        # Fill in any voice fields the user left out.
        for speaker, defaults in DEFAULT_VOICES.items():
            merged = dict(defaults)
            merged.update(cfg.voices.get(speaker, {}))
            cfg.voices[speaker] = merged
        for speaker, defaults in DEFAULT_SAPI_VOICES.items():
            merged = dict(defaults)
            merged.update(cfg.sapi_voices.get(speaker, {}))
            cfg.sapi_voices[speaker] = merged
        return cfg

    def save(self, path: Path | None = None) -> Path:
        path = path or Path.cwd() / CONFIG_NAME
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path

    def pitch_for(self, speaker: str) -> float:
        table = self.sapi_voices if self.tts_backend == "sapi" else self.voices
        return float(table.get(speaker, {}).get("pitch_semitones", 0.0))

    def word_budget(self, usable_seconds: float) -> int:
        return max(2, int(usable_seconds * self.words_per_second))


def anthropic_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set.\n"
            "  PowerShell:  $env:ANTHROPIC_API_KEY = 'sk-ant-...'\n"
            "  Permanent:   setx ANTHROPIC_API_KEY \"sk-ant-...\"  (then reopen the terminal)"
        )
    return key


def elevenlabs_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY is not set.\n"
            "  PowerShell:  $env:ELEVENLABS_API_KEY = '...'\n"
            "  Permanent:   setx ELEVENLABS_API_KEY \"...\"  (then reopen the terminal)\n"
            "Get one at https://elevenlabs.io/app/settings/api-keys"
        )
    return key
