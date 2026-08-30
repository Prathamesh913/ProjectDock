"""Application controller: owns config/state, runs the scan in the
background and glues the UI to the system actions.

Instances are unique per session via GApplication's DBus registration, so
`projectdock toggle` from a global keybinding talks to the running daemon
and the window shows/hides instantly. If no session bus is available the
app degrades to a plain single window.
"""

import os
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gtk, GLib, Gio

from . import actions, commands, config, creation, discovery, hyprland, intelligence, search, sessions, state, theme, tools, ui, workspace

APP_ID = "dev.projectdock.ProjectDock"

FOCUS_POLL_MS = 400
FOCUS_GRACE_MS = 700


class DockApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.cfg = config.load()
        self.state = state.load()
        self.window = None
        self.scanning = False
        self._scan_thread = None
        self._focus_poll = None
        self._shown_at = 0.0
        self._theme = None
        self.palette = None
        self._held = False
        # Transient footer status (e.g. "Rescanning projects…").
        # Cleared by _clear_status_message or by the next user action.
        self._status_message = ""
        self._status_source = ""
        self._status_clear_at = 0.0
        self._rescan_started_at = 0.0
        self.workspace = workspace.WorkspaceStore(self.state)
        self.workspace.load_from_state(self.state)
        self.sessions = sessions.SessionStore()
        # Async workspace refresh tracking
        self._workspace_refresh_thread = None
        # Annotation cache: avoids re-annotating on every keystroke.
        # Only invalidated when project list / pins / recents change.
        self._annotation_cache = None  # (version_key, annotated_list)
        self._annotation_version = 0  # bumped on state changes

    # ------------------------------------------------------------- gtk app

    def _hold_once(self):
        if not self._held:
            self._held = True
            self.hold()

    def do_command_line(self, command_line):
        args = command_line.get_arguments()
        action = args[1].lower() if len(args) > 1 else "toggle"
        if action in ("quit", "exit"):
            self.quit()
            return 0
        self._hold_once()
        self._handle_command(action)
        return 0

    def do_activate(self):
        self._hold_once()
        self._handle_command("show")

    def _handle_command(self, action):
        if action in ("quit", "exit"):
            self.quit()
            return
        if action == "hide":
            self.hide_window()
            return
        if action == "rescan":
            self._ensure_window()
            self.rescan()
            return
        if self.window is not None and self.window.get_visible():
            self.hide_window()
        else:
            self._show()

    # ------------------------------------------------------------- window

    def _ensure_window(self):
        if self.window is not None:
            return
        if self.palette is None:
            self.palette = theme.load_palette()
        css = theme.build_css(self.palette)
        self.window = ui.LauncherWindow(self, self.cfg, self.palette, css)

    def hide_window(self):
        """Hide the launcher window; the next show re-maps the same window.

        The window must NOT be destroyed here. Destroying and recreating a
        gtk4-layer-shell surface within one process intermittently produces
        a zombie surface: GTK reports it mapped and visible, but the
        compositor never receives a first frame (alpha 0) and never grants
        it keyboard focus — typed characters then leak into whatever window
        sits underneath. gtk_window_hide() fully unmaps the layer surface,
        so every later show is still a brand-new map as far as Hyprland is
        concerned, while the widget tree stays valid.
        """
        self._stop_focus_poll()
        if self.window is not None:
            try:
                self.window.hide()
            except Exception:
                pass

    def _show(self):
        self._ensure_window()
        # Show window immediately from cached state — no blocking I/O.
        self.window.show_dock()
        self._shown_at = time.monotonic()
        self._start_focus_poll()
        # Refresh workspace awareness asynchronously so the UI is not
        # blocked by hyprctl or /proc reads.
        self._refresh_workspace_async()
        self._maybe_rescan()

    # ---- async workspace refresh ----

    def _refresh_workspace_async(self):
        """Kick off a background workspace refresh. Guards against storms.

        At most one refresh thread runs at a time. If a refresh is
        already in flight we bump the generation so the in-flight
        result is discarded when it arrives, and start a fresh one.
        """
        if self.workspace._refresh_in_flight:
            # Bump generation so the in-flight result is stale.
            self.workspace.start_refresh()
        gen = self.workspace.start_refresh()
        projects = list(self.state.projects)
        def _bg():
            try:
                result = self.workspace.collect(projects)
            except Exception:
                result = None
            GLib.idle_add(self._apply_workspace_refresh, result, gen)
        self._workspace_refresh_thread = threading.Thread(
            target=_bg, daemon=True)
        self._workspace_refresh_thread.start()

    def _apply_workspace_refresh(self, result, gen):
        """Apply async workspace result on the GTK main thread."""
        # Discard if generation moved on (a newer refresh started).
        if gen != self.workspace._refresh_generation:
            return GLib.SOURCE_REMOVE
        self.workspace.apply(result)
        self.workspace.cleanup_stale(self.state.projects)
        self.invalidate_annotation()
        # Rebuild UI to reflect updated active/focus state
        if self.window is not None and self.window.get_visible():
            self.window.rebuild()
        return GLib.SOURCE_REMOVE

    def _start_focus_poll(self):
        if self._focus_poll is not None:
            return
        self._focus_poll = GLib.timeout_add(FOCUS_POLL_MS, self._poll_focus)

    def _stop_focus_poll(self):
        if self._focus_poll is not None:
            GLib.source_remove(self._focus_poll)
            self._focus_poll = None

    def _poll_focus(self):
        window = self.window
        if window is None or not window.get_visible():
            self._focus_poll = None
            return GLib.SOURCE_REMOVE
        if not self.cfg.hide_on_focus_loss:
            self._focus_poll = None
            return GLib.SOURCE_REMOVE
        elapsed = time.monotonic() - self._shown_at
        if elapsed > (FOCUS_GRACE_MS / 1000) and not window.is_active():
            window.hide_dock()
            self._focus_poll = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # ------------------------------------------------------------- project list

    def projects_for_query(self, query):
        annotated = self._get_annotated_projects()
        if query:
            return search.filter_and_rank(query, annotated)
        return search.sorted_by_activity(annotated)

    def _get_annotated_projects(self):
        """Return annotated project list, using cache when inputs unchanged."""
        ver = self._annotation_version
        cached = self._annotation_cache
        if cached is not None and cached[0] == ver:
            return cached[1]
        # Rebuild from scratch
        projects = [dict(p) for p in self.state.projects]
        self.state.annotate(projects)
        try:
            self.workspace.annotate_projects(projects)
        except Exception:
            pass
        self._annotation_cache = (ver, projects)
        return projects

    def invalidate_annotation(self):
        """Mark annotation cache as stale. Call after project list / pins /
        recents / workspace state changes."""
        self._annotation_version += 1
        self._annotation_cache = None

    def project_count(self):
        return len(self.state.projects)

    # ------------------------------------------------------------- scanning

    def _set_status_message(self, text, source="", clear_after=0.0):
        """Set a transient footer message (e.g. "Rescanning projects…").

        ``source`` records what produced the message so a different
        consumer (manual vs monitor) can refresh the footer correctly. The
        message can also be auto-cleared after ``clear_after`` seconds when
        the source wants the footer to revert automatically.
        """
        self._status_message = text or ""
        self._status_source = source or ""
        self._status_clear_at = (time.time() + clear_after) if clear_after > 0 else 0.0
        win = getattr(self, "window", None)
        if win is not None:
            try:
                win._update_footer()
            except Exception:
                pass

    def _clear_status_message(self, source=None):
        if source is not None and self._status_source and source != self._status_source:
            return
        self._status_message = ""
        self._status_source = ""
        self._status_clear_at = 0.0
        win = getattr(self, "window", None)
        if win is not None:
            try:
                win._update_footer()
            except Exception:
                pass

    def status_message(self):
        """Return the current transient footer text, honouring auto-clear."""
        if not self._status_message:
            return ""
        if self._status_clear_at and time.time() >= self._status_clear_at:
            self._status_message = ""
            self._status_source = ""
            self._status_clear_at = 0.0
        return self._status_message

    def _maybe_rescan(self):
        if self.state.projects and not discovery.roots_changed(
                self.cfg.expanded_roots(), self.state.root_mtimes):
            elapsed = time.time() - self.state.scanned_at
            if elapsed < self.cfg.rescan_minutes * 60:
                return
        self.rescan()

    def rescan(self):
        self.rescan_all()

    def rescan_all(self):
        if self.scanning:
            return
        self._rescan_started_at = time.time()
        self._set_status_message("Rescanning projects\u2026", source="rescan")
        self._do_rescan_all()

    def _do_rescan_all(self):
        """Internal: actually kick off a global rescan without toggling status."""
        intelligence.invalidate()
        try:
            from . import gitinfo as _gi
            _gi.invalidate()
        except Exception:
            pass
        roots = self.cfg.expanded_roots()
        self.scanning = True
        if self.window is not None:
            self.window._update_footer()
        self._scan_thread = threading.Thread(
            target=self._scan_work, args=(roots,), daemon=True)
        self._scan_thread.start()

    def rescan_root(self, root=None):
        """Rescan a single root only. Never raises."""
        if self.scanning:
            return False
        if root is None:
            # pick root of selected project if possible
            root = None
            try:
                if self.window is not None:
                    row = self.window.listbox.get_selected_row()
                    proj = getattr(row, "project", None)
                    if proj:
                        root = self._root_for_path(proj.get("path"))
            except Exception:
                root = None
            if root is None:
                # fallback to global
                self.rescan_all()
                return True
        root = str(root)
        if not root or not os.path.isdir(root):
            return False
        self._rescan_started_at = time.time()
        self._set_status_message("Rescanning projects\u2026", source="rescan")
        self._do_rescan_root(root)
        return True

    def _do_rescan_root(self, root):
        """Internal: actually kick off a root rescan without toggling status."""
        intelligence.invalidate()
        try:
            from . import gitinfo as _gi
            _gi.invalidate(root)
        except Exception:
            pass
        self.scanning = True
        if self.window is not None:
            self.window._update_footer()
        self._scan_thread = threading.Thread(
            target=self._scan_root_work, args=(root,), daemon=True)
        self._scan_thread.start()

    def rescan_project(self, project):
        """Refresh a single project's metadata (intelligence, git, cover)."""
        path = project.get("path") if isinstance(project, dict) else str(project)
        if not path or not os.path.isdir(path):
            # project deleted externally: trigger root rescan to remove it
            try:
                root = self._root_for_path(path)
                if root:
                    self.rescan_root(root)
            except Exception:
                pass
            return False
        # Invalidate caches for this path
        intelligence.invalidate(path)
        try:
            from . import gitinfo as _gi
            _gi.invalidate(path)
        except Exception:
            pass
        # Refresh discovery for this project
        new_desc = discovery.refresh_project(path)
        if new_desc is None:
            return False
        # Update cached entry in state.projects
        updated = False
        for i, p in enumerate(self.state.projects):
            if p.get("path") == path:
                # preserve pinned/recents? state.update will handle but we do in-place
                # Keep original path, update kind/label/icon/color/is_git/cover/name
                merged = dict(p)
                merged.update(new_desc)
                self.state.projects[i] = merged
                updated = True
                break
        if not updated:
            # project not yet in cache (maybe generic empty not yet scanned) -> append
            self.state.projects.append(new_desc)
            self.state.projects.sort(key=lambda p: p.get("name","").lower())
        self.invalidate_annotation()
        state.save(self.state)
        if self.window is not None:
            self.window.rebuild()
        return True

    def _root_for_path(self, path):
        if not path:
            return None
        try:
            expanded = self.cfg.expanded_roots()
            best = None
            best_len = -1
            norm = os.path.normpath(path)
            for root in expanded:
                rn = os.path.normpath(root)
                if norm == rn or norm.startswith(rn + os.sep):
                    if len(rn) > best_len:
                        best = rn
                        best_len = len(rn)
            return best
        except Exception:
            return None

    def _scan_work(self, roots):
        try:
            result = discovery.scan(roots, max_depth=self.cfg.max_depth)
        except Exception:
            GLib.idle_add(self._scan_failed)
            return
        GLib.idle_add(self._scan_done, result)

    def _scan_root_work(self, root):
        try:
            result = discovery.scan_root(root, max_depth=self.cfg.max_depth)
        except Exception:
            GLib.idle_add(self._scan_failed)
            return
        GLib.idle_add(self._scan_root_done, result, root)

    def _scan_failed(self):
        # discovery.scan is designed not to raise, but if it ever does, keep
        # the previously cached projects instead of wiping them.
        self.scanning = False
        return GLib.SOURCE_REMOVE

    def _supplement_generic_children(self, root, existing_paths, discovered_paths):
        """Find new generic empty directories under root that are not yet tracked."""
        added = []
        try:
            # Build set of ancestors to avoid double-counting parent of nested project
            # e.g., if discovered contains /root/a/b/c, don't add /root/a
            discovered_set = set(discovered_paths)
            with os.scandir(root) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=True):
                        continue
                    if entry.path in existing_paths or entry.path in discovered_set:
                        continue
                    try:
                        from . import markers as _mk
                        if _mk.is_ignored_dir(entry.name):
                            continue
                        if entry.name.startswith("."):
                            continue
                        # Skip if entry is ancestor of any discovered project
                        is_ancestor = False
                        for dpath in discovered_set:
                            if dpath.startswith(entry.path + os.sep):
                                is_ancestor = True
                                break
                        if is_ancestor:
                            continue
                        desc = discovery.describe_project(entry.path)
                        if desc is not None:
                            added.append(desc)
                    except Exception:
                        continue
        except OSError:
            pass
        return added

    def _scan_done(self, result):
        self.scanning = False
        # Preserve existing generic empty projects that still exist but were not discovered (marker-only scan)
        try:
            discovered_paths = {p.get("path") for p in result.projects}
            keep_empty = []
            for p in self.state.projects:
                pp = p.get("path")
                if pp not in discovered_paths and pp and os.path.isdir(pp):
                    # Keep if still valid project (generic) and still inside a configured root
                    root = self._root_for_path(pp)
                    if root:
                        # Only preserve if describe still yields a project (not ignored)
                        desc = discovery.describe_project(pp)
                        if desc is not None:
                            # update with fresh metadata
                            merged = dict(p)
                            merged.update(desc)
                            keep_empty.append(merged)
                        else:
                            keep_empty.append(p)
            merged = result.projects + keep_empty
            merged.sort(key=lambda p: p.get("name","").lower())
            # Deduplicate by path
            seen = set()
            dedup = []
            for p in merged:
                pp = p.get("path")
                if pp not in seen:
                    seen.add(pp)
                    dedup.append(p)
            self.state.update_projects(dedup, result.scanned_at, result.root_mtimes)
            try:
                self.workspace.cleanup_stale(self.state.projects)
            except Exception:
                pass
        except Exception:
            self.state.update_projects(
                result.projects, result.scanned_at, result.root_mtimes)
        self.invalidate_annotation()
        state.save(self.state)
        # Replace transient "Rescanning…" with a brief confirmation that
        # auto-clears so the footer returns to its normal hints. A monitor-
        # driven rescan still surfaces the message; a global rescan can
        # always be manually triggered again.
        try:
            count = len(self.state.projects)
            self._set_status_message(
                f"Projects updated \u00b7 {count}",
                source="rescan", clear_after=1.6)
        except Exception:
            self._set_status_message("Projects updated",
                                    source="rescan", clear_after=1.6)
        if self.window is not None:
            self.window.rebuild()
        return GLib.SOURCE_REMOVE

    def _scan_root_done(self, result, root):
        self.scanning = False
        # Merge root-scanned projects into state
        # Remove old projects belonging to this root, add new ones, preserve state for survivors
        try:
            norm_root = os.path.normpath(root)
            # Keep projects not under this root
            keep = []
            old_root_projects = []
            for p in self.state.projects:
                pp = os.path.normpath(p.get("path",""))
                if pp == norm_root or pp.startswith(norm_root + os.sep):
                    old_root_projects.append(p)
                    continue
                keep.append(p)
            # Preserve generic empty that still exists but not discovered
            discovered_paths = {p.get("path") for p in result.projects}
            for p in old_root_projects:
                pp = p.get("path")
                if pp not in discovered_paths and pp and os.path.isdir(pp):
                    desc = discovery.describe_project(pp)
                    if desc is not None:
                        merged = dict(p)
                        merged.update(desc)
                        result.projects.append(merged)
                        discovered_paths.add(pp)
                    else:
                        result.projects.append(p)
                        discovered_paths.add(pp)
            # Supplement with new manual empty directories under this root (discover untracked)
            existing_paths = {p.get("path") for p in keep} | discovered_paths
            extra = self._supplement_generic_children(root, existing_paths, discovered_paths)
            result.projects.extend(extra)
            # Add new discovered roots
            # Preserve pinned/recents/workspace for survivors automatically via keep; new projects start fresh
            merged = keep + result.projects
            merged.sort(key=lambda p: p.get("name","").lower())
            # Preserve scanned_at and mtimes: update root mtime for this root
            new_mtimes = dict(self.state.root_mtimes)
            new_mtimes.update(result.root_mtimes)
            self.state.update_projects(merged, result.scanned_at or time.time(), new_mtimes)
            # Also clean ephemeral workspace for deleted projects
            try:
                self.workspace.cleanup_stale(self.state.projects)
            except Exception:
                pass
            self.invalidate_annotation()
            state.save(self.state)
        except Exception:
            # fallback to full rescan
            self.state.update_projects(result.projects, result.scanned_at, result.root_mtimes)
            self.invalidate_annotation()
            state.save(self.state)
        # Replace transient "Rescanning…" with a brief confirmation that
        # auto-clears so the footer returns to its normal hints. Use the
        # root basename (when available) to make the message specific.
        try:
            base = os.path.basename(os.path.normpath(root)) or root
            self._set_status_message(
                f"Rescanned {base} \u00b7 {len(self.state.projects)}",
                source="rescan", clear_after=1.6)
        except Exception:
            self._set_status_message("Projects updated",
                                    source="rescan", clear_after=1.6)
        if self.window is not None:
            self.window.rebuild()
        return GLib.SOURCE_REMOVE

    def create_project_from_query(self, query):
        """Create project folder for query, returning project dict or (None, error)."""
        roots = self.cfg.expanded_roots()
        active_path = None
        try:
            if self.window is not None:
                row = self.window.listbox.get_selected_row()
                proj = getattr(row, "project", None)
                if proj:
                    active_path = proj.get("path")
        except Exception:
            active_path = None
        # also consider workspace active
        if not active_path:
            try:
                # last project
                if getattr(self.window, "_last_project", None):
                    active_path = self.window._last_project.get("path")
            except Exception:
                pass
        path, err = creation.create_project(query, roots, active_path)
        if path is None:
            return (None, err)
        # Invalidate relevant caches
        intelligence.invalidate(path)
        # Describe and insert into state if not already there
        desc = discovery.describe_project(path)
        if desc is None:
            # fallback generic
            import os as _os
            desc = {
                "path": path,
                "name": _os.path.basename(_os.path.normpath(path)),
                "kind": "generic",
                "label": "Project",
                "icon": "\U000f07c0",
                "color": "#a5adcb",
                "is_git": False,
                "cover": None,
            }
        # Merge into state
        found = False
        for i, p in enumerate(self.state.projects):
            if p.get("path") == path:
                merged = dict(p)
                merged.update(desc)
                self.state.projects[i] = merged
                found = True
                break
        if not found:
            self.state.projects.append(desc)
            self.state.projects.sort(key=lambda p: p.get("name","").lower())
        # Update root mtimes
        try:
            roots = self.cfg.expanded_roots()
            self.state.root_mtimes = discovery.root_mtimes(roots)
            self.state.scanned_at = time.time()
        except Exception:
            pass
        self.invalidate_annotation()
        state.save(self.state)
        # Invalidate caches for new project
        intelligence.invalidate(path)
        return (desc, None)

    # ------------------------------------------------------------- actions

    def preferred_tool_for(self, project):
        """Validated Tool for the project's remembered preference, or None.

        Availability is re-checked against the registry so a persisted id
        whose executable disappeared is silently ignored.
        """
        path = project.get("path", "") if isinstance(project, dict) else ""
        tool_id = self.workspace.get_preferred_tool_id(path)
        if not tool_id:
            return None
        return tools.validate_tool_id(tool_id)

    def launch_tool(self, project, tool_id):
        """Open the project with an explicit registry tool.

        Validates the id against installed tools (persisted or UI-supplied
        ids can never execute anything outside the registry), remembers the
        choice per project on success, and records usage so Quick Actions
        can rank it. Returns True when a process was started.
        """
        tool = tools.validate_tool_id(tool_id)
        if tool is None or not isinstance(project, dict):
            return False
        path = project.get("path", "")
        if not path:
            return False
        pid = actions.launch_tool(tool, path, self.cfg)
        if not pid:
            return False
        # pid may be int or True; normalize
        pid_val = pid if isinstance(pid, int) else None
        self.workspace.set_preferred_tool(path, tool.id)
        # Also remember as preferred editor if editor kind
        if tool.kind == "editor":
            # store preferred editor string for config editor preference
            try:
                exe = tools.resolve_executable(tool)
                pref = exe.split("/")[-1] if exe and "/" in exe else tool.probe[0]
                self.workspace.record(path, action=f"tool:{tool.id}", editor=pref)
            except Exception:
                pass
        editor = tool.name if tool.kind == "editor" else ""
        self._record_workspace(project, f"tool:{tool.id}", editor=editor)
        # session tracking: editor launch
        try:
            self.sessions.create(path, f"tool:{tool.id}", tool.name, tool.id, "editor", pid=pid_val, long_running=False)
        except Exception:
            pass
        return True

    def picker_rows(self):
        """Rows for the Open With picker: grouped available tools.

        Headers use the same non-selectable convention as action sections;
        empty categories are omitted entirely. Row tuples follow the
        existing (action_id, label, sub, hint) shape with stable
        ``tool:<id>`` ids so activation and preferences stay validated.
        """
        rows = []
        try:
            groups = tools.grouped_available()
        except Exception:
            groups = []
        icons = {"editor": "\U000f0174", "agent": "\U000f06a9"}
        for header, items in groups:
            rows.append((f"header:{header}", header, None, None))
            for tool in items:
                rows.append((f"tool:{tool.id}", tool.name,
                             icons.get(tool.kind), None))
        return rows

    def editor_picker_rows(self):
        """Rows for the explicit Choose Editor picker: only available editors."""
        rows = []
        try:
            from . import tools as _tools
            editors = [t for t in _tools.available_tools() if t.kind == "editor"]
            # Already deduplicated via probe caching
            if not editors:
                return rows
            rows.append(("header:EDITORS", "EDITORS", None, None))
            # Mark preferred with subtle hint
            # need current project's preferred? caller will pass project, but we keep generic here
            # The UI will highlight preferred via sub text; we just list names
            for tool in editors:
                rows.append((f"tool:{tool.id}", tool.name, None, None))
        except Exception:
            rows = []
        return rows

    def choose_editor_rows(self, project):
        """Editor picker rows with preferred hint for a specific project."""
        rows = []
        try:
            from . import tools as _tools
            editors = [t for t in _tools.available_tools() if t.kind == "editor"]
            if not editors:
                return rows
            rows.append(("header:SELECT EDITOR", "SELECT EDITOR", None, None))
            pref = ""
            try:
                pref = self.workspace.get_preferred_editor(project.get("path","")) if isinstance(project, dict) else ""
            except Exception:
                pref = ""
            pref_base = pref.split()[0] if pref else ""
            for tool in editors:
                exe = _tools.resolve_executable(tool)
                base = exe.split("/")[-1] if exe and "/" in exe else tool.probe[0]
                is_pref = (base == pref_base) or (tool.id == self.workspace.get_preferred_tool_id(project.get("path","")))
                hint = "preferred" if is_pref else None
                rows.append((f"tool:{tool.id}", tool.name, None, hint))
        except Exception:
            rows = []
        return rows

    def _smart_primary(self, project, is_active, intel_rows):
        """Determine smart primary action id/label.

        `intel_rows` MUST contain only available (executable-present)
        capabilities. Unavailable intelligence capabilities are not
        candidates for the primary action.
        """
        path = project["path"]
        editor_label = self.cfg.editor_label()
        # 1. Active → Focus (highest)
        if is_active:
            return ("focus", "Focus Project", "Focus active window")
        # 2. Running dev session → Focus/Manage if confident window exists
        try:
            sess = self.sessions.dev_session(path)
            if sess and sess.is_running():
                wins = self.workspace.windows_for(path)
                if wins:
                    return ("focus", "Focus Dev Server", sess.label)
                # If no window but session running, don't hijack primary; fall through
        except Exception:
            pass
        # 2. Explicitly remembered tool (validated against the registry and
        #    current availability; uninstalled tools fall through silently).
        try:
            tool = self.preferred_tool_for(project)
        except Exception:
            tool = None
        if tool is not None:
            return (f"tool:{tool.id}", f"Continue in {tool.name}", None)
        # 3. Remembered preferred valid action (usage-ranked)
        try:
            caps = intelligence.capabilities_for(project)
            valid_ids = set()
            # Only available intelligence caps are eligible for primary
            for cap in caps.as_list():
                if getattr(cap, "available", True):
                    valid_ids.add(f"int:{cap.key}:{cap.command}")
            valid_ids.update(["open", "terminal", "folder"])
            # add editor variants
            try:
                eds = self.cfg.available_editors()
                for ed in eds:
                    base = ed[0] if ed else ""
                    if base:
                        valid_ids.add(f"editor:{base}")
            except Exception:
                pass
            pref = self.workspace.get_preferred_primary(path, valid_ids)
            if pref:
                # map pref id to label
                if pref.startswith("int:"):
                    # find cap label
                    for cap in caps.as_list():
                        if f"int:{cap.key}:{cap.command}" == pref:
                            return (pref, cap.label, cap.command)
                if pref == "terminal":
                    return (pref, "Open in terminal", None)
                if pref == "open":
                    return (pref, f"Open in {editor_label}", "Default action")
                if pref.startswith("editor:"):
                    base = pref.split(":",1)[1]
                    return (pref, f"Open in {base}", None)
                # generic fallback for pref
                return (pref, pref, None)
            # 3. Optionally prefer dev intelligence if clearly useful and no preference
            # Keep conservative: don't auto-prefer dev without usage; fall through to open
        except Exception:
            pass
        # 4. Default: Open in editor (respect per-project preferred editor)
        pref_ed = ""
        try:
            pref_ed = self.workspace.get_preferred_editor(path)
        except Exception:
            pref_ed = ""
        if pref_ed:
            base = pref_ed.split()[0]
            return ("open", f"Open in {base}", "Preferred editor")
        return ("open", f"Open in {editor_label}", "Default action")

    def actions_for(self, project):
        """Return the action menu rows for `project`.

        Behavioural contract:
        - Unavailable intelligence capabilities are NOT shown at all. The
          validity model is consistent: if a required runtime / package
          manager / tool is missing, the action simply does not appear
          (Actions, Quick Actions, smart primary, persisted preference,
          picker all agree).
        - There is no UNAVAILABLE section.
        - Rescan actions are NOT in this list. Discovery/rescan is a
          library-level operation that lives in the main search UI; the
          actions menu is reserved for per-project actions only.
        """
        path = project["path"]
        is_active = False
        try:
            is_active = self.workspace.is_active(path)
        except Exception:
            is_active = False

        # Intelligence rows: ONLY available capabilities. Unavailable caps
        # (missing runtime, missing package manager) are excluded here and
        # therefore excluded from every downstream consumer (Quick Actions,
        # smart primary, persisted preference, picker).
        intel_rows = []
        try:
            caps = intelligence.capabilities_for(project)
            for cap in caps.as_list()[:3]:
                if getattr(cap, "available", True):
                    intel_rows.append((f"int:{cap.key}:{cap.command}", cap.label, cap.command, None))
        except Exception:
            intel_rows = []

        # Smart primary action uses available rows only (intel_rows already
        # filtered).
        primary_id, primary_label, primary_sub = self._smart_primary(project, is_active, intel_rows)
        primary_hint = "\u21b5"

        # Quick Actions: derived from frequent usage (only available)
        quick_rows = []
        try:
            valid_for_quick = ["open", "terminal", "folder"]
            for cap in intel_rows:
                valid_for_quick.append(cap[0])
            try:
                eds = self.cfg.available_editors()
                for ed in eds[:3]:
                    base = ed[0]
                    vid = f"editor:{base}"
                    if vid not in valid_for_quick:
                        valid_for_quick.append(vid)
            except Exception:
                pass
            try:
                for tool in tools.available_tools():
                    valid_for_quick.append(f"tool:{tool.id}")
            except Exception:
                pass
            ranked = self.workspace.ranked_quick_candidates(path, valid_for_quick)
            for rid in ranked[:2]:
                if rid == primary_id:
                    continue
                if rid.startswith("int:"):
                    # Only include if we have an available intel row for it
                    for cap in intel_rows:
                        if cap[0] == rid:
                            quick_rows.append(cap)
                            break
                elif rid.startswith("tool:"):
                    tid = rid.split(":", 1)[1]
                    t = tools.get_tool(tid)
                    if t is not None and tools.validate_tool_id(tid):
                        quick_rows.append((rid, f"Continue in {t.name}", None, None))
                elif rid == "terminal":
                    quick_rows.append(("terminal", "Open in terminal", None, "Ctrl+T"))
                elif rid == "open":
                    editor_label = self.cfg.editor_label()
                    quick_rows.append(("open", f"Open in {editor_label}", None, None))
                elif rid.startswith("editor:"):
                    base = rid.split(":",1)[1]
                    quick_rows.append((rid, f"Open in {base}", None, None))
                if len(quick_rows) >= 2:
                    break
        except Exception:
            quick_rows = []

        # Session handling: dev session running?
        session_rows = []
        dev_sess = None
        try:
            self.sessions.cleanup()
            dev_sess = self.sessions.dev_session(path)
            if dev_sess and dev_sess.is_running():
                # Session info row (non-actionable style? but make activatable for focus if possible)
                age = dev_sess.age_str()
                cmd_hint = dev_sess.command or dev_sess.label
                # Offer Focus if Hyprland window exists
                wins = self.workspace.windows_for(path)
                if wins:
                    session_rows.append(("focus", "Focus Dev Server", f"{cmd_hint} · {age}", None))
                else:
                    session_rows.append(("session_info", f"Dev Server Running", f"{cmd_hint} · {age}", None))
                session_rows.append(("restart_dev", "Restart Dev Server", dev_sess.command, None))
                session_rows.append(("stop_dev", "Stop Dev Server", dev_sess.command, None))
        except Exception:
            session_rows = []
            dev_sess = None

        # Build sections with headers
        quick_section = []
        quick_section.append((primary_id, primary_label, primary_sub, primary_hint))
        for qr in quick_rows:
            if qr[0] not in [r[0] for r in quick_section]:
                quick_section.append(qr)
        project_section = []
        for ir in intel_rows:
            if ir[0] not in [r[0] for r in quick_section]:
                # Avoid showing dev capability if already running as session (prevent duplicate)
                if dev_sess and dev_sess.is_running() and ir[0] == f"int:dev:{dev_sess.command}":
                    continue
                project_section.append(ir)
        # OTHER: generic - keep dense
        other_section = []
        if not any(r[0] == "open" for r in quick_section + project_section):
            editor_label = self.cfg.editor_label()
            other_section.append(("open", f"Open in {editor_label}", None, None))
        for gid, glabel, gsub, ghint in [("terminal", "Open in terminal", None, "Ctrl+T"),
                                         ("folder", "Open file manager", None, "Ctrl+F"),
                                         ("copy", "Copy path", None, "Ctrl+C")]:
            if not any(r[0] == gid for r in quick_section + project_section + other_section):
                other_section.append((gid, glabel, gsub, ghint))
        # Choose Editor explicit picker (replaces generic Open With for editors)
        try:
            has_editors = bool([t for t in tools.available_tools() if t.kind == "editor"])
        except Exception:
            has_editors = False
        if has_editors:
            other_section.append(("choose_editor", "Choose Editor\u2026",
                                  "Pick editor for this project", None))
        # Keep Open With for agents if any (optional, but keep for parity)
        try:
            has_agents = bool([t for t in tools.available_tools() if t.kind == "agent"])
        except Exception:
            has_agents = False
        if has_agents:
            # Only show Open With if not already covered? Keep distinct
            # To avoid duplication, only show if editors already shown? We'll keep separate
            other_section.append(("openwith", "Open With\u2026",
                                  "Choose editor or AI agent", None))
        if not project_section and not quick_section:
            for name, cmd in commands.discover(project)[:2]:
                if not any(r[0]==f"cmd:{cmd}" for r in quick_section + project_section + other_section):
                    other_section.append((f"cmd:{cmd}", f"Run: {cmd}", name, None))

        rows = []
        if quick_section:
            rows.append(("header:QUICK", "QUICK", None, None))
            rows.extend(quick_section)
        if session_rows:
            rows.append(("header:SESSION", "SESSION", None, None))
            rows.extend(session_rows)
        if project_section:
            rows.append(("header:PROJECT", "PROJECT", None, None))
            rows.extend(project_section)
        # OTHER always has at least folder/copy etc
        rows.append(("header:OTHER", "OTHER", None, None))
        rows.extend(other_section)
        # Rescan actions are intentionally NOT in this menu. Rescanning is
        # a discovery/library operation and lives in the main search mode
        # (the Rescan utility row + Ctrl+R / Ctrl+Shift+R shortcuts).
        if is_active and not any(r[0]=="focus" for r in rows):
            rows.insert(1, ("focus", "Focus Project", "Focus active window", None))
        if self.state.is_pinned(path):
            rows.append(("pin", "Unpin project", None, "Ctrl+P"))
        else:
            rows.append(("pin", "Pin project", None, "Ctrl+P"))
        return rows

    def _record_workspace(self, project, action, editor="", terminal_cmd=""):
        try:
            self.workspace.record(project["path"], action=action, editor=editor, terminal_cmd=terminal_cmd)
            # persist workspace
            state.save(self.state)
        except Exception:
            pass

    def focus_project(self, project):
        wins = []
        try:
            wins = self.workspace.windows_for(project["path"])
        except Exception:
            wins = []
        addr = None
        if wins:
            addr = wins[0].get("address")
        if addr:
            ok = hyprland.focus_window(addr)
            if ok:
                self._touch(project)
                self._record_workspace(project, action="focus")
                return True
        return False

    def _track_editor(self, project_path, editor_label, pid):
        try:
            pid_val = pid if isinstance(pid, int) else None
            self.sessions.create(project_path, f"editor:{editor_label}", f"Open in {editor_label}", editor_label, "editor", pid=pid_val, long_running=False)
        except Exception:
            pass

    def open_default(self, project):
        # Smart primary: if active window exists, focus instead of opening duplicate
        try:
            if self.workspace.is_active(project["path"]):
                if self.focus_project(project):
                    return
        except Exception:
            pass
        self._touch(project)
        # Check for a validated preferred tool first (e.g. Zed, VS Code)
        try:
            tool = self.preferred_tool_for(project)
            if tool is not None:
                self._record_workspace(project, action=f"tool:{tool.id}")
                self.launch_tool(project, tool.id)
                return
        except Exception:
            pass
        # per-project preferred editor
        pref = ""
        try:
            pref = self.workspace.get_preferred_editor(project["path"])
        except Exception:
            pref = ""
        ed_label = pref if pref else self.cfg.editor_label()
        # record with editor label for memory
        self._record_workspace(project, action="open", editor=ed_label)
        # if preferred editor is valid, use it directly
        if pref:
            argv = self.cfg.editor_argv_for(pref)
            if argv:
                try:
                    import subprocess
                    # reuse actions spawning with uwsm handling
                    if actions._has("uwsm-app") and argv[0] not in ("omarchy-launch-editor",):
                        argv = ["uwsm-app", "--", *argv, project["path"]]
                    else:
                        argv = [*argv, project["path"]]
                    proc = subprocess.Popen(argv, cwd=project["path"], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self._track_editor(project["path"], ed_label, getattr(proc, "pid", None))
                    # remember editor preference
                    try:
                        self.workspace.record(project["path"], action="open", editor=pref)
                    except Exception:
                        pass
                    return
                except Exception:
                    pass
        pid = actions.open_in_editor(project["path"], self.cfg)
        if pid:
            self._track_editor(project["path"], ed_label, pid if isinstance(pid, int) else None)

    def open_terminal(self, project):
        self._touch(project)
        self._record_workspace(project, action="terminal")
        pid = actions.open_in_terminal(project["path"], self.cfg)
        try:
            pid_val = pid if isinstance(pid, int) else None
            self.sessions.create(project["path"], "terminal", "Open in terminal", "", "terminal", pid=pid_val, long_running=False)
        except Exception:
            pass

    def open_folder(self, project):
        self._touch(project)
        self._record_workspace(project, action="folder")
        actions.open_folder(project["path"], self.cfg)

    def copy_path(self, project):
        clipboard = None
        try:
            clipboard = Gdk.Display.get_default().get_clipboard()
        except Exception:
            clipboard = None
        actions.copy_path(project["path"], clipboard)

    def run_action(self, action_id, project):
        if action_id == "focus":
            if self.focus_project(project):
                return
            self.open_default(project)
            return
        if action_id.startswith("tool:"):
            tool_id = action_id.split(":", 1)[1]
            self._touch(project)
            if not self.launch_tool(project, tool_id):
                # Tool unavailable or launch failed — fall through to default.
                self.open_default(project)
            return
        if action_id.startswith("editor:"):
            base = action_id.split(":", 1)[1]
            # validate editor still available
            argv = self.cfg.editor_argv_for(base)
            if argv is None:
                self.open_default(project)
                return
            self._touch(project)
            self._record_workspace(project, action=f"editor:{base}", editor=base)
            # Remember as preferred editor via workspace (also handled by _record but explicit)
            try:
                from . import workspace as _ws
                self.workspace.record(project["path"], action=f"editor:{base}", editor=base)
            except Exception:
                pass
            pid = None
            try:
                import subprocess
                cmd = [*argv, project["path"]]
                if actions._has("uwsm-app") and argv[0] not in ("omarchy-launch-editor",):
                    cmd = ["uwsm-app", "--", *cmd]
                proc = subprocess.Popen(cmd, cwd=project["path"], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                pid = getattr(proc, "pid", None)
            except Exception:
                pid2 = actions.open_in_editor(project["path"], self.cfg)
                pid = pid2 if isinstance(pid2, int) else None
            self._track_editor(project["path"], base, pid)
            return
        if action_id.startswith("int:"):
            try:
                _, key, command = action_id.split(":", 2)
            except ValueError:
                return
            try:
                caps = intelligence.capabilities_for(project)
                valid_map = {c.command: c for c in caps.as_list()}
                cap = valid_map.get(command)
                if cap is None:
                    return
                import re
                if not re.fullmatch(r"[A-Za-z0-9 _./:\-@]+", command):
                    return
                # Block execution if required executable is missing
                if not getattr(cap, "available", True):
                    return
                # Duplicate protection for long-running dev
                if getattr(cap, "long_running", False):
                    existing = self.sessions.find_by_action(project["path"], action_id)
                    if existing and existing.is_running():
                        # Avoid duplicate dev server; optionally focus
                        try:
                            wins = self.workspace.windows_for(project["path"])
                            if wins:
                                addr = wins[0].get("address")
                                if addr:
                                    hyprland.focus_window(addr)
                        except Exception:
                            pass
                        return
            except Exception:
                return
            self._touch(project)
            self._record_workspace(project, action=key, terminal_cmd=command)
            # Do not pollute long-term preference with operational dev session usage? But keep limited
            # Track session
            pid = actions.open_in_terminal(project["path"], self.cfg, command=command)
            try:
                pid_val = pid if isinstance(pid, int) else None
                self.sessions.create(project["path"], action_id, cap.label if 'cap' in locals() else key, command, "dev" if getattr(cap, "long_running", False) else key, pid=pid_val, long_running=getattr(cap, "long_running", False))
            except Exception:
                pass
            return
        if action_id.startswith("cmd:"):
            command = action_id[len("cmd:"):]
            known = {cmd for _, cmd in commands.discover(project)}
            if command not in known:
                return
            self._touch(project)
            self._record_workspace(project, action="cmd", terminal_cmd=command)
            pid = actions.open_in_terminal(project["path"], self.cfg, command=command)
            try:
                pid_val = pid if isinstance(pid, int) else None
                self.sessions.create(project["path"], action_id, command, command, "cmd", pid=pid_val, long_running=False)
            except Exception:
                pass
            return
        if action_id in ("stop_dev",):
            dev = self.sessions.dev_session(project["path"])
            if dev:
                self.sessions.stop(dev)
            return
        if action_id in ("restart_dev",):
            dev = self.sessions.dev_session(project["path"])
            if dev is None:
                return
            # Validate capability still exists and available
            try:
                caps = intelligence.capabilities_for(project)
                valid_map = {c.command: c for c in caps.as_list()}
                # dev.action_id is like "int:dev:npm run dev"
                if dev.action_id.startswith("int:"):
                    _, key, command = dev.action_id.split(":", 2)
                    cap = valid_map.get(command)
                    if cap is None or not getattr(cap, "available", True):
                        return
                    # stop old
                    self.sessions.stop(dev)
                    # restart same validated capability
                    pid = actions.open_in_terminal(project["path"], self.cfg, command=command)
                    pid_val = pid if isinstance(pid, int) else None
                    self.sessions.create(project["path"], dev.action_id, cap.label, command, "dev", pid=pid_val, long_running=True)
                    self._touch(project)
                    self._record_workspace(project, action=key, terminal_cmd=command)
            except Exception:
                pass
            return
        if action_id == "session_info":
            return
        if action_id in ("choose_editor", "openwith"):
            # UI handles picker mode; no direct launch
            return
        if action_id in ("rescan_project", "rescan_root", "rescan_all", "rescan"):
            # Rescan actions are no longer exposed in the project Actions
            # menu; they live in the main search mode and the Ctrl+R / Ctrl+
            # Shift+R shortcuts. If we land here, fall through to a global
            # rescan so an obsolete row-activation cannot silently do
            # something unexpected.
            self.rescan_all()
            return
        if action_id == "terminal":
            self.open_terminal(project)
        elif action_id == "folder":
            self.open_folder(project)
        elif action_id == "copy":
            self.copy_path(project)
        else:
            self.open_default(project)

    def toggle_pin(self, project):
        path = project["path"]
        if self.state.is_pinned(path):
            self.state.unpin(path)
        else:
            self.state.pin(path)
        self.invalidate_annotation()
        state.save(self.state)

    def open_config(self):
        import subprocess
        path = self.cfg.path
        editor = self.cfg.detected_editor()
        if editor is None:
            subprocess.Popen(["xdg-open", path], start_new_session=True)
            return
        argv = [*editor, path]
        if actions._has("uwsm-app") and editor[0] not in ("omarchy-launch-editor",):
            argv = ["uwsm-app", "--", *argv]
        try:
            subprocess.Popen(argv, start_new_session=True)
        except OSError:
            pass

    def _touch(self, project):
        self.state.touch_recent(project["path"])
        self.invalidate_annotation()
        state.save(self.state)

    def quit(self):
        state.save(self.state)
        super().quit()


def main():
    """Run the application; returns an exit code."""
    try:
        app = DockApp()
        return app.run(list(sys.argv))
    except GLib.Error:
        # No session bus: run a single non-daemon window instead.
        return _run_standalone()


def _run_standalone():
    app = DockApp()
    app._ensure_window()
    app._show()
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    state.save(app.state)
    return 0
