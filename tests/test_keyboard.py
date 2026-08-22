"""Regression tests for the text-input bug.

The launcher must behave like a normal text field: typing a word such as
"projectdock" has to insert every character into the search entry. The
rule enforced here is that unmodified printable keys are NEVER classified
as shortcuts (they pass through to the focused entry), while explicit
modifier combinations and navigation keys are.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import ui

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk
    from projectdock.ui import key_action, MODE_SEARCH, MODE_ACTIONS
    _HAS_GTK = True
except ImportError:
    _HAS_GTK = False


@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class PrintableTextPassesThroughTest(unittest.TestCase):
    """Unmodified printable keys must never trigger application shortcuts."""

    def _assert_passthrough(self, keyval, state=0):
        for mode in (MODE_SEARCH, MODE_ACTIONS):
            self.assertIsNone(key_action(keyval, state, mode))

    def test_typing_projectdock_inserts_every_character(self):
        word = "projectdock"
        for ch in word:
            keyval = Gdk.unicode_to_keyval(ord(ch))
            self.assertIsNone(key_action(keyval, 0, MODE_SEARCH),
                              f"'{ch}' was consumed as a shortcut")

    def test_single_shortcut_letters_are_text(self):
        # p r t c a were the reported offenders: they must be search text.
        for ch in "prtca":
            self._assert_passthrough(Gdk.unicode_to_keyval(ord(ch)))

    def test_digits_punctuation_and_space_are_text(self):
        for ch in "0123456789 .-_/?:!@#$%^&*()[]<>,;'\"=`~+|\\":
            self._assert_passthrough(Gdk.unicode_to_keyval(ord(ch)))

    def test_uppercase_and_shifted_keys_are_text(self):
        shift = int(Gdk.ModifierType.SHIFT_MASK)
        for upper in "PRTCA":
            keyval = Gdk.unicode_to_keyval(ord(upper))
            self.assertIsNone(key_action(keyval, shift, MODE_SEARCH))

    def test_numlock_and_capslock_state_does_not_change_classification(self):
        lock = int(Gdk.ModifierType.MOD2_MASK) if hasattr(
            Gdk.ModifierType, "MOD2_MASK") else int(Gdk.ModifierType.LOCK_MASK)
        for ch in "projectdock1":
            keyval = Gdk.unicode_to_keyval(ord(ch))
            self.assertIsNone(key_action(keyval, lock, MODE_SEARCH))


@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class ModifierShortcutsTest(unittest.TestCase):
    """Only explicit modifier combinations may trigger shortcuts."""

    CTRL = int(Gdk.ModifierType.CONTROL_MASK)
    ALT = int(Gdk.ModifierType.ALT_MASK)

    def test_ctrl_combos_classified(self):
        self.assertEqual(key_action(Gdk.KEY_p, self.CTRL, MODE_SEARCH), "ctrl:p")
        self.assertEqual(key_action(Gdk.KEY_t, self.CTRL, MODE_SEARCH), "ctrl:t")
        self.assertEqual(key_action(Gdk.KEY_f, self.CTRL, MODE_SEARCH), "ctrl:f")
        self.assertEqual(key_action(Gdk.KEY_c, self.CTRL, MODE_SEARCH), "ctrl:c")
        self.assertEqual(key_action(Gdk.KEY_r, self.CTRL, MODE_SEARCH), "ctrl:r")
        self.assertEqual(key_action(Gdk.KEY_q, self.CTRL, MODE_SEARCH), "ctrl:q")

    def test_alt_numbers(self):
        self.assertEqual(key_action(Gdk.KEY_1, self.ALT, MODE_SEARCH), "alt-num")
        self.assertEqual(key_action(Gdk.KEY_9, self.ALT, MODE_SEARCH), "alt-num")
        self.assertIsNone(key_action(Gdk.KEY_0, self.ALT, MODE_SEARCH))
        self.assertIsNone(key_action(Gdk.KEY_q, self.ALT, MODE_SEARCH))

    def test_reserved_keys_regardless_of_mode(self):
        for mode in (MODE_SEARCH, MODE_ACTIONS):
            self.assertEqual(key_action(Gdk.KEY_Escape, 0, mode), "escape")
            self.assertEqual(key_action(Gdk.KEY_Tab, 0, mode), "tab")
            self.assertEqual(key_action(Gdk.KEY_Return, 0, mode), "enter")

    def test_navigation_in_both_modes(self):
        # Navigation must work in both search and actions modes; previously
        # actions mode incorrectly returned None and relied on implicit Gtk
        # handling which broke Up/Down wrapping and Home/End.
        nav_keys = (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Home, Gdk.KEY_End,
                    Gdk.KEY_Page_Up, Gdk.KEY_Page_Down)
        for kv in nav_keys:
            self.assertEqual(key_action(kv, 0, MODE_SEARCH), "nav")
            self.assertEqual(key_action(kv, 0, MODE_ACTIONS), "nav")


@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class ActionsModeKeyboardTest(unittest.TestCase):
    """Actions menu keyboard state model (issues #2)."""

    def test_tab_is_always_tab(self):
        for mode in (MODE_SEARCH, MODE_ACTIONS):
            self.assertEqual(key_action(Gdk.KEY_Tab, 0, mode), "tab")
            self.assertEqual(key_action(Gdk.KEY_ISO_Left_Tab, 0, mode), "tab")

    def test_up_down_navigate_actions(self):
        for kv in (Gdk.KEY_Up, Gdk.KEY_Down):
            self.assertEqual(key_action(kv, 0, MODE_ACTIONS), "nav")
            self.assertEqual(key_action(kv, 0, MODE_SEARCH), "nav")

    def test_home_end_navigate_actions(self):
        for kv in (Gdk.KEY_Home, Gdk.KEY_End):
            self.assertEqual(key_action(kv, 0, MODE_ACTIONS), "nav")
            self.assertEqual(key_action(kv, 0, MODE_SEARCH), "nav")

    def test_page_keys_navigate(self):
        for kv in (Gdk.KEY_Page_Up, Gdk.KEY_Page_Down):
            self.assertEqual(key_action(kv, 0, MODE_ACTIONS), "nav")

    def test_enter_and_escape_in_actions(self):
        self.assertEqual(key_action(Gdk.KEY_Return, 0, MODE_ACTIONS), "enter")
        self.assertEqual(key_action(Gdk.KEY_KP_Enter, 0, MODE_ACTIONS), "enter")
        self.assertEqual(key_action(Gdk.KEY_Escape, 0, MODE_ACTIONS), "escape")

    def test_printable_never_consumed_in_either_mode(self):
        for ch in "prtca12 .-":
            kv = Gdk.unicode_to_keyval(ord(ch))
            self.assertIsNone(key_action(kv, 0, MODE_SEARCH))
            self.assertIsNone(key_action(kv, 0, MODE_ACTIONS))
        # shifted
        shift = int(Gdk.ModifierType.SHIFT_MASK)
        for ch in "PRTCA":
            kv = Gdk.unicode_to_keyval(ord(ch))
            self.assertIsNone(key_action(kv, shift, MODE_SEARCH))
            self.assertIsNone(key_action(kv, shift, MODE_ACTIONS))

    def test_move_in_list_wraps_for_actions(self):
        from projectdock.ui import move_in_list
        items = [0, 1, 2, 3, 4]
        self.assertEqual(move_in_list(0, -1, items), 4)
        self.assertEqual(move_in_list(4, 1, items), 0)
        self.assertEqual(move_in_list(2, 1, items), 3)
        self.assertEqual(move_in_list(2, -1, items), 1)

    def test_ctrl_c_is_ctrl_prefix(self):
        ctrl = int(Gdk.ModifierType.CONTROL_MASK)
        self.assertEqual(key_action(Gdk.KEY_c, ctrl, MODE_SEARCH), "ctrl:c")
        self.assertEqual(key_action(Gdk.KEY_C, ctrl, MODE_SEARCH), "ctrl:C")
        # In actions mode also classified as ctrl (handler decides selection)
        self.assertEqual(key_action(Gdk.KEY_c, ctrl, MODE_ACTIONS), "ctrl:c")


@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class BranchClampTest(unittest.TestCase):
    def test_short_branch_unchanged(self):
        self.assertEqual(ui._clamp_branch("main"), "main")

    def test_long_branch_is_head_truncated(self):
        long_branch = "fix/very-long-descriptive-branch-name"
        clamped = ui._clamp_branch(long_branch)
        self.assertLessEqual(len(clamped), 24)
        self.assertTrue(clamped.startswith("\u2026"))
        self.assertTrue(long_branch.endswith(clamped[1:]))

    def test_none_is_empty(self):
        self.assertEqual(ui._clamp_branch(None), "")


if __name__ == "__main__":
    unittest.main()
