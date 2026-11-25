"""Minimal menu bar UI: mode, speed, languages, engine, voice, length and stop."""

from __future__ import annotations

import atexit
import subprocess
import sys
import threading
import time

import rumps
from AppKit import NSApp

from . import config, hotkey, writer
from .config import ENGINES, LANGUAGE_NAMES, LANGUAGES, MINUTES, MODES, SPEEDS, VOICES
from .i18n import t
from .hud import HUD, PanelActions, PlayerPanel
from .player import CHIME_ERROR, Player, chime
from .server import serve

ICONS = {"idle": "🗣", "preparing": "⏳", "thinking": "💭", "speaking": "🔊", "paused": "⏸"}
FLASH_SECONDS = 4


class SusurroApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Susurro", title=ICONS["idle"], quit_button=None)
        self.settings = config.load()
        self.player = Player()
        self.hud = HUD(on_click=self.player.stop)
        self.panel = PlayerPanel(PanelActions(
            toggle_pause=self.player.toggle_pause, skip=self.player.skip, stop=self.player.stop,
            seek=self.player.seek, set_setting=self._set_from_panel,
            toggle_section=self._toggle_section))
        self._hud_state: tuple[str, bool] | None = None
        self._flash: tuple[str, float] | None = None  # (text, until) for short notices
        self.status_item: rumps.MenuItem | None = None
        # Ask for Accessibility once (shows the system prompt if not granted yet).
        self.trusted = hotkey.is_trusted(prompt=True)
        print(f"susurro: accessibility trusted={self.trusted} ({sys.executable})",
              file=sys.stderr, flush=True)
        self.hotkey = hotkey.Hotkey(self.settings.hotkey, self.on_selection, self.on_no_selection)
        active = self.hotkey.start()
        print(f"susurro: hotkey {self.settings.hotkey} active={active}", file=sys.stderr, flush=True)
        self._build_menu()
        self.httpd = serve(self.player, lambda: self.settings)
        self._last_status = None
        self._prewarm()
        rumps.Timer(self._tick, 0.15).start()
        rumps.Timer(self._check_trust, 2).start()

    # ------------------------------------------------------------ menus

    def _tr(self, key: str) -> str:
        return t(self.settings.ui_language, key)

    @staticmethod
    def _radio(menu: rumps.MenuItem, options: dict, current, on_pick) -> rumps.MenuItem:
        for value, label in options.items():
            item = rumps.MenuItem(label, callback=lambda _, v=value: on_pick(v))
            item.state = int(value == current)
            menu.add(item)
        return menu

    def _submenu(self, key: str, options: dict, current, on_pick) -> rumps.MenuItem:
        return self._radio(rumps.MenuItem(self._tr(key)), options, current, on_pick)

    def _build_menu(self) -> None:
        s, tr = self.settings, self._tr
        self.status_item = rumps.MenuItem(tr("idle"))
        self._last_status = None

        voice_menu = rumps.MenuItem(tr("voice"))
        for lang in LANGUAGES:
            sub = rumps.MenuItem(LANGUAGE_NAMES[lang])
            options = {v: label for v, (label, _) in VOICES[s.engine][lang].items()}
            self._radio(sub, options, s.voice(lang),
                        lambda v, lang=lang: self._set_voice(lang, v))
            voice_menu.add(sub)

        speech_langs = {"auto": tr("lang.auto"), **LANGUAGE_NAMES}
        backend = tr(f"backend.{writer.backend_name()}")

        shortcut = rumps.MenuItem(f"{tr('shortcut')}: {hotkey.pretty(s.hotkey)}")
        for spec in dict.fromkeys((*hotkey.PRESETS, s.hotkey)):
            item = rumps.MenuItem(hotkey.pretty(spec),
                                  callback=lambda _, v=spec: self._set_hotkey(v))
            item.state = int(spec == s.hotkey)
            shortcut.add(item)
        shortcut.add(None)
        shortcut.add(rumps.MenuItem(tr("shortcut.other"), callback=self.on_custom_hotkey))
        top = [self.status_item, shortcut]
        if not self.trusted:
            top.append(rumps.MenuItem(tr("grant"), callback=self.on_grant))

        if self.menu:
            self.menu.clear()
        self.menu = [
            *top,
            rumps.MenuItem(tr("stop"), callback=self.on_stop, key="."),
            rumps.MenuItem(tr("clipboard"), callback=self.on_clipboard),
            rumps.MenuItem(tr("repeat"), callback=self.on_repeat),
            None,
            self._submenu("mode", {m: tr(f"mode.{m}") for m in MODES}, s.mode,
                          lambda v: self._set(mode=v)),
            self._submenu("speed", {x: f"{x:g}×" for x in SPEEDS}, s.speed,
                          lambda v: self._set(speed=v)),
            self._submenu("speech_language", speech_langs, s.language,
                          lambda v: self._set(language=v)),
            self._submenu("engine", {e: tr(f"engine.{e}") for e in ENGINES}, s.engine,
                          lambda v: self._set(engine=v)),
            voice_menu,
            self._submenu("minutes", {m: f"~{m} min" for m in MINUTES}, s.minutes,
                          lambda v: self._set(minutes=v)),
            None,
            self._submenu("ui_language", LANGUAGE_NAMES, s.ui_language,
                          lambda v: self._set(ui_language=v)),
            rumps.MenuItem(f"{tr('claude')}: {backend}"),
            rumps.MenuItem(tr("quit"), callback=self.on_quit, key="q"),
        ]

    def _set(self, **changes) -> None:
        for k, v in changes.items():
            setattr(self.settings, k, v)
        config.save(self.settings)
        self._build_menu()
        self.player.update(self.settings)  # the running job picks the change up live
        self._prewarm()

    def _prewarm(self) -> None:
        """Start Claude ahead in the language the next request most likely needs."""
        s = self.settings
        lang = s.language if s.language != "auto" else (self.player.language or s.ui_language)
        threading.Thread(target=writer.prewarm, args=(s, lang), daemon=True).start()
        self.player.warm_up(s, lang)

    def _set_voice(self, lang: str, voice: str) -> None:
        self.settings.voices[self.settings.engine][lang] = voice
        config.save(self.settings)
        self._build_menu()
        self.player.update(self.settings)

    def _speech_language(self) -> str:
        """Language whose voice the panel offers: the job's, else the likely next one."""
        s = self.settings
        return self.player.language or (s.language if s.language != "auto" else s.ui_language)

    def _set_from_panel(self, name: str, value) -> None:
        if name == "voice":
            self._set_voice(self._speech_language(), str(value))
            return
        kind = type(getattr(self.settings, name))  # popups hand back NSString/NSNumber
        self._set(**{name: kind(value)})

    def _toggle_section(self, name: str) -> None:
        attr = f"panel_{name}"
        setattr(self.settings, attr, not getattr(self.settings, attr))
        config.save(self.settings)

    def _panel_settings(self) -> dict:
        s, tr = self.settings, self._tr
        lang = self._speech_language()
        return {
            "mode": (tr("short.mode"), {m: tr(f"mode.{m}") for m in MODES}, s.mode),
            "speed": (tr("short.speed"), {x: f"{x:g}×" for x in SPEEDS}, s.speed),
            "minutes": (tr("short.minutes"), {m: f"~{m} min" for m in MINUTES}, s.minutes),
            "language": (tr("short.language"), {"auto": tr("lang.auto"), **LANGUAGE_NAMES},
                         s.language),
            "engine": (tr("short.engine"), {e: tr(f"engine.{e}") for e in ENGINES}, s.engine),
            "voice": (tr("short.voice"),
                      {v: label for v, (label, _) in VOICES[s.engine][lang].items()},
                      s.voice(lang)),
        }

    def _set_hotkey(self, spec: str) -> None:
        self.hotkey.stop()
        self.hotkey = hotkey.Hotkey(spec, self.on_selection, self.on_no_selection)
        active = self.hotkey.start()
        print(f"susurro: hotkey {spec} active={active}", file=sys.stderr, flush=True)
        self._set(hotkey=spec)

    def on_custom_hotkey(self, _=None) -> None:
        tr = self._tr
        message = tr("shortcut.prompt")
        while True:
            NSApp.activateIgnoringOtherApps_(True)  # menu bar apps open dialogs behind others
            reply = rumps.Window(message=message, title=tr("shortcut.title"),
                                 default_text=self.settings.hotkey, cancel=tr("cancel"),
                                 dimensions=(260, 22)).run()
            if not reply.clicked:
                return
            try:
                spec = hotkey.validate(reply.text)
            except ValueError as e:
                message = f"⚠️ {tr('shortcut.invalid')}: {e}\n\n{tr('shortcut.prompt')}"
                continue
            self._set_hotkey(spec)
            return

    # ------------------------------------------------------------ actions

    def on_selection(self, text: str) -> None:
        self.player.speak(text, self.settings)

    def on_no_selection(self) -> None:  # called from the hotkey thread
        self._flash = ("⚠️  " + self._tr("no_selection"), time.monotonic() + FLASH_SECONDS)
        chime(CHIME_ERROR)

    def on_grant(self, _=None) -> None:
        hotkey.is_trusted(prompt=True)
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security"
                        "?Privacy_Accessibility"])

    def _check_trust(self, _) -> None:
        if self.trusted or not hotkey.is_trusted():
            return
        # Permission just granted: re-register the key monitors so they start receiving.
        self.trusted = True
        print("susurro: accessibility granted", file=sys.stderr, flush=True)
        self.hotkey.stop()
        active = self.hotkey.start()
        print(f"susurro: hotkey {self.settings.hotkey} active={active}", file=sys.stderr, flush=True)
        self._build_menu()

    def on_stop(self, _=None) -> None:
        self.player.stop()

    def on_clipboard(self, _=None) -> None:
        text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
        self.player.speak(text, self.settings)

    def on_repeat(self, _=None) -> None:
        if self.player.last_text:
            self.player.speak(self.player.last_text, self.settings)

    def on_quit(self, _=None) -> None:
        self.player.stop()
        self.hotkey.stop()
        self.httpd.shutdown()
        writer.shutdown()
        rumps.quit_application()

    def _tick(self, _) -> None:
        st = self.player.status
        if st != self._last_status:
            self._last_status = st
            self._update_menu_bar(st)
        self._update_hud(st)

    def _status_label(self, st: str) -> str:
        label = self._tr(st if st in ICONS else "idle")
        if st != "idle" and self.player.language:
            label += f"  ({self.player.language.upper()})"
        return label

    def _update_menu_bar(self, st: str) -> None:
        if st.startswith("error"):
            self.title = "⚠️"
            self.status_item.title = st[:90]
            msg = st.removeprefix("error: ")
            self._flash = ("⚠️  " + (msg[:80] + "…" if len(msg) > 80 else msg),
                           time.monotonic() + FLASH_SECONDS)
            return
        self.title = ICONS.get(st, ICONS["idle"])
        self.status_item.title = self._status_label(st)

    def _update_hud(self, st: str) -> None:
        flash = self._flash if self._flash and self._flash[1] > time.monotonic() else None
        progress = self.player.progress()
        if progress is not None:  # a job is running: the player panel shows everything
            self._show_hud(None)
            if flash:
                text = flash[0]
            elif st.startswith("error"):
                text = "⚠️  " + st.removeprefix("error: ")
            else:
                text = self._status_label(st)
            self.panel.show(text, st in ("preparing", "thinking"), progress,
                            self._panel_settings(), self.settings.panel_settings)
            return
        self.panel.hide()
        self._show_hud((flash[0], False) if flash else None)

    def _show_hud(self, state: tuple[str, bool] | None) -> None:
        if state == self._hud_state:
            return
        self._hud_state = state
        if state:
            self.hud.show(*state)
        else:
            self.hud.hide()

def run() -> None:
    atexit.register(writer.shutdown)  # don't leave the warm Claude process behind
    SusurroApp().run()
