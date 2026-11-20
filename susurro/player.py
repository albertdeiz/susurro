"""Pipeline: script (Claude or the text as is) → per-line synthesis → queued playback.

Lines are synthesized ahead, several at a time (the TTS service has a long latency tail), and
played in order: the audio queue holds futures, so the player waits only for the next line.
A new `speak` cancels the previous one; `stop` cuts the audio immediately.
"""

from __future__ import annotations

import copy
import queue
import subprocess
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from . import i18n, tts, writer
from .config import Settings

CHIME_START = "/System/Library/Sounds/Tink.aiff"
CHIME_ERROR = "/System/Library/Sounds/Basso.aiff"
PREFETCH = 3  # lines synthesized concurrently ahead of playback
# Shared by all jobs so worker threads (and the local voices loaded in them) stay warm.
_POOL = ThreadPoolExecutor(max_workers=PREFETCH, thread_name_prefix="tts")

_DONE = object()


def chime(path: str) -> None:
    subprocess.Popen(["afplay", "-v", "0.4", path], stderr=subprocess.DEVNULL)


def resolve_language(text: str, s: Settings) -> str:
    """Speech language: the fixed one from settings, or detected from the text."""
    if s.language != "auto":
        return s.language
    return i18n.detect(text, fallback=s.ui_language)


class Job:
    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.audio: queue.Queue = queue.Queue(maxsize=PREFETCH)  # Future[Path] | _DONE
        self.proc: subprocess.Popen | None = None


class Player:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: Job | None = None
        self.status = "idle"  # idle | preparing | thinking | speaking | error: …
        self.language = ""  # language of the current/last job
        self.last_text = ""

    # ------------------------------------------------------------ API

    def speak(self, text: str, settings: Settings) -> None:
        text = text.strip()
        if not text:
            return
        self.stop()
        job = Job()
        with self._lock:
            self._job = job
        self.last_text = text
        s = copy.deepcopy(settings)  # later settings changes don't affect this job
        lang = resolve_language(text, s)
        self.language = lang
        self.status = "preparing" if s.mode == "read" else "thinking"
        chime(CHIME_START)
        threading.Thread(target=self._produce, args=(job, text, s, lang), daemon=True).start()
        threading.Thread(target=self._consume, args=(job,), daemon=True).start()

    def warm_up(self, s: Settings, lang: str) -> None:
        """Load the local voice in every worker thread ahead of the first request."""
        if s.engine != "say" or self.busy:  # never hold up the workers of a running job
            return
        voices = {s.voice(lang), s.partner_voice(lang)}
        barrier = threading.Barrier(PREFETCH)  # one task per worker thread, not all on one

        def warm() -> None:
            try:
                barrier.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass
            for v in voices:
                tts.warm_up(s.engine, v)

        for _ in range(PREFETCH):
            _POOL.submit(warm)

    def stop(self) -> None:
        with self._lock:
            job, self._job = self._job, None
        if job:
            job.cancel.set()
            if job.proc and job.proc.poll() is None:
                job.proc.terminate()
            _drain(job.audio)
        self.status = "idle"

    @property
    def busy(self) -> bool:
        return self._job is not None

    # ------------------------------------------------------------ threads

    def _produce(self, job: Job, text: str, s: Settings, lang: str) -> None:
        voices = {"A": s.voice(lang), "B": s.partner_voice(lang)}
        try:
            for u in writer.write(text, s, lang, job.cancel):
                if job.cancel.is_set():
                    break
                if not tts.speakable(u.text):
                    continue
                voice = voices.get(u.speaker, voices["A"])
                _put(job, _POOL.submit(tts.synthesize, s.engine, u.text, voice, s.speed))
        except writer.WriterError as e:
            self._fail(job, str(e))
        except Exception as e:  # never leave the app stuck in "thinking"
            self._fail(job, f"{type(e).__name__}: {e}")
        finally:
            _put(job, _DONE)

    def _consume(self, job: Job) -> None:
        while not job.cancel.is_set():
            try:
                item = job.audio.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is _DONE:
                break
            path = _result(job, item, lambda msg: self._fail(job, msg))
            if path is None:
                if job.cancel.is_set():
                    break
                continue  # this line failed (already reported); try the next one
            if job.cancel.is_set():
                path.unlink(missing_ok=True)
                break
            self.status = "speaking"
            job.proc = subprocess.Popen(["afplay", str(path)])
            job.proc.wait()
            path.unlink(missing_ok=True)
        with self._lock:
            if self._job is job:
                self._job = None
                if not self.status.startswith("error"):
                    self.status = "idle"

    def _fail(self, job: Job, msg: str) -> None:
        if job.cancel.is_set():
            return
        self.status = f"error: {msg}"
        chime(CHIME_ERROR)


def _discard(item: object) -> None:
    """Drop a queued line: cancel its synthesis, or delete its file if it already finished."""
    if not isinstance(item, Future):
        return
    if not item.cancel() and item.done() and not item.exception():
        item.result().unlink(missing_ok=True)
    elif not item.done():
        item.add_done_callback(_discard)


def _result(job: Job, fut: Future, on_error) -> Path | None:
    """Wait for a line's audio, waking up to honour cancel. None if it failed or was cancelled."""
    while not job.cancel.is_set():
        try:
            return fut.result(timeout=0.1)
        except TimeoutError:
            continue
        except Exception as e:
            on_error(str(e))
            return None
    _discard(fut)
    return None


def _put(job: Job, item: object) -> None:
    while True:
        if job.cancel.is_set():
            _discard(item)
            return
        try:
            job.audio.put(item, timeout=0.2)
            return
        except queue.Full:
            continue


def _drain(q: queue.Queue) -> None:
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            return
        _discard(item)
