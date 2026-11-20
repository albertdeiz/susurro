"""Voice engines. Each one synthesizes one line into an audio file.

- edge: Microsoft Edge neural voices via `edge-tts`. Free, no API key, but online.
- say:  macOS built-in voices, 100% local.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

TMP = Path(tempfile.gettempdir()) / "susurro"


class TTSError(RuntimeError):
    pass


def _out(suffix: str) -> Path:
    TMP.mkdir(exist_ok=True)
    fd, name = tempfile.mkstemp(suffix=suffix, dir=TMP)
    import os

    os.close(fd)
    return Path(name)


def _edge(text: str, voice: str, speed: float) -> Path:
    import edge_tts

    pct = round((speed - 1.0) * 100)
    rate = f"{pct:+d}%"
    out = _out(".mp3")

    async def run() -> None:
        await edge_tts.Communicate(text, voice, rate=rate).save(str(out))

    try:
        asyncio.run(run())
    except Exception as e:  # edge_tts raises several types (network, bad voice…)
        out.unlink(missing_ok=True)
        raise TTSError(f"edge-tts: {e}") from e
    if out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise TTSError("edge-tts returned empty audio (offline?)")
    return out


def _say(text: str, voice: str, speed: float) -> Path:
    out = _out(".aiff")
    wpm = str(int(185 * speed))
    r = subprocess.run(
        ["say", "-v", voice, "-r", wpm, "-o", str(out), text],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        out.unlink(missing_ok=True)
        raise TTSError(f"say: {r.stderr.strip() or 'failed'} (is voice '{voice}' installed?)")
    return out


ENGINES = {"edge": _edge, "say": _say}


def synthesize(engine: str, text: str, voice: str, speed: float) -> Path:
    return ENGINES[engine](text, voice, speed)
