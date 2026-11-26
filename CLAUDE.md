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
- **Running a branch or worktree for real.** Started from a terminal, the app is not trusted
  for Accessibility (the terminal is the responsible process), so the hotkey can't work. Stop
  the installed agent and run the checkout as its own launchd job:
  ```sh
  launchctl bootout gui/$(id -u)/com.susurro.app
  launchctl submit -l com.susurro.dev -o ~/Library/Logs/susurro-dev.log \
    -e ~/Library/Logs/susurro-dev.log -- /bin/sh -c "cd $PWD && exec .venv/bin/python -m susurro app"
  # reload: launchctl kickstart -k gui/$(id -u)/com.susurro.dev
  # back:   launchctl remove com.susurro.dev
  #         launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.susurro.app.plist
  ```
- **Looking at the panels without screen recording:** build a `PlayerPanel`, call `show` with a
  hand-made `player.Progress`, and render the content view to a PNG with
  `bitmapImageRepForCachingDisplayInRect_` + `cacheDisplayInRect_toBitmapImageRep_` (`screencapture`
  isn't allowed from the shell).

## Architecture

```
hotkey.py   event tap catches the shortcut → AX selected text, else Copy menu / ⌘C + clipboard
app.py      rumps menu bar app: menus, settings, wires hotkey → player, drives the panels
hud.py      non-activating NSPanels: player panel (transport, seek bar, karaoke transcript,
            live settings as tiles)
            from a job's start until closed; status pill for notices otherwise
player.py   job per request: producer thread (writer → lines) and consumer thread (NSSound)
writer.py   Claude script generation: Anthropic SDK if ANTHROPIC_API_KEY/AUTH_TOKEN, else `claude -p`
tts.py      edge-tts (online neural voices) or macOS voices via NSSpeechSynthesizer (offline)
server.py   HTTP on 127.0.0.1:47823, used by the CLI and the Services entry
config.py   Settings dataclass, voice catalog, load/save/normalize
i18n.py     UI strings + es/en/pt detection of the selected text
cli.py      argparse entry point, LaunchAgent and Services installers
```

Latency design (measured; keep it):
- `player._POOL` is one shared thread pool. The job keeps every line with its synthesis
  *future*; `Player._schedule` keeps the line playing and the next `PREFETCH` submitted, so
  they are synthesized ahead and played in order. The pool is shared so worker threads, and
  the local voices loaded in them, stay warm between requests.
- Local voices run in-process with one `NSSpeechSynthesizer` per worker thread (~0.05 s a
  line, versus ~0.7 s spawning `say`). The voice only loads on its first synthesis, so
  `Player.warm_up` synthesizes a word in every worker at start-up.
- `writer._Warm` keeps one `claude -p --input-format stream-json` process started ahead with
  the current system prompt (~0.8 s saved). Plain stdin doesn't work for this: `claude -p`
  stops waiting for stdin after ~3 s.
- The first utterance from Claude is cut at its first sentence end, not the end of its line.
- Edge latency is bimodal (~1 s or 5-15 s) and degrades for a long while after bursts of
  requests. Hedged (duplicate) requests were tried and didn't help: when it's slow, both are.
- Benchmark or test without sound by wrapping `AppKit.NSSound` so each sound gets
  `setVolume_(0)`; `player._play` imports it at call time.

Player status is a plain string read by a 0.15 s `rumps.Timer` in `app.py`: `idle`, `preparing`,
`thinking`, `speaking`, `paused` or `error: …`; `Player.progress()` gives the panel a copy of the
lines, position and current word. A job that played to the end stays as `Player._done`
(`Progress.finished`) until `stop` (the panel's ✕, the menu, the CLI); play/seek on it
replays its script without asking Claude again. The hotkey callbacks run on a background thread, so they only
set state; the timer is what touches AppKit.

## Hard-won details (do not regress)

- **Grabbing the selection** doesn't use macOS Services, which only see the selection in apps
  that support them (not Warp, VS Code, Slack…). `copy_selection` tries the focused element's
  `AXSelectedText`, then presses the app's Copy menu item (found by its ⌘C key equivalent, so
  it works in any UI language), then posts a synthetic ⌘C. Slack, Safari and Orca ignore the
  synthetic ⌘C. Electron apps only expose their AX tree after `AXManualAccessibility` is set
  on the app. For the synthetic ⌘C, two fixes matter:
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
- **NSSound off the main thread** never reports the end of a sound (`isPlaying` stays true).
  `_play` tracks the time played and treats `currentTime` rewinding to 0 as the end.
- **The HUD and the player panel** are borderless non-activating panels and must stay so: if
  one takes focus, the selection/⌘C goes to Susurro instead of the user's app. Their
  controls override `acceptsFirstMouse_` so a click acts without activating anything. Dialogs (e.g. the custom shortcut prompt) call
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
