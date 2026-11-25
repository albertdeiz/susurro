"""Pipeline: script (Claude or the text as is) → per-line synthesis → playback, line by line.

The job keeps every line of the script, so playback can pause, jump back or forward, and
pick up setting changes while it runs. Lines are synthesized ahead, several at a time (the
TTS service has a long latency tail): each line from the one playing up to PREFETCH ahead
has its synthesis running on the shared pool, so the player only waits for the next line.
Audio already played is kept until the job ends, so jumping back is instant.

Audio plays in-process through NSSound, which can pause, seek and report its position.
A new `speak` cancels the previous job; `stop` cuts the audio immediately.
"""

from __future__ import annotations

import bisect
import copy
import re
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from . import i18n, tts, writer
from .config import Settings

CHIME_START = "/System/Library/Sounds/Tink.aiff"
CHIME_ERROR = "/System/Library/Sounds/Basso.aiff"
PREFETCH = 3  # lines synthesized concurrently ahead of playback
# Shared by all jobs so worker threads (and the local voices loaded in them) stay warm.
_POOL = ThreadPoolExecutor(max_workers=PREFETCH, thread_name_prefix="tts")
TICK = 0.02  # how often playback checks for pause, seek and the end of a line
RESTART_LINE = 0.15  # "previous" past this fraction of a line restarts it instead


def chime(path: str) -> None:
    subprocess.Popen(["afplay", "-v", "0.4", path], stderr=subprocess.DEVNULL)


def resolve_languages(text: str, s: Settings) -> tuple[str, str]:
    """(language of the text, speech language): fixed in settings, else detected / the same."""
    source = s.input_language
    if source == "auto":
        source = i18n.detect(text, fallback=s.ui_language)
    return source, s.language if s.language != "auto" else source


def needs_rewrite(job: "Job", new: Settings) -> bool:
    """Whether a settings change needs a new script (Claude again), not just new audio."""
    old = job.settings
    if new.mode != old.mode:
        return True
    source, lang = resolve_languages(job.text, new)
    before = writer.needs_claude(old.mode, job.source, job.lang)
    after = writer.needs_claude(new.mode, source, lang)
    if before != after:
        return True
    if not after:
        return False  # read as is either way: only the voice may change
    return (lang, new.detail if new.mode != "read" else "") != \
        (job.lang, old.detail if old.mode != "read" else "")


@dataclass
class Line:
    speaker: str  # "A" or "B"
    text: str
    audio: Future | None = None  # Future[tts.Audio]
    key: tuple = ()  # (engine, voice, speed) the audio was made with


@dataclass
class Progress:
    """What the player panel shows; a copy, safe to read from any thread."""

    lines: list[tuple[str, str]]  # (speaker, text)
    index: int  # line being played
    position: float  # fraction of that line already played
    written: bool  # the whole script is in (Claude has finished)
    paused: bool
    mode: str
    word: tuple[int, int] | None  # (start, end) of the word being said in the current line


class Job:
    def __init__(self, text: str, settings: Settings, source: str, lang: str) -> None:
        self.text, self.settings = text, settings
        self.source, self.lang = source, lang  # language of the text, and of the speech
        self.cancel = threading.Event()
        self.cond = threading.Condition()
        self.lines: list[Line] = []
        self.written = False
        self.index = 0
        self.offset = 0.0  # fraction of the line to start from (after a seek)
        self.position = 0.0
        self.gen = 0  # bumped by seeks and voice changes: interrupts the line playing
        self.paused = False
        self.word: tuple[int, int] | None = None


class Player:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: Job | None = None
        self.status = "idle"  # idle | preparing | thinking | speaking | paused | error: …
        self.language = ""  # speech language of the current/last job
        self.source = ""  # language of its text
        self.last_text = ""

    # ------------------------------------------------------------ API

    def speak(self, text: str, settings: Settings) -> None:
        text = text.strip()
        if not text:
            return
        self.stop()
        s = copy.deepcopy(settings)  # changes reach the job only through `update`
        source, lang = resolve_languages(text, s)
        job = Job(text, s, source, lang)
        with self._lock:
            self._job = job
        self.last_text = text
        self.source, self.language = source, lang
        self.status = "thinking" if writer.needs_claude(s.mode, source, lang) else "preparing"
        chime(CHIME_START)
        threading.Thread(target=self._produce, args=(job,), daemon=True).start()
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
            job.cancel.set()  # the consumer stops the sound and deletes the audio
            with job.cond:
                job.cond.notify_all()
        self.status = "idle"

    def toggle_pause(self) -> None:
        job = self._job
        if job:
            job.paused = not job.paused

    def seek(self, pos: float) -> None:
        """Jump to line int(pos), a fraction pos % 1 into it."""
        job = self._job
        if not job:
            return
        with job.cond:
            n = len(job.lines)
            if not n:
                return
            # Past the last line ends the job once the script is complete.
            pos = max(0.0, min(pos, float(n) if job.written else n - 1e-6))
            job.index, job.offset = int(pos), pos - int(pos)
            job.position, job.word = job.offset, None
            job.gen += 1
            self._schedule(job)
            job.cond.notify_all()

    def skip(self, delta: int) -> None:
        """Next/previous line. Previous restarts the current line if it's well under way."""
        job = self._job
        if not job:
            return
        if delta < 0 and job.position > RESTART_LINE:
            delta = 0
        self.seek(job.index + delta)

    def update(self, settings: Settings) -> None:
        """Apply settings changed during playback: new audio, or a new script if needed."""
        job = self._job
        if not job:
            return
        s = copy.deepcopy(settings)
        if needs_rewrite(job, s):
            self.speak(job.text, s)
            return
        with job.cond:
            job.settings = s
            job.source, job.lang = resolve_languages(job.text, s)
            self.source, self.language = job.source, job.lang
            current = job.lines[job.index] if job.index < len(job.lines) else None
            if current is not None and current.key and current.key != self._key(job, current):
                job.offset = job.position  # replay the current line from where it was
                job.gen += 1
            for line in job.lines:  # audio made with the old voice is no use any more
                if line.audio is not None and line.key != self._key(job, line):
                    _discard(line.audio)
                    line.audio, line.key = None, ()
            self._schedule(job)
            job.cond.notify_all()

    def progress(self) -> Progress | None:
        job = self._job
        if not job:
            return None
        with job.cond:
            return Progress([(ln.speaker, ln.text) for ln in job.lines], job.index,
                            job.position, job.written, job.paused, job.settings.mode,
                            job.word)

    @property
    def busy(self) -> bool:
        return self._job is not None

    # ------------------------------------------------------------ threads

    @staticmethod
    def _key(job: Job, line: Line) -> tuple:
        s = job.settings
        voice = s.partner_voice(job.lang) if line.speaker == "B" else s.voice(job.lang)
        return (s.engine, voice, s.speed)

    def _schedule(self, job: Job) -> None:
        """Start synthesis of the line playing and the next PREFETCH. Hold job.cond."""
        if job.cancel.is_set():
            return
        for line in job.lines[job.index:job.index + PREFETCH + 1]:
            key = self._key(job, line)
            if line.audio is not None and line.key == key:
                continue
            if line.audio is not None:
                _discard(line.audio)
            engine, voice, speed = key
            line.key = key
            line.audio = _POOL.submit(tts.synthesize, engine, line.text, voice, speed)

    def _produce(self, job: Job) -> None:
        try:
            for u in writer.write(job.text, job.settings, job.source, job.lang,
                                   job.cancel):
                if job.cancel.is_set():
                    break
                if not tts.speakable(u.text):
                    continue
                with job.cond:
                    job.lines.append(Line(u.speaker, u.text))
                    self._schedule(job)
                    job.cond.notify_all()
        except writer.WriterError as e:
            self._fail(job, str(e))
        except Exception as e:  # never leave the app stuck in "thinking"
            self._fail(job, f"{type(e).__name__}: {e}")
        finally:
            with job.cond:
                job.written = True
                job.cond.notify_all()

    def _consume(self, job: Job) -> None:
        try:
            while not job.cancel.is_set():
                with job.cond:
                    while (job.index >= len(job.lines) and not job.written
                           and not job.cancel.is_set()):
                        job.cond.wait(0.2)
                    if job.cancel.is_set() or job.index >= len(job.lines):
                        break  # stopped, or the end of the script
                    self._schedule(job)
                    gen, line = job.gen, job.lines[job.index]
                audio = self._wait(job, line.audio, gen)
                if audio is not None:
                    self._play(job, audio, line.text, gen)
                with job.cond:
                    if job.gen == gen:  # finished (or failed): on to the next line
                        job.index += 1
                        job.offset = job.position = 0.0
                        job.word = None
        finally:
            with job.cond:
                job.cancel.set()  # no more scheduling for this job
                for line in job.lines:
                    if line.audio is not None:
                        _discard(line.audio)
            with self._lock:
                if self._job is job:
                    self._job = None
                    if not self.status.startswith("error"):
                        self.status = "idle"

    def _wait(self, job: Job, fut: Future, gen: int) -> tts.Audio | None:
        """The line's audio; None if it failed, or if cancelled or interrupted by a seek."""
        while not job.cancel.is_set() and job.gen == gen:
            try:
                return fut.result(timeout=0.05)
            except TimeoutError:
                continue
            except Exception as e:
                self._fail(job, str(e))
                return None
        return None

    def _play(self, job: Job, audio: tts.Audio, text: str, gen: int) -> None:
        """Play one line until it ends, honouring pause, seeks and cancel."""
        from AppKit import NSSound

        sound = NSSound.alloc().initWithContentsOfFile_byReference_(str(audio.path), False)
        if sound is None:
            self._fail(job, f"can't play {audio.path.name}")
            return
        duration = sound.duration() or 0.001
        words = audio.words or _estimate_words(text, duration)
        starts = [w[0] for w in words]
        played = job.offset * duration
        started = paused = False
        last = time.monotonic()
        try:
            while not job.cancel.is_set() and job.gen == gen:
                now = time.monotonic()
                if job.paused:
                    if started and not paused:
                        sound.pause()
                    paused = True
                    self.status = "paused"
                elif not started:
                    sound.play()
                    if played:
                        sound.setCurrentTime_(played)
                    started, paused = True, False
                    self.status = "speaking"
                elif paused:
                    sound.resume()
                    paused = False
                    self.status = "speaking"
                else:
                    played += now - last
                    # NSSound gives no end-of-sound callback off the main thread: track the
                    # time played, and notice it rewinding to 0 when it ends.
                    current = sound.currentTime()
                    if current > 0:
                        played = current
                    elif played > 0.25:
                        break
                    if played >= duration:
                        break
                    job.position = min(played / duration, 1.0)
                    i = bisect.bisect_right(starts, played) - 1
                    job.word = words[i][1:] if i >= 0 else None
                last = now
                time.sleep(TICK)
        finally:
            sound.stop()

    def _fail(self, job: Job, msg: str) -> None:
        if job.cancel.is_set():
            return
        self.status = f"error: {msg}"
        chime(CHIME_ERROR)


def _estimate_words(text: str, duration: float) -> list[tuple[float, int, int]]:
    """Word timings for voices that don't report them: proportional to the characters."""
    n = len(text) or 1
    return [(duration * m.start() / n, m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def _discard(item: object) -> None:
    """Drop a queued line: cancel its synthesis, or delete its file if it already finished."""
    if not isinstance(item, Future):
        return
    if not item.cancel() and item.done() and not item.exception():
        item.result().path.unlink(missing_ok=True)
    elif not item.done():
        item.add_done_callback(_discard)
