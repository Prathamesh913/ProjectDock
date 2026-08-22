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
        subprocess.Popen(
            argv, cwd=cwd, start_new_session=True,
            stdin=_DEVNULL, stdout=_DEVNULL, stderr=_DEVNULL,
        )
        return True
    except OSError:
        return False


def open_in_editor(path, config):
    editor = config.detected_editor()
    if editor is None:
        return open_folder(path, config)
    argv = [*editor, path]
    if _spawn(argv, cwd=path):
        return True
    return open_folder(path, config)


def open_in_terminal(path, config, command=None):
    argv = config.terminal_command(path)
    if argv is None:
        return False
    if command:
        shell = _interactive_command(command)
        argv = [*argv, "bash", "-lc", shell]
    return _spawn(argv, cwd=path)


def _interactive_command(command):
    banner = shlex.quote("> " + command)
    return f"printf '\\033[1;36m%s\\033[0m\\n' {banner}; {command}; exec bash"


def open_folder(path, config=None):
    cmd = config.file_manager_command(path) if config else None
    if cmd is None:
        cmd = ["xdg-open", path] if _has("xdg-open") else None
    return _spawn(cmd, cwd=path)


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
