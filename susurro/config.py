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
MINUTES = [1, 3, 5, 10]
ENGINES = ("edge", "say")
DEFAULT_HOTKEY = "ctrl+s"

# VOICES[engine][language] = {voice_id: (label, partner_voice_id)}
# The partner is the second host's voice in podcast mode.
VOICES: dict[str, dict[str, dict[str, tuple[str, str]]]] = {
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
    "say": {
        "es": {
            "Mónica": ("Mónica · España", "Eddy (Spanish (Spain))"),
            "Paulina": ("Paulina · México", "Eddy (Spanish (Mexico))"),
            "Flo (Spanish (Mexico))": ("Flo · México", "Eddy (Spanish (Mexico))"),
            "Eddy (Spanish (Spain))": ("Eddy · España", "Mónica"),
            "Eddy (Spanish (Mexico))": ("Eddy · México", "Flo (Spanish (Mexico))"),
        },
        "en": {
            "Samantha": ("Samantha · US", "Eddy (English (US))"),
            "Daniel": ("Daniel · UK", "Flo (English (UK))"),
            "Flo (English (US))": ("Flo · US", "Eddy (English (US))"),
            "Eddy (English (US))": ("Eddy · US", "Samantha"),
        },
        "pt": {
            "Luciana": ("Luciana · Brasil", "Eddy (Portuguese (Brazil))"),
            "Joana": ("Joana · Portugal", "Eddy (Portuguese (Brazil))"),
            "Eddy (Portuguese (Brazil))": ("Eddy · Brasil", "Luciana"),
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
    # Claude model used for narrate / podcast.
    model: str = "claude-opus-5-5"
    # Target length of narrate / podcast, in minutes.
    minutes: int = 3
    # System-wide shortcut that speaks the current selection (changeable from the menu).
    hotkey: str = DEFAULT_HOTKEY
    # Player panel sections shown while speaking.
    panel_settings: bool = False

    def voice(self, lang: str) -> str:
        return self.voices.get(self.engine, {}).get(lang) or DEFAULT_VOICES[self.engine][lang]

    def partner_voice(self, lang: str) -> str:
        v = self.voice(lang)
        return VOICES[self.engine][lang].get(v, ("", v))[1]

    def normalize(self) -> "Settings":
        if self.mode not in MODES:
            self.mode = {"leer": "read", "narrar": "narrate"}.get(self.mode, "read")
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
