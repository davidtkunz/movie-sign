"""ElevenLabs text-to-speech, with an on-disk cache so re-runs are free."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import requests

from . import sapi
from .config import Config, elevenlabs_key

API = "https://api.elevenlabs.io/v1"


class VoiceError(RuntimeError):
    pass


def list_voices() -> list[dict[str, Any]]:
    resp = requests.get(API + "/voices", headers={"xi-api-key": elevenlabs_key()}, timeout=30)
    if resp.status_code == 401:
        raise VoiceError("ElevenLabs rejected the API key (401).")
    resp.raise_for_status()
    return resp.json().get("voices", [])


def _cache_key(text: str, voice_id: str, model_id: str, settings: dict) -> str:
    blob = json.dumps(
        {"t": text, "v": voice_id, "m": model_id, "s": settings},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def synthesize(
    text: str,
    speaker: str,
    cfg: Config,
    cache_dir: Path,
    *,
    retries: int = 4,
) -> Path:
    """Render one line through whichever backend the config selects."""
    if cfg.tts_backend == "sapi":
        voice = cfg.sapi_voices[speaker]
        return sapi.speak(
            text, voice.get("voice", ""), int(voice.get("rate", 0)), cache_dir
        )
    if cfg.tts_backend != "elevenlabs":
        raise VoiceError(
            f"unknown tts_backend {cfg.tts_backend!r} "
            "(expected 'sapi' or 'elevenlabs')"
        )
    return _elevenlabs(text, speaker, cfg, cache_dir, retries=retries)


def _elevenlabs(
    text: str,
    speaker: str,
    cfg: Config,
    cache_dir: Path,
    *,
    retries: int = 4,
) -> Path:
    voice = cfg.voices[speaker]
    voice_id = voice["voice_id"]
    settings = {
        "stability": voice.get("stability", 0.4),
        "similarity_boost": voice.get("similarity_boost", 0.75),
        "style": voice.get("style", 0.4),
        "use_speaker_boost": True,
    }

    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{speaker}-{_cache_key(text, voice_id, cfg.tts_model, settings)}.mp3"
    if out.exists() and out.stat().st_size > 0:
        return out

    url = f"{API}/text-to-speech/{voice_id}"
    body = {"text": text, "model_id": cfg.tts_model, "voice_settings": settings}
    headers = {
        "xi-api-key": elevenlabs_key(),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    delay = 2.0
    last_error = ""
    for attempt in range(retries):
        try:
            resp = requests.post(
                url,
                json=body,
                headers=headers,
                params={"output_format": "mp3_44100_128"},
                timeout=120,
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            time.sleep(delay)
            delay *= 2
            continue

        if resp.status_code == 200:
            out.write_bytes(resp.content)
            return out

        if resp.status_code == 401:
            raise VoiceError("ElevenLabs rejected the API key (401). Check ELEVENLABS_API_KEY.")
        if resp.status_code == 402:
            raise VoiceError(
                "ElevenLabs says you're out of credits (402). "
                "Top up, or switch to a cheaper tts_model in the config."
            )
        if resp.status_code == 404:
            raise VoiceError(
                f"Voice id {voice_id!r} for '{speaker}' doesn't exist on this account.\n"
                "Run `python -m moviesign voices` and put a real id in moviesign.config.json."
            )

        last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(delay)
            delay *= 2
            continue
        break

    raise VoiceError(f"Text-to-speech failed for {speaker!r}: {last_error}")
