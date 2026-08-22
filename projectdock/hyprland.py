"""Hyprland window awareness – safe read-only inspection.

Uses ``hyprctl clients -j`` and ``/proc/<pid>/cwd``.
Never raises, never trusts window titles alone.
"""

import json
import os
import subprocess
import shutil

def _has_hyprctl():
    return shutil.which("hyprctl") is not None

def clients():
    """Return list of client dicts or [] on failure."""
    if not _has_hyprctl():
        return []
    try:
        proc = subprocess.run(
            ["hyprctl", "clients", "-j"],
            capture_output=True, text=True, timeout=2.0,
        )
        if proc.returncode != 0:
            return []
        data = json.loads(proc.stdout or "[]")
        if not isinstance(data, list):
            return []
        # sanitize: ensure each has address/pid
        out = []
        for c in data:
            if not isinstance(c, dict):
                continue
            addr = c.get("address")
            pid = c.get("pid")
            if not addr or not isinstance(pid, int):
                continue
            out.append(c)
        return out
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return []

def cwd_for_pid(pid):
    """Return cwd for pid via /proc/<pid>/cwd or None."""
    try:
        pid_int = int(pid)
    except (ValueError, TypeError):
        return None
    link = f"/proc/{pid_int}/cwd"
    try:
        # os.readlink follows symlink, may raise permission error
        target = os.readlink(link)
        # validate it is a directory
        if os.path.isdir(target):
            return os.path.realpath(target)
        # even if not dir, return realpath
        return os.path.realpath(target)
    except OSError:
        return None

def focus_window(address):
    """Focus window by address using safe structured call. Returns bool."""
    if not address or not isinstance(address, str):
        return False
    if not _has_hyprctl():
        return False
    # validate address looks like 0x...
    if not address.startswith("0x"):
        return False
    # allow only hex chars after 0x
    hexpart = address[2:]
    if not hexpart or not all(c in "0123456789abcdefABCDEF" for c in hexpart):
        return False
    try:
        proc = subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", f"address:{address}"],
            capture_output=True, text=True, timeout=1.5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

def associate_clients_to_projects(clients_list, projects, cwd_resolver=None):
    """Associate clients to projects via cwd.

    Returns dict project_path -> list of client dicts (most recent first if we sort by focus?).
    Confidence: only associate if cwd is inside project path (prefix with / boundary).
    Chooses longest matching project path for nested projects.
    """
    if not clients_list or not projects:
        return {}
    resolver = cwd_resolver or cwd_for_pid
    # map project path -> project dict for quick lookup, sorted longest first for precedence
    sorted_projects = sorted(projects, key=lambda p: len(p.get("path","")), reverse=True)
    assoc = {}
    for client in clients_list:
        pid = client.get("pid")
        if pid is None:
            continue
        cwd = resolver(pid)
        if not cwd:
            continue
        # normalize cwd
        cwd = os.path.normpath(cwd)
        for proj in sorted_projects:
            ppath = os.path.normpath(proj.get("path",""))
            if not ppath:
                continue
            # need prefix match with boundary: cwd == ppath or cwd starts with ppath + "/"
            if cwd == ppath or cwd.startswith(ppath + os.sep):
                assoc.setdefault(proj["path"], []).append(client)
                break
    return assoc
