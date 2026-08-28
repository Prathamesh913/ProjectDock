"""Project-opening tools: code editors and AI coding environments.

A small, conservative registry. Each tool is a frozen descriptor; availability
is probed through ``shutil.which`` over a short list of candidate executable
names and cached briefly so menu rebuilds stay cheap. Launching always uses
structured argv (never shell interpolation) resolved at launch time, so a
tool that disappears between detection and launch fails gracefully.

Extensible: add a ``Tool`` entry to ``REGISTRY``. Nothing else needs to
change - detection, the Open With picker, preferences and smart-primary all
read from this registry.

Categories:
    editor - traditional GUI/terminal code editors (Zed, VS Code, ...)
    agent  - AI coding environments (T3 Code, OpenCode, ...)

``in_terminal`` tools are TUI programs: they are launched inside the user's
configured terminal in the project directory instead of as direct children.
"""

import re
import shutil
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    id: str                 # stable identifier used for preferences ("zed")
    name: str               # display name ("Zed")
    kind: str               # "editor" | "agent"
    probe: tuple            # candidate executables, best first
    args: tuple = ()        # argv template for GUI tools; "{path}" substituted
    in_terminal: bool = False  # TUI tool -> launch inside user's terminal

    def argv_for(self, path):
        """Structured argv to open `path`, or None for terminal tools."""
        if self.in_terminal:
            return None
        exe = resolve_executable(self)
        if exe is None:
            return None
        # args template may contain "{exe}" and "{path}" placeholders
        out = []
        for a in self.args:
            if a == "{exe}":
                out.append(exe)
            else:
                out.append(a.replace("{path}", path).replace("{exe}", exe))
        # Ensure exe is first element without duplication
        if not out:
            return [exe, path]
        if out[0] != exe:
            return [exe, *out]
        return out

    def command_for(self):
        """Shell command word for terminal tools (validated executable)."""
        if not self.in_terminal:
            return None
        return resolve_executable(self)


# Conservative registry: only tools with well-known, stable executables.
# Order defines picker display order within each category.
# T3 Code supports both `t3code` and shorthand `t3` (alias).
REGISTRY = (
    Tool("zed", "Zed", "editor",
         probe=("zeditor", "zed"), args=("{exe}", "{path}")),
    Tool("vscode", "VS Code", "editor",
         probe=("code", "code-insiders"), args=("{exe}", "{path}")),
    Tool("cursor", "Cursor", "editor",
         probe=("cursor",), args=("{exe}", "{path}")),
    Tool("t3code", "T3 Code", "editor",
         probe=("t3code", "t3"), args=("{exe}", "{path}")),
    Tool("opencode", "OpenCode", "agent",
         probe=("opencode",), in_terminal=True),
    Tool("nvim", "Neovim", "editor",
         probe=("nvim", "neovim"), args=("{exe}", "{path}")),
    Tool("vim", "Vim", "editor",
         probe=("vim",), args=("{exe}", "{path}")),
    Tool("helix", "Helix", "editor",
         probe=("hx", "helix"), args=("{exe}", "{path}")),
    Tool("subl", "Sublime Text", "editor",
         probe=("subl",), args=("{exe}", "{path}")),
    Tool("micro", "Micro", "editor",
         probe=("micro",), args=("{exe}", "{path}")),
)

_TOOL_IDS = frozenset(t.id for t in REGISTRY)

_TOOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Availability cache: tool.id -> (expires_at, resolved_exe_or_None)
_CACHE_TTL = 30.0
_avail_cache = {}

# Indirections for tests.
_which = shutil.which
_now = time.time


def resolve_executable(tool):
    """Return the first available executable for `tool`, or None.

    Result is cached briefly; a tool that disappears mid-session is
    re-probed when the cache expires and launch-time resolution fails
    gracefully regardless.
    """
    if not isinstance(tool, Tool):
        return None
    hit = _avail_cache.get(tool.id)
    now = _now()
    if hit is not None and hit[0] > now:
        return hit[1]
    exe = None
    for name in tool.probe:
        try:
            found = _which(name)
        except OSError:
            found = None
        if found:
            exe = found
            break
    _avail_cache[tool.id] = (now + _CACHE_TTL, exe)
    return exe


def clear_cache():
    """Forget cached availability (used by tests and explicit rescans)."""
    _avail_cache.clear()


def available_tools():
    """All registry tools whose executable is currently installed."""
    return [t for t in REGISTRY if resolve_executable(t) is not None]


def get_tool(tool_id):
    """Registry tool by id, or None. Does NOT check availability."""
    if not isinstance(tool_id, str) or not _TOOL_ID_RE.match(tool_id):
        return None
    for tool in REGISTRY:
        if tool.id == tool_id:
            return tool
    return None


def validate_tool_id(tool_id):
    """Tool for `tool_id` iff it is registered AND currently installed."""
    tool = get_tool(tool_id)
    if tool is None or resolve_executable(tool) is None:
        return None
    return tool


def grouped_available():
    """Available tools grouped as [(kind_label, [Tool, ...]), ...].

    Categories with no available tools are omitted entirely so the picker
    never renders empty sections.
    """
    groups = []
    for kind, label in (("editor", "CODE EDITORS"), ("agent", "AI CODING")):
        items = [t for t in available_tools() if t.kind == kind]
        if items:
            groups.append((label, items))
    return groups
