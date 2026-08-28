"""Persistent configuration (TOML, ~/.config/projectdock/config.toml).

The config file is created on first run with sensible defaults adapted to the
current machine (only existing project roots are kept).
"""

import os
import shutil
import tomllib
from dataclasses import dataclass, field

from . import paths

DEFAULT_ROOTS = ["~/Projects", "~/Code", "~/Development"]

KNOWN_EDITORS = [
    "t3code",
    "zeditor",
    "zed",
    "code",
    "code-insiders",
    "cursor",
    "nvim",
    "neovim",
    "vim",
    "helix",
    "subl",
    "micro",
]

KNOWN_TERMINALS = [
    "ghostty",
    "alacritty",
    "foot",
    "kitty",
    "wezterm",
    "konsole",
    "xterm",
]


@dataclass
class Config:
    roots: list = field(default_factory=list)
    max_depth: int = 4
    rescan_minutes: int = 5
    editor: str = ""
    terminal: str = ""
    file_manager: str = ""
    width: int = 720
    max_height_pct: int = 70
    top_margin_pct: int = 14
    theme: str = "auto"
    hide_on_focus_loss: bool = False

    @property
    def path(self):
        return paths.config_file()

    def expanded_roots(self):
        out = []
        for root in self.roots:
            root = os.path.expanduser(root)
            if root and os.path.isdir(root):
                out.append(root)
        return out

    def detected_editor(self):
        """Resolve the editor the default open action should use.

        Returns a list argv used to open a project (the editor command and
        its fixed arguments), or None when no editor is available so callers
        fall back to opening the project folder.
        """
        if self.editor:
            return self.editor.split()

        for cmd in ("omarchy-launch-editor",):
            if shutil.which(cmd):
                return [cmd]

        for name in KNOWN_EDITORS:
            if shutil.which(name):
                return [name]

        return None

    def available_editors(self):
        """Return list of detected editor argv lists present on system."""
        # Prefer centralized registry but keep omarchy-launch-editor first
        out = []
        seen = set()
        for cmd in ("omarchy-launch-editor",):
            if shutil.which(cmd) and cmd not in seen:
                out.append([cmd])
                seen.add(cmd)
        # Use tools registry for editors (deduplicates aliases like zed/zeditor)
        try:
            from . import tools as _tools
            for tool in _tools.available_tools():
                if tool.kind != "editor":
                    continue
                exe = _tools.resolve_executable(tool)
                if exe:
                    base = exe.split("/")[-1] if "/" in exe else tool.probe[0]
                    # Use first probe name for display stability; avoid duplicates
                    alias = tool.probe[0]
                    if alias not in seen:
                        out.append([alias])
                        seen.add(alias)
            # Fallback to legacy list for any editors not in registry (defensive)
            for name in KNOWN_EDITORS:
                if name not in seen and shutil.which(name):
                    out.append([name])
                    seen.add(name)
        except Exception:
            for name in KNOWN_EDITORS:
                if name not in seen and shutil.which(name):
                    out.append([name])
                    seen.add(name)
        return out

    def editor_argv_for(self, preferred):
        """Validate preferred editor string and return argv if available."""
        if not preferred or not isinstance(preferred, str):
            return None
        base = preferred.strip().split()[0] if preferred.strip() else ""
        if not base:
            return None
        import shutil
        if shutil.which(base):
            return preferred.strip().split()
        return None

    def editor_label(self):
        if self.editor:
            return os.path.basename(self.editor.split()[0])
        default_file = os.path.expanduser("~/.local/state/omarchy/defaults/editor")
        try:
            with open(default_file, encoding="utf-8") as fh:
                return os.path.basename(fh.read().strip())
        except OSError:
            pass
        if shutil.which("omarchy-launch-editor"):
            return "editor"
        for name in KNOWN_EDITORS:
            if shutil.which(name):
                return name
        return "editor"

    def terminal_command(self, cwd):
        """Command line that opens the preferred terminal in `cwd`."""
        if self.terminal:
            cmd = self.terminal.split()
            cmd = [c if c != "{dir}" else cwd for c in cmd]
            if "{dir}" not in self.terminal:
                cmd.append(cwd)
            return cmd
        if shutil.which("xdg-terminal-exec"):
            return ["xdg-terminal-exec", f"--dir={cwd}"]
        for name in KNOWN_TERMINALS:
            if shutil.which(name):
                return [name, "--working-directory", cwd]
        return None

    def file_manager_command(self, cwd):
        if self.file_manager:
            cmd = self.file_manager.split()
            cmd = [c if c != "{dir}" else cwd for c in cmd]
            if "{dir}" not in self.file_manager:
                cmd.append(cwd)
            return cmd
        if shutil.which("xdg-open"):
            return ["xdg-open", cwd]
        if shutil.which("nautilus"):
            return ["nautilus", "--new-window", cwd]
        return None


def default_roots():
    """Default roots, keeping only directories that exist on this machine."""
    found = []
    for root in DEFAULT_ROOTS:
        expanded = os.path.expanduser(root)
        if os.path.isdir(expanded):
            found.append(root)
    return found


def load():
    """Load config, creating a default file on first run. Never raises."""
    cfg = Config(roots=default_roots())
    paths.ensure_dirs()
    try:
        with open(paths.config_file(), "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        save(cfg)
        return cfg

    general = data.get("general", {})
    open_sec = data.get("open", {})
    ui = data.get("ui", {})

    if "roots" in general:
        roots = general["roots"]
        if isinstance(roots, list):
            cfg.roots = [r for r in roots if isinstance(r, str) and r.strip()]
    cfg.max_depth = _int(general.get("max_depth"), cfg.max_depth, 1, 10)
    cfg.rescan_minutes = _int(general.get("rescan_minutes"), cfg.rescan_minutes, 0, 1440)
    cfg.editor = _str(open_sec.get("editor"), cfg.editor)
    cfg.terminal = _str(open_sec.get("terminal"), cfg.terminal)
    cfg.file_manager = _str(open_sec.get("file_manager"), cfg.file_manager)
    cfg.width = _int(ui.get("width"), cfg.width, 420, 2000)
    cfg.max_height_pct = _int(ui.get("max_height_pct"), cfg.max_height_pct, 10, 100)
    cfg.top_margin_pct = _int(ui.get("top_margin_pct"), cfg.top_margin_pct, 0, 90)
    cfg.theme = _str(ui.get("theme"), cfg.theme)
    if "hide_on_focus_loss" in ui:
        cfg.hide_on_focus_loss = bool(ui["hide_on_focus_loss"])
    return cfg


def save(cfg: Config):
    paths.ensure_dirs()
    lines = [
        "# ProjectDock configuration",
        "#",
        "# roots: directories scanned for projects (relative paths in ~ allowed).",
        "#         Only existing directories are scanned.",
        "# editor: command used by the default open action. Leave empty to use",
        "#         Omarchy's default editor (omarchy-launch-editor).",
        "# terminal: command used to open a terminal in the project. Leave empty",
        "#         to use xdg-terminal-exec (respects the Omarchy default).",
        "# file_manager: command used to open the project folder. Leave empty",
        "#         to use xdg-open.",
        "# theme: 'auto' follows the active Omarchy theme.",
        "",
        "[general]",
        f'roots = {_toml_str_list(cfg.roots)}',
        f"max_depth = {cfg.max_depth}",
        f"rescan_minutes = {cfg.rescan_minutes}",
        "",
        "[open]",
        f"editor = {_toml_str(cfg.editor)}",
        f"terminal = {_toml_str(cfg.terminal)}",
        f"file_manager = {_toml_str(cfg.file_manager)}",
        "",
        "[ui]",
        f"width = {cfg.width}",
        f"max_height_pct = {cfg.max_height_pct}",
        f"top_margin_pct = {cfg.top_margin_pct}",
        f"theme = {_toml_str(cfg.theme)}",
        f"hide_on_focus_loss = {str(cfg.hide_on_focus_loss).lower()}",
        "",
    ]
    with open(paths.config_file(), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _str(value, default):
    return value if isinstance(value, str) else default


def _toml_str(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_str_list(values):
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"
