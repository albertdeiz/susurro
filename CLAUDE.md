# Susurro

macOS menu bar app: select text in any app, press a global shortcut (⌃S by default), and hear
it read aloud, narrated by Claude, or as a two-host podcast. Python 3.12, managed with `uv`.
See README.md for the user-facing description.

## Language

- Everything in the repo is in **English**: code, comments, README, commit messages.
- UI strings live in `susurro/i18n.py` in three languages (en, es, pt). When you add a UI
  string, add it to all three.

## Commands

```sh
uv sync                          # install deps into .venv
uv run susurro app               # run the menu bar app in the foreground
uv run susurro install           # LaunchAgent (start at login) + right-click Services entry
uv run susurro uninstall
uv run susurro speak "text"      # or: pbpaste | uv run susurro speak [--mode podcast --lang pt]
uv run susurro stop | status
```

- **Reload after code changes.** The installed app runs from `.venv` through the LaunchAgent
  `com.susurro.app` and does not pick up edits on its own:
  `launchctl kickstart -k gui/$(id -u)/com.susurro.app`
- **Log:** `~/Library/Logs/susurro.log` (stdout and stderr of the agent). Diagnostics are printed
  as `susurro: ...` lines on stderr.
- **Settings:** `~/.config/susurro/settings.json`. Saved settings override code defaults, so
  changing a default in `config.py` does not affect an existing install until that key is
  changed or removed.
- **No test suite yet.** Verify by importing the modules (`.venv/bin/python -c "import
  susurro.app"`), calling the pure functions (`i18n.detect`, `hotkey.validate`), and reading the
  log after a real run.

## Architecture

```
hotkey.py   event tap catches the shortcut → sends ⌘C → reads and restores the clipboard
app.py      rumps menu bar app: menus, settings, wires hotkey → player, drives the HUD
hud.py      floating non-activating NSPanel (spinner / "speaking · click to stop" / errors)
player.py   job per request: producer thread (writer → tts) and consumer thread (afplay)
writer.py   Claude script generation: Anthropic SDK if ANTHROPIC_API_KEY/AUTH_TOKEN, else `claude -p`
tts.py      edge-tts (online neural voices) or macOS voices via NSSpeechSynthesizer (offline)
server.py   HTTP on 127.0.0.1:47823, used by the CLI and the Services entry
config.py   Settings dataclass, voice catalog, load/save/normalize
i18n.py     UI strings + es/en/pt detection of the selected text
cli.py      argparse entry point, LaunchAgent and Services installers
```

Latency design (measured; keep it):
- `player._POOL` is one shared thread pool. Each line's synthesis is submitted to it and the
  *future* goes into the audio queue, so up to `PREFETCH` lines are synthesized ahead and
  played in order. The pool is shared so worker threads, and the local voices loaded in them,
  stay warm between requests.
- Local voices run in-process with one `NSSpeechSynthesizer` per worker thread (~0.05 s a
  line, versus ~0.7 s spawning `say`). The voice only loads on its first synthesis, so
  `Player.warm_up` synthesizes a word in every worker at start-up.
- `writer._Warm` keeps one `claude -p --input-format stream-json` process started ahead with
  the current system prompt (~0.8 s saved). Plain stdin doesn't work for this: `claude -p`
  stops waiting for stdin after ~3 s.
- The first utterance from Claude is cut at its first sentence end, not the end of its line.
- Edge latency is bimodal (~1 s or 5-15 s) and degrades for a long while after bursts of
  requests. Hedged (duplicate) requests were tried and didn't help: when it's slow, both are.
- Benchmark without sound by replacing `afplay` with a stand-in that lasts as long as the
  audio. `afplay` started from a sandboxed shell can hang because it has no audio device.

Player status is a plain string read by a 0.15 s `rumps.Timer` in `app.py`: `idle`, `preparing`,
`thinking`, `speaking` or `error: …`. The hotkey callbacks run on a background thread, so they
only set state; the timer is what touches AppKit.

## Hard-won details (do not regress)

- **Grabbing the selection** goes through a synthetic ⌘C, because macOS Services only see the
  selection in apps that support them (not Warp, VS Code, Slack…). Two fixes in
  `hotkey.py` matter:
  - Wait until the shortcut and all modifiers are **physically released** before posting ⌘C.
    Otherwise apps that read the hardware state see ⌃⌘C.
  - Set the event's character with `CGEventKeyboardSetUnicodeString(ev, 1, "c")`. A background
    process has no keyboard layout, so the event carries no character, and Warp matches
    shortcuts by character, not key code.
- **The event tap swallows the shortcut**, so the front app never receives it. Never allow a
  shortcut without ⌃/⌥/⌘, or ⌘C itself (`hotkey.validate` enforces both).
- **Accessibility permission** belongs to the real interpreter binary (`python3.12`), not to
  `.venv/bin/python`. If the tap fails to start, the log says `trusted=False`.
- **Language detection** (`i18n.detect`) first decides English vs Spanish/Portuguese from
  common words. Only then do spelling hints (ñ, ção, ll, nh…) split Spanish from Portuguese.
  Those hints also appear in English words (hello, will, enhance), so they must never count
  against English.
- **The HUD** is a non-activating panel and must stay one: if it takes focus, ⌘C goes to
  Susurro instead of the user's app. Dialogs (e.g. the custom shortcut prompt) call
  `NSApp.activateIgnoringOtherApps_(True)` first, because a menu bar app's windows open behind
  the others otherwise.
- **Testing the hotkey from a script:** post ⌃Tab/⌃S with `CGEventPost` and read the log. Make
  sure the intended app is really frontmost: `osascript ... activate` often fails silently
  while the user is working in another app.

## Git

- Conventional Commits, one commit per scope (`feat(hotkey): …`, `fix(i18n): …`, `docs: …`,
  `build: …`). Scopes match module names.
- No `Co-Authored-By` or other AI attribution in commits or PRs.
- Keep history clean: fold follow-up changes into the commit they belong to with
  `git commit --fixup=<sha>` (or `--fixup=amend:<sha>` when the message must change), then
  `GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash --root`. Rewriting pushed history needs
  `git push --force-with-lease`. Don't push without the owner's say-so.
- `.claude/` is git-ignored (local Claude Code settings).
