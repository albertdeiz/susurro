"""Persistent settings (~/.config/susurro/settings.json) and the voice catalog."""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "susurro" / "settings.json"

LANGUAGES = ("es", "en", "pt")
LANGUAGE_NAMES = {"es": "Español", "en": "English", "pt": "Português"}

MODES = ("read", "narrate", "podcast")
SPEEDS = [0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]
# How much Claude writes in narrate / podcast, shortest first.
DETAILS = ("concise", "summary", "extended", "deep")
ENGINES = ("edge", "say")
DEFAULT_HOTKEY = "ctrl+s"

# VOICES[engine][language] = {voice_id: (label, partner)}
# The partner is the second host's voice in podcast mode: a voice id, or candidates in order.
VOICES: dict[str, dict[str, dict[str, tuple[str, str | tuple[str, ...]]]]] = {
    # Microsoft Edge neural voices: free, no API key, online.
    "edge": {
        "es": {
            "es-VE-PaolaNeural": ("Paola · Venezuela", "es-VE-SebastianNeural"),
            "es-VE-SebastianNeural": ("Sebastián · Venezuela", "es-VE-PaolaNeural"),
            "es-MX-DaliaNeural": ("Dalia · México", "es-MX-JorgeNeural"),
            "es-MX-JorgeNeural": ("Jorge · México", "es-MX-DaliaNeural"),
            "es-ES-ElviraNeural": ("Elvira · España", "es-ES-AlvaroNeural"),
            "es-ES-AlvaroNeural": ("Álvaro · España", "es-ES-ElviraNeural"),
            "es-CL-CatalinaNeural": ("Catalina · Chile", "es-CL-LorenzoNeural"),
            "es-CL-LorenzoNeural": ("Lorenzo · Chile", "es-CL-CatalinaNeural"),
            "es-AR-ElenaNeural": ("Elena · Argentina", "es-AR-TomasNeural"),
            "es-AR-TomasNeural": ("Tomás · Argentina", "es-AR-ElenaNeural"),
            "es-US-PalomaNeural": ("Paloma · neutro", "es-US-AlonsoNeural"),
            "es-US-AlonsoNeural": ("Alonso · neutro", "es-US-PalomaNeural"),
            # Multilingual voices: the most expressive, with a slight accent.
            "en-US-AvaMultilingualNeural": ("Ava · multilingüe", "en-US-AndrewMultilingualNeural"),
            "en-US-AndrewMultilingualNeural": ("Andrew · multilingüe", "en-US-AvaMultilingualNeural"),
        },
        "en": {
            "en-US-AvaMultilingualNeural": ("Ava · US", "en-US-AndrewMultilingualNeural"),
            "en-US-AndrewMultilingualNeural": ("Andrew · US", "en-US-AvaMultilingualNeural"),
            "en-US-EmmaMultilingualNeural": ("Emma · US", "en-US-BrianMultilingualNeural"),
            "en-US-BrianMultilingualNeural": ("Brian · US", "en-US-EmmaMultilingualNeural"),
            "en-US-JennyNeural": ("Jenny · US", "en-US-GuyNeural"),
            "en-US-GuyNeural": ("Guy · US", "en-US-JennyNeural"),
            "en-GB-SoniaNeural": ("Sonia · UK", "en-GB-RyanNeural"),
            "en-GB-RyanNeural": ("Ryan · UK", "en-GB-SoniaNeural"),
        },
        "pt": {
            "pt-BR-FranciscaNeural": ("Francisca · Brasil", "pt-BR-AntonioNeural"),
            "pt-BR-AntonioNeural": ("Antonio · Brasil", "pt-BR-FranciscaNeural"),
            "pt-BR-ThalitaMultilingualNeural": ("Thalita · Brasil", "pt-BR-AntonioNeural"),
            "pt-PT-RaquelNeural": ("Raquel · Portugal", "pt-PT-DuarteNeural"),
            "pt-PT-DuarteNeural": ("Duarte · Portugal", "pt-PT-RaquelNeural"),
        },
    },
    # macOS voices: 100% local. "Enhanced"/"Premium" variants sound much better:
    # System Settings → Accessibility → Spoken Content → System voice → Manage voices.
    # The partner is a list of candidates: the first one installed is used. Eddy, Flo and the
    # rest of the Eloquence voices sound robotic, so they are only the last resort.
    "say": {
        "es": {
            "Mónica": ("Mónica · España", ("Jorge", "Juan", "Paulina", "Eddy (Spanish (Spain))")),
            "Paulina": ("Paulina · México", ("Juan", "Jorge", "Mónica", "Eddy (Spanish (Mexico))")),
            "Flo (Spanish (Mexico))": ("Flo · México", ("Juan", "Jorge", "Paulina")),
            "Eddy (Spanish (Spain))": ("Eddy · España", ("Mónica",)),
            "Eddy (Spanish (Mexico))": ("Eddy · México", ("Paulina",)),
        },
        "en": {
            "Samantha": ("Samantha · US", ("Evan", "Nathan", "Tom", "Daniel")),
            "Daniel": ("Daniel · UK", ("Kate", "Serena", "Samantha")),
            "Flo (English (US))": ("Flo · US", ("Evan", "Nathan", "Tom", "Daniel")),
            "Eddy (English (US))": ("Eddy · US", ("Samantha",)),
        },
        "pt": {
            "Luciana": ("Luciana · Brasil", ("Felipe", "Joana", "Eddy (Portuguese (Brazil))")),
            "Joana": ("Joana · Portugal", ("Felipe", "Luciana")),
            "Eddy (Portuguese (Brazil))": ("Eddy · Brasil", ("Luciana",)),
        },
    },
}

DEFAULT_VOICES = {
    "edge": {"es": "es-VE-PaolaNeural", "en": "en-US-AvaMultilingualNeural",
             "pt": "pt-BR-FranciscaNeural"},
    "say": {"es": "Mónica", "en": "Samantha", "pt": "Luciana"},
}


def system_language() -> str:
    """macOS UI language if supported, else English."""
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                             capture_output=True, text=True, timeout=2).stdout
        for line in out.splitlines():
            code = line.strip(' ",()')[:2]
            if code in LANGUAGES:
                return code
            if code:
                break
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "en"


def _default_voices() -> dict[str, dict[str, str]]:
    return {e: dict(v) for e, v in DEFAULT_VOICES.items()}


@dataclass
class Settings:
    mode: str = "read"
    speed: float = 1.0
    engine: str = "edge"
    # Speech language: "auto" (detected from the text) or one of LANGUAGES.
    language: str = "auto"
    # Menu bar UI language.
    ui_language: str = field(default_factory=system_language)
    # Chosen voice per engine and language.
    voices: dict[str, dict[str, str]] = field(default_factory=_default_voices)
    # Second host's voice per engine and language; missing means the catalog's pairing.
    partners: dict[str, dict[str, str]] = field(default_factory=dict)
    # Claude model used for narrate / podcast.
    model: str = "claude-opus-5-5"
    # How much Claude writes in narrate / podcast: one of DETAILS.
    detail: str = "summary"
    # System-wide shortcut that speaks the current selection (changeable from the menu).
    hotkey: str = DEFAULT_HOTKEY
    # Player panel sections shown while speaking.
    panel_settings: bool = False
    panel_transcript: bool = True

    def voice(self, lang: str) -> str:
        return self.voices.get(self.engine, {}).get(lang) or DEFAULT_VOICES[self.engine][lang]

    def partner_voice(self, lang: str) -> str:
        return self.partners.get(self.engine, {}).get(lang) or self.auto_partner(lang)

    def auto_partner(self, lang: str) -> str:
        """The catalog's pairing for the main voice (the first installed candidate)."""
        v = self.voice(lang)
        partner = VOICES[self.engine][lang].get(v, ("", v))[1]
        if isinstance(partner, str):
            return partner
        from . import tts

        return next((c for c in partner if tts.installed(c)), partner[-1])

    def normalize(self) -> "Settings":
        if self.mode not in MODES:
            self.mode = {"leer": "read", "narrar": "narrate"}.get(self.mode, "read")
        if self.detail not in DETAILS:
            self.detail = "summary"
        if self.engine not in ENGINES:
            self.engine = "edge"
        if self.language not in ("auto", *LANGUAGES):
            self.language = "auto"
        if self.ui_language not in LANGUAGES:
            self.ui_language = "en"
        merged = _default_voices()
        for eng, per_lang in (self.voices or {}).items():
            for lang, v in (per_lang or {}).items():
                if v in VOICES.get(eng, {}).get(lang, {}):
                    merged[eng][lang] = v
        self.voices = merged
        self.partners = {
            eng: {lang: v for lang, v in (per_lang or {}).items()
                  if v in VOICES.get(eng, {}).get(lang, {})}
            for eng, per_lang in (self.partners or {}).items() if eng in VOICES}
        from .hotkey import validate  # local import: hotkey pulls in Quartz
        try:
            self.hotkey = validate(self.hotkey)
        except ValueError:
            self.hotkey = DEFAULT_HOTKEY
        return self


_lock = threading.Lock()


def load() -> Settings:
    try:
        data = json.loads(CONFIG_PATH.read_text())
        known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
        s = Settings(**known)
        # v0.2 stored a target length in minutes.
        if "detail" not in data and isinstance(data.get("minutes"), int):
            m = data["minutes"]
            s.detail = ("concise" if m <= 1 else "summary" if m <= 3
                        else "extended" if m <= 5 else "deep")
        # v0.1 stored a single "voice", always from the Spanish catalog.
        legacy = data.get("voice")
        if legacy and "voices" not in data and legacy in VOICES.get(s.engine, {}).get("es", {}):
            s.voices[s.engine]["es"] = legacy
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        s = Settings()
    return s.normalize()


def save(s: Settings) -> None:
    with _lock:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(s), indent=2, ensure_ascii=False))
