"""Project discovery: recursive scan of configured roots.

Scans are bounded by a depth limit and a directory budget, skip generated /
dependency directories, and stop descending as soon as a project root is
found. Results are a list of dicts:

    {"path": ..., "name": ..., "kind": ..., "is_git": ...}
"""

import os
import time
from dataclasses import dataclass, field

from . import cover as _cover
from . import markers

MAX_DIRS_PER_SCAN = 8000


@dataclass
class ScanResult:
    projects: list = field(default_factory=list)
    scanned_at: float = 0.0
    root_mtimes: dict = field(default_factory=dict)
    errors: int = 0


def root_mtimes(roots):
    result = {}
    for root in roots:
        try:
            result[root] = os.stat(root).st_mtime
        except OSError:
            result[root] = 0.0
    return result


def scan(roots, max_depth=4):
    """Scan roots for projects. Never raises."""
    result = ScanResult(scanned_at=time.time(), root_mtimes=root_mtimes(roots))
    seen_real = set()
    budget = {"dirs": 0}

    for root in roots:
        if not os.path.isdir(root):
            continue
        _scan_dir(root, 0, max_depth, seen_real, budget, result)

    result.projects.sort(key=lambda p: p["name"].lower())
    return result


def _scan_dir(path, depth, max_depth, seen_real, budget, result):
    budget["dirs"] += 1
    if budget["dirs"] > MAX_DIRS_PER_SCAN:
        result.errors += 1
        return

    real = os.path.realpath(path)
    if real in seen_real:
        return
    seen_real.add(real)

    try:
        entries = list(os.scandir(path))
    except OSError:
        result.errors += 1
        return

    if _is_project_entries(entries):
        result.projects.append(_describe(path, entries))
        return

    if depth >= max_depth:
        return

    for entry in entries:
        if not entry.is_dir(follow_symlinks=True):
            continue
        if markers.is_ignored_dir(entry.name):
            continue
        _scan_dir(entry.path, depth + 1, max_depth, seen_real, budget, result)


def _is_project_entries(entries):
    return any(markers.name_is_marker(entry.name) for entry in entries)


def _describe(path, entries):
    name = os.path.basename(os.path.normpath(path)) or path
    is_git = any(e.name == ".git" for e in entries)
    kind = markers.detect(path)
    return {
        "path": path,
        "name": name,
        "kind": kind.id,
        "label": kind.label,
        "icon": kind.icon,
        "color": kind.color,
        "is_git": is_git,
        "cover": _cover.discover_cover(path),
    }


def roots_changed(roots, cached_mtimes):
    if not roots:
        return False
    current = root_mtimes(roots)
    if set(current) != set(cached_mtimes):
        return True
    return any(current[r] != cached_mtimes.get(r) for r in current)
