"""System-wide hotkey that grabs the current selection from any app.

macOS Services hotkeys only fire in apps that hand selected text to Services (many
terminals and Electron apps don't). Instead, the menu bar app installs a keyboard event
tap: when the hotkey is pressed, the tap swallows it (so the front app never sees it, e.g.
⌃S won't also reach the terminal) and grabs the selection, trying in order:

1. Accessibility: the focused element's selected text. No clipboard involved.
2. The app's own Copy menu item, pressed through Accessibility: the same action as a real
   ⌘C, for apps that ignore a synthetic keystroke.
3. A synthetic ⌘C, for apps with no usable menu (Warp…).

2 and 3 read the clipboard and put its previous content back. All of it needs the
Accessibility permission (System Settings → Privacy & Security → Accessibility).
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable

from AppKit import NSPasteboard, NSPasteboardTypeString
from ApplicationServices import (
    AXIsProcessTrustedWithOptions,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
    AXUIElementSetMessagingTimeout,
    kAXChildrenAttribute,
    kAXEnabledAttribute,
    kAXErrorSuccess,
    kAXFocusedUIElementAttribute,
    kAXMenuBarAttribute,
    kAXMenuItemCmdCharAttribute,
    kAXMenuItemCmdModifiersAttribute,
    kAXPressAction,
    kAXSelectedTextAttribute,
    kAXTrustedCheckOptionPrompt,
)
from Quartz import (
    CFMachPortCreateRunLoopSource,
    CFMachPortInvalidate,
    CFRunLoopAddSource,
    CFRunLoopGetMain,
    CFRunLoopRemoveSource,
    CGEventCreateKeyboardEvent,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventKeyboardSetUnicodeString,
    CGEventMaskBit,
    CGEventPost,
    CGEventSourceFlagsState,
    CGEventSourceKeyState,
    CGEventSetFlags,
    CGEventTapCreate,
    CGEventTapEnable,
    kCFRunLoopCommonModes,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskShift,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventSourceStateHIDSystemState,
    kCGEventTapDisabledByTimeout,
    kCGEventTapDisabledByUserInput,
    kCGEventTapOptionDefault,
    kCGHeadInsertEventTap,
    kCGHIDEventTap,
    kCGKeyboardEventAutorepeat,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
)

# Virtual key codes (physical positions, US layout names).
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "o": 31, "u": 32,
    "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    "tab": 48, "space": 49, "return": 36, "escape": 53,
}
MODIFIERS = {
    "ctrl": kCGEventFlagMaskControl, "control": kCGEventFlagMaskControl,
    "alt": kCGEventFlagMaskAlternate, "option": kCGEventFlagMaskAlternate,
    "cmd": kCGEventFlagMaskCommand, "command": kCGEventFlagMaskCommand,
    "shift": kCGEventFlagMaskShift,
}
_MOD_MASK = (kCGEventFlagMaskControl | kCGEventFlagMaskAlternate
             | kCGEventFlagMaskCommand | kCGEventFlagMaskShift)
_KEY_C = 8
_SYMBOLS = {"ctrl": "⌃", "control": "⌃", "alt": "⌥", "option": "⌥",
            "cmd": "⌘", "command": "⌘", "shift": "⇧"}
_KEY_NAMES = {"tab": "Tab", "space": "Space", "return": "Return", "escape": "Esc"}


# Offered in the menu; any other combination can be typed in.
PRESETS = ("ctrl+s", "ctrl+option+s", "ctrl+tab", "ctrl+option+space")


def validate(spec: str) -> str:
    """Normalize a shortcut like 'Ctrl + S' to 'ctrl+s'. ValueError if unusable."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty shortcut")
    *mods, key = parts
    unknown = [m for m in mods if m not in MODIFIERS] + ([key] if key not in KEYCODES else [])
    if unknown:
        raise ValueError(f"unknown key: {', '.join(unknown)}")
    flags = {MODIFIERS[m] for m in mods}
    # Shift alone would swallow capital letters; ⌘C is what the hotkey itself sends.
    if not flags - {kCGEventFlagMaskShift}:
        raise ValueError("needs ctrl, option or cmd")
    if flags == {kCGEventFlagMaskCommand} and key == "c":
        raise ValueError("cmd+c is used to copy the selection")
    canonical = {"control": "ctrl", "alt": "option", "command": "cmd"}
    return "+".join([*dict.fromkeys(canonical.get(m, m) for m in mods), key])


def parse(spec: str) -> tuple[int, int]:
    """'ctrl+s' → (modifier flags, keycode)."""
    *mods, key = [p.strip().lower() for p in spec.split("+")]
    flags = 0
    for m in mods:
        flags |= MODIFIERS[m]
    return flags, KEYCODES[key]


def pretty(spec: str) -> str:
    *mods, key = [p.strip().lower() for p in spec.split("+")]
    return "".join(_SYMBOLS[m] for m in mods) + _KEY_NAMES.get(key, key.upper())


def is_trusted(prompt: bool = False) -> bool:
    return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))


def _send_copy() -> None:
    for down in (True, False):
        ev = CGEventCreateKeyboardEvent(None, _KEY_C, down)
        CGEventSetFlags(ev, kCGEventFlagMaskCommand)  # only ⌘, even if ⌃ is still held
        # A background process has no keyboard layout, so the event carries no characters.
        # Most apps match ⌘C by key code, but Warp matches by character and ignores it.
        CGEventKeyboardSetUnicodeString(ev, 1, "c")
        CGEventPost(kCGHIDEventTap, ev)


def _front_app():
    from AppKit import NSWorkspace
    return NSWorkspace.sharedWorkspace().frontmostApplication()


def _app_name(app) -> str:
    return str(app.bundleIdentifier()) if app else "?"


def _log(msg: str) -> None:
    print(f"susurro: {msg}", file=sys.stderr, flush=True)


def wait_for_release(keycode: int, timeout: float = 2.0) -> bool:
    """Wait until the hotkey and every modifier are physically released.

    Some apps (Warp, browsers…) read the real keyboard state, so a synthetic ⌘C sent while
    ⌃ is still held arrives as ⌃⌘C and gets ignored.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        held_mods = CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState) & _MOD_MASK
        if not held_mods and not CGEventSourceKeyState(kCGEventSourceStateHIDSystemState, keycode):
            return True
        time.sleep(0.005)
    return False


# ---------------------------------------------------------------- Accessibility

_AX_TIMEOUT = 0.3  # seconds; an app that doesn't answer must not stall the hotkey
_electron_enabled: set[int] = set()


def _ax(element, attribute: str):
    err, value = AXUIElementCopyAttributeValue(element, attribute, None)
    return value if err == kAXErrorSuccess else None


def _ax_app(pid: int):
    app = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(app, _AX_TIMEOUT)
    # Electron (Slack, VS Code…) only builds its accessibility tree when asked to. The tree
    # appears asynchronously, so the first request in a fresh app may still miss it.
    if pid not in _electron_enabled:
        AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)
        _electron_enabled.add(pid)
    return app


def _ax_selected_text(app) -> str:
    focused = _ax(app, kAXFocusedUIElementAttribute)
    text = _ax(focused, kAXSelectedTextAttribute) if focused is not None else None
    return str(text) if text else ""


def _ax_copy_item(app):
    """The app's 'Copy' menu item: the one bound to plain ⌘C, whatever the UI language."""
    bar = _ax(app, kAXMenuBarAttribute)
    for top in (_ax(bar, kAXChildrenAttribute) or []) if bar is not None else []:
        for menu in _ax(top, kAXChildrenAttribute) or []:
            for item in _ax(menu, kAXChildrenAttribute) or []:
                if (str(_ax(item, kAXMenuItemCmdCharAttribute) or "").lower() == "c"
                        and _ax(item, kAXMenuItemCmdModifiersAttribute) == 0):  # 0 = ⌘ only
                    return item
    return None


# ---------------------------------------------------------------- grabbing


def _wait_change(pb, before: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while pb.changeCount() == before and time.monotonic() < deadline:
        time.sleep(0.005)
    return pb.changeCount() != before


def copy_selection(timeout: float = 1.0) -> str:
    """Return the front app's selection ("" if none), leaving the clipboard as it was."""
    front = _front_app()
    name = _app_name(front)
    app = _ax_app(front.processIdentifier()) if front else None
    if app is not None:
        text = _ax_selected_text(app)
        if text.strip():
            _log(f"read {len(text)} chars from {name} (accessibility)")
            return text

    pb = NSPasteboard.generalPasteboard()
    before = pb.changeCount()
    saved = pb.stringForType_(NSPasteboardTypeString)
    how = ""
    item = _ax_copy_item(app) if app is not None else None
    if item is not None and _ax(item, kAXEnabledAttribute) is not False:
        AXUIElementPerformAction(item, kAXPressAction)
        if _wait_change(pb, before, timeout / 2):
            how = "Copy menu"
    if not how:
        _send_copy()
        if _wait_change(pb, before, timeout):
            how = "⌘C"
    if not how:
        _log(f"nothing copied from {name} (no accessible selection, Copy menu "
             f"{'found' if item is not None else 'not found'}, ⌘C ignored)")
        return ""  # nothing selected, or the app ignores every way of copying
    text = pb.stringForType_(NSPasteboardTypeString) or ""
    if saved is not None:
        pb.clearContents()
        pb.setString_forType_(saved, NSPasteboardTypeString)
    _log(f"copied {len(text)} chars from {name} ({how})")
    return str(text)


class Hotkey:
    def __init__(self, spec: str, on_text: Callable[[str], None],
                 on_empty: Callable[[], None]) -> None:
        self.flags, self.keycode = parse(spec)
        self.on_text, self.on_empty = on_text, on_empty
        self._tap = None
        self._source = None
        self._swallow_up = False
        self._busy = threading.Lock()

    @property
    def active(self) -> bool:
        return self._tap is not None

    def start(self) -> bool:
        """Install the event tap on the main run loop. False if not permitted (yet)."""
        if self._tap is not None:
            return True
        mask = CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp)
        tap = CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
                               kCGEventTapOptionDefault, mask, self._callback, None)
        if tap is None:
            return False
        self._tap = tap
        self._source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), self._source, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)
        return True

    def stop(self) -> None:
        if self._tap is None:
            return
        CGEventTapEnable(self._tap, False)
        CFRunLoopRemoveSource(CFRunLoopGetMain(), self._source, kCFRunLoopCommonModes)
        CFMachPortInvalidate(self._tap)
        self._tap = self._source = None

    def _callback(self, proxy, type_, event, refcon):
        # macOS disables slow or interrupted taps; turn it straight back on.
        if type_ in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
            if self._tap is not None:
                CGEventTapEnable(self._tap, True)
            return event
        if CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode) != self.keycode:
            return event
        if type_ == kCGEventKeyUp:
            if self._swallow_up:
                self._swallow_up = False
                return None
            return event
        if (CGEventGetFlags(event) & _MOD_MASK) != self.flags:
            return event
        self._swallow_up = True
        if not CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat) \
                and not self._busy.locked():
            threading.Thread(target=self._grab, daemon=True).start()
        return None  # swallow: the front app never sees the hotkey

    def _grab(self) -> None:
        try:
            with self._busy:
                if not wait_for_release(self.keycode):
                    _log("keys still held after 2s; copying anyway")
                text = copy_selection()
        except Exception as e:  # keep the tap alive whatever happens
            print(f"susurro: copy failed: {e}", file=sys.stderr, flush=True)
            text = ""
        if text.strip():
            self.on_text(text)
        else:
            self.on_empty()
