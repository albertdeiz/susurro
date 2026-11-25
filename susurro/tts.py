"""Voice engines. Each one synthesizes one line into an audio file.

Each line comes back with the time each word starts, for the karaoke transcript: real
timings from Edge, none from macOS voices (the player estimates them).

- edge: Microsoft Edge neural voices via `edge-tts`. Free, no API key, but online.
- say:  macOS built-in voices, 100% local. Synthesized in-process with NSSpeechSynthesizer,
        one kept per worker thread: ~0.05 s per line instead of ~0.7 s for spawning `say`.
        Falls back to the `say` command if that fails.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

TMP = Path(tempfile.gettempdir()) / "susurro"


class TTSError(RuntimeError):
    pass


@dataclass
class Audio:
    path: Path
    # (start in seconds, first char, end char) of each word in the line; [] if unknown.
    words: list[tuple[float, int, int]] = field(default_factory=list)


def _out(suffix: str) -> Path:
    TMP.mkdir(exist_ok=True)
    fd, name = tempfile.mkstemp(suffix=suffix, dir=TMP)
    import os

    os.close(fd)
    return Path(name)


def _edge(text: str, voice: str, speed: float) -> Audio:
    import edge_tts

    pct = round((speed - 1.0) * 100)
    rate = f"{pct:+d}%"
    out = _out(".mp3")
    boundaries: list[tuple[float, str]] = []

    async def run() -> None:
        tts = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
        with out.open("wb") as f:
            async for chunk in tts.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    boundaries.append((chunk["offset"] / 1e7, chunk["text"]))  # 100 ns units

    try:
        asyncio.run(run())
    except Exception as e:  # edge_tts raises several types (network, bad voice…)
        out.unlink(missing_ok=True)
        raise TTSError(f"edge-tts: {e} (text: {text[:60]!r})") from e
    if out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise TTSError("edge-tts returned empty audio (offline?)")
    return Audio(out, _locate(text, boundaries))


def _locate(text: str, boundaries: list[tuple[float, str]]) -> list[tuple[float, int, int]]:
    """Find each spoken word in the line, in order. Words not found are skipped."""
    words, cursor = [], 0
    for start, word in boundaries:
        i = text.find(word, cursor)
        if i < 0:
            continue
        words.append((start, i, i + len(word)))
        cursor = i + len(word)
    return words


_local = threading.local()


@cache
def _voice_id(name: str) -> str | None:
    """'Mónica' → the best installed voice with that name (premium > enhanced > compact)."""
    from AppKit import NSSpeechSynthesizer

    voices = {str(v): str(NSSpeechSynthesizer.attributesForVoice_(v)["VoiceName"])
              for v in NSSpeechSynthesizer.availableVoices()}
    # 'Eddy (Spanish (Spain))' names one locale; a bare 'Eddy' exists in several languages.
    found = [v for v, n in voices.items() if n == name] or [
        v for v, n in voices.items() if n.split(" (")[0] == name.split(" (")[0]]
    rank = {"premium": 0, "enhanced": 1}
    found.sort(key=lambda v: next((r for k, r in rank.items() if k in v), 2))
    return str(found[0]) if found else None


def installed(name: str) -> bool:
    """Whether a macOS voice with that name is installed."""
    return _voice_id(name) is not None


def _synth_for(voice_id: str):
    from AppKit import NSSpeechSynthesizer

    cached = getattr(_local, "synths", None)
    if cached is None:
        cached = _local.synths = {}
    if voice_id not in cached:
        cached[voice_id] = NSSpeechSynthesizer.alloc().initWithVoice_(voice_id)
    return cached[voice_id]


def _say_native(text: str, voice: str, speed: float) -> Path:
    from Foundation import NSURL, NSDate, NSRunLoop

    voice_id = _voice_id(voice)
    if not voice_id:
        raise TTSError(f"voice '{voice}' is not installed")
    synth = _synth_for(voice_id)
    synth.setRate_(185 * speed)
    out = _out(".aiff")
    if not synth.startSpeakingString_toURL_(text, NSURL.fileURLWithPath_(str(out))):
        out.unlink(missing_ok=True)
        raise TTSError("NSSpeechSynthesizer refused the text")
    deadline = time.monotonic() + 10 + len(text) / 20
    run_loop = NSRunLoop.currentRunLoop()
    while synth.isSpeaking():
        if time.monotonic() > deadline:
            synth.stopSpeaking()
            out.unlink(missing_ok=True)
            raise TTSError("NSSpeechSynthesizer timed out")
        run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.005))
    if not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise TTSError("NSSpeechSynthesizer produced no audio")
    return out


def warm_up(engine: str, voice: str) -> None:
    """Load the voice in this thread so the first real line is fast (local voices only).

    Creating the synthesizer isn't enough: the voice data loads on the first synthesis."""
    if engine == "say" and _voice_id(voice):
        _say_native("ok", voice, 1.0).unlink(missing_ok=True)


def _say(text: str, voice: str, speed: float) -> Audio:
    try:
        return Audio(_say_native(text, voice, speed))
    except Exception:
        return Audio(_say_command(text, voice, speed))


def _say_command(text: str, voice: str, speed: float) -> Path:
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


def speakable(text: str) -> bool:
    """Edge returns no audio for text without letters or digits (e.g. '---', '…')."""
    return bool(re.search(r"\w", text))


def synthesize(engine: str, text: str, voice: str, speed: float, retries: int = 1) -> Audio:
    for attempt in range(retries + 1):
        try:
            return ENGINES[engine](text, voice, speed)
        except TTSError:
            if attempt == retries:
                raise
    raise AssertionError("unreachable")
