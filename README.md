# ProjectDock

A fast, keyboard-first project launcher for [Omarchy](https://omarchy.org/) and Hyprland.

ProjectDock answers one question: **“What do I want to work on right now?”**

Press `SUPER + D`, type a few characters, and your projects filter instantly. Hit `Enter` to open in your editor, or `Tab` for contextual actions — dev servers, tests, builds, terminals, and Git status — all without leaving the keyboard.

ProjectDock is **not** a file picker and **not** a project manager. It understands projects — detects them, classifies them, and offers the right actions for each.

## Features

**Discover**
- Instant scan of configurable roots (`~/Projects`, `~/Code`, `~/Development` …) with depth/budget limits.
- Type detection: Node.js/TypeScript, Rust, Python, Go, Ruby, PHP, Elixir, Java, .NET, Haskell, Dart/Flutter, Nix, Zig, C/C++, Julia, Deno and more — icon + label.
- Local project covers from `public/logo.*`, `assets/logo.*`, etc., with deterministic initials fallback.

**Navigate**
- Fuzzy, ranked search over name, folder, path, and type. Pinned > Active > Recent precedence; sections `Pinned → Active → Recent → Projects`.
- Keyboard-first: `↑↓` navigate, `Enter` smart open/focus, `Tab` actions, `Esc` close, `Alt+1..9` jump.

**Work**
- Open in editor (`omarchy-launch-editor`), terminal (`xdg-terminal-exec`), file manager (`xdg-open`), all scoped via `uwsm-app`.
- Per-project editor memory — ProjectDock remembers the editor you last used for each project.
- Copy path, pin/unpin, rescan, config.

**Intelligence**
- Parses `package.json` scripts, `pyproject.toml`, `Cargo.toml`, `go.mod`, `Makefile` conservatively.
- Surfaces contextual `Run Dev Server`, `Run Tests`, `Build Project`, `Run Project` (max 3, no `npm install`).
- Structured `ProjectCapabilities` cached by mtime, validated before execution.

**Workspace**
- Hyprland awareness via `hyprctl clients -j` + `/proc/<pid>/cwd` (high-confidence, longest-prefix match).
- Active project detection, `Focus Project` action, smart `Enter` (focus if active else open), subtle `●` indicator.
- Lightweight `workspace` in `state.json` (bounded, corrupt-safe) — no process monitoring, no daemon polling.

**Git**
- Async branch, dirty, untracked, ahead/behind (cached 10s, `↑2 ↓1`, `●`/`✓`).

## Requirements

- Linux with Wayland (primary: Omarchy / Hyprland).
- Python 3.11+ (stdlib only — **no pip dependencies**).
- GTK 4, PyGObject, `gtk4-layer-shell` (preinstalled on Omarchy).

Degrades gracefully outside Hyprland (workspace awareness disabled).

## Installation

```bash
git clone https://github.com/Prathamesh913/ProjectDock.git ~/Projects/ProjectDock
cd ~/Projects/ProjectDock
./bin/projectdock --version
```

Wire up `PATH` + shortcut:

```bash
make install          # symlinks ~/.local/bin/projectdock and adds SUPER+D to ~/.config/hypr/bindings.lua
hyprctl reload
```

Uninstall:

```bash
make uninstall
```

## Usage

```
./bin/projectdock toggle   # SUPER+D
./bin/projectdock show
./bin/projectdock hide
./bin/projectdock quit
./bin/projectdock rescan
```

First launch scans roots, writes `~/.config/projectdock/config.toml`, caches under `~/.local/state/projectdock/`.

Sections when idle: `Pinned`, `Active` (Hyprland windows), `Recent`, `Projects`. Searching ranks by match quality + active/recent boost.

```
  󰍉  Search projects by name, path, type…
  ─────────────────────────────
  ACTIVE
     ●  CP cine-print-gallery            TypeScript
        ~/Projects/cine-print-gallery  main ↑1
  RECENT
     PR ProjectDock                       Python
        ~/Projects/ProjectDock
  ↑↓ navigate · ↵ open · tab actions · esc close
```

## Actions menu (`Tab`)

```
QUICK
  ↵ Focus Project / Open in code — primary (smart)
  >_ Open Terminal (frequent)
PROJECT
  ▶ Run Dev Server — npm run dev
  ✓ Run Tests
  ◉ Build Project
OTHER
  󰉋 Open Folder — Ctrl+F
  󰆏 Copy Path — Ctrl+C
  󰐃 Pin — Ctrl+P
  󰑓 Rescan — Ctrl+R
```

Quick Actions derived from intelligence + frequent usage (capped, deterministic, deduped). Headers only when section has items.

## Configuration

`~/.config/projectdock/config.toml`:

```toml
[general]
roots = ["~/Projects", "~/Code", "~/Development"]
max_depth = 4
rescan_minutes = 5

[open]
editor = ""          # empty = omarchy-launch-editor
terminal = ""        # empty = xdg-terminal-exec
file_manager = ""    # empty = xdg-open

[ui]
width = 720
max_height_pct = 70
top_margin_pct = 14
theme = "auto"       # auto follows Omarchy theme
hide_on_focus_loss = false
```

See `config.example.toml` for annotated defaults.

## Keyboard shortcuts

Unmodified keys always type. Modifiers required for shortcuts.

| Key | Action |
|---|---|
| `Enter` | Smart primary: `Focus` if active else `Open` (or preferred `Run Dev` if frequently used) |
| `↑` / `↓` (`Ctrl+J/K`, `Ctrl+N`) | Navigate |
| `PageUp` / `PageDown` | Jump 8 |
| `Alt+1`…`9` | Jump to Nth |
| `Tab` | Actions menu |
| `Esc` | Close / back to search |
| `Ctrl+P` | Pin/unpin |
| `Ctrl+T` | Terminal |
| `Ctrl+F` | Folder |
| `Ctrl+C` | Copy path (preserves text selection) |
| `Ctrl+R` | Rescan |
| `Ctrl+Q` | Quit daemon |

## Omarchy integration

- `SUPER+D` → `projectdock toggle` (idempotent `make install`, `SUPER+P` avoided — `pseudo` tiling).
- Layer-shell `EXCLUSIVE` focus (Hyprland-safe), top-centered.
- Editors/terminals/file managers via Omarchy tools + `uwsm-app`.
- Theme via `omarchy theme current` → `colors.toml`.

## Architecture

| Module | Role |
|---|---|
| `discovery.py` | Bounded scan, marker detection |
| `markers.py` | Type registry |
| `search.py` | Fuzzy scoring + `pinned>active>recent` ordering |
| `state.py` | Pins, recents, cache + `workspace` profiles |
| `intelligence.py` | `ProjectCapabilities` parsers + mtime cache |
| `workspace.py` | Session tracker, Hyprland association, active ranking |
| `hyprland.py` | `hyprctl` + `/proc` (safe, structured) |
| `gitinfo.py` | `branch/dirty/untracked/ahead/behind` (cached) |
| `config.py` / `paths.py` | XDG load/save |
| `cover.py` | Local asset + initials |
| `theme.py` | Omarchy palette → CSS |
| `ui.py` | GTK4 layer-shell window |
| `app.py` | Controller, D-Bus daemon, threading |
| `cli.py` | Layer-shell preload entry |

No plugin system, no cloud — fast launcher.

## Development

```bash
python3 -m unittest discover -s tests   # 253 tests
```

## Limitations

- Primary environment: Omarchy/Hyprland. Outside, workspace detection degrades (no polling, no background scans).
- Intelligence is conservative (only `dev/test/build/run`, no `preinstall`, no `npm install`).
- Git ahead/behind requires upstream; otherwise 0.
- Not a process supervisor, IDE, or dashboard.

## License

MIT — see `LICENSE`.
