"""Minimal menu bar UI: mode, speed, languages, engine, voice, length and stop."""

from __future__ import annotations

import subprocess
import sys
import time

import rumps
from AppKit import NSApp

from . import config, hotkey, writer
from .config import ENGINES, LANGUAGE_NAMES, LANGUAGES, MINUTES, MODES, SPEEDS, VOICES
from .i18n import t
from .hud import HUD
from .player import CHIME_ERROR, Player, chime
from .server import serve

ICONS = {"idle": "🗣", "preparing": "⏳", "thinking": "💭", "speaking": "🔊"}
BUSY = ("preparing", "thinking", "speaking")
FLASH_SECONDS = 4


class SusurroApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Susurro", title=ICONS["idle"], quit_button=None)
        self.settings = config.load()
        self.player = Player()
        self.hud = HUD(on_click=self.player.stop)
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

    def _set_voice(self, lang: str, voice: str) -> None:
        self.settings.voices[self.settings.engine][lang] = voice
        config.save(self.settings)
        self._build_menu()

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
        flash = self._flash
        if st in ("preparing", "thinking"):
            state = (self._status_label(st), True)
        elif st == "speaking":
            state = (f"🔊  {self._status_label(st)} · {self._tr('click_stop')}", False)
        elif flash and flash[1] > time.monotonic():
            state = (flash[0], False)
        else:
            state = None
        if state == self._hud_state:
            return
        self._hud_state = state
        if state:
            self.hud.show(*state)
        else:
            self.hud.hide()


def run() -> None:
    SusurroApp().run()
