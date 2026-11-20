"""On-screen status pill: a floating, non-activating panel at the top center of the screen.

It never takes focus (the front app must keep it, so ⌘C reaches it), shows on every
Space and over full-screen apps, and stops playback when clicked.
"""

from __future__ import annotations

from collections.abc import Callable

from AppKit import (
    NSBackingStoreBuffered,
    NSColor,
    NSControlSizeSmall,
    NSEvent,
    NSFont,
    NSMakeRect,
    NSPanel,
    NSProgressIndicator,
    NSProgressIndicatorStyleSpinning,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSPointInRect

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
