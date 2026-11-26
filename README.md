# Susurro

Select text in any Mac app, press **⌃S**, and listen to it, in Spanish, English or Portuguese.

| Mode | What it does |
|---|---|
| Read as is | Reads the selected text aloud. No model involved. |
| Narrated (Claude) | Claude rewrites it as a spoken explanation. |
| Two-host podcast (Claude) | Claude writes a dialogue between two hosts, A and B, each with their own voice. |

## Languages

- **Language** has two settings, each Español, English or Português:
  - *Text language* (input): *Detect* guesses it from the selected text; fix it when the
    guess is wrong, for example with very short or mixed-language selections.
  - *Speech language* (output): *Same as the text*, or a fixed language. When it differs from
    the text's, Claude translates, also in *Read as is* mode (a faithful translation, read in
    full).
- **Voices** are chosen per language. In podcast mode, host B uses the partner of the voice you
  picked, for example Paola ↔ Sebastián or Ava ↔ Andrew.
- **Interface language** (the menu bar app) can be Español, English or Português. By default it
  follows the macOS language.

## How it works

```
selection ──⌃S──▶ menu bar app (reads it through Accessibility, or copies it and restores the clipboard)
                                          │
                 (or right-click → Services → "Susurro: Speak selection" ──HTTP──▶ 127.0.0.1:47823)
                                          │
                   writer.py  (Claude streams the script, line by line, in the target language)
                                          │
                   tts.py     (Edge neural or local macOS voices, one line at a time)
                                          │
                   player.py  (one line plays while the next ones are synthesized; pause/seek)
```

- **Player:** when it starts speaking, a floating panel shows up (it never takes focus from
  your app; drag it anywhere). It stays open after the speech ends, so you can replay it, until
  you close it with the ✕ in its top-left corner (shown on hover, and always once it's done):
  - previous/next line, pause, restart from the beginning (Claude keeps writing meanwhile),
    and a seek bar over the whole script with a line counter;
  - a karaoke transcript (the quote button shows or hides it): the line being read and its
    current word are highlighted; click any word to jump there;
  - live settings (the sliders button): a grid of tiles for mode, detail, speed, engine,
    text language, speech language and voice. A new voice, engine or speed applies from the
    current point on; a new mode, detail level or language that changes what Claude writes
    rewrites the script from the start.

  Which sections are open is remembered (`panel_settings`, `panel_transcript` in the settings
  file).
- **Menu bar:** an icon (🗣 ready · 💭 Claude writing · 🔊 speaking · ⏸ paused · ⚠️ error) with:
  - mode, speed, and text and speech language;
  - voice engine and voice (one submenu per language);
  - detail: concise, summary, extended or in depth;
  - interface language;
  - stop and repeat.

  Settings are saved in `~/.config/susurro/settings.json`.
- **Claude:** if `ANTHROPIC_API_KEY` is set, the SDK is used. Otherwise it uses `claude -p` with
  your Claude Code session. The default model is `claude-opus-5-5` at low effort, so the first
  line arrives in about 3 to 6 seconds.
- **Voices:**
  - `edge`: Microsoft neural voices. Free and no key needed, but online. It is an unofficial
    endpoint and could stop working.
  - `say`: macOS voices, 100% local.

## Usage

```sh
uv sync
uv run susurro install     # starts at login (LaunchAgent) + right-click Services entry
```

The first time, macOS asks to allow **Accessibility** for Susurro's Python (System Settings →
Privacy & Security → Accessibility). The shortcut needs it to catch ⌃S and to read the selection
from the front app. Until it's granted, the menu shows a "⚠️ Allow Accessibility…" item.

## Shortcut

The default is **⌃S**. Change it any time from the menu bar → **Shortcut**: pick ⌃S, ⌃⌥S, ⌃Tab or
⌃⌥Space, or choose **Other…** and type a combination such as `ctrl+option+space` (modifiers:
`ctrl`, `option`, `cmd`, `shift`). It takes effect at once and is saved to
`~/.config/susurro/settings.json` (`"hotkey"`), which you can also edit by hand.

Susurro swallows the shortcut, so the front app never receives it while Susurro runs. With ⌃S
that means terminal flow control (XOFF) and shell forward history search (`⌃S` in bash/zsh)
stop working; pick another combination if you rely on them.

Why not a macOS Services shortcut: Services only receive the selection from apps that support
them, and many terminals and Electron apps (Warp, VS Code, Slack…) don't. Susurro asks the app
for the selected text through Accessibility; if the app doesn't expose it, it presses the app's
own Edit → Copy, and as a last resort sends ⌘C.

From the terminal:

```sh
uv run susurro speak "hello"                          # or: pbpaste | uv run susurro speak
uv run susurro speak --mode podcast --lang pt < note.txt
uv run susurro stop
uv run susurro status
uv run susurro uninstall
```

## Notes

- If the shortcut does nothing, check `~/Library/Logs/susurro.log`: the first line says whether
  Accessibility is granted. Also make sure Python is enabled in the Accessibility list.
- TLS is validated against the macOS keychain (`truststore`), so it works behind proxies that
  inspect traffic with their own CA.
- **Speed.** macOS local voices are the fastest: in *Read as is* the first audio starts ~0.1 s
  after the selection is grabbed. Edge voices take ~1.2 s per line when the service is quick,
  but Microsoft throttles it after heavy use, and then a line can take 5-15 s. Susurro
  synthesizes several lines ahead to hide that, but the first line still has to wait. In
  *Narrated* and *Podcast* modes, Claude needs ~1.5 s to write the first sentence; Susurro keeps
  a `claude -p` process started ahead (about 250 MB of memory) to save another ~0.8 s.
- Local "Enhanced" and "Premium" voices sound far better than the basic ones. Install them from
  System Settings → Accessibility → Spoken Content → System voice → Manage voices.
- Language detection is a simple word-frequency heuristic. For very short or mixed-language
  selections, set the text language by hand.
