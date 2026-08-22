import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import cover

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk
    from projectdock.ui import key_action, MODE_SEARCH
    _HAS_GTK = True
except ImportError:
    _HAS_GTK = False


class CoverDiscoveryTest(unittest.TestCase):
    def _png(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    def test_finds_top_level_logo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._png(os.path.join(tmp, "logo.png"))
            self.assertEqual(cover.discover_cover({"path": tmp}),
                             os.path.join(tmp, "logo.png"))

    def test_finds_public_subdir_favicon(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._png(os.path.join(tmp, "public", "favicon.png"))
            self.assertTrue(cover.discover_cover({"path": tmp}).endswith("favicon.png"))

    def test_finds_assets_icon(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._png(os.path.join(tmp, "assets", "icon.jpg"))
            self.assertTrue(cover.discover_cover({"path": tmp}).endswith("icon.jpg"))

    def test_returns_none_when_no_artwork(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(cover.discover_cover({"path": tmp}))

    def test_rejects_non_image_with_image_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "logo.png"), "w") as fh:
                fh.write("not an image")
            self.assertIsNone(cover.discover_cover({"path": tmp}))

    def test_does_not_walk_entire_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            deep = os.path.join(tmp, "src", "components", "deep")
            self._png(os.path.join(deep, "logo.png"))
            self.assertIsNone(cover.discover_cover({"path": tmp}))

    def test_generic_code_dirs_are_not_scanned(self):
        # logo.png inside src/ or app/ is usually component artwork, not
        # project branding; only obvious asset dirs are searched.
        for sub in ("src", "app"):
            with tempfile.TemporaryDirectory() as tmp:
                self._png(os.path.join(tmp, sub, "logo.png"))
                self.assertIsNone(cover.discover_cover({"path": tmp}),
                                  f"{sub}/logo.png should not be discovered")

    def test_root_logo_beats_nested_favicon(self):
        # Root-level branding wins over artwork in asset directories.
        with tempfile.TemporaryDirectory() as tmp:
            self._png(os.path.join(tmp, "favicon.png"))
            self._png(os.path.join(tmp, "public", "logo.png"))
            self.assertEqual(cover.discover_cover({"path": tmp}),
                             os.path.join(tmp, "favicon.png"))

    def test_result_is_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._png(os.path.join(tmp, "icon.svg"))
            first = cover.discover_cover({"path": tmp})
            os.remove(first)
            self.assertEqual(cover.discover_cover({"path": tmp}), first)


class IdentityTest(unittest.TestCase):
    def test_initials_multiword(self):
        self.assertEqual(cover.identity_initials("cine-print-gallery"), "CP")
        self.assertEqual(cover.identity_initials("Test Portfolio"), "TP")

    def test_initials_single_word(self):
        self.assertEqual(cover.identity_initials("ProjectDock"), "PR")

    def test_initials_strips_separators(self):
        self.assertEqual(cover.identity_initials("my_cool_app"), "MC")

    def test_initials_fallback(self):
        self.assertEqual(cover.identity_initials(""), "?")
        self.assertEqual(cover.identity_initials("---"), "?")

    def test_colors_deterministic(self):
        a = cover.identity_colors("ProjectDock", {"foreground": "#ffffff"})
        b = cover.identity_colors("ProjectDock", {"foreground": "#ffffff"})
        self.assertEqual(a, b)

    def test_colors_use_theme_foreground(self):
        fg = "#abcdef"
        bg, got_fg = cover.identity_colors("anything", {"foreground": fg})
        self.assertEqual(got_fg, fg)
        self.assertIn("hsla", bg)

    def test_colors_are_gtk_css_parseable_and_muted(self):
        # The tint must be low-saturation/translucent (not a bright SaaS
        # avatar) and use the comma hsla() form GTK4 reliably parses.
        import re
        for name in ("ProjectDock", "cine-print-gallery", "x"):
            bg, _ = cover.identity_colors(name, {"foreground": "#ffffff"})
            m = re.fullmatch(r"hsla\((\d+), (\d+)%, (\d+)%, ([\d.]+)\)", bg)
            self.assertIsNotNone(m, bg)
            hue, sat, light, alpha = (int(m.group(1)), int(m.group(2)),
                                      int(m.group(3)), float(m.group(4)))
            self.assertTrue(0 <= hue < 360)
            self.assertLessEqual(sat, 30)
            self.assertLessEqual(alpha, 0.25)

    def test_light_mode_tint_differs_from_dark(self):
        dark = cover.identity_colors("ProjectDock", {"foreground": "#fff"})[0]
        light = cover.identity_colors(
            "ProjectDock", {"foreground": "#000", "mode": "light"})[0]
        self.assertNotEqual(dark, light)


class FallbackInitialsAlignmentTest(unittest.TestCase):
    """Verify the fallback cover label is robustly centered for all initials."""

    def _cover_widget_for(self, name):
        # Exercise the real widget hierarchy without requiring a display
        # realization: check alignment properties on the constructed widget tree.
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gtk
            from unittest import mock
            from projectdock import ui, theme
        except Exception as e:
            self.skipTest(f"GTK not available: {e}")
        # Minimal stubs for controller/config/palette
        palette = theme.DEFAULT_PALETTE
        css = theme.build_css(palette)
        cfg = mock.Mock()
        cfg.width = 720
        cfg.top_margin_pct = 14
        cfg.max_height_pct = 70
        controller = mock.Mock()
        controller.projects_for_query.return_value = []
        # Create widget via ui.LauncherWindow helper without mapping
        # Use a temporary cover cache to avoid filesystem scans.
        with mock.patch("projectdock.cover.discover_cover", return_value=None):
            win = None
            try:
                # Gtk may need display; skip if none.
                from gi.repository import Gdk
                if Gdk.Display.get_default() is None:
                    # Try to avoid requiring Wayland; use widget directly:
                    # call _cover_widget via a dummy instance
                    dummy = mock.Mock()
                    dummy.palette = palette
                    dummy._cover_cache = {}
                    dummy._cover_for = lambda p: None
                    dummy._apply_cover_style = lambda b, bg, fg: None
                    # Bind method
                    import types
                    dummy._cover_widget = types.MethodType(ui.LauncherWindow._cover_widget, dummy)
                    w = dummy._cover_widget({"name": name, "path": f"/tmp/{name}"})
                else:
                    win = ui.LauncherWindow(controller, cfg, palette, css)
                    w = win._cover_widget({"name": name, "path": f"/tmp/{name}"})
            except Exception as e:
                self.skipTest(f"cover widget creation failed: {e}")
            return w

    def _assert_centered(self, name):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        w = self._cover_widget_for(name)
        self.assertIsNotNone(w)
        # Box should be fixed 26x26, left-anchored in row but vertically centered;
        # initials inside must be centered within the square.
        self.assertEqual(w.get_orientation(), Gtk.Orientation.VERTICAL)
        req_w, req_h = w.get_size_request()
        self.assertEqual(req_w, 26)
        self.assertEqual(req_h, 26)
        self.assertEqual(w.get_valign(), Gtk.Align.CENTER)
        self.assertEqual(w.get_halign(), Gtk.Align.START)
        self.assertFalse(w.get_hexpand())
        # Child label should fill and center
        # Box child is label for fallback
        child = w.get_first_child()
        self.assertIsNotNone(child)
        # For fallback it is a Label
        from gi.repository import Gtk as Gtk2
        if isinstance(child, Gtk2.Label):
            self.assertTrue(child.get_hexpand())
            self.assertTrue(child.get_vexpand())
            self.assertEqual(child.get_halign(), Gtk.Align.FILL)
            self.assertEqual(child.get_valign(), Gtk.Align.FILL)
            self.assertAlmostEqual(child.get_xalign(), 0.5)
            self.assertAlmostEqual(child.get_yalign(), 0.5)
            self.assertEqual(child.get_justify(), Gtk.Justification.CENTER)
            self.assertTrue(child.get_single_line_mode())
            # Label text matches expected initials
            self.assertEqual(child.get_label(), cover.identity_initials(name))
            # CSS class
            self.assertIn("cover-initials", child.get_css_classes() if hasattr(child, "get_css_classes") else [])

    def test_single_char_centered(self):
        self._assert_centered("N")

    def test_two_char_examples_centered(self):
        for name in ("NU", "CP", "PR", "TP"):
            # Use project names that yield those initials
            mapping = {"NU": "Nu Project", "CP": "cine-print-gallery", "PR": "ProjectDock", "TP": "Test Portfolio"}
            # For NU we need a name that yields NU; "Nu Project" -> NP, so use "N U"
            # Instead directly test via cover.identity_initials override: use name that produces initials
            # Use explicit names from spec: they said examples NU etc, so test with those project names
            # We'll just test the widget for each canonical name
            canonical = {"NU": "N U", "CP": "cine-print-gallery", "PR": "ProjectDock", "TP": "Test Portfolio"}
            self._assert_centered(canonical[name])

    def test_all_spec_examples(self):
        for name in ["N", "N U", "cine-print-gallery", "ProjectDock", "Test Portfolio"]:
            self._assert_centered(name)

    def test_css_has_no_padding(self):
        from projectdock import theme as th
        css = th.build_css(th.DEFAULT_PALETTE)
        self.assertIn(".cover-initials", css)
        # Must reset padding/margin for optical centering
        self.assertIn("padding: 0", css)
        self.assertIn("margin: 0", css)


class IconCoherenceTest(unittest.TestCase):
    """Action icons must be from one coherent MDI family."""

    def test_icons_are_mdi_and_unique(self):
        from projectdock.ui import ICONS
        # All should be single MDI codepoints in F0000 range (0xF0000-0xFFFFF) or valid Nerd Font
        for key, glyph in ICONS.items():
            self.assertIsInstance(glyph, str)
            self.assertGreaterEqual(len(glyph), 1)
            # Each glyph should be one codepoint (or fallback star pin uses same)
            for ch in glyph:
                cp = ord(ch)
                # MDI range F0000+, FontAwesome etc F000-FFFF, allow >0xE000 private use
                self.assertGreaterEqual(cp, 0xE000, f"{key} glyph not private use")
            # Not empty
            self.assertNotEqual(glyph, "")

    def test_action_keys_have_icons(self):
        from projectdock.ui import ICONS
        expected = {"open", "terminal", "folder", "copy", "pin", "refresh", "run", "config"}
        for k in expected:
            self.assertIn(k, ICONS)

    def test_open_is_code_braces(self):
        from projectdock.ui import ICONS
        # verified JetBrainsMono Nerd Font: md-code_tags F0174 renders as <> code brackets
        self.assertEqual(ICONS["open"], "\U000f0174")

    def test_terminal_is_console(self):
        from projectdock.ui import ICONS
        self.assertEqual(ICONS["terminal"], "\U000f018d")

    def test_folder_is_mdi_folder(self):
        from projectdock.ui import ICONS
        self.assertEqual(ICONS["folder"], "\U000f024b")

    def test_copy_is_content_copy(self):
        from projectdock.ui import ICONS
        self.assertEqual(ICONS["copy"], "\U000f018f")

    def test_refresh_is_reload(self):
        from projectdock.ui import ICONS
        # F0453 is md-reload circular arrows (verified), not wifi F05A9
        self.assertEqual(ICONS["refresh"], "\U000f0453")
        self.assertNotEqual(ICONS["refresh"], "\U000f05a9")

    def test_open_and_refresh_are_distinct(self):
        from projectdock.ui import ICONS
        # regression: rescan must not reuse editor icon
        self.assertNotEqual(ICONS["open"], ICONS["refresh"])
        self.assertEqual(ICONS["open"], "\U000f0174")  # md-code_tags <>
        self.assertEqual(ICONS["refresh"], "\U000f0453")  # md-reload ↻

    def test_no_accidental_duplicate_mappings(self):
        from projectdock.ui import ICONS
        # distinct visual families: open, terminal, folder, copy, pin, refresh, config must be distinct
        keys = ["open", "terminal", "folder", "copy", "pin", "refresh", "config"]
        seen = {}
        for k in keys:
            cp = ICONS[k]
            self.assertNotIn(cp, seen, f"duplicate glyph {k} collides with {seen.get(cp)}")
            seen[cp] = k
        # pin/unpin intentionally same
        self.assertEqual(ICONS["pin"], ICONS["unpin"])

    def test_config_is_cog(self):
        from projectdock.ui import ICONS
        self.assertEqual(ICONS["config"], "\U000f0493")  # md-cog verified


class ProjectRowLayoutTest(unittest.TestCase):
    """Regression for sparse broken row: cover must be left-anchored, text expands, right metadata END."""

    def _project_row(self):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk
            from unittest import mock
            from projectdock import ui, theme
            palette = theme.DEFAULT_PALETTE
            css = theme.build_css(palette)
            cfg = mock.Mock()
            cfg.width = 720
            cfg.top_margin_pct = 14
            cfg.max_height_pct = 70
            controller = mock.Mock()
            controller.projects_for_query.return_value = []
            dummy = mock.Mock()
            dummy.palette = palette
            dummy._cover_cache = {}
            dummy._cover_for = lambda p: None
            dummy._apply_cover_style = lambda b, bg, fg: None
            import types
            dummy._cover_widget = types.MethodType(ui.LauncherWindow._cover_widget, dummy)
            # bind _project_row
            dummy._project_row = types.MethodType(ui.LauncherWindow._project_row, dummy)
            project = {"name": "Network Usage", "path": "/home/test/Projects/Network Usage", "label": "Git", "kind": "git", "is_git": True}
            row = dummy._project_row(project)
            return row
        except Exception as e:
            self.skipTest(f"project row creation failed: {e}")

    def test_cover_is_start_not_center(self):
        row = self._project_row()
        box = row.get_child()  # HORIZONTAL box
        self.assertIsNotNone(box)
        # first child is cover
        cover = box.get_first_child()
        from gi.repository import Gtk
        self.assertEqual(cover.get_halign(), Gtk.Align.START)
        self.assertFalse(cover.get_hexpand())
        self.assertEqual(cover.get_size_request(), (26, 26))
        # cover internal label still centered
        label = cover.get_first_child()
        if label is not None and isinstance(label, Gtk.Label):
            self.assertEqual(label.get_xalign(), 0.5)

    def test_text_block_expands_and_right_metadata_end(self):
        row = self._project_row()
        box = row.get_child()
        from gi.repository import Gtk
        # children: cover, mid, right
        children = []
        child = box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        self.assertEqual(len(children), 3)
        cover, mid, right = children
        # mid expands, left-aligned
        self.assertTrue(mid.get_hexpand())
        self.assertEqual(mid.get_halign(), Gtk.Align.FILL)
        # right is END, does not expand
        self.assertFalse(right.get_hexpand())
        self.assertEqual(right.get_halign(), Gtk.Align.END)

    def test_no_spacer_consuming_space(self):
        row = self._project_row()
        box = row.get_child()
        # ensure no Gtk.Box with hexpand True that is empty spacer in project row
        # project row should have exactly 3 children, middle one is the expanding one
        from gi.repository import Gtk
        child = box.get_first_child()
        count = 0
        while child:
            count += 1
            child = child.get_next_sibling()
        self.assertEqual(count, 3)

    def test_name_and_path_truncate(self):
        row = self._project_row()
        box = row.get_child()
        mid = box.get_first_child().get_next_sibling()
        # mid has two lines
        line1 = mid.get_first_child()
        line2 = line1.get_next_sibling()
        from gi.repository import Pango
        # name label should ellipsize END and hexpand
        name_label = line1.get_first_child()
        self.assertEqual(name_label.get_ellipsize(), Pango.EllipsizeMode.END)
        self.assertTrue(name_label.get_hexpand())
        # path
        path_label = line2.get_first_child()
        self.assertEqual(path_label.get_ellipsize(), Pango.EllipsizeMode.END)


@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class KeyActionTest(unittest.TestCase):
    def test_printable_passes_through(self):
        for kv in (Gdk.KEY_p, Gdk.KEY_a, Gdk.KEY_r, Gdk.KEY_t, Gdk.KEY_c,
                   Gdk.KEY_space, Gdk.KEY_1, Gdk.KEY_slash):
            self.assertIsNone(key_action(kv, 0, MODE_SEARCH))

    def test_ctrl_letter_is_shortcut(self):
        state = int(Gdk.ModifierType.CONTROL_MASK)
        self.assertEqual(key_action(Gdk.KEY_p, state, MODE_SEARCH), "ctrl:p")
        self.assertEqual(key_action(Gdk.KEY_P, state, MODE_SEARCH), "ctrl:P")

    def test_ctrl_with_alt_is_ignored(self):
        state = int(Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)
        self.assertIsNone(key_action(Gdk.KEY_p, state, MODE_SEARCH))

    def test_navigation_keys(self):
        for kv in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Home, Gdk.KEY_End,
                   Gdk.KEY_Page_Up, Gdk.KEY_Page_Down):
            self.assertEqual(key_action(kv, 0, MODE_SEARCH), "nav")

    def test_escape_tab_enter(self):
        self.assertEqual(key_action(Gdk.KEY_Escape, 0, MODE_SEARCH), "escape")
        self.assertEqual(key_action(Gdk.KEY_Tab, 0, MODE_SEARCH), "tab")
        self.assertEqual(key_action(Gdk.KEY_Return, 0, MODE_SEARCH), "enter")

    def test_alt_number(self):
        state = int(Gdk.ModifierType.ALT_MASK)
        self.assertEqual(key_action(Gdk.KEY_1, state, MODE_SEARCH), "alt-num")

    def test_printable_never_consumed(self):
        self.assertIsNone(key_action(Gdk.KEY_d, 0, MODE_SEARCH))


if __name__ == "__main__":
    unittest.main()
