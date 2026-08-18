"""Windows SAPI speech — the free, no-account voice backend.

The voices that ship with Windows sound like a 1998 text-to-speech program,
which is close to ideal for two robot puppets heckling a B-movie. There are
usually only two installed (David and Zira), so the three bots are separated
by a combination of voice, speaking rate, and a pitch shift applied afterward.

Requires nothing but Windows. No key, no account, no per-word cost.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("speak.ps1")


class SapiError(RuntimeError):
    pass


def available() -> bool:
    return platform.system() == "Windows" and _powershell() is not None


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def installed_voices() -> list[str]:
    """Names of the SAPI voices on this machine."""
    shell = _powershell()
    if not shell:
        return []
    proc = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command",
         "Add-Type -AssemblyName System.Speech; "
         "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
         ".GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"],
        capture_output=True, text=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _cache_key(text: str, voice: str, rate: int) -> str:
    blob = json.dumps({"t": text, "v": voice, "r": rate, "b": "sapi"}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def speak(text: str, voice: str, rate: int, cache_dir: Path) -> Path:
    """Render one line to a wav. Cached by content, like the paid backend."""
    if not available():
        raise SapiError(
            "The sapi backend needs Windows PowerShell. "
            "On macOS or Linux, set tts_backend to 'elevenlabs' in the config."
        )
    if not SCRIPT.exists():
        raise SapiError(f"missing helper script: {SCRIPT}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"sapi-{_cache_key(text, voice, rate)}.wav"
    if out.exists() and out.stat().st_size > 44:  # bigger than a bare wav header
        return out

    handle, name = tempfile.mkstemp(suffix=".txt", dir=str(cache_dir))
    text_file = Path(name)
    try:
        import os

        os.close(handle)
        text_file.write_text(text, encoding="utf-8")

        proc = subprocess.run(
            [_powershell(), "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
             "-TextFile", str(text_file), "-OutFile", str(out),
             "-Voice", voice, "-Rate", str(rate)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not out.exists():
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise SapiError("SAPI failed: " + (detail[-1] if detail else "no output"))
    finally:
        text_file.unlink(missing_ok=True)

    return out
