# Susurro

Select text in any Mac app, press **⌃S**, and listen to it, in Spanish, English or Portuguese.

| Mode | What it does |
|---|---|
| Read as is | Reads the selected text aloud. No model involved. |
| Narrated (Claude) | Claude rewrites it as a spoken explanation. |
| Two-host podcast (Claude) | Claude writes a dialogue between two hosts, A and B, each with their own voice. |

## Languages

- **Speech language** can be *Auto* or fixed to Español, English or Português.
  - *Auto* detects the language from the selected text.
  - A fixed language makes Claude narrate in it, translating if needed. In *Read as is* mode
    there is no translation: the text is just read with that language's voice.
- **Voices** are chosen per language. In podcast mode, host B uses the partner of the voice you
  picked, for example Paola ↔ Sebastián or Ava ↔ Andrew.
- **Interface language** (the menu bar app) can be Español, English or Português. By default it
  follows the macOS language.

## How it works

```
selection ──⌃S──▶ menu bar app (sends ⌘C, reads the clipboard, restores it)
                                          │
                 (or right-click → Services → "Susurro: Speak selection" ──HTTP──▶ 127.0.0.1:47823)
                                          │
                   writer.py  (Claude streams the script, line by line, in the target language)
                                          │
                   tts.py     (Edge neural or local macOS voices, one line at a time)
                                          │
                   player.py  (queue: one line plays while the next is synthesized)
```

- **UI:** a menu bar icon (🗣 ready · 💭 Claude writing · 🔊 speaking · ⚠️ error) with:
  - mode, speed and speech language;
  - voice engine and voice (one submenu per language);
  - length;
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
Privacy & Security → Accessibility). The shortcut needs it to catch ⌃S and to send ⌘C to the
front app. Until it's granted, the menu shows a "⚠️ Allow Accessibility…" item.

## Shortcut

The default is **⌃S**. Change it any time from the menu bar → **Shortcut**: pick ⌃S, ⌃⌥S, ⌃Tab or
⌃⌥Space, or choose **Other…** and type a combination such as `ctrl+option+space` (modifiers:
`ctrl`, `option`, `cmd`, `shift`). It takes effect at once and is saved to
`~/.config/susurro/settings.json` (`"hotkey"`), which you can also edit by hand.

Susurro swallows the shortcut, so the front app never receives it while Susurro runs. With ⌃S
that means terminal flow control (XOFF) and shell forward history search (`⌃S` in bash/zsh)
stop working; pick another combination if you rely on them.

Why not a macOS Services shortcut: Services only receive the selection from apps that support
them, and many terminals and Electron apps (Warp, VS Code, Slack…) don't. ⌘C works everywhere.

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
- Local "Enhanced" and "Premium" voices sound far better than the basic ones. Install them from
  System Settings → Accessibility → Spoken Content → System voice → Manage voices.
- Language detection is a simple word-frequency heuristic. For very short or mixed-language
  selections, set the speech language by hand.
