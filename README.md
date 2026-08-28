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
- **Choose Editor…** picker (`Tab` → `Choose Editor`): keyboard-first `↑↓`/`Home/End`/`PgUp/PgDn`, `Enter` launches and remembers, `Esc` back; shows only available editors (T3 Code via `t3code`/`t3`, Zed `zeditor`/`zed`, VS Code `code`/`code-insiders`, Cursor, Neovim `nvim`/`neovim`, Vim, Helix `hx`/`helix`, Sublime, Micro) with preferred hint; never duplicates aliases.
- Copy path, pin/unpin, rescan, config.

**Intelligence**
- Parses `package.json` scripts, `pyproject.toml`, `Cargo.toml`, `go.mod`, `Makefile` conservatively.
- Surfaces contextual `Run Dev Server`, `Run Tests`, `Build Project`, `Run Project` (max 3, no `npm install`); `dev`/`run`/`serve`/`start` marked `long_running=True`, `test`/`build` not.
- Structured `ProjectCapabilities` cached by mtime, validated before execution, with `long_running` metadata for session handling.

**Workspace**
- Hyprland awareness via `hyprctl clients -j` + `/proc/<pid>/cwd` (high-confidence, longest-prefix match, no title guessing).
- Active project detection, `Focus Project`/`Focus Dev Server` actions, smart `Enter` (focus if active else open/session-aware), subtle `●` indicator.
- Lightweight `workspace` in `state.json` (bounded, corrupt-safe) — no process monitoring, no daemon polling.

**Sessions** (ProjectDock-owned, runtime-only)
- Tracks only sessions ProjectDock launches: editor, terminal, dev server (`dev`/`run` long-running). Stores `pid`, `pgid`, `/proc` start-time, `started_at`; bounded (`5` per project, `50` total) and per-capability deduped.
- Duplicate `Run Dev Server` is blocked while a confident owned session is running (focuses existing window if possible).
- **SESSION** controls when relevant: `Dev Server Running — <cmd> · <age>`, `Restart Dev Server`, `Stop Dev Server` (SIGTERM to owned `pgid`/`pid` with `/proc` start-time validation, no `SIGKILL`, no cwd-guessing kills). Restart revalidates current `ProjectCapabilities`.
- Hyprland windows for launched terminals/editors are associated via same `cwd` confidence; `Focus Dev Server` only when address valid.
- Persistence: runtime `pid` not trusted after reload; only historical `preferred_editor`, `last_action`, `action_usage` persist.

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

## Creating projects

Type a name that does not match any existing project (e.g. `My New App`) and a `+ Create "My New App"` row appears for the configured root (`~/Projects` by default, or the active project's root when multiple roots are configured). Press `Enter` to create the folder — it becomes a valid ProjectDock project immediately, even when empty, and the launcher switches directly into its actions.

Sanitization is conservative: empty names, `.`/`..`, path separators, absolute paths, traversal (`../`) and control characters are rejected; creation never leaves configured roots and uses structured `os.makedirs` (no shell).

Empty projects expose a minimal action set (`Open in Editor/Terminal/File Manager`, `Copy Path`, `Pin`, rescans) — `Run Dev`/`Build`/`Test` appear only after project metadata (e.g. `package.json`) is added and the project is rescanned.

## Actions menu (`Tab`)

```
QUICK
   ↵ Focus Project / Open in code — primary (smart, session-aware)
   >_ Open Terminal (frequent)
SESSION  (only when dev running)
  ● Dev Server Running — npm run dev · 8m ago
  ↻ Restart Dev Server
  ■ Stop Dev Server
PROJECT
  ▶ Run Dev Server — npm run dev  (pm from project, not global preference)
  ✓ Run Tests
  ◉ Build Project
OTHER
  󰉋 Choose Editor…  → SELECT EDITOR (T3 Code, Zed, VS Code …)
  󰉋 Open With… (agents)
  󰉋 Open Folder — Ctrl+F
  󰆏 Copy Path — Ctrl+C
  󰐃 Pin — Ctrl+P
```

- `QUICK` smart primary priority: `Focus Project` (active window) → `Focus Dev Server` (running owned session with window) → preferred tool/editor → usage-ranked capability → `Open`. Session controls never distort long-term preference ranking.
- `PROJECT` actions are filtered by executable availability: `npm`/`pnpm`/`yarn`/`bun` is detected from the project (`packageManager` field → lockfiles → fallback). Unavailable capabilities are **not** rendered at all (no `UNAVAILABLE` section, no disabled rows, no `X is not installed` hints); they are also excluded from Quick Actions, smart primary, persisted-preference resolution, and the picker.
- Package manager hints are visible via the command (`npm run dev` vs `pnpm run dev`); detection order is `packageManager` field (if valid), then lockfiles in this deterministic order — `package-lock.json`/`npm-shrinkwrap.json` → `pnpm-lock.yaml` → `yarn.lock` → `bun.lockb`/`bun.lock` — then the first installed fallback (prefer `npm`). A stale `bun.lock` left over from a previous experiment does not win over a real `package-lock.json`; the fallback never invents a non-installed runtime. Cache invalidates when `package.json`/lockfiles change.
- `Run` actions auto-execute in the terminal (`bash -lc 'printf banner; <command>; exec bash'`) in the project directory, no second `Enter` required. Commands are validated against an allowlist (`[A-Za-z0-9 _./:\-@]+`) and re-checked before execution; missing executables block launch. Duplicate dev launches blocked while owned session running.
- `SESSION` appears only for long-running ProjectDock-owned dev sessions; `Stop` uses `SIGTERM` on validated `pgid`/`pid` (`/proc` start-time check), `Restart` revalidates current capabilities.

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
| `Enter` | Smart primary: `Focus` if active else `Open` (or preferred `Run Dev` if frequently used). On a `+ Create "…" ` row, creates folder and enters its actions. |
| `↑` / `↓` (`Ctrl+J/K`, `Ctrl+N`) | Navigate (wraps, includes create row) |
| `PageUp` / `PageDown` | Jump 8 |
| `Alt+1`…`9` | Jump to Nth |
| `Tab` | Actions menu / Choose Editor / Open With picker (`Esc` back) |
| `Esc` | Close / back to search (`Actions → Search`, `Picker → Actions`) |
| `Ctrl+P` | Pin/unpin |
| `Ctrl+T` | Terminal (project directory) |
| `Ctrl+F` | Folder (file manager) |
| `Ctrl+C` | Copy path (preserves text selection) |
| `Ctrl+R` | Rescan — from search mode: targeted root rescan (selected/last project), or global if none. From actions mode: targeted root rescan. |
| `Ctrl+Shift+R` | Rescan all projects (global, all configured roots) |
| `Ctrl+Q` | Quit daemon |

All rescans and runs preserve pinned/recent/editor-preference/action-usage state. Filesystem changes are picked up on show (root mtime check) and, while visible, via a lightweight `Gio.FileMonitor` (debounced 500 ms, no permanent polling) with graceful fallback. Manual rescans and the in-window filesystem monitor coalesce — a monitor-driven rescan does not start if a scan is already in progress, and vice versa, so there is no feedback loop.

Discovery / rescan is a library-level operation and lives in the **main search mode** (a subtle `↻ Rescan projects` row near the bottom of the list, only when the query is empty) and as `Ctrl+R` / `Ctrl+Shift+R` shortcuts. It is **not** in the per-project `Actions` menu. Manual rescans show a brief footer status (`Rescanning projects…` → `Projects updated`) that auto-clears; no toast libraries, modal dialogs, or persistent status clutter.

## Omarchy integration

- `SUPER+D` → `projectdock toggle` (idempotent `make install`, `SUPER+P` avoided — `pseudo` tiling).
- Layer-shell `EXCLUSIVE` focus (Hyprland-safe), top-centered.
- Editors/terminals/file managers via Omarchy tools + `uwsm-app`.
- Theme via `omarchy theme current` → `colors.toml`.

## Architecture

| Module | Role |
|---|---|
| `discovery.py` | Bounded scan, marker detection, `describe_project`/`scan_root`/`refresh_project` for targeted rescans |
| `creation.py` | Safe project creation (sanitization, root selection, `os.makedirs` only) |
| `markers.py` | Type registry |
| `search.py` | Fuzzy scoring + `pinned>active>recent` ordering |
| `state.py` | Pins, recents, cache + `workspace` profiles |
| `intelligence.py` | `ProjectCapabilities` parsers + mtime cache, package-manager-aware + availability + `long_running` |
| `workspace.py` | Session tracker, Hyprland association, active ranking |
| `sessions.py` | Ephemeral ProjectDock-owned sessions (`pid`/`pgid`/`start_ticks`, bounded, deduped, SPI-safe stop) |
| `hyprland.py` | `hyprctl` + `/proc` (safe, structured) |
| `gitinfo.py` | `branch/dirty/untracked/ahead/behind` (cached) |
| `config.py` / `paths.py` | XDG load/save |
| `cover.py` | Local asset + initials |
| `theme.py` | Omarchy palette → CSS |
| `ui.py` | GTK4 layer-shell window, creation row, `Gio.FileMonitor` while visible, Choose Editor picker, `SESSION` section |
| `app.py` | Controller, D-Bus daemon, threading, creation + targeted rescans + editor/session intelligence |
| `cli.py` | Layer-shell preload entry |

No plugin system, no cloud — fast launcher.

## Development

```bash
python3 -m unittest discover -s tests   # 323 tests (296 prior + 27 Editor & Session V1)
```

## Limitations

- Primary environment: Omarchy/Hyprland. Outside, workspace detection degrades (no polling, no background scans).
- Intelligence is conservative (only `dev/test/build/run`, no `preinstall`, no `npm install`).
- Git ahead/behind requires upstream; otherwise 0.
- Sessions are ProjectDock-owned only; external terminals/editors not tracked, `pid` not persisted, no title guessing, no system-wide scan.
- Not a process supervisor, multiplexer, IDE, or dashboard.

## License

MIT — see `LICENSE`.
