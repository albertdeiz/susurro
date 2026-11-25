"""Menu bar UI strings (es / en / pt) and a lightweight text language detector."""

from __future__ import annotations

import re

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "paused": "Paused", "short.mode": "Mode", "short.speed": "Speed",
        "short.minutes": "Length", "short.language": "Language", "short.engine": "Engine",
        "short.voice": "Voice",
        "preparing": "Preparing audio…",
        "shortcut": "Shortcut", "grant": "⚠️ Allow Accessibility for the shortcut…",
        "no_selection": "No text selected",
        "shortcut.other": "Other…", "shortcut.title": "Shortcut",
        "shortcut.prompt": "Type a key combination, for example ctrl+s or ctrl+option+space.\nModifiers: ctrl, option, cmd, shift.",
        "shortcut.invalid": "Invalid shortcut", "cancel": "Cancel",
        "idle": "Ready", "thinking": "Claude is writing…", "speaking": "Speaking…",
        "stop": "Stop", "clipboard": "Speak clipboard", "repeat": "Repeat last",
        "mode": "Mode", "speed": "Speed", "engine": "Voice engine", "voice": "Voice",
        "speech_language": "Speech language", "ui_language": "Interface language",
        "minutes": "Length (Claude)", "quit": "Quit",
        "mode.read": "Read as is", "mode.narrate": "Narrated (Claude)",
        "mode.podcast": "Two-host podcast (Claude)",
        "engine.edge": "Edge neural (free, online)", "engine.say": "macOS local (offline)",
        "lang.auto": "Auto (text language)",
        "claude": "Claude", "backend.api": "Anthropic API",
        "backend.cli": "Claude Code (claude -p)", "backend.none": "not available",
    },
    "es": {
        "paused": "En pausa", "short.mode": "Modo", "short.speed": "Velocidad",
        "short.minutes": "Duración", "short.language": "Idioma", "short.engine": "Motor",
        "short.voice": "Voz",
        "preparing": "Preparando audio…",
        "shortcut": "Atajo", "grant": "⚠️ Permitir Accesibilidad para el atajo…",
        "no_selection": "No hay texto seleccionado",
        "shortcut.other": "Otro…", "shortcut.title": "Atajo",
        "shortcut.prompt": "Escribe una combinación, por ejemplo ctrl+s o ctrl+option+space.\nModificadores: ctrl, option, cmd, shift.",
        "shortcut.invalid": "Atajo no válido", "cancel": "Cancelar",
        "idle": "Listo", "thinking": "Claude está escribiendo…", "speaking": "Hablando…",
        "stop": "Detener", "clipboard": "Escuchar portapapeles", "repeat": "Repetir último",
        "mode": "Modo", "speed": "Velocidad", "engine": "Motor de voz", "voice": "Voz",
        "speech_language": "Idioma del habla", "ui_language": "Idioma de la interfaz",
        "minutes": "Duración (Claude)", "quit": "Salir",
        "mode.read": "Leer tal cual", "mode.narrate": "Narrado (Claude)",
        "mode.podcast": "Podcast 2 voces (Claude)",
        "engine.edge": "Edge neural (gratis, online)", "engine.say": "macOS local (offline)",
        "lang.auto": "Auto (idioma del texto)",
        "claude": "Claude", "backend.api": "API de Anthropic",
        "backend.cli": "Claude Code (claude -p)", "backend.none": "no disponible",
    },
    "pt": {
        "paused": "Pausado", "short.mode": "Modo", "short.speed": "Velocidade",
        "short.minutes": "Duração", "short.language": "Idioma", "short.engine": "Motor",
        "short.voice": "Voz",
        "preparing": "Preparando áudio…",
        "shortcut": "Atalho", "grant": "⚠️ Permitir Acessibilidade para o atalho…",
        "no_selection": "Nenhum texto selecionado",
        "shortcut.other": "Outro…", "shortcut.title": "Atalho",
        "shortcut.prompt": "Digite uma combinação, por exemplo ctrl+s ou ctrl+option+space.\nModificadores: ctrl, option, cmd, shift.",
        "shortcut.invalid": "Atalho inválido", "cancel": "Cancelar",
        "idle": "Pronto", "thinking": "Claude está escrevendo…", "speaking": "Falando…",
        "stop": "Parar", "clipboard": "Ouvir área de transferência", "repeat": "Repetir último",
        "mode": "Modo", "speed": "Velocidade", "engine": "Motor de voz", "voice": "Voz",
        "speech_language": "Idioma da fala", "ui_language": "Idioma da interface",
        "minutes": "Duração (Claude)", "quit": "Sair",
        "mode.read": "Ler como está", "mode.narrate": "Narrado (Claude)",
        "mode.podcast": "Podcast com 2 vozes (Claude)",
        "engine.edge": "Edge neural (grátis, online)", "engine.say": "macOS local (offline)",
        "lang.auto": "Auto (idioma do texto)",
        "claude": "Claude", "backend.api": "API da Anthropic",
        "backend.cli": "Claude Code (claude -p)", "backend.none": "indisponível",
    },
}


def t(lang: str, key: str) -> str:
    return STRINGS.get(lang, STRINGS["en"]).get(key) or STRINGS["en"][key]


# ---------------------------------------------------------------- language detection

# Frequent words that are (mostly) exclusive to each language.
_MARKERS = {
    "es": set("el los las del y una por para con pero muy también está están es son "
              "qué cuando como más este esta eso hay porque sin sobre entre puede "
              "pueden ser tiene hace ya nos les lo su sus al".split()),
    "pt": set("o os as do da dos das um uma não é são com para pelo pela mas muito "
              "também está estão você isso essa esse ele ela eles há ao à em no na "
              "nos nas seu sua mais quando como pode ser tem faz já".split()),
    "en": set("the and of to is are was were in that it for you with on this be "
              "have has not but they from at by what which will would can there "
              "their an or i we my your our us them he she his her its been do does "
              "did if so just about all get got please let know think should could "
              "how why who when where good morning thanks thank hello hi yes ok okay "
              "into out up then than these those some any more most also very only "
              "new back here now one".split()),
}
_WORD = re.compile(r"[a-záéíóúâêôãõçñü]+")


def detect(text: str, fallback: str = "es") -> str:
    """Guess es/en/pt from common words; spelling hints only split Spanish from Portuguese.

    The hints (ll, nh, lh…) also occur in English words (hello, will, enhance), so they
    must never count against English.
    """
    sample = text[:4000].lower()
    words = _WORD.findall(sample)
    score = {lang: sum(w in m for w in words) for lang, m in _MARKERS.items()}
    es_hint = len(re.findall(r"ción|ciones|ñ|¿|¡|\by\b|ll", sample))
    pt_hint = len(re.findall(r"ção|ções|ão\b|õe|[ãõ]|\bnão\b|lh|nh", sample))
    if score["en"] > max(score["es"], score["pt"]):
        return "en"
    es, pt = score["es"] + 3 * es_hint, score["pt"] + 3 * pt_hint
    if score["es"] or score["pt"]:
        return "es" if es >= pt else "pt"
    if score["en"]:
        return "en"
    # No marker words at all (very short text): trust only unambiguous characters.
    if re.search(r"[ñ¿¡]", sample):
        return "es"
    if re.search(r"[ãõç]", sample):
        return "pt"
    return fallback
