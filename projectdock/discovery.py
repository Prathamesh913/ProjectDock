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
    """Scan roots for projects. Never raises.

    Only marker-based directories are discovered. Empty directories are
    handled at the app layer via state preservation and targeted root
    supplement, so library-level scans remain predictable for tests.
    """
    result = ScanResult(scanned_at=time.time(), root_mtimes=root_mtimes(roots))
    seen_real = set()
    budget = {"dirs": 0}

    for root in roots:
        if not os.path.isdir(root):
            continue
        _scan_dir(root, 0, max_depth, seen_real, budget, result)

    result.projects.sort(key=lambda p: p["name"].lower())
    return result


def scan_root(root, max_depth=4):
    """Scan a single root for projects. Never raises.

    Returns ScanResult with only projects under `root`.
    """
    return scan([root], max_depth=max_depth)


def refresh_project(path):
    """Refresh metadata for a single project path.

    Returns project dict or None. Used for targeted project rescan.
    """
    return describe_project(path)


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


def describe_project(path):
    """Describe a single project path regardless of markers.

    Returns project dict or None if path is not a directory or is ignored.
    Handles generic/empty projects.
    """
    if not path or not os.path.isdir(path):
        return None
    basename = os.path.basename(os.path.normpath(path))
    if markers.is_ignored_dir(basename):
        return None
    # Ignore hidden directories unless they contain markers? Keep consistent.
    if basename.startswith(".") and not os.path.exists(os.path.join(path, ".git")):
        # hidden dirs are ignored by markers logic; treat as not project unless is_git
        return None
    try:
        entries = list(os.scandir(path))
    except OSError:
        return None
    is_git = any(e.name == ".git" for e in entries)
    kind = markers.detect(path)
    return {
        "path": path,
        "name": basename or path,
        "kind": kind.id,
        "label": kind.label,
        "icon": kind.icon,
        "color": kind.color,
        "is_git": is_git,
        "cover": _cover.discover_cover(path),
    }


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
