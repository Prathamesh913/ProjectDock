import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import theme


class ThemeTest(unittest.TestCase):
    def test_default_palette(self):
        palette = theme.load_palette(name="definitely-not-a-theme")
        self.assertIn("background", palette)
        self.assertIn("accent", palette)

    def test_palette_follows_stock_theme(self):
        palette = theme.load_palette(name="vantablack")
        self.assertEqual(palette["background"], "#000000")
        self.assertTrue(palette["accent"])

    def test_css_contains_colors(self):
        palette = theme.load_palette(name="vantablack")
        css = theme.build_css(palette)
        self.assertIn("#000000", css)
        self.assertIn(".dock-box", css)
        self.assertIn("row.project-row:selected", css)

    def test_light_mode_css(self):
        palette = dict(theme.DEFAULT_PALETTE, mode="light")
        css = theme.build_css(palette)
        self.assertIn(".dock-box", css)

    def test_active_theme_name_smoke(self):
        name = theme.active_theme_name()
        self.assertIsInstance(name, (str, type(None)))

    def test_non_string_color_is_ignored(self):
        # A theme that provides a non-scalar color must not corrupt the CSS.
        palette = theme.load_palette(force={
            "background": ["#", "evil"],  # invalid -> keep default
            "accent": "#ffffff",
        })
        self.assertEqual(palette["background"], theme.DEFAULT_PALETTE["background"])
        self.assertEqual(palette["accent"], "#ffffff")

    def test_invalid_color_string_is_ignored(self):
        palette = theme.load_palette(force={
            "accent": '#fff" onload="alert(1)',
        })
        self.assertEqual(palette["accent"], theme.DEFAULT_PALETTE["accent"])

    def test_mode_is_validated(self):
        palette = theme.load_palette(force={"mode": "party"})
        self.assertEqual(palette["mode"], theme.DEFAULT_PALETTE["mode"])

    def test_case_insensitive_theme_lookup(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "cooltheme"))
            with open(os.path.join(tmp, "cooltheme", "colors.toml"), "w") as fh:
                fh.write('mode = "dark"\nbackground = "#123456"\nforeground = "#ffffff"\n')
            with mock.patch.object(theme, "_BASE_DIRS", (tmp,)):
                palette = theme.load_palette(name="CoolTheme")
                self.assertEqual(palette["background"], "#123456")

    def test_muted_is_foreground_derived(self):
        palette = dict(theme.DEFAULT_PALETTE, background="#000000", foreground="#ffffff")
        css = theme.build_css(palette)
        block = css.split(".project-name")[1].split("}")[0]
        self.assertIn("color:#ffffff;", block.replace(" ", ""))
        self.assertNotIn("accent", block)


if __name__ == "__main__":
    unittest.main()
