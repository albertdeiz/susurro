"""On-screen windows: the player panel shown while speaking, and a status pill for notices.

Both are floating, non-activating panels at the top center of the screen. They never take
focus (the front app must keep it, so ⌘C reaches it) and show on every Space and over
full-screen apps.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSControlSizeSmall,
    NSEvent,
    NSBox,
    NSFont,
    NSImage,
    NSImageOnly,
    NSImageView,
    NSLineBreakByTruncatingTail,
    NSMakeRect,
    NSPanel,
    NSPopUpButton,
    NSProgressIndicator,
    NSProgressIndicatorStyleSpinning,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSView,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSObject, NSPointInRect

HEIGHT = 38
PAD = 16
SPINNER = 16
GAP = 8
TOP_MARGIN = 14

_on_click: Callable[[], None] | None = None


class _PillView(NSVisualEffectView):
    def acceptsFirstMouse_(self, event) -> bool:
        return True

    def mouseDown_(self, event) -> None:
        if _on_click:
            _on_click()


class HUD:
    """Call only from the main thread."""

    def __init__(self, on_click: Callable[[], None]) -> None:
        global _on_click
        _on_click = on_click

        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 200, HEIGHT), style, NSBackingStoreBuffered, False
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setFloatingPanel_(True)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setReleasedWhenClosed_(False)

        pill = _PillView.alloc().initWithFrame_(NSMakeRect(0, 0, 200, HEIGHT))
        pill.setMaterial_(NSVisualEffectMaterialHUDWindow)
        pill.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        pill.setState_(NSVisualEffectStateActive)
        pill.setWantsLayer_(True)
        pill.layer().setCornerRadius_(HEIGHT / 2)
        pill.layer().setMasksToBounds_(True)

        spinner = NSProgressIndicator.alloc().initWithFrame_(
            NSMakeRect(PAD, (HEIGHT - SPINNER) / 2, SPINNER, SPINNER)
        )
        spinner.setStyle_(NSProgressIndicatorStyleSpinning)
        spinner.setControlSize_(NSControlSizeSmall)
        spinner.setDisplayedWhenStopped_(False)

        label = NSTextField.labelWithString_("")
        label.setFont_(NSFont.systemFontOfSize_weight_(13, 0.3))
        label.setTextColor_(NSColor.labelColor())

        pill.addSubview_(spinner)
        pill.addSubview_(label)
        panel.setContentView_(pill)
        self._panel, self._pill, self._spinner, self._label = panel, pill, spinner, label
        self._visible = False

    def show(self, text: str, spinning: bool) -> None:
        self._label.setStringValue_(text)
        self._label.sizeToFit()
        lw = self._label.frame().size.width
        lh = self._label.frame().size.height
        x = PAD + (SPINNER + GAP if spinning else 0)
        width = x + lw + PAD
        self._label.setFrameOrigin_((x, (HEIGHT - lh) / 2))
        if spinning:
            self._spinner.startAnimation_(None)
        else:
            self._spinner.stopAnimation_(None)

        screen = self._screen()
        vf = screen.visibleFrame()
        frame = NSMakeRect(vf.origin.x + (vf.size.width - width) / 2,
                           vf.origin.y + vf.size.height - HEIGHT - TOP_MARGIN, width, HEIGHT)
        self._panel.setFrame_display_(frame, True)
        self._pill.setFrame_(NSMakeRect(0, 0, width, HEIGHT))
        if not self._visible:
            self._panel.orderFrontRegardless()
            self._visible = True

    def hide(self) -> None:
        if self._visible:
            self._spinner.stopAnimation_(None)
            self._panel.orderOut_(None)
            self._visible = False

    @staticmethod
    def _screen():
        """The screen under the mouse pointer (where the user is working)."""
        mouse = NSEvent.mouseLocation()
        for s in NSScreen.screens():
            if NSPointInRect(mouse, s.frame()):
                return s
        return NSScreen.mainScreen()


# ---------------------------------------------------------------- player panel

PANEL_WIDTH = 620
PANEL_PAD = 12
ROW = 28  # transport row
SETTING_ROW = 46  # one row of setting tiles
TILE_GAP = 8
ROW_GAP = 6
# Icon of each live setting's tile (SF Symbols).
SETTING_ICONS = {"mode": "text.bubble", "speed": "speedometer", "minutes": "clock",
                 "language": "globe", "engine": "cpu", "voice": "person.wave.2"}
COLUMNS = 3


class _Button(NSButton):
    def acceptsFirstMouse_(self, event) -> bool:  # the panel is never key: act on first click
        return True


class _PopUp(NSPopUpButton):
    def acceptsFirstMouse_(self, event) -> bool:
        return True


class _PanelView(NSVisualEffectView):
    def acceptsFirstMouse_(self, event) -> bool:
        return True


class _Target(NSObject):
    """Target/action trampoline to a Python callable."""

    def initWithHandler_(self, handler):
        self = objc.super(_Target, self).init()
        if self is None:
            return None
        self.handler = handler
        return self

    def fire_(self, sender) -> None:
        self.handler(sender)


@dataclass
class PanelActions:
    toggle_pause: Callable[[], None]
    skip: Callable[[int], None]
    stop: Callable[[], None]
    seek: Callable[[float], None]
    # set_setting(name, value); name is a Settings field, or "voice" for the current language.
    set_setting: Callable[[str, object], None]
    toggle_section: Callable[[str], None]  # "settings" | "transcript"


def _symbol(name: str):
    return NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)


class PlayerPanel:
    """Floating player shown while a job runs: transport, live settings. Main thread only.

    Like the HUD pill it is a non-activating panel that never becomes key, so the front
    app keeps focus (and the hotkey's ⌘C keeps reaching it).
    """

    def __init__(self, actions: PanelActions) -> None:
        self.actions = actions
        self._targets: list[_Target] = []  # keep the trampolines alive
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, PANEL_WIDTH, 100), style, NSBackingStoreBuffered, False
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setFloatingPanel_(True)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setReleasedWhenClosed_(False)
        panel.setMovableByWindowBackground_(True)

        view = _PanelView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_WIDTH, 100))
        view.setMaterial_(NSVisualEffectMaterialHUDWindow)
        view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        view.setState_(NSVisualEffectStateActive)
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(14)
        view.layer().setMasksToBounds_(True)
        panel.setContentView_(view)
        self._panel, self._view = panel, view

        # Transport row.
        self._prev = self._button("backward.end.fill", lambda _: actions.skip(-1))
        self._play = self._button("pause.fill", lambda _: actions.toggle_pause())
        self._next = self._button("forward.end.fill", lambda _: actions.skip(1))
        self._stop = self._button("stop.fill", lambda _: actions.stop())
        self._gear = self._button("slider.horizontal.3",
                                  lambda _: actions.toggle_section("settings"))
        self._spinner = NSProgressIndicator.alloc().initWithFrame_(
            NSMakeRect(0, 0, SPINNER, SPINNER))
        self._spinner.setStyle_(NSProgressIndicatorStyleSpinning)
        self._spinner.setControlSize_(NSControlSizeSmall)
        self._spinner.setDisplayedWhenStopped_(False)
        view.addSubview_(self._spinner)
        self._status = NSTextField.labelWithString_("")
        self._status.setFont_(NSFont.systemFontOfSize_weight_(13, 0.3))
        self._status.setTextColor_(NSColor.labelColor())
        self._status.setLineBreakMode_(NSLineBreakByTruncatingTail)
        view.addSubview_(self._status)

        # Live settings: a grid of tiles (icon, caption, value), filled by _fill_settings.
        self._separator = NSBox.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 1))
        self._separator.setBoxType_(2)  # NSBoxSeparator
        view.addSubview_(self._separator)
        self._setting_names = ("mode", "speed", "minutes", "language", "engine", "voice")
        self._tiles: dict[str, NSView] = {}
        self._labels: dict[str, NSTextField] = {}
        self._popups: dict[str, NSPopUpButton] = {}
        for name in self._setting_names:
            tile = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, SETTING_ROW))
            tile.setWantsLayer_(True)
            tile.layer().setCornerRadius_(9)
            tile.layer().setBackgroundColor_(
                NSColor.colorWithWhite_alpha_(1.0, 0.07).CGColor())
            icon = NSImageView.imageViewWithImage_(_symbol(SETTING_ICONS[name]))
            icon.setContentTintColor_(NSColor.secondaryLabelColor())
            icon.setFrame_(NSMakeRect(10, SETTING_ROW - 20, 13, 13))
            label = NSTextField.labelWithString_("")
            label.setFont_(NSFont.systemFontOfSize_weight_(9.5, 0.4))
            label.setTextColor_(NSColor.secondaryLabelColor())
            popup = _PopUp.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 100, 20), False)
            popup.setBordered_(False)
            popup.setFont_(NSFont.systemFontOfSize_weight_(12.5, 0.23))
            popup.setContentTintColor_(NSColor.labelColor())
            target = _Target.alloc().initWithHandler_(
                lambda sender, n=name: self._picked(n, sender))
            self._targets.append(target)
            popup.setTarget_(target)
            popup.setAction_("fire:")
            for sub in (icon, label, popup):
                tile.addSubview_(sub)
            view.addSubview_(tile)
            self._tiles[name], self._labels[name], self._popups[name] = tile, label, popup

        self._visible = False
        self._placed_at = None  # origin we put it at; a different one means the user moved it
        self._show_settings = False
        self._settings_key = None
        self._height = 0

    # ------------------------------------------------------------ building blocks

    def _button(self, symbol: str, handler) -> NSButton:
        b = _Button.alloc().initWithFrame_(NSMakeRect(0, 0, ROW, ROW))
        b.setBordered_(False)
        b.setImage_(_symbol(symbol))
        b.setImagePosition_(NSImageOnly)
        b.setContentTintColor_(NSColor.labelColor())
        target = _Target.alloc().initWithHandler_(handler)
        self._targets.append(target)
        b.setTarget_(target)
        b.setAction_("fire:")
        self._view.addSubview_(b)
        return b

    def _picked(self, name: str, popup) -> None:
        item = popup.selectedItem()
        if item is not None:
            self.actions.set_setting(name, item.representedObject())

    def _fill_settings(self, options: dict[str, tuple[str, dict, object]]) -> None:
        """options[name] = (label, {value: title}, current value)."""
        for name, (label, choices, current) in options.items():
            self._labels[name].setStringValue_(label.upper())
            popup = self._popups[name]
            popup.removeAllItems()
            for value, title in choices.items():
                popup.addItemWithTitle_(title)
                popup.lastItem().setRepresentedObject_(value)
                if value == current:
                    popup.selectItem_(popup.lastItem())

    # ------------------------------------------------------------ layout

    def _layout(self) -> None:
        rows = [ROW]
        if self._show_settings:
            rows.append(1)  # separator
            rows += [SETTING_ROW] * ((len(self._setting_names) + COLUMNS - 1) // COLUMNS)
        height = PANEL_PAD * 2 + sum(rows) + ROW_GAP * (len(rows) - 1)
        self._height = height
        y = height - PANEL_PAD - ROW  # AppKit: y grows upwards

        x = PANEL_PAD
        for b in (self._prev, self._play, self._next, self._stop):
            b.setFrame_(NSMakeRect(x, y, ROW, ROW))
            x += ROW + 2
        x += GAP
        self._spinner.setFrameOrigin_((x, y + (ROW - SPINNER) / 2))
        if not self._spinner.isHidden():
            x += SPINNER + GAP
        right = PANEL_WIDTH - PANEL_PAD - ROW
        self._gear.setFrame_(NSMakeRect(right, y, ROW, ROW))
        lh = self._status.fittingSize().height
        self._status.setFrame_(NSMakeRect(x, y + (ROW - lh) / 2, right - GAP - x, lh))

        self._separator.setHidden_(not self._show_settings)
        for tile in self._tiles.values():
            tile.setHidden_(not self._show_settings)
        if not self._show_settings:
            return
        y -= ROW_GAP + 1
        self._separator.setFrame_(NSMakeRect(PANEL_PAD, y, PANEL_WIDTH - 2 * PANEL_PAD, 1))
        col_w = (PANEL_WIDTH - 2 * PANEL_PAD - (COLUMNS - 1) * TILE_GAP) / COLUMNS
        for i, name in enumerate(self._setting_names):
            row, col = divmod(i, COLUMNS)
            ty = y - ROW_GAP - (row + 1) * SETTING_ROW - row * ROW_GAP
            self._tiles[name].setFrame_(NSMakeRect(PANEL_PAD + col * (col_w + TILE_GAP), ty,
                                                   col_w, SETTING_ROW))
            label = self._labels[name]
            lh = label.fittingSize().height
            label.setFrame_(NSMakeRect(28, SETTING_ROW - 13.5 - lh / 2, col_w - 36, lh))
            # The value spans the tile so a click anywhere below the caption opens it.
            self._popups[name].setFrame_(NSMakeRect(4, 3, col_w - 10, 22))

    def _resize(self) -> None:
        frame = self._panel.frame()
        top = frame.origin.y + frame.size.height  # grow and shrink downwards
        self._panel.setFrame_display_(
            NSMakeRect(frame.origin.x, top - self._height, PANEL_WIDTH, self._height), True)
        self._view.setFrame_(NSMakeRect(0, 0, PANEL_WIDTH, self._height))

    def _place(self) -> None:
        frame = self._panel.frame()
        if self._placed_at is not None and (frame.origin.x, frame.origin.y + frame.size.height) \
                != self._placed_at:
            return  # moved by the user: keep it where they put it
        vf = HUD._screen().visibleFrame()
        x = vf.origin.x + (vf.size.width - PANEL_WIDTH) / 2
        top = vf.origin.y + vf.size.height - TOP_MARGIN
        self._panel.setFrame_display_(NSMakeRect(x, top - self._height, PANEL_WIDTH,
                                                 self._height), True)
        self._view.setFrame_(NSMakeRect(0, 0, PANEL_WIDTH, self._height))
        self._placed_at = (x, top)

    # ------------------------------------------------------------ API

    def show(self, status: str, spinning: bool, paused: bool,
             settings: dict[str, tuple[str, dict, object]], show_settings: bool) -> None:
        """settings: see _fill_settings; rebuilt only when it changes."""
        relayout = False
        if self._status.stringValue() != status:
            self._status.setStringValue_(status)
        if spinning == self._spinner.isHidden():
            self._spinner.setHidden_(not spinning)
            (self._spinner.startAnimation_ if spinning else self._spinner.stopAnimation_)(None)
            relayout = True
        icon = "play.fill" if paused else "pause.fill"
        if getattr(self, "_icon", None) != icon:
            self._icon = icon
            self._play.setImage_(_symbol(icon))
        key = repr(settings)
        if key != self._settings_key:
            self._settings_key = key
            self._fill_settings(settings)
        if show_settings != self._show_settings:
            self._show_settings = show_settings
            relayout = True
        self._gear.setContentTintColor_(NSColor.controlAccentColor() if show_settings
                                        else NSColor.secondaryLabelColor())
        if relayout or not self._height:
            self._layout()
            if self._visible:
                self._resize()
        if not self._visible:
            self._layout()
            self._place()
            self._panel.orderFrontRegardless()
            self._visible = True

    def hide(self) -> None:
        if self._visible:
            self._spinner.stopAnimation_(None)
            self._panel.orderOut_(None)
            self._visible = False
