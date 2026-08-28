"""Active workspace: lightweight project session tracking.

Tracks ProjectDock-launched activity (editor/terminal/dev etc) and
ephemeral Hyprland window association. Persisted via state.json
``workspace`` dict; window addresses are ephemeral and validated on refresh.

Design goals: false negatives preferred over false positives, never crash,
never scan arbitrary processes, bounded refresh.
"""

import os
import re
import time
from dataclasses import dataclass, field, asdict
import json

from . import hyprland

_TOOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _sanitize_preferred_tool(entry):
    """Drop malformed preferred_tool fields; keep sane ones.

    Migration-safe: unknown/missing/corrupt values simply leave no
    preference behind. The id is only shape-validated here; availability is
    re-checked against the tool registry at read time.
    """
    pref = entry.get("preferred_tool")
    if pref is None:
        return
    if not isinstance(pref, str) or not _TOOL_ID_RE.match(pref):
        entry.pop("preferred_tool", None)

@dataclass
class WorkspaceEntry:
    path: str
    last_active_at: float = 0.0
    last_action: str = ""  # e.g., "open", "editor", "terminal", "dev", "test"
    editor: str = ""
    terminal_cmd: str = ""
    # ephemeral, not persisted long-term; kept in memory only
    window_addresses: list = field(default_factory=list)

class WorkspaceStore:
    def __init__(self, state=None):
        self._state = state
        self._ephemeral_active = {}  # path -> list of client dicts
        self._last_refresh = 0.0

    def load_from_state(self, state):
        self._state = state
        # ensure workspace dict exists
        if not hasattr(state, "workspace"):
            state.workspace = {}
        if not isinstance(state.workspace, dict):
            state.workspace = {}
        # sanitize persisted preferred tools (migration-safe)
        for entry in self._state.workspace.values():
            if isinstance(entry, dict):
                _sanitize_preferred_tool(entry)

    def _ensure_state(self):
        if self._state is None:
            return
        if not hasattr(self._state, "workspace"):
            self._state.workspace = {}
        if not isinstance(self._state.workspace, dict):
            self._state.workspace = {}

    def _sanitize_usage(self, entry):
        # keep bounded usage
        usage = entry.get("action_usage")
        if not isinstance(usage, dict):
            entry["action_usage"] = {}
            return
        # filter to sane keys/capped counts
        clean = {}
        for k, v in usage.items():
            if not isinstance(k, str) or not k or len(k) > 128:
                continue
            try:
                iv = int(v)
            except Exception:
                continue
            iv = max(0, min(20, iv))
            if iv > 0:
                clean[k] = iv
        # cap distinct actions to 10, keep highest counts
        if len(clean) > 10:
            sorted_items = sorted(clean.items(), key=lambda kv: -kv[1])
            clean = dict(sorted_items[:10])
        entry["action_usage"] = clean
        # action_last_used sanitized
        last_used = entry.get("action_last_used")
        if not isinstance(last_used, dict):
            entry["action_last_used"] = {}
        else:
            clean2 = {}
            for k, v in last_used.items():
                if not isinstance(k, str) or len(k) > 128:
                    continue
                try:
                    fv = float(v)
                except Exception:
                    continue
                clean2[k] = fv
            entry["action_last_used"] = clean2
        # keep preferred tool sane on every write as well
        _sanitize_preferred_tool(entry)

    def _record_usage(self, path, action_id):
        if not path or not action_id:
            return
        self._ensure_state()
        entry = self._state.workspace.get(path)
        if not isinstance(entry, dict):
            entry = {}
        usage = entry.get("action_usage")
        if not isinstance(usage, dict):
            usage = {}
        # increment capped
        cnt = int(usage.get(action_id, 0)) + 1
        cnt = min(20, cnt)
        usage[action_id] = cnt
        entry["action_usage"] = usage
        last_used = entry.get("action_last_used")
        if not isinstance(last_used, dict):
            last_used = {}
        last_used[action_id] = time.time()
        entry["action_last_used"] = last_used
        self._sanitize_usage(entry)
        self._state.workspace[path] = entry

    def record(self, path, action="open", editor="", terminal_cmd=""):
        """Record ProjectDock-launched activity for a project."""
        if not path:
            return
        self._ensure_state()
        now = time.time()
        entry = self._state.workspace.get(path)
        if not isinstance(entry, dict):
            entry = {}
        entry["last_active_at"] = now
        entry["last_used_at"] = now
        entry["last_action"] = action
        if editor:
            entry["editor"] = editor
            entry["preferred_editor"] = editor
        if terminal_cmd:
            entry["terminal_cmd"] = terminal_cmd
            # remember last intelligence terminal command
            entry["last_terminal_cmd"] = terminal_cmd
        # also keep path for sanity
        entry["path"] = path
        # usage tracking for meaningful actions
        # track both generic and intelligence
        self._state.workspace[path] = entry
        # record usage after storing base entry
        self._record_usage(path, action)
        self._sanitize_usage(entry)
        # keep ephemeral windows untouched

    def get_preferred_tool_id(self, path):
        """Sanitized persisted tool id for a project, or empty string.

        Shape-validated only; callers re-validate availability against the
        tool registry so an uninstalled tool is never launched.
        """
        self._ensure_state()
        entry = self._state.workspace.get(path, {}) if hasattr(self._state, "workspace") else {}
        if not isinstance(entry, dict):
            return ""
        pref = entry.get("preferred_tool")
        if isinstance(pref, str) and _TOOL_ID_RE.match(pref):
            return pref
        return ""

    def set_preferred_tool(self, path, tool_id):
        """Remember which tool the user explicitly chose for a project."""
        if not path or not isinstance(tool_id, str) or not _TOOL_ID_RE.match(tool_id):
            return
        self._ensure_state()
        entry = self._state.workspace.get(path)
        if not isinstance(entry, dict):
            entry = {"path": path}
        entry["preferred_tool"] = tool_id
        entry["path"] = path
        self._state.workspace[path] = entry

    def get_preferred_editor(self, path):
        self._ensure_state()
        entry = self._state.workspace.get(path, {}) if hasattr(self._state, "workspace") else {}
        if not isinstance(entry, dict):
            return ""
        pref = entry.get("preferred_editor") or entry.get("editor") or ""
        if not isinstance(pref, str):
            return ""
        pref = pref.strip()
        # validate editor still available (lightweight)
        if pref:
            import shutil
            base = pref.split()[0]
            if base and shutil.which(base):
                return pref
            # if not found, fallback gracefully
            return ""
        return ""

    def get_preferred_primary(self, path, valid_ids):
        """Return most frequent valid action id from usage, or None."""
        self._ensure_state()
        entry = self._state.workspace.get(path, {}) if hasattr(self._state, "workspace") else {}
        if not isinstance(entry, dict):
            return None
        usage = entry.get("action_usage")
        if not isinstance(usage, dict) or not valid_ids:
            return None
        # filter to valid ids
        candidates = [(act, usage.get(act, 0)) for act in valid_ids if act in usage]
        if not candidates:
            return None
        # also consider recency tie-break
        last_used = entry.get("action_last_used", {}) if isinstance(entry.get("action_last_used"), dict) else {}
        def rank(item):
            act, cnt = item
            ts = float(last_used.get(act, 0)) if isinstance(last_used.get(act), (int, float)) else 0
            return (cnt, ts)
        candidates.sort(key=rank, reverse=True)
        top = candidates[0][0]
        # revalidate
        if top in valid_ids:
            return top
        return None

    def ranked_quick_candidates(self, path, available_ids):
        """Return usage-ranked valid ids for quick actions."""
        self._ensure_state()
        entry = self._state.workspace.get(path, {}) if hasattr(self._state, "workspace") else {}
        usage = entry.get("action_usage", {}) if isinstance(entry, dict) else {}
        last_used = entry.get("action_last_used", {}) if isinstance(entry, dict) else {}
        if not isinstance(usage, dict):
            return []
        valid = [a for a in available_ids if a in usage]
        def rank(act):
            cnt = int(usage.get(act, 0))
            ts = float(last_used.get(act, 0)) if isinstance(last_used, dict) else 0
            return (cnt, ts)
        valid.sort(key=rank, reverse=True)
        return valid

    def persist(self):
        """Caller can invoke to save state after record."""
        if self._state is None:
            return
        try:
            from . import state as state_mod
            state_mod.save(self._state)
        except Exception:
            pass

    def is_active(self, path):
        """True if project has live window or very recent launch (5 min window for fallback)."""
        # ephemeral window wins
        if path in self._ephemeral_active and self._ephemeral_active[path]:
            return True
        # recent launch fallback: within 30 minutes and last_action known
        self._ensure_state()
        entry = self._state.workspace.get(path) if hasattr(self._state, "workspace") else None
        if isinstance(entry, dict):
            last = entry.get("last_active_at", 0)
            if last and (time.time() - last) < 30*60:
                # only consider if we have no hyprctl case; if hyprctl available but no window, don't mark active
                # To avoid false positives, only treat as active if we recently launched terminal/editor/dev
                # When hyprctl is available, prefer window-based only; so check if hyprland clients were refreshed recently
                # If we have ever refreshed, window absence means not active – don't fallback
                if self._last_refresh == 0:
                    return True
                # if refreshed and no window, not active
                return False
            # if never refreshed, fallback allows recent
            if self._last_refresh == 0 and last and (time.time() - last) < 30*60:
                return True
        return False

    def windows_for(self, path):
        return list(self._ephemeral_active.get(path, []))

    def most_recent_window(self, path):
        wins = self._ephemeral_active.get(path, [])
        if not wins:
            return None
        # assume first is most relevant (hyprctl order is not guaranteed, but we keep insertion order)
        # Could sort by workspace focusHistoryID if present – keep simple
        return wins[0]

    def active_paths(self):
        return [p for p, wins in self._ephemeral_active.items() if wins]

    def refresh(self, projects):
        """Query hyprland clients and associate to known projects.

        Bounded, safe, never raises. Updates ephemeral map and validates stale entries.
        Should be called when launcher opens or on explicit rescan.
        """
        self._last_refresh = time.time()
        clients = hyprland.clients()
        if not clients:
            # clear ephemeral if no clients (e.g., hyprctl unavailable)
            self._ephemeral_active = {}
            return {}
        assoc = hyprland.associate_clients_to_projects(clients, projects)
        # validate: only keep projects that still exist in current project list
        known_paths = {p.get("path") for p in projects}
        filtered = {k: v for k, v in assoc.items() if k in known_paths}
        self._ephemeral_active = filtered
        # update last_active_at for active projects (touch)
        now = time.time()
        for path in filtered:
            self._ensure_state()
            entry = self._state.workspace.get(path, {}) if hasattr(self._state, "workspace") else {}
            if not isinstance(entry, dict):
                entry = {}
            # don't overwrite last_active_at if already recent, but ensure at least now if not recent
            # keep original last_active for historical, but bump if window active
            entry["last_active_at"] = max(float(entry.get("last_active_at", 0)), now)
            entry["path"] = path
            if hasattr(self._state, "workspace"):
                self._state.workspace[path] = entry
        return filtered

    def cleanup_stale(self, projects):
        """Remove ephemeral entries for projects no longer known."""
        known = {p.get("path") for p in projects}
        self._ephemeral_active = {k: v for k, v in self._ephemeral_active.items() if k in known}
        # also prune workspace dict for deleted projects? Keep historical but cap size
        if hasattr(self._state, "workspace") and isinstance(self._state.workspace, dict):
            # keep max 100 entries, prune oldest
            if len(self._state.workspace) > 100:
                sorted_items = sorted(self._state.workspace.items(), key=lambda kv: kv[1].get("last_active_at", 0) if isinstance(kv[1], dict) else 0)
                # keep newest 100
                self._state.workspace = dict(sorted_items[-100:])

    def last_editor_for(self, path):
        self._ensure_state()
        entry = self._state.workspace.get(path, {}) if hasattr(self._state, "workspace") else {}
        if isinstance(entry, dict):
            return entry.get("editor", "")
        return ""

    def annotate_projects(self, projects):
        """Add active flag, active_rank and usage for search/sorted."""
        active_list = []
        for p in projects:
            if self.is_active(p.get("path")):
                entry = self._state.workspace.get(p.get("path"), {}) if hasattr(self._state, "workspace") else {}
                ts = float(entry.get("last_active_at", 0)) if isinstance(entry, dict) else 0
                if p.get("path") in self._ephemeral_active:
                    ts = max(ts, self._last_refresh)
                active_list.append((ts, p))
        active_list.sort(key=lambda x: -x[0])
        active_order = {p["path"]: i for i, (_, p) in enumerate(active_list)}
        for p in projects:
            path = p.get("path")
            p["active"] = path in active_order
            p["active_rank"] = active_order.get(path)
            p["active_windows"] = len(self._ephemeral_active.get(path, []))
            # usage tie-breaker
            entry = self._state.workspace.get(path, {}) if hasattr(self._state, "workspace") else {}
            if isinstance(entry, dict):
                usage = entry.get("action_usage", {})
                if isinstance(usage, dict):
                    total = sum(int(v) for v in usage.values() if isinstance(v, int))
                    p["usage_count"] = total
                else:
                    p["usage_count"] = 0
            else:
                p["usage_count"] = 0
        return projects
