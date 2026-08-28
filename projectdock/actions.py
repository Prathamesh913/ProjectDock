"""System integration: launching editors, terminals and file managers.

Reuses the desktop's own tools wherever possible: omarchy-launch-editor for
the preferred editor, xdg-terminal-exec for terminals, xdg-open for the file
manager and uwsm-app to scope GUI processes into systemd services, exactly
like Omarchy's own keybindings do.
"""

import os
import shlex
import shutil
import subprocess

_DEVNULL = subprocess.DEVNULL

# Commands that already scope their own GUI children into systemd services
# (e.g. omarchy-launch-editor wraps GUI editors with uwsm-app itself).
_UWSM_SELF_WRAPPED = {"omarchy-launch-editor"}


def _has(cmd):
    return shutil.which(cmd) is not None


def _spawn(argv, cwd=None):
    if argv is None:
        return False
    if argv[0] not in _UWSM_SELF_WRAPPED and _has("uwsm-app"):
        argv = ["uwsm-app", "--", *argv]
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, start_new_session=True,
            stdin=_DEVNULL, stdout=_DEVNULL, stderr=_DEVNULL,
        )
        return proc.pid if hasattr(proc, "pid") else True
    except OSError:
        return False

def open_in_editor(path, config):
    editor = config.detected_editor()
    if editor is None:
        return open_folder(path, config)
    argv = [*editor, path]
    pid = _spawn(argv, cwd=path)
    if pid:
        return pid
    return open_folder(path, config)


def launch_tool(tool, path, config):
    """Open `path` with a registry Tool.

    GUI tools spawn directly (uwsm-scoped like every other launch); TUI
    tools run inside the user's configured terminal in the project
    directory. Executables are re-resolved at launch time so a tool that
    vanished since detection fails gracefully instead of raising.
    Returns pid/truthy when a process was started, else falsy.
    """
    if tool is None or not path:
        return False
    if getattr(tool, "in_terminal", False):
        command = tool.command_for()
        if not command:
            return False
        return open_in_terminal(path, config, command=command)
    argv = tool.argv_for(path)
    if argv is None:
        return False
    pid = _spawn(argv, cwd=path)
    if pid:
        return pid
    # Tool disappeared between menu build and launch: fall back to folder.
    return open_folder(path, config)


def open_in_terminal(path, config, command=None):
    argv = config.terminal_command(path)
    if argv is None:
        return False
    if command:
        # Validate command is still allowlisted before auto-executing
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9 _./:\-@]+", command):
            return False
        shell = _interactive_command(command)
        argv = [*argv, "bash", "-lc", shell]
    return _spawn(argv, cwd=path)


def build_terminal_argv(base_argv, command):
    """Construct terminal argv for tests: base + bash -lc wrapper.

    Returns list suitable for Popen. Validates command allowlist.
    """
    import re as _re
    if command and not _re.fullmatch(r"[A-Za-z0-9 _./:\-@]+", command):
        return None
    if command:
        shell = _interactive_command(command)
        return [*base_argv, "bash", "-lc", shell]
    return list(base_argv)


def _interactive_command(command):
    banner = shlex.quote("> " + command)
    # Auto-execute command and keep shell open: banner, command, exec bash
    return f"printf '\\033[1;36m%s\\033[0m\\n' {banner}; {command}; exec bash"


def open_folder(path, config=None):
    cmd = config.file_manager_command(path) if config else None
    if cmd is None:
        cmd = ["xdg-open", path] if _has("xdg-open") else None
    pid = _spawn(cmd, cwd=path)
    return pid if pid else False


def copy_path(path, clipboard=None):
    if clipboard is not None:
        try:
            clipboard.set_text(path)
            return True
        except Exception:
            pass
    for copier in (("wl-copy", None), ("xclip", ["-selection", "clipboard"]), ("xsel", ["-b"])):
        name, args = copier
        if _has(name):
            try:
                proc = subprocess.Popen(
                    [name, *(args or [])], stdin=subprocess.PIPE,
                    stdout=_DEVNULL, stderr=_DEVNULL,
                )
                proc.communicate(path.encode("utf-8"), timeout=3)
                return proc.returncode == 0
            except (OSError, subprocess.SubprocessError):
                continue
    return False
