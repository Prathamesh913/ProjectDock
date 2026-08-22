"""Persistent state: pins, recents and the project cache.

Stored as JSON in ~/.local/state/projectdock/state.json (plus a separate
cache.json for the scanned project list so large writes are cheap).
"""

import json
import os
import time
from dataclasses import dataclass, field

from . import paths

MAX_RECENTS = 25


@dataclass
class State:
    pins: list = field(default_factory=list)
    recents: list = field(default_factory=list)
    projects: list = field(default_factory=list)
    scanned_at: float = 0.0
    root_mtimes: dict = field(default_factory=dict)
    workspace: dict = field(default_factory=dict)

    def pin(self, path):
        if path in self.pins:
            return
        self.pins.insert(0, path)

    def unpin(self, path):
        if path in self.pins:
            self.pins.remove(path)

    def is_pinned(self, path):
        return path in self.pins

    def touch_recent(self, path):
        self.recents = [r for r in self.recents if r["path"] != path]
        self.recents.insert(0, {"path": path, "at": time.time()})
        self.recents = self.recents[:MAX_RECENTS]

    def recent_rank(self, path):
        for i, entry in enumerate(self.recents):
            if entry["path"] == path:
                return i
        return None

    def update_projects(self, projects, scanned_at, root_mtimes):
        self.projects = projects
        self.scanned_at = scanned_at
        self.root_mtimes = root_mtimes

    def annotate(self, projects):
        """Attach pinned / pin_order / recent_rank to project dicts."""
        pin_order = {p: i for i, p in enumerate(self.pins)}
        for project in projects:
            path = project["path"]
            project["pinned"] = path in pin_order
            project["pin_order"] = pin_order.get(path, 1 << 30)
            project["recent_rank"] = self.recent_rank(path)
        return projects


def load():
    state = State()
    try:
        with open(paths.state_file(), encoding="utf-8") as fh:
            data = json.load(fh)
        state.pins = [p for p in data.get("pins", []) if isinstance(p, str)]
        state.recents = [
            r for r in data.get("recents", [])
            if isinstance(r, dict) and isinstance(r.get("path"), str)
        ][:MAX_RECENTS]
        ws = data.get("workspace", {})
        if isinstance(ws, dict):
            # sanitize: keep only dict values with path string
            clean = {}
            for k, v in ws.items():
                if isinstance(k, str) and isinstance(v, dict):
                    clean[k] = v
            state.workspace = clean
    except (OSError, ValueError):
        pass
    try:
        with open(paths.cache_file(), encoding="utf-8") as fh:
            cache = json.load(fh)
        projects = cache.get("projects", [])
        if isinstance(projects, list):
            state.projects = [
                p for p in projects
                if isinstance(p, dict) and isinstance(p.get("path"), str)
            ]
        try:
            state.scanned_at = float(cache.get("scanned_at", 0.0))
        except (TypeError, ValueError):
            state.scanned_at = 0.0
        mtimes = cache.get("root_mtimes", {})
        if isinstance(mtimes, dict):
            state.root_mtimes = mtimes
    except (OSError, ValueError):
        pass
    return state


def save(state: State):
    paths.ensure_dirs()
    payload = {
        "pins": state.pins,
        "recents": state.recents,
        "workspace": state.workspace if isinstance(getattr(state, "workspace", None), dict) else {},
    }
    _atomic_write(paths.state_file(), payload)

    cache_payload = {
        "scanned_at": state.scanned_at,
        "root_mtimes": state.root_mtimes,
        "projects": state.projects,
    }
    _atomic_write(paths.cache_file(), cache_payload)


def _atomic_write(target, payload):
    tmp = f"{target}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, target)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
