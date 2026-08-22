"""Follow the active Omarchy theme so ProjectDock blends in.

The active theme name is resolved via `omarchy theme current` (or the
OMARCHY_THEME env var), then its colors.toml is read from the stock theme
directory or a user overlay in ~/.config/omarchy/themes/. Everything
degrades to a catppuccin-mocha-like default palette.

The CSS intentionally follows the Omarchy shell design language (the same
system GameDock and the first-party panels use):

  * monospace type, 13px base (15px search, 14px project names)
  * foreground-derived muted text (never a separate "muted" accent)
  * foreground-tinted selection/hover fills (NOT accent-coloured outlines)
  * 1px separators at ~12% foreground alpha
  * accent reserved for the caret, the pinned star and status dots only
"""

import os
import re
import subprocess
import tomllib

DEFAULT_PALETTE = {
    "mode": "dark",
    "background": "#0a0a0f",
    "dark_background": "#101018",
    "lighter_background": "#1c1c26",
    "selection": "#232333",
    "foreground": "#e6e9f2",
    "muted": "#8b90a3",
    "accent": "#89b4fa",
    "green": "#a6da95",
    "red": "#ed8796",
    "yellow": "#eed49f",
    "blue": "#8aadf4",
}

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_NAMED_COLOR = re.compile(r"^[a-zA-Z]{3,30}$")

_BASE_DIRS = (
    "/usr/share/omarchy/themes",
    os.path.expanduser("~/.config/omarchy/themes"),
)


def active_theme_name():
    name = os.environ.get("OMARCHY_THEME")
    if name:
        return name
    try:
        proc = subprocess.run(
            ["omarchy", "theme", "current"],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _valid_color(value):
    return isinstance(value, str) and bool(
        _HEX_COLOR.match(value.strip()) or _NAMED_COLOR.match(value.strip()))


def _validated_palette(data):
    """Build a palette from raw TOML data, validating every value."""
    palette = dict(DEFAULT_PALETTE)
    for key in palette:
        if key not in data:
            continue
        value = data[key]
        if key == "mode":
            if value in ("dark", "light"):
                palette[key] = value
        elif _valid_color(value):
            palette[key] = value.strip()
    return palette


def _find_theme_dir(name):
    """Resolve a theme name to its directory, case-insensitively.

    `omarchy theme current` returns title-cased names (e.g. "Vantablack")
    while the on-disk theme directories are lowercased ("vantablack"); a
    plain path join misses on a case-sensitive filesystem.
    """
    for base in _BASE_DIRS:
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for entry in entries:
            if entry.lower() == name.lower():
                return os.path.join(base, entry)
    return None


def load_palette(name=None, force=None):
    if force:
        return _validated_palette(force)
    name = name or active_theme_name()
    if name:
        theme_dir = _find_theme_dir(name)
        if theme_dir:
            colors_path = os.path.join(theme_dir, "colors.toml")
            try:
                with open(colors_path, "rb") as fh:
                    data = tomllib.load(fh)
                palette = _validated_palette(data)
                palette["_name"] = name
                return palette
            except (OSError, tomllib.TOMLDecodeError):
                pass
    return dict(DEFAULT_PALETTE)


# ------------------------------------------------------------- color math

def _to_rgb(color):
    color = str(color).strip()
    if color.startswith("#"):
        h = color[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) >= 6:
            try:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            except ValueError:
                pass
    return (128, 128, 128)


def _mix(foreground, background, t):
    """Blend foreground toward background by `t` (0..1)."""
    fr, fg, fb = _to_rgb(foreground)
    br, bg, bb = _to_rgb(background)
    return "#%02x%02x%02x" % (
        round(fr + (br - fr) * t),
        round(fg + (bg - fg) * t),
        round(fb + (bb - fb) * t),
    )


def _rgba(color, alpha):
    r, g, b = _to_rgb(color)
    return "rgba(%d, %d, %d, %s)" % (r, g, b, alpha)


def build_css(palette):
    """Render the GTK4 stylesheet with theme colors substituted."""
    bg = palette["background"]
    fg = palette["foreground"]
    accent = palette["accent"]
    green = palette.get("green", accent)
    yellow = palette.get("yellow", accent)

    # Foreground-derived roles (work correctly on both dark and light themes).
    muted = _mix(fg, bg, 0.28)   # secondary text ~72% brightness
    faint = _mix(fg, bg, 0.45)   # tertiary text ~55% brightness

    # Foreground-tinted fills/borders, matching the shell's state tokens.
    border = _rgba(fg, 0.10)     # panel border
    sep = _rgba(fg, 0.12)        # section separator (PanelSeparator strength)
    hover = _rgba(fg, 0.08)      # hover-cursor fill
    selected = _rgba(fg, 0.14)   # selected/current fill

    return f"""
 window.dock {{
     background: transparent;
     font-family: monospace;
     font-size: 13px;
 }}
 .dock-box {{
     background-color: {bg};
     border: 1px solid {border};
     border-radius: 8px;
     padding: 8px;
 }}
 .search-row {{
     padding: 8px 10px 10px 10px;
     border-bottom: 1px solid {sep};
 }}
 .search-row entry {{
     background: transparent;
     background-color: transparent;
     background-image: none;
     border: none;
     box-shadow: none;
     outline: none;
     border-radius: 0;
     min-height: 0;
     padding: 0;
     color: {fg};
     caret-color: {accent};
     font-size: 15px;
 }}
 .search-row entry:focus {{
     background: transparent;
     background-color: transparent;
     background-image: none;
     box-shadow: none;
     outline: none;
 }}
 .search-row entry text {{
     background: transparent;
     background-color: transparent;
 }}
 .search-row entry placeholder {{
     color: {faint};
 }}
 .list {{
     background: transparent;
     padding: 4px 0;
 }}
 row.project-row {{
     background: transparent;
     border: none;
     box-shadow: none;
     border-radius: 0;
     padding: 7px 10px;
     transition: none;
 }}
 row.project-row:hover {{
     background: {hover};
 }}
 row.project-row:selected {{
     background: {selected};
     box-shadow: inset 2px 0 0 0 {accent};
 }}
 row.project-row:selected:hover {{
     background: {selected};
 }}
 row.header-row {{
     background: transparent;
     border: none;
     box-shadow: none;
     padding: 11px 10px 4px 10px;
 }}
 row.header-row label {{
     color: {muted};
     font-size: 10px;
     font-weight: 700;
     letter-spacing: 1.2px;
 }}
 .cover {{
      border-radius: 6px;
      border: 1px solid {border};
      background-color: {selected};
      min-width: 26px;
      min-height: 26px;
  }}
  .cover-img {{
      border-radius: 6px;
  }}
  .cover-initials {{
      font-weight: 700;
      font-size: 11px;
      letter-spacing: 0.4px;
      padding: 0;
      margin: 0;
      min-height: 0;
      min-width: 0;
  }}
  .action-icon {{
      color: {muted};
      font-size: 14px;
      min-width: 18px;
  }}
 .project-name {{
     color: {fg};
     font-weight: 700;
     font-size: 14px;
 }}
 row.project-row:selected .project-name {{
     color: {fg};
 }}
 .project-type {{
     color: {muted};
     font-size: 12px;
 }}
 .project-path {{
     color: {faint};
     font-size: 12px;
 }}
 .git-badge {{
     color: {faint};
     font-size: 12px;
 }}
 .star {{
      color: {accent};
      font-size: 11px;
  }}
  .active-dot {{
      color: {accent};
      font-size: 8px;
  }}
 .empty-title {{
     color: {fg};
     font-size: 13px;
     font-weight: 700;
 }}
 .empty-hint {{
     color: {faint};
     font-size: 12px;
 }}
 .empty-box {{
     padding: 14px 10px;
 }}
 .action-label {{
     color: {fg};
     font-size: 13px;
     font-weight: 700;
 }}
 .action-hint {{
     color: {muted};
     font-size: 12px;
 }}
 .action-sub {{
     color: {faint};
     font-size: 12px;
 }}
 .footer {{
     border-top: 1px solid {sep};
     padding: 7px 10px 6px 10px;
 }}
 .footer label {{
     color: {faint};
     font-size: 11px;
 }}
 .title-label {{
     color: {muted};
     font-size: 12px;
     font-weight: 700;
     padding: 6px 10px 8px 10px;
 }}
 """
