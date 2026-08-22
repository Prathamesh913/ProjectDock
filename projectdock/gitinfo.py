"""Lightweight Git metadata for the selected project.

Only the current branch and a clean/dirty flag for the fast path.
An extended health check (untracked, ahead/behind) is available via
``health()`` without regressing the simple ``info()`` API. Everything is
cached per path with a short TTL so repeated redraws stay cheap. Any
failure degrades to None - ProjectDock never becomes a Git client.
"""

import subprocess
import threading
import time
from dataclasses import dataclass

CACHE_TTL = 10.0

_cache = {}
_health_cache = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class GitHealth:
    branch: str
    dirty: bool
    untracked: int = 0
    ahead: int = 0
    behind: int = 0
    clean: bool = True


def git_available():
    import shutil
    return shutil.which("git") is not None


def info(path):
    """Return (branch, dirty) or None when unavailable. Kept for compatibility."""
    with _lock:
        cached = _cache.get(path)
        if cached and time.time() - cached["at"] < CACHE_TTL:
            return cached["value"]

    value = _fetch(path)
    with _lock:
        _cache[path] = {"at": time.time(), "value": value}
    return value


def health(path):
    """Return GitHealth or None. Extends info with untracked/ahead/behind."""
    with _lock:
        cached = _health_cache.get(path)
        if cached and time.time() - cached["at"] < CACHE_TTL:
            return cached["value"]
    value = _fetch_health(path)
    with _lock:
        _health_cache[path] = {"at": time.time(), "value": value}
    return value


def invalidate(path=None):
    with _lock:
        if path is None:
            _cache.clear()
            _health_cache.clear()
        else:
            _cache.pop(path, None)
            _health_cache.pop(path, None)


def _fetch(path):
    if not git_available():
        return None
    try:
        branch_proc = subprocess.run(
            ["git", "-C", path, "branch", "--show-current"],
            capture_output=True, text=True, timeout=1.5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if branch_proc.returncode != 0:
        return None
    branch = branch_proc.stdout.strip()
    if not branch:
        try:
            rev = subprocess.run(
                ["git", "-C", path, "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=1.5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if rev.returncode != 0:
            return None
        branch = rev.stdout.strip()[:12] or "detached"

    try:
        status = subprocess.run(
            ["git", "-C", path, "status", "--porcelain"],
            capture_output=True, text=True, timeout=1.5,
        )
    except (OSError, subprocess.SubprocessError):
        return (branch, False)
    dirty = status.returncode == 0 and bool(status.stdout.strip())
    return (branch, dirty)


def _fetch_health(path):
    base = _fetch(path)
    if base is None:
        return None
    branch, dirty = base
    untracked = 0
    ahead = 0
    behind = 0
    # untracked count via status porcelain '??' lines – cheap
    try:
        status = subprocess.run(
            ["git", "-C", path, "status", "--porcelain"],
            capture_output=True, text=True, timeout=1.5,
        )
        if status.returncode == 0:
            for line in status.stdout.splitlines():
                if line.startswith("??"):
                    untracked += 1
    except (OSError, subprocess.SubprocessError):
        pass
    # ahead/behind via rev-list
    try:
        # only if upstream exists
        up = subprocess.run(
            ["git", "-C", path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            capture_output=True, text=True, timeout=1.0,
        )
        if up.returncode == 0 and up.stdout.strip():
            lr = subprocess.run(
                ["git", "-C", path, "rev-list", "--left-right", "--count", "HEAD...@{u}"],
                capture_output=True, text=True, timeout=1.5,
            )
            if lr.returncode == 0:
                parts = lr.stdout.strip().split()
                if len(parts) == 2:
                    ahead = int(parts[0])
                    behind = int(parts[1])
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    clean = not dirty
    return GitHealth(branch=branch, dirty=dirty, untracked=untracked, ahead=ahead, behind=behind, clean=clean)
