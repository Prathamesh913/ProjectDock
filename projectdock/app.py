"""Application controller: owns config/state, runs the scan in the
background and glues the UI to the system actions.

Instances are unique per session via GApplication's DBus registration, so
`projectdock toggle` from a global keybinding talks to the running daemon
and the window shows/hides instantly. If no session bus is available the
app degrades to a plain single window.
"""

import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gtk, GLib, Gio

from . import actions, commands, config, discovery, hyprland, intelligence, search, state, theme, ui, workspace

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
        self.workspace = workspace.WorkspaceStore(self.state)
        self.workspace.load_from_state(self.state)

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
        # refresh workspace awareness when launcher opens (bounded, safe)
        try:
            # use current known projects for association
            self.workspace.refresh(self.state.projects)
            self.workspace.cleanup_stale(self.state.projects)
        except Exception:
            pass
        self.window.show_dock()
        self._shown_at = time.monotonic()
        self._start_focus_poll()
        self._maybe_rescan()

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
        projects = [dict(p) for p in self.state.projects]
        self.state.annotate(projects)
        try:
            self.workspace.annotate_projects(projects)
        except Exception:
            pass
        if query:
            return search.filter_and_rank(query, projects)
        return search.sorted_by_activity(projects)

    def project_count(self):
        return len(self.state.projects)

    # ------------------------------------------------------------- scanning

    def _maybe_rescan(self):
        if self.state.projects and not discovery.roots_changed(
                self.cfg.expanded_roots(), self.state.root_mtimes):
            elapsed = time.time() - self.state.scanned_at
            if elapsed < self.cfg.rescan_minutes * 60:
                return
        self.rescan()

    def rescan(self):
        if self.scanning:
            return
        intelligence.invalidate()
        try:
            import gitinfo as _gi
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

    def _scan_work(self, roots):
        try:
            result = discovery.scan(roots, max_depth=self.cfg.max_depth)
        except Exception:
            GLib.idle_add(self._scan_failed)
            return
        GLib.idle_add(self._scan_done, result)

    def _scan_failed(self):
        # discovery.scan is designed not to raise, but if it ever does, keep
        # the previously cached projects instead of wiping them.
        self.scanning = False
        return GLib.SOURCE_REMOVE

    def _scan_done(self, result):
        self.scanning = False
        self.state.update_projects(
            result.projects, result.scanned_at, result.root_mtimes)
        state.save(self.state)
        if self.window is not None:
            self.window.rebuild()
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------- actions

    def _smart_primary(self, project, is_active, intel_rows):
        """Determine smart primary action id/label."""
        path = project["path"]
        editor_label = self.cfg.editor_label()
        # 1. Active → Focus
        if is_active:
            return ("focus", "Focus Project", "Focus active window")
        # 2. Remembered preferred valid action
        try:
            caps = intelligence.capabilities_for(project)
            valid_ids = set()
            for cap in caps.as_list():
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
        path = project["path"]
        is_active = False
        try:
            is_active = self.workspace.is_active(path)
        except Exception:
            is_active = False

        # Intelligence rows
        intel_rows = []
        try:
            caps = intelligence.capabilities_for(project)
            for cap in caps.as_list()[:3]:
                intel_rows.append((f"int:{cap.key}:{cap.command}", cap.label, cap.command, None))
        except Exception:
            intel_rows = []

        # Smart primary action
        primary_id, primary_label, primary_sub = self._smart_primary(project, is_active, intel_rows)
        primary_hint = "\u21b5"

        # Quick Actions: derived from frequent usage
        quick_rows = []
        try:
            # build valid ids for ranking
            valid_for_quick = ["open", "terminal", "folder"]
            for cap in intel_rows:
                valid_for_quick.append(cap[0])
            # include editor variants if any
            try:
                eds = self.cfg.available_editors()
                for ed in eds[:3]:
                    base = ed[0]
                    vid = f"editor:{base}"
                    if vid not in valid_for_quick:
                        valid_for_quick.append(vid)
            except Exception:
                pass
            ranked = self.workspace.ranked_quick_candidates(path, valid_for_quick)
            # take top 1-2 not already primary
            for rid in ranked[:2]:
                if rid == primary_id:
                    continue
                # map rid to row
                if rid.startswith("int:"):
                    for cap in intel_rows:
                        if cap[0] == rid:
                            quick_rows.append(cap)
                            break
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

        # Build sections with headers
        # QUICK: primary + ranked frequent
        quick_section = []
        quick_section.append((primary_id, primary_label, primary_sub, primary_hint))
        for qr in quick_rows:
            if qr[0] not in [r[0] for r in quick_section]:
                quick_section.append(qr)
        # PROJECT: intelligence
        project_section = []
        for ir in intel_rows:
            if ir[0] not in [r[0] for r in quick_section]:
                project_section.append(ir)
        # OTHER: generic
        other_section = []
        if not any(r[0] == "open" for r in quick_section + project_section):
            editor_label = self.cfg.editor_label()
            other_section.append(("open", f"Open in {editor_label}", None, None))
        for gid, glabel, gsub, ghint in [("terminal", "Open in terminal", None, "Ctrl+T"),
                                         ("folder", "Open file manager", None, "Ctrl+F"),
                                         ("copy", "Copy path", None, "Ctrl+C")]:
            if not any(r[0] == gid for r in quick_section + project_section + other_section):
                other_section.append((gid, glabel, gsub, ghint))
        try:
            eds = self.cfg.available_editors()
            if len(eds) > 1:
                seen_editors = {r[0] for r in quick_section + project_section + other_section if r[0].startswith("editor:")}
                for ed in eds:
                    base = ed[0]
                    eid = f"editor:{base}"
                    if eid not in seen_editors and not any(base.lower() in r[1].lower() for r in quick_section + project_section if r[0]=="open"):
                        other_section.append((eid, f"Open in {base}", None, None))
                        if len([r for r in other_section if r[0].startswith("editor:")]) >= 2:
                            break
        except Exception:
            pass
        if not project_section and not quick_section:
            for name, cmd in commands.discover(project)[:2]:
                if not any(r[0]==f"cmd:{cmd}" for r in quick_section + project_section + other_section):
                    other_section.append((f"cmd:{cmd}", f"Run: {cmd}", name, None))

        rows = []
        if quick_section:
            rows.append(("header:QUICK", "QUICK", None, None))
            rows.extend(quick_section)
        if project_section:
            rows.append(("header:PROJECT", "PROJECT", None, None))
            rows.extend(project_section)
        # OTHER always has at least folder/copy etc
        rows.append(("header:OTHER", "OTHER", None, None))
        rows.extend(other_section)
        if is_active and not any(r[0]=="focus" for r in rows):
            # ensure focus in quick if active but not primary (should already be primary)
            rows.insert(1, ("focus", "Focus Project", "Focus active window", None))
        if self.state.is_pinned(path):
            rows.append(("pin", "Unpin project", None, "Ctrl+P"))
        else:
            rows.append(("pin", "Pin project", None, "Ctrl+P"))
        rows.append(("rescan", "Rescan projects", None, "Ctrl+R"))
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

    def open_default(self, project):
        # Smart primary: if active window exists, focus instead of opening duplicate
        try:
            if self.workspace.is_active(project["path"]):
                if self.focus_project(project):
                    return
        except Exception:
            pass
        self._touch(project)
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
                    subprocess.Popen(argv, cwd=project["path"], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except Exception:
                    pass
        actions.open_in_editor(project["path"], self.cfg)

    def open_terminal(self, project):
        self._touch(project)
        self._record_workspace(project, action="terminal")
        actions.open_in_terminal(project["path"], self.cfg)

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
        if action_id.startswith("editor:"):
            base = action_id.split(":", 1)[1]
            # validate editor still available
            argv = self.cfg.editor_argv_for(base)
            if argv is None:
                self.open_default(project)
                return
            self._touch(project)
            self._record_workspace(project, action=f"editor:{base}", editor=base)
            try:
                import subprocess
                cmd = [*argv, project["path"]]
                if actions._has("uwsm-app") and argv[0] not in ("omarchy-launch-editor",):
                    cmd = ["uwsm-app", "--", *cmd]
                subprocess.Popen(cmd, cwd=project["path"], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                actions.open_in_editor(project["path"], self.cfg)
            return
        if action_id.startswith("int:"):
            try:
                _, key, command = action_id.split(":", 2)
            except ValueError:
                return
            try:
                caps = intelligence.capabilities_for(project)
                valid = {c.command for c in caps.as_list()}
                if command not in valid:
                    return
                import re
                if not re.fullmatch(r"[A-Za-z0-9 _./:\-@]+", command):
                    if command not in valid:
                        return
            except Exception:
                return
            self._touch(project)
            self._record_workspace(project, action=key, terminal_cmd=command)
            actions.open_in_terminal(project["path"], self.cfg, command=command)
            return
        if action_id.startswith("cmd:"):
            command = action_id[len("cmd:"):]
            known = {cmd for _, cmd in commands.discover(project)}
            if command not in known:
                return
            self._touch(project)
            self._record_workspace(project, action="cmd", terminal_cmd=command)
            actions.open_in_terminal(project["path"], self.cfg, command=command)
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
