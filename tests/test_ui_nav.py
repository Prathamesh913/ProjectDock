import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from projectdock.ui import move_in_list, _safe_color
    _HAS_GTK = True
except ImportError:
    _HAS_GTK = False


@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class NavHelperTest(unittest.TestCase):
    def test_forward(self):
        self.assertEqual(move_in_list(2, 1, [0, 1, 2, 3]), 3)

    def test_wrap_forward(self):
        self.assertEqual(move_in_list(3, 1, [0, 1, 2, 3]), 0)

    def test_wrap_backward(self):
        self.assertEqual(move_in_list(0, -1, [0, 1, 2, 3]), 3)

    def test_empty(self):
        self.assertIsNone(move_in_list(0, 1, []))

    def test_current_not_in_list_returns_first(self):
        self.assertEqual(move_in_list(99, 1, [0, 1, 2]), 0)

    def test_skips_over_nonselectable_rows(self):
        # Selectable rows live at indices 1, 3, 5 (headers at 0, 2, 4).
        selectable = [1, 3, 5]
        self.assertEqual(move_in_list(1, 1, selectable), 3)
        self.assertEqual(move_in_list(3, -1, selectable), 1)
        self.assertEqual(move_in_list(5, 1, selectable), 1)  # wraps past headers


class SafeColorTest(unittest.TestCase):
    def test_valid_hex(self):
        self.assertEqual(_safe_color("#a6da95"), "#a6da95")

    def test_injection_is_neutralized(self):
        self.assertEqual(_safe_color('#fff" onclick="alert(1)'), "#a5adcb")

    def test_non_string(self):
        self.assertEqual(_safe_color(123), "#a5adcb")
        self.assertEqual(_safe_color(None), "#a5adcb")


if __name__ == "__main__":
    unittest.main()
