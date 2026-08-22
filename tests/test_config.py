import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import config, paths, state


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "cfg"),
            "XDG_STATE_HOME": os.path.join(self.tmp.name, "state"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_first_run_creates_default_config(self):
        cfg = config.load()
        self.assertTrue(os.path.isfile(paths.config_file()))
        self.assertEqual(cfg.max_depth, 4)
        self.assertIsInstance(cfg.roots, list)

    def test_default_roots_only_existing_dirs(self):
        roots = config.default_roots()
        for root in roots:
            self.assertTrue(os.path.isdir(os.path.expanduser(root)))

    def test_save_load_roundtrip(self):
        cfg = config.load()
        cfg.roots = ["~/Projects", "~/Code"]
        cfg.editor = "zeditor"
        cfg.width = 900
        config.save(cfg)
        loaded = config.load()
        self.assertEqual(loaded.roots, ["~/Projects", "~/Code"])
        self.assertEqual(loaded.editor, "zeditor")
        self.assertEqual(loaded.width, 900)

    def test_invalid_toml_falls_back(self):
        os.makedirs(os.path.dirname(paths.config_file()), exist_ok=True)
        with open(paths.config_file(), "w") as fh:
            fh.write("this is :: not toml")
        cfg = config.load()
        self.assertEqual(cfg.max_depth, 4)

    def test_expanded_roots_filters_missing(self):
        cfg = config.load()
        cfg.roots = ["~/Projects", "~/does-not-exist-xyz"]
        expanded = cfg.expanded_roots()
        self.assertTrue(all(os.path.isdir(r) for r in expanded))
        self.assertNotIn("does-not-exist-xyz", " ".join(expanded))

    def test_detected_editor_omarchy_fallback(self):
        cfg = config.load()
        cfg.editor = "zeditor"
        self.assertEqual(cfg.detected_editor(), ["zeditor"])

    def test_terminal_command(self):
        cfg = config.load()
        cfg.terminal = "foot --working-directory {dir}"
        cmd = cfg.terminal_command("/tmp/x")
        self.assertIn("/tmp/x", cmd)

    def test_roots_with_non_string_entries_filtered(self):
        os.makedirs(os.path.dirname(paths.config_file()), exist_ok=True)
        with open(paths.config_file(), "w") as fh:
            fh.write('[general]\nroots = ["~/Projects", 123, "", "~/Code"]\n')
        cfg = config.load()
        self.assertEqual(cfg.roots, ["~/Projects", "~/Code"])

    def test_roots_as_string_ignored(self):
        os.makedirs(os.path.dirname(paths.config_file()), exist_ok=True)
        with open(paths.config_file(), "w") as fh:
            fh.write('[general]\nroots = "~/Projects"\n')
        cfg = config.load()
        self.assertEqual(cfg.roots, config.default_roots())


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "cfg"),
            "XDG_STATE_HOME": os.path.join(self.tmp.name, "state"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_pin_unpin(self):
        st = state.State()
        st.pin("/a")
        st.pin("/b")
        st.pin("/a")
        self.assertEqual(st.pins, ["/b", "/a"])
        st.unpin("/a")
        self.assertEqual(st.pins, ["/b"])
        self.assertFalse(st.is_pinned("/a"))

    def test_recents_capped_and_deduped(self):
        st = state.State()
        for i in range(40):
            st.touch_recent(f"/p{i}")
        self.assertEqual(len(st.recents), state.MAX_RECENTS)
        self.assertEqual(st.recents[0]["path"], "/p39")
        st.touch_recent("/p39")
        self.assertEqual(st.recents[0]["path"], "/p39")
        paths_ = [r["path"] for r in st.recents]
        self.assertEqual(len(paths_), len(set(paths_)))

    def test_recent_rank(self):
        st = state.State()
        st.touch_recent("/a")
        st.touch_recent("/b")
        self.assertEqual(st.recent_rank("/b"), 0)
        self.assertEqual(st.recent_rank("/a"), 1)
        self.assertIsNone(st.recent_rank("/c"))

    def test_annotate(self):
        st = state.State()
        st.pin("/pinned")
        st.touch_recent("/recent")
        projects = [{"path": "/pinned"}, {"path": "/recent"}, {"path": "/other"}]
        st.annotate(projects)
        self.assertTrue(projects[0]["pinned"])
        self.assertEqual(projects[0]["pin_order"], 0)
        self.assertEqual(projects[1]["recent_rank"], 0)
        self.assertFalse(projects[2]["pinned"])

    def test_persistence(self):
        st = state.State()
        st.pin("/a")
        st.touch_recent("/b")
        st.update_projects([{"path": "/a", "name": "a"}], 123.0, {"/r": 1.0})
        state.save(st)
        loaded = state.load()
        self.assertEqual(loaded.pins, ["/a"])
        self.assertEqual(loaded.recents[0]["path"], "/b")
        self.assertEqual(loaded.projects[0]["name"], "a")
        self.assertEqual(loaded.scanned_at, 123.0)

    def test_load_corrupt_file(self):
        st = state.State()
        st.pin("/x")
        state.save(st)
        with open(paths.state_file(), "w") as fh:
            fh.write("{broken json")
        loaded = state.load()
        self.assertEqual(loaded.pins, [])

    def test_corrupt_projects_filtered(self):
        st = state.State()
        st.update_projects(
            [{"path": "/ok", "name": "ok"},
             {"name": "missing path"},
             "not-a-dict",
             None,
             42],
            1.0, {})
        state.save(st)
        loaded = state.load()
        self.assertEqual([p["path"] for p in loaded.projects], ["/ok"])

    def test_corrupt_projects_not_a_list(self):
        st = state.State()
        st.update_projects([{"path": "/x"}], 1.0, {})
        state.save(st)
        with open(paths.cache_file(), "w") as fh:
            json.dump({"projects": {"path": "/x"}, "scanned_at": 1.0}, fh)
        loaded = state.load()
        self.assertEqual(loaded.projects, [])

    def test_corrupt_scanned_at(self):
        st = state.State()
        st.update_projects([{"path": "/x"}], 1.0, {})
        state.save(st)
        with open(paths.cache_file(), "w") as fh:
            json.dump({"projects": [{"path": "/x"}], "scanned_at": "garbage"}, fh)
        loaded = state.load()
        self.assertEqual(loaded.scanned_at, 0.0)
        self.assertEqual(len(loaded.projects), 1)


if __name__ == "__main__":
    unittest.main()
