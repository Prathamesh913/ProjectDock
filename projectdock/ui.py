"""The GTK4 layer-shell launcher window.

Design goals: keyboard-first, minimal visual noise, instant filtering,
temporary utility window. The window is a top-centered layer-shell surface
on Wayland (falling back to a plain window elsewhere) that hides instead of
closing so the daemon can toggle it instantly.
"""

import os
import re
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk, GLib, Pango

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell
    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    Gtk4LayerShell = None
    HAS_LAYER_SHELL = False

from . import cover as _cover
from . import creation as _creation
from . import gitinfo
from . import tools as _tools

MODE_SEARCH = 0
MODE_ACTIONS = 1
MODE_PICKER = 2

ICONS = {
    "open": "\U000f0174",      # mdi-code_tags  <>  (coherent MDI family, verified JetBrainsMono Nerd Font)
    "terminal": "\U000f018d",  # mdi-console  >_  (Omarchy TUI style)
    "folder": "\U000f024b",    # mdi-folder
    "copy": "\U000f018f",      # mdi-content-copy  overlapping rectangles
    "pin": "\U000f0403",       # mdi-pin  pushpin
    "unpin": "\U000f0403",     # same pin, label differentiates
    "refresh": "\U000f0453",   # mdi-reload  circular arrows (verified, not wifi)
    "run": "\U000f040a",       # mdi-play
    "config": "\U000f0493",    # mdi-cog  (verified)
    "focus": "\U000f01a3",     # mdi-crosshairs  focus window
    "int": "\U000f040a",       # intelligence dev/test/build – same as run
    "openwith": "\U000f0423",  # mdi-launch  arrow-out-of-box (verified)
    "agent": "\U000f06a9",     # mdi-robot  AI coding agent (verified)
}

MAX_VISIBLE = 400

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

_CSS_CACHE = {}
_COVER_STYLE_CACHE = {}


def move_in_list(current, step, items):
    """Return the element `step` positions after `current` in `items`
    (wrapping), or None when `items` is empty. Pure helper for keyboard nav.
    """
    if not items:
        return None
    if current not in items:
        return items[0]
    return items[(items.index(current) + step) % len(items)]


def _is_printable_key(keyval):
    """True for ordinary text characters (letters, digits, punctuation, space).

    Control/function keys (Enter, Tab, Escape, arrows, Backspace, ...) map to
    no printable Unicode and are excluded. This is the gate that guarantees
    unmodified text never triggers a shortcut.
    """
    uni = Gdk.keyval_to_unicode(keyval)
    return uni > 0 and uni >= 0x20 and uni != 0x7f


def key_action(keyval, state, mode):
    """Classify a key press into a shortcut action, or None to pass through.

    Explicit state machine with two keyboard modes:

    * MODE_SEARCH  – search entry owns focus; printable text inserts,
      arrows navigate projects, Tab enters actions, Enter opens.
    * MODE_ACTIONS – action list owns focus; entry is hidden so printable
      text is irrelevant, arrows/Home/End navigate actions, Enter executes,
      Escape returns.

    The invariant that fixes the text-input bug: ordinary, unmodified,
    printable characters ALWAYS return None so they reach the focused entry
    untouched. Only explicit modifiers (Ctrl/Alt) or reserved keys (Tab,
    Enter, Escape, nav) are consumed.
    """
    ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
    alt = bool(state & Gdk.ModifierType.ALT_MASK)

    if keyval == Gdk.KEY_Escape:
        return "escape"
    if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
        return "tab"
    if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
        return "enter"
    if ctrl and not alt:
        name = Gdk.keyval_name(keyval) or ""
        return "ctrl:" + name
    if alt and not ctrl and Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
        return "alt-num"
    # Ordinary printable text must never be swallowed by a shortcut.
    if not ctrl and not alt and _is_printable_key(keyval):
        return None
    if not ctrl and not alt:
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up, Gdk.KEY_Home, Gdk.KEY_End,
                      Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            return "nav"
    return None


def _safe_color(color, fallback="#a5adcb"):
    return color if isinstance(color, str) and _HEX_COLOR.match(color) else fallback


def _trace(msg):
    """Lifecycle tracing for field debugging (PROJECTDOCK_TRACE=1)."""
    if os.environ.get("PROJECTDOCK_TRACE"):
        sys.stderr.write(f"[pd-trace] {msg}\n")
        sys.stderr.flush()


class LauncherWindow(Gtk.Window):
    def __init__(self, controller, config, palette, css):
        super().__init__(title="ProjectDock")
        self.controller = controller
        self.config = config
        self.palette = palette
        self.mode = MODE_SEARCH
        self._resetting = False
        self._git_timeout = None
        self._last_project = None
        self._cover_cache = {}
        self._picker_project = None
        self._picker_filter = "all"
        self._rebuild_timeout = None

        self.add_css_class("dock")
        self.set_resizable(False)
        self.connect("close-request", self._on_close_request)
        self.connect("map", self._on_map)
        self.connect("destroy", self._on_destroyed)
        self._monitors = []
        self._monitor_debounce = None

        self._build_ui()
        self._apply_css(css)
        self._init_layer_shell()

        key = Gtk.EventControllerKey.new()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key)

    # ------------------------------------------------------------- setup

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("dock-box")
        root.set_size_request(self.config.width, -1)
        self.set_child(root)

        self.search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search_row.add_css_class("search-row")
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text(
            "\U000f0349  Search projects by name, path, type\u2026")
        self.entry.set_hexpand(True)
        self.entry.connect("changed", self._on_search_changed)
        self.search_row.append(self.entry)
        root.append(self.search_row)

        self.title_label = Gtk.Label(xalign=0)
        self.title_label.add_css_class("title-label")
        self.title_label.set_visible(False)
        root.append(self.title_label)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_propagate_natural_height(True)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_activate_on_single_click(False)
        self.listbox.add_css_class("list")
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.connect("row-selected", self._on_row_selected)
        self.scrolled.set_child(self.listbox)
        root.append(self.scrolled)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.add_css_class("footer")
        self.hint_left = Gtk.Label(xalign=0, hexpand=True, use_markup=True)
        self.hint_right = Gtk.Label(xalign=1, use_markup=True)
        footer.append(self.hint_left)
        footer.append(self.hint_right)
        root.append(footer)

    def _apply_css(self, css):
        provider = _CSS_CACHE.get(css)
        if provider is None:
            provider = Gtk.CssProvider()
            provider.load_from_string(css)
            _CSS_CACHE[css] = provider
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _init_layer_shell(self):
        self._layer_shell = False
        if not HAS_LAYER_SHELL:
            return
        try:
            if Gdk.Display.get_default() is None:
                Gdk.Display.open(os.environ.get("WAYLAND_DISPLAY", ""))
        except Exception:
            pass
        if not Gtk4LayerShell.is_supported():
            return
        try:
            Gtk4LayerShell.init_for_window(self)
            Gtk4LayerShell.set_namespace(self, "projectdock")
            Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.TOP)
            Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.TOP, True)
            # EXCLUSIVE for the launcher's whole (short) visible lifetime:
            # this is what guarantees the compositor hands keyboard focus to
            # the summoned surface, so typed characters always reach the
            # search entry instead of leaking to the window underneath.
            # ON_DEMAND alone intermittently never receives focus at map,
            # and switching a mapped surface EXCLUSIVE->ON_DEMAND proved
            # fragile under Hyprland. Focus returns to other windows the
            # moment the surface unmaps on hide — the standard modal-launcher
            # model (wofi/fuzzel do the same).
            Gtk4LayerShell.set_keyboard_mode(
                self, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
            Gtk4LayerShell.set_respect_close(self, True)
            margin, max_h = self._metrics()
            Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.TOP, margin)
            self.scrolled.set_max_content_height(max_h)
            self._layer_shell = True
        except Exception:
            self._layer_shell = False

    # ------------------------------------------------------------- focus

    def _on_map(self, *args):
        """On map: put the caret in the search entry immediately."""
        self.entry.grab_focus()
        return False

    def _on_destroyed(self, *args):
        _trace("destroy: window destroyed")
        self._cancel_git_timeout()
        self._cancel_search_rebuild()
        self._stop_monitors()

    def _metrics(self):
        try:
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitors().get_item(0)
            geo = monitor.get_geometry()
            margin = int(geo.height * self.config.top_margin_pct / 100)
            max_h = int(geo.height * self.config.max_height_pct / 100) - margin
            return max(margin, 0), max(max_h, 200)
        except Exception:
            return 120, 500

    # ------------------------------------------------------------- showing

    def show_dock(self):
        _trace("show_dock")
        self._resetting = True
        self.entry.set_text("")
        self._resetting = False
        self.set_mode(MODE_SEARCH)
        # Lightweight root signature check when showing: if roots changed or mtime out-of-date, rescan will be triggered via controller._maybe_rescan
        # Also start directory monitors while visible
        self._start_monitors()
        # Present FIRST so the compositor sees the surface immediately,
        # then rebuild the list on the next idle cycle to avoid blocking
        # the first frame.
        self.present()
        GLib.idle_add(self._show_dock_idle)

    def _show_dock_idle(self):
        """Rebuild the list and grab focus after the first frame is painted."""
        self.rebuild()
        self.entry.grab_focus()
        return GLib.SOURCE_REMOVE

    def hide_dock(self):
        _trace("hide_dock: called (esc/toggle)")
        self._cancel_git_timeout()
        self._cancel_search_rebuild()
        self._stop_monitors()
        self.controller.hide_window()

    def _on_close_request(self, *args):
        _trace("close-request: compositor close")
        self.controller.hide_window()
        return True

    # ------------------------------------------------------------- mode

    def set_mode(self, mode):
        self.mode = mode
        if mode == MODE_SEARCH:
            self.title_label.set_visible(False)
            self.search_row.set_visible(True)
            self.title_label.set_label("")
        else:
            self.search_row.set_visible(False)
            self.title_label.set_visible(True)
            name = self._last_project["name"] if self._last_project else ""
            if mode == MODE_PICKER:
                suffix = "Choose Editor" if getattr(self, "_picker_filter", "all") == "editor" else "Open With"
            else:
                suffix = "Actions"
            self.title_label.set_label(f"{name} \u2014 {suffix}")

    # ------------------------------------------------------------- rebuild

    def rebuild(self):
        self._cancel_git_timeout()
        self.listbox.remove_all()
        if self.mode == MODE_ACTIONS:
            self._build_action_rows(self._last_project)
        elif self.mode == MODE_PICKER:
            filt = getattr(self, "_picker_filter", "all")
            self._build_action_rows(self._picker_project, picker=True, picker_filter=filt)
        else:
            self._build_project_rows(self.entry.get_text().strip())
        self._select_first()
        self._update_footer()
        self._schedule_git_for_selected()

    def _should_offer_create(self, query, projects):
        try:
            return _creation.should_offer_create(query, projects)
        except Exception:
            return False

    def _create_row(self, query):
        row = Gtk.ListBoxRow(activatable=True)
        row.add_css_class("project-row")
        row.add_css_class("create-row")
        row.is_create = True
        row.create_query = query.strip()
        # target root hint
        try:
            roots = self.controller.cfg.expanded_roots()
            active = None
            try:
                if self._last_project:
                    active = self._last_project.get("path")
            except Exception:
                pass
            target_root = _creation.choose_target_root(roots, active)
        except Exception:
            target_root = None
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox.set_valign(Gtk.Align.CENTER)
        # plus icon
        plus = Gtk.Label(label="\u002b")
        plus.add_css_class("action-icon")
        plus.set_size_request(26, -1)
        plus.set_xalign(0.5)
        hbox.append(plus)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        vbox.set_hexpand(True)
        # Use markup to show query safely
        esc = _escape(query.strip())
        label = Gtk.Label(label=f'Create "{esc}"', xalign=0, use_markup=False)
        label.add_css_class("project-name")
        label.set_xalign(0.0)
        label.set_hexpand(True)
        vbox.append(label)
        if target_root:
            # shorten path like _short_path
            sub = Gtk.Label(label=_short_path(target_root), xalign=0)
            sub.add_css_class("project-path")
            sub.set_xalign(0.0)
            vbox.append(sub)
        hbox.append(vbox)
        # subtle hint
        hint = Gtk.Label(label="\u21b5")
        hint.add_css_class("action-hint")
        hint.set_valign(Gtk.Align.CENTER)
        hbox.append(hint)
        row.set_child(hbox)
        return row

    def _build_project_rows(self, query):
        projects = self.controller.projects_for_query(query)
        offer_create = self._should_offer_create(query, self.controller.state.projects if hasattr(self.controller.state, "projects") else projects)
        if not projects:
            self._build_empty_rows(query)
            if offer_create:
                self.listbox.append(self._create_row(query))
            self._append_rescan_utility_row(query)
            return
        if query:
            for project in projects[:MAX_VISIBLE]:
                self.listbox.append(self._project_row(project))
            if offer_create:
                # place create row after existing matches (does not dominate)
                self.listbox.append(self._create_row(query))
            self._append_rescan_utility_row(query)
            return
        # Precedence: Pinned → Active → Recent → Projects (no duplication)
        pinned = [p for p in projects if p.get("pinned")]
        active = [p for p in projects if not p.get("pinned") and p.get("active")]
        recent = [p for p in projects
                  if not p.get("pinned") and not p.get("active") and p.get("recent_rank") is not None]
        rest = [p for p in projects
                if not p.get("pinned") and not p.get("active") and p.get("recent_rank") is None]
        if pinned:
            self.listbox.append(self._header_row("Pinned"))
            for project in pinned[:MAX_VISIBLE]:
                self.listbox.append(self._project_row(project))
        if active:
            self.listbox.append(self._header_row("Active"))
            for project in active[:MAX_VISIBLE]:
                self.listbox.append(self._project_row(project))
        if recent:
            self.listbox.append(self._header_row("Recent"))
            for project in recent[:MAX_VISIBLE]:
                self.listbox.append(self._project_row(project))
        if rest:
            if pinned or active or recent:
                self.listbox.append(self._header_row("Projects"))
            for project in rest[:MAX_VISIBLE]:
                self.listbox.append(self._project_row(project))
        # Rescan utility row only when search query is empty: a discovery
        # action is not a project and should not appear alongside filtered
        # results. This row is NOT a project and uses an internal
        # representation (`is_rescan`) so it never collides with project
        # selection logic (open_default, smart primary, etc).
        self._append_rescan_utility_row(query)

    def _build_empty_rows(self, query):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.add_css_class("empty-box")
        title = Gtk.Label(
            label="no matches" if query else "no projects")
        title.add_css_class("empty-title")
        box.append(title)
        hint = Gtk.Label(
            label="add folders in ~/.config/projectdock/config.toml\n"
                  "then press Ctrl+R to rescan",
            justify=Gtk.Justification.CENTER)
        hint.add_css_class("empty-hint")
        box.append(hint)
        wrap = Gtk.ListBoxRow()
        wrap.set_selectable(False)
        wrap.set_activatable(False)
        wrap.set_child(box)
        self.listbox.append(wrap)

        row = Gtk.ListBoxRow(activatable=True)
        row.add_css_class("project-row")
        row.action = ("config", None)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Label(label=ICONS["config"])
        icon.add_css_class("action-icon")
        hbox.append(icon)
        label = Gtk.Label(label="open config", xalign=0)
        label.add_css_class("action-label")
        hbox.append(label)
        row.set_child(hbox)
        self.listbox.append(row)

    def _rescan_utility_row(self, kind="all", label="Rescan projects"):
        """Build a non-project utility row for the search mode.

        This row is visually secondary to project results but uses the same
        flat row language. It carries an explicit `is_rescan` flag so the
        rest of the UI (project selection, smart primary, open_default) is
        guaranteed never to confuse it with a real project.
        """
        row = Gtk.ListBoxRow(activatable=True)
        row.add_css_class("project-row")
        row.add_css_class("rescan-row")
        row.is_rescan = True
        row.rescan_kind = kind
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Label(label=ICONS["refresh"])
        icon.add_css_class("action-icon")
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        icon.set_size_request(18, -1)
        icon.set_xalign(0.5)
        hbox.append(icon)
        label_w = Gtk.Label(label=label, xalign=0)
        label_w.add_css_class("action-label")
        label_w.set_valign(Gtk.Align.CENTER)
        hbox.append(label_w)
        hbox.append(Gtk.Box(hexpand=True))
        hint = Gtk.Label(label="\u21b5")
        hint.add_css_class("action-hint")
        hint.set_valign(Gtk.Align.CENTER)
        hint.set_xalign(1.0)
        hbox.append(hint)
        row.set_child(hbox)
        return row

    def _append_rescan_utility_row(self, query):
        """Append the Rescan utility row when it is appropriate to do so.

        It is shown only in the main search mode and only when the query is
        empty. A discovery / rescan action is meaningless when the user is
        filtering for a specific project name, so the row disappears as
        soon as they start typing.
        """
        if query:
            return
        # Subtle separator so the utility row does not look like another
        # project. Header text is intentionally lowercase / minimal so it
        # reads as a utility, not a section of projects.
        sep = Gtk.ListBoxRow()
        sep.set_selectable(False)
        sep.set_activatable(False)
        sep.add_css_class("rescan-separator")
        label = Gtk.Label(label="", xalign=0)
        label.set_size_request(-1, 1)
        sep.set_child(label)
        self.listbox.append(sep)
        # Prefer targeted root rescan when the previously selected project
        # is known and belongs to a configured root, otherwise global.
        kind = "all"
        label_text = "Rescan projects"
        try:
            last = getattr(self, "_last_project", None)
            if last:
                root = self.controller._root_for_path(last.get("path", ""))
                if root:
                    kind = "root"
                    base = os.path.basename(root) or root
                    label_text = f"Rescan {base}"
        except Exception:
            pass
        self.listbox.append(self._rescan_utility_row(kind=kind, label=label_text))

    def _cover_widget(self, project):
        """Compact square project identity: artwork when found, else initials.

        The fallback initials are rendered in a fixed 26×26 square that is
        robustly centered both horizontally and vertically. A plain Gtk.Box
        with a single child does NOT center its child (Box packs to the start),
        which caused the earlier optical misalignment for N/NU/CP/PR/TP. The
        fix makes the label fill the square and uses label alignment properties
        (xalign/yalign + halign/valign + hexpand/vexpand) so the glyph is
        centered inside its allocation regardless of 1-char vs 2-char width,
        font metrics, or inherited CSS padding.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(26, 26)
        box.add_css_class("cover")
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.START)
        box.set_hexpand(False)
        box.set_vexpand(False)
        box.set_overflow(Gtk.Overflow.HIDDEN)

        path = self._cover_for(project)
        if path:
            img = Gtk.Image()
            img.set_from_file(path)
            img.set_pixel_size(26)
            img.add_css_class("cover-img")
            img.set_halign(Gtk.Align.CENTER)
            img.set_valign(Gtk.Align.CENTER)
            box.append(img)
        else:
            label = Gtk.Label(label=_cover.identity_initials(project.get("name", "?")))
            label.add_css_class("cover-initials")
            # Fill the 26×26 allocation, then center text inside it.
            label.set_hexpand(True)
            label.set_vexpand(True)
            label.set_halign(Gtk.Align.FILL)
            label.set_valign(Gtk.Align.FILL)
            label.set_xalign(0.5)
            label.set_yalign(0.5)
            label.set_justify(Gtk.Justification.CENTER)
            label.set_wrap(False)
            label.set_single_line_mode(True)
            bg, fg = _cover.identity_colors(project.get("name", "?"), self.palette)
            self._apply_cover_style(box, bg, fg)
            box.append(label)
        return box

    def _cover_for(self, project):
        path = project.get("path")
        if path in self._cover_cache:
            return self._cover_cache[path]
        result = _cover.discover_cover(project)
        self._cover_cache[path] = result
        return result

    def _apply_cover_style(self, box, bg, fg):
        key = bg + "|" + fg
        provider = _COVER_STYLE_CACHE.get(key)
        if provider is None:
            provider = Gtk.CssProvider()
            provider.load_from_string(
                f'.cover{{background:{bg};}} .cover-initials{{color:{fg};}}')
            _COVER_STYLE_CACHE[key] = provider
        box.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

    def _header_row(self, text):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        row.add_css_class("header-row")
        label = Gtk.Label(label=text.upper(), xalign=0)
        row.set_child(label)
        return row

    def _project_row(self, project):
        row = Gtk.ListBoxRow(activatable=True)
        row.add_css_class("project-row")
        row.project = project

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_halign(Gtk.Align.FILL)
        box.set_hexpand(True)

        cover = self._cover_widget(project)
        # left-anchored, never expands
        cover.set_halign(Gtk.Align.START)
        cover.set_hexpand(False)
        cover.set_valign(Gtk.Align.CENTER)
        box.append(cover)

        # main text block — consumes remaining space, left-aligned
        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        mid.set_hexpand(True)
        mid.set_halign(Gtk.Align.FILL)
        mid.set_valign(Gtk.Align.CENTER)

        line1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        line1.set_halign(Gtk.Align.FILL)
        line1.set_hexpand(True)
        name = Gtk.Label(label=project["name"], xalign=0)
        name.add_css_class("project-name")
        name.set_hexpand(True)
        name.set_halign(Gtk.Align.FILL)
        name.set_xalign(0.0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        line1.append(name)
        if project.get("active"):
            dot = Gtk.Label(label="\u25cf")
            dot.add_css_class("active-dot")
            dot.set_valign(Gtk.Align.CENTER)
            # subtle accent indicator for active workspace
            line1.append(dot)
        if project.get("pinned"):
            star = Gtk.Label(label="\u2605")
            star.add_css_class("star")
            star.set_valign(Gtk.Align.CENTER)
            line1.append(star)
        mid.append(line1)

        line2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        line2.set_halign(Gtk.Align.FILL)
        line2.set_hexpand(True)
        path_label = Gtk.Label(label=_short_path(project["path"]), xalign=0)
        path_label.add_css_class("project-path")
        path_label.set_hexpand(True)
        path_label.set_halign(Gtk.Align.FILL)
        path_label.set_xalign(0.0)
        path_label.set_ellipsize(Pango.EllipsizeMode.END)
        line2.append(path_label)
        mid.append(line2)

        box.append(mid)

        # right metadata block — never expands, pushed to right edge
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        right.set_hexpand(False)
        right.set_halign(Gtk.Align.END)
        right.set_valign(Gtk.Align.CENTER)
        right.set_size_request(96, -1)

        type_label = Gtk.Label(label=project.get("label", "Project"), xalign=1.0)
        type_label.add_css_class("project-type")
        type_label.set_halign(Gtk.Align.END)
        type_label.set_xalign(1.0)
        type_label.set_ellipsize(Pango.EllipsizeMode.END)
        type_label.set_max_width_chars(14)
        right.append(type_label)

        git_badge = Gtk.Label(xalign=1.0)
        git_badge.add_css_class("git-badge")
        git_badge.set_halign(Gtk.Align.END)
        git_badge.set_xalign(1.0)
        git_badge.set_ellipsize(Pango.EllipsizeMode.START)
        git_badge.set_width_chars(10)
        git_badge.set_max_width_chars(24)
        right.append(git_badge)

        box.append(right)
        row.set_child(box)
        row.git_badge = git_badge
        return row

    def _build_action_rows(self, project, picker=False, picker_filter="all"):
        if not project:
            self._build_empty_rows("")
            return
        if picker:
            if picker_filter == "editor":
                # Use explicit editor picker with preferred hint
                try:
                    rows = self.controller.choose_editor_rows(project)
                    if not rows:
                        rows = self.controller.editor_picker_rows()
                    if not rows:
                        rows = self.controller.picker_rows()
                except Exception:
                    rows = self.controller.picker_rows()
            else:
                rows = self.controller.picker_rows()
        else:
            rows = self.controller.actions_for(project)
        for action_id, label, sub, hint in rows:
            if action_id.startswith("header:"):
                # subtle section header, not selectable
                self.listbox.append(self._header_row(label))
                continue
            row = Gtk.ListBoxRow(activatable=True)
            row.add_css_class("project-row")
            row.add_css_class("action-row")
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            hbox.set_valign(Gtk.Align.CENTER)
            icon_key = action_id.split(":", 1)[0]
            glyph = ICONS.get(icon_key)
            if picker and action_id.startswith("tool:"):
                tool_id = action_id.split(":", 1)[1]
                from . import tools as _tools
                tool = _tools.get_tool(tool_id)
                row.tool_id = tool_id
                glyph = ICONS.get(
                    "agent" if tool is not None and tool.kind == "agent"
                    else "open")
                row.action = (action_id, project)
            else:
                row.action = (action_id, project)
                if glyph is None:
                    glyph = ICONS["open"]
            icon = Gtk.Label(label=glyph)
            icon.add_css_class("action-icon")
            icon.set_halign(Gtk.Align.CENTER)
            icon.set_valign(Gtk.Align.CENTER)
            icon.set_size_request(18, -1)
            icon.set_xalign(0.5)
            hbox.append(icon)
            label_w = Gtk.Label(label=label, xalign=0)
            label_w.add_css_class("action-label")
            label_w.set_valign(Gtk.Align.CENTER)
            hbox.append(label_w)
            hbox.append(Gtk.Box(hexpand=True))
            if sub:
                sub_w = Gtk.Label(label=sub)
                sub_w.add_css_class("action-sub")
                sub_w.set_valign(Gtk.Align.CENTER)
                hbox.append(sub_w)
            if hint:
                hint_w = Gtk.Label(label=hint)
                hint_w.add_css_class("action-hint")
                hint_w.set_valign(Gtk.Align.CENTER)
                hint_w.set_xalign(1.0)
                hbox.append(hint_w)
            row.set_child(hbox)
            self.listbox.append(row)

    def _select_first(self):
        for i in range(MAX_VISIBLE * 4):
            row = self.listbox.get_row_at_index(i)
            if row is None:
                return
            if row.get_selectable():
                self.listbox.select_row(row)
                return

    def _selectable_indices(self):
        indices = []
        for i in range(MAX_VISIBLE * 4):
            row = self.listbox.get_row_at_index(i)
            if row is None:
                break
            if row.get_selectable():
                indices.append(i)
        return indices

    def _update_footer(self):
        """Footer hints. Only the primary, always-true shortcuts are shown;
        everything else (pin/terminal/folder/copy/rescan) lives in the
        actions menu (Tab) where each row shows its real modifier. The
        project count is intentionally not shown here — it competed with
        the hints for attention."""
        accent = self.palette["accent"]
        key = lambda text: f'<span foreground="{accent}">{text}</span>'
        if self.mode == MODE_ACTIONS:
            self.hint_left.set_markup(
                f'{key("\u21b5")} run \u00b7 {key("esc")} back')
            self.hint_right.set_text(
                _short_path(self._last_project["path"]) if self._last_project else "")
            return
        self.hint_left.set_markup(
            f'{key("\u2191\u2193")} navigate \u00b7 {key("\u21b5")} open \u00b7 '
            f'{key("tab")} actions \u00b7 {key("esc")} close')
        # Status message (e.g. "Rescanning projects…" / "Projects updated")
        # has priority over the plain "rescanning…" indicator. The status
        # message is owned by the controller and can auto-clear.
        status_text = ""
        try:
            status_text = self.controller.status_message() or ""
        except Exception:
            status_text = ""
        if status_text:
            self.hint_right.set_text(status_text)
        elif self.controller.scanning:
            self.hint_right.set_text("rescanning\u2026")
        else:
            self.hint_right.set_text("")

    # ------------------------------------------------------------- signals

    def _on_search_changed(self, entry):
        if self._resetting:
            return
        if self.mode == MODE_SEARCH:
            self._schedule_search_rebuild()

    def _schedule_search_rebuild(self):
        """Debounce rapid keystrokes: coalesce into one rebuild after 30ms."""
        self._cancel_search_rebuild()
        self._rebuild_timeout = GLib.timeout_add(
            30, self._run_scheduled_rebuild)

    def _cancel_search_rebuild(self):
        if self._rebuild_timeout is not None:
            GLib.source_remove(self._rebuild_timeout)
            self._rebuild_timeout = None

    def _run_scheduled_rebuild(self):
        self._rebuild_timeout = None
        if self.mode == MODE_SEARCH and self.get_visible():
            self.rebuild()
        return GLib.SOURCE_REMOVE

    def _on_row_activated(self, listbox, row):
        if self.mode == MODE_PICKER:
            tool_id = getattr(row, "tool_id", None)
            project = self._picker_project
            if not tool_id or not project:
                return
            if not self.controller.launch_tool(project, tool_id):
                return
            self.hide_dock()
            return
        if self.mode == MODE_ACTIONS:
            action = getattr(row, "action", None)
            if not action:
                return
            action_id, project = action
            if action_id == "pin":
                self.controller.toggle_pin(project)
                self.rebuild()
                self._restore_selection(project["path"])
                return
            if action_id == "config":
                self.controller.open_config()
                return
            if action_id == "choose_editor":
                self._picker_project = project
                self._picker_filter = "editor"
                # ensure _last_project is set for title
                self._last_project = project
                self.set_mode(MODE_PICKER)
                self.rebuild()
                self.listbox.grab_focus()
                return
            if action_id == "openwith":
                self._picker_project = project
                self._picker_filter = "all"
                self._last_project = project
                self.set_mode(MODE_PICKER)
                self.rebuild()
                self.listbox.grab_focus()
                return
            # Session actions need no hide (stop/restart keep window for feedback)
            if action_id in ("stop_dev", "restart_dev"):
                self.controller.run_action(action_id, project)
                self.rebuild()
                self._update_footer()
                return
            if action_id == "session_info":
                return
            # Defensive: rescan_* actions are not in the actions menu anymore.
            # If something stale still produces one, route to global rescan.
            if action_id in ("rescan", "rescan_project", "rescan_root", "rescan_all"):
                self.controller.rescan_all()
                self._update_footer()
                return
            self.controller.run_action(action_id, project)
            # Session actions keep window; hide only for launch actions
            if action_id not in ("pin", "stop_dev", "restart_dev", "session_info"):
                self.hide_dock()
            else:
                self.rebuild()
                self._update_footer()
            return
        # MODE_SEARCH: handle creation row first
        if getattr(row, "is_create", False):
            query = getattr(row, "create_query", "") or self.entry.get_text().strip()
            # race: if exact project appeared between typing and enter, just open it
            # Check if query now matches existing project exactly
            try:
                from . import creation as _cre
                if _cre.is_exact_match(query, self.controller.state.projects):
                    # find exact match and open it
                    for p in self.controller.state.projects:
                        if query.strip().lower() in (p.get("name","").lower(), p.get("path","").lower(), p.get("name","").lower()):
                            # fallback: open first matching via filter
                            pass
                    # just rebuild and select? For now, create will handle duplicate as success
            except Exception:
                pass
            proj, err = self.controller.create_project_from_query(query)
            if proj is None:
                # creation failed: stay in search, maybe show footer error
                self._update_footer()
                return
            # Immediately enter Actions mode for new project (frictionless)
            self._last_project = proj
            self.set_mode(MODE_ACTIONS)
            self.rebuild()
            self.listbox.grab_focus()
            return
        # Rescan utility row (search mode only, when query is empty)
        if getattr(row, "is_rescan", False):
            rescan_kind = getattr(row, "rescan_kind", "all") or "all"
            if rescan_kind == "root" and self._last_project:
                root = self.controller._root_for_path(self._last_project.get("path", ""))
                if root:
                    self.controller.rescan_root(root)
                else:
                    self.controller.rescan_all()
            else:
                self.controller.rescan_all()
            self._update_footer()
            return
        # handle config row in empty state
        action = getattr(row, "action", None)
        if action and action[0] == "config":
            self.controller.open_config()
            return
        project = getattr(row, "project", None)
        if project:
            self.controller.open_default(project)
            self.hide_dock()

    def _on_row_selected(self, listbox, row):
        self._schedule_git_for_selected()
        if self.mode == MODE_SEARCH and row is not None:
            project = getattr(row, "project", None)
            if project:
                self._last_project = project
        # NOTE: do NOT grab_focus() here. The entry keeps focus while typing;
        # re-grabbing on every selection change (which fires on each
        # keystroke because the list rebuilds) was the root cause of the
        # "types only one character" bug.

    # ------------------------------------------------------------- git badge

    def _schedule_git_for_selected(self):
        self._cancel_git_timeout()

        def fetch():
            self._git_timeout = None
            row = self.listbox.get_selected_row()
            if row is None or self.mode != MODE_SEARCH:
                return GLib.SOURCE_REMOVE
            project = getattr(row, "project", None)
            if not project or not project.get("is_git"):
                return GLib.SOURCE_REMOVE
            badge = getattr(row, "git_badge", None)
            if badge is not None:
                self._spawn_git_fetch(project["path"], badge)
            return GLib.SOURCE_REMOVE

        self._git_timeout = GLib.timeout_add(150, fetch)

    def _cancel_git_timeout(self):
        if self._git_timeout is not None:
            GLib.source_remove(self._git_timeout)
            self._git_timeout = None

    # ------------------------------------------------------------- monitors
    def _start_monitors(self):
        self._stop_monitors()
        try:
            from gi.repository import Gio
            roots = self.controller.cfg.expanded_roots()
            for root in roots[:3]:  # limit to avoid explosion
                try:
                    gfile = Gio.File.new_for_path(root)
                    mon = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
                    # debounce burst
                    mon.connect("changed", self._on_dir_changed)
                    self._monitors.append(mon)
                except Exception:
                    continue
        except Exception:
            self._monitors = []

    def _stop_monitors(self):
        for mon in getattr(self, "_monitors", []):
            try:
                mon.cancel()
            except Exception:
                pass
        self._monitors = []
        if self._monitor_debounce is not None:
            try:
                GLib.source_remove(self._monitor_debounce)
            except Exception:
                pass
            self._monitor_debounce = None

    def _on_dir_changed(self, *args):
        # debounce 500ms bursts; coalesce monitor and manual rescans so
        # they do not produce overlapping work or a feedback flicker.
        if self._monitor_debounce is not None:
            try:
                GLib.source_remove(self._monitor_debounce)
            except Exception:
                pass
        def do_refresh():
            self._monitor_debounce = None
            # only refresh while visible and in search mode
            if self.mode != MODE_SEARCH:
                return GLib.SOURCE_REMOVE
            if not self.get_visible():
                return GLib.SOURCE_REMOVE
            # If a scan is already in progress (manual or monitor), let
            # it finish; the rebuild at the end of the scan will pick up
            # the new state. This avoids a feedback loop where the
            # monitor's own rescan triggers more filesystem changes.
            try:
                if self.controller.scanning:
                    return GLib.SOURCE_REMOVE
            except Exception:
                pass
            try:
                from . import discovery as _disc
                roots = self.controller.cfg.expanded_roots()
                if _disc.roots_changed(roots, self.controller.state.root_mtimes):
                    self.controller.rescan_all()
                else:
                    # still rebuild to catch any new generic empty project without mtime change? fallback
                    self.rebuild()
            except Exception:
                pass
            return GLib.SOURCE_REMOVE
        self._monitor_debounce = GLib.timeout_add(500, do_refresh)

    def _spawn_git_fetch(self, path, badge):
        def work():
            # prefer extended health, fall back to simple info
            try:
                value = gitinfo.health(path)
                if value is None:
                    value = gitinfo.info(path)
            except Exception:
                value = gitinfo.info(path)
            GLib.idle_add(self._apply_badge, badge, value)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _apply_badge(self, badge, value):
        if value is None:
            badge.set_label("")
            return GLib.SOURCE_REMOVE
        icon = "\U0000f0209"
        # support both legacy (branch, dirty) tuple and GitHealth
        try:
            # GitHealth dataclass
            branch = getattr(value, "branch", None)
            if branch is not None:
                dirty = bool(getattr(value, "dirty", False))
                untracked = int(getattr(value, "untracked", 0) or 0)
                ahead = int(getattr(value, "ahead", 0) or 0)
                behind = int(getattr(value, "behind", 0) or 0)
                clean = not dirty and untracked == 0
                color = _safe_color(self.palette["yellow"] if dirty else self.palette["green"])
                # subtle health: branch + status dot, plus ahead/behind if any
                extra = ""
                if ahead or behind:
                    parts = []
                    if ahead:
                        parts.append(f"↑{ahead}")
                    if behind:
                        parts.append(f"↓{behind}")
                    extra = " " + " ".join(parts)
                # show dot semantics: ● for dirty, ✓ (as dot green) for clean
                dot = "●" if dirty or untracked else "✓"
                # use dot with color; for clean, green ✓
                badge.set_markup(
                    f'{icon} {_escape(_clamp_branch(branch))}  '
                    f'<span foreground="{color}">{dot}</span>'
                    f'<span foreground="{_safe_color(self.palette["muted"])}">{_escape(extra)}</span>')
                return GLib.SOURCE_REMOVE
        except Exception:
            pass
        # legacy tuple fallback
        try:
            branch, dirty = value
        except Exception:
            badge.set_label("")
            return GLib.SOURCE_REMOVE
        color = _safe_color(self.palette["yellow"] if dirty else self.palette["green"])
        badge.set_markup(
            f'{icon} {_escape(_clamp_branch(branch))}  '
            f'<span foreground="{color}">\u25cf</span>')
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------- keys

    def _on_key_pressed(self, controller, keyval, keycode, state):
        action = key_action(keyval, state, self.mode)

        if action == "escape":
            if self.mode == MODE_PICKER:
                # back to the Actions menu the picker was opened from
                self.set_mode(MODE_ACTIONS)
                self.rebuild()
                self.listbox.grab_focus()
            elif self.mode == MODE_ACTIONS:
                self.set_mode(MODE_SEARCH)
                self.rebuild()
                self.entry.grab_focus()
            else:
                self.hide_dock()
            return True

        if action == "tab":
            self._toggle_mode()
            return True

        if action == "enter":
            row = self.listbox.get_selected_row()
            if row is not None and row.get_activatable():
                row.activate()
            return True

        if action == "alt-num":
            indices = self._selectable_indices()
            n = keyval - Gdk.KEY_1
            if 0 <= n < len(indices):
                row = self.listbox.get_row_at_index(indices[n])
                self.listbox.select_row(row)
                row.activate()
            return True

        if action == "ctrl:p" or action == "ctrl:t" or action == "ctrl:f" \
                or action == "ctrl:c" or action == "ctrl:r" or action == "ctrl:R" \
                or action == "ctrl:j" or action == "ctrl:k" or action == "ctrl:n" or action == "ctrl:q" \
                or action == "ctrl:P" or action == "ctrl:T" or action == "ctrl:F" or action == "ctrl:C" \
                or action == "ctrl:J" or action == "ctrl:K" or action == "ctrl:N" or action == "ctrl:Q":
            return self._on_ctrl(keyval)

        if action == "nav":
            self._move_selection(keyval)
            return True

        # action is None (printable text or unknown key): let it through so
        # the search entry and other widgets behave normally.
        return False

    def _on_ctrl(self, keyval):
        row = self.listbox.get_selected_row()
        project = getattr(row, "project", None)

        if keyval in (Gdk.KEY_p, Gdk.KEY_P) and project:
            self.controller.toggle_pin(project)
            self.rebuild()
            self._restore_selection(project["path"])
            return True
        if keyval in (Gdk.KEY_t, Gdk.KEY_T) and project:
            self.controller.open_terminal(project)
            self.hide_dock()
            return True
        if keyval in (Gdk.KEY_f, Gdk.KEY_F) and project:
            self.controller.open_folder(project)
            self.hide_dock()
            return True
        if keyval in (Gdk.KEY_c, Gdk.KEY_C) and project:
            start, end = self.entry.get_selection_bounds()
            if start < 0 or start == end:
                self.controller.copy_path(project)
                return True
            return False
        if keyval == Gdk.KEY_r:
            # Ctrl+R (no Shift) from the main search mode: prefer a
            # targeted root rescan when a relevant project path is known
            # (the selected project, or the last opened one); fall back
            # to a global rescan otherwise. The rescan utility row in the
            # search list intentionally does not have a `.project`
            # attribute, so it cleanly falls through to the global
            # rescan path.
            try:
                if self.mode == MODE_SEARCH:
                    row = self.listbox.get_selected_row()
                    proj = getattr(row, "project", None)
                    target = None
                    if proj:
                        target = proj.get("path", "")
                    else:
                        # fall back to last known project path
                        last = getattr(self, "_last_project", None)
                        if last:
                            target = last.get("path", "")
                    if target:
                        root = self.controller._root_for_path(target)
                        if root:
                            self.controller.rescan_root(root)
                            self._update_footer()
                            return True
                elif self.mode == MODE_ACTIONS and self._last_project:
                    root = self.controller._root_for_path(
                        self._last_project.get("path", ""))
                    if root:
                        self.controller.rescan_root(root)
                        self._update_footer()
                        return True
            except Exception:
                pass
            self.controller.rescan_all()
            self._update_footer()
            return True
        if keyval == Gdk.KEY_R:
            # Ctrl+Shift+R -> always global rescan (all configured roots)
            self.controller.rescan_all()
            self._update_footer()
            return True
        if keyval in (Gdk.KEY_j, Gdk.KEY_J):
            self._move_selection(Gdk.KEY_Down)
            return True
        if keyval in (Gdk.KEY_k, Gdk.KEY_K):
            self._move_selection(Gdk.KEY_Up)
            return True
        if keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self._move_selection(Gdk.KEY_Down)
            return True
        if keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            self.controller.quit()
            return True
        return False

    def _toggle_mode(self):
        if self.mode == MODE_SEARCH:
            row = self.listbox.get_selected_row()
            if row is None or not getattr(row, "project", None):
                return
            self._last_project = row.project
            self.set_mode(MODE_ACTIONS)
            self.rebuild()
            self.listbox.grab_focus()
        elif self.mode == MODE_ACTIONS:
            # cycle forward into the tool picker when it is available
            if self._open_picker():
                return
            self.set_mode(MODE_SEARCH)
            self.rebuild()
            self.entry.grab_focus()
        else:
            self.set_mode(MODE_SEARCH)
            self.rebuild()
            self.entry.grab_focus()

    def _open_picker(self):
        """Enter the Open With picker for the selected project.

        Returns False (without changing mode) when no registry tools are
        installed, so Tab keeps its meaning instead of dead-ending.
        """
        project = self._last_project
        if not project:
            return False
        try:
            has_tools = bool(self.controller.picker_rows())
        except Exception:
            has_tools = False
        if not has_tools:
            return False
        self._picker_project = project
        self.set_mode(MODE_PICKER)
        self.rebuild()
        self.listbox.grab_focus()
        return True

    def _move_selection(self, keyval):
        indices = self._selectable_indices()
        if not indices:
            return
        row = self.listbox.get_selected_row()
        current = row.get_index() if row is not None else -1
        if current not in indices:
            current = indices[0]

        if keyval == Gdk.KEY_Home:
            target = indices[0]
        elif keyval == Gdk.KEY_End:
            target = indices[-1]
        elif keyval == Gdk.KEY_Page_Down:
            target = move_in_list(current, 8, indices)
        elif keyval == Gdk.KEY_Page_Up:
            target = move_in_list(current, -8, indices)
        elif keyval == Gdk.KEY_Down:
            target = move_in_list(current, 1, indices)
        else:  # Up
            target = move_in_list(current, -1, indices)

        new_row = self.listbox.get_row_at_index(target)
        if new_row is not None:
            self.listbox.select_row(new_row)
            self._reveal_row(new_row)

    def _reveal_row(self, row):
        def adjust():
            adj = self.scrolled.get_vadjustment()
            upper = adj.get_upper() - adj.get_page_size()
            if upper > 0:
                y = row.get_allocation().y - adj.get_page_size() * 0.15
                adj.set_value(min(max(0.0, y), upper))
            return GLib.SOURCE_REMOVE
        GLib.idle_add(adjust)

    def _restore_selection(self, path):
        for i in range(MAX_VISIBLE * 4):
            row = self.listbox.get_row_at_index(i)
            if row is None:
                return
            if getattr(row, "project", None) and row.project["path"] == path:
                self.listbox.select_row(row)
                if self.mode == MODE_SEARCH:
                    self.entry.grab_focus()
                return


def _clamp_branch(branch, limit=24):
    """Keep long Git branch names from pushing the path out of the row."""
    branch = branch or ""
    return branch if len(branch) <= limit else "\u2026" + branch[-(limit - 1):]


def _short_path(path):
    try:
        import os
        home = os.path.expanduser("~")
    except Exception:
        home = None
    if home and path.startswith(home):
        display = "~" + path[len(home):]
    else:
        display = path
    parts = [p for p in display.split("/") if p]
    if len(parts) > 4:
        display = "/".join(parts[:1]) + "/\u2026/" + "/".join(parts[-2:])
        if not display.startswith("~"):
            display = "/" + display
    return display or path


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
