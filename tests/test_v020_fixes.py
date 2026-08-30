"""Regression tests for v0.2.0 performance & action correctness fixes.

Covers:
- Async workspace refresh (no blocking on _show critical path)
- tool:* action routing (exact tool launch, no fallback to open_default)
- Search debounce and annotation cache invalidation
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from projectdock.app import DockApp
    from projectdock.workspace import WorkspaceStore
    from projectdock import state as state_mod
    from projectdock import tools, hyprland
    _HAS_GTK = True
except ImportError:
    _HAS_GTK = False


# ---------------------------------------------------------------------------
# Phase 2 — Async workspace refresh
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class AsyncWorkspaceRefreshTest(unittest.TestCase):
    """Verify _show() does not synchronously invoke hyprctl on the main thread."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "cfg"),
            "XDG_STATE_HOME": os.path.join(self.tmp.name, "state"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = DockApp()

    def test_show_does_not_call_refresh_syncronously(self):
        """_show() must NOT synchronously call workspace.refresh()."""
        with mock.patch.object(self.app, "_ensure_window") as ensure, \
             mock.patch.object(self.app.workspace, "refresh") as refresh, \
             mock.patch.object(self.app.workspace, "collect") as collect, \
             mock.patch.object(self.app, "_refresh_workspace_async") as async_ref, \
             mock.patch.object(self.app, "_maybe_rescan"), \
             mock.patch.object(self.app, "_start_focus_poll"), \
             mock.patch.object(self.app, "window") as mock_window:
            self.app._show()
            # Synchronous refresh must NOT be called
            refresh.assert_not_called()
            # Async refresh should be scheduled
            async_ref.assert_called_once()

    def test_refresh_workspace_async_starts_background_thread(self):
        """_refresh_workspace_async must start a daemon thread."""
        with mock.patch.object(self.app, "window") as mock_window, \
             mock.patch("threading.Thread") as MockThread:
            mock_thread = mock.Mock()
            MockThread.return_value = mock_thread
            self.app._refresh_workspace_async()
            MockThread.assert_called_once()
            mock_thread.start.assert_called_once()
            # Verify daemon=True
            _, kwargs = MockThread.call_args
            self.assertTrue(kwargs.get("daemon", False))

    def test_apply_workspace_refresh_respects_generation(self):
        """Stale generation results must be discarded."""
        ws = self.app.workspace
        # Start a refresh (bumps generation to 1)
        gen = ws.start_refresh()
        self.assertEqual(gen, 1)
        # Simulate a second refresh (bumps to 2)
        gen2 = ws.start_refresh()
        self.assertEqual(gen2, 2)
        # Try to apply result from generation 1 — should be discarded
        result = {"generation": 1, "assoc": {"/fake": [{"addr": "0x1"}]}, "projects": []}
        ws.apply(result)
        # Ephemeral active should NOT contain /fake
        self.assertNotIn("/fake", ws._ephemeral_active)

    def test_apply_workspace_refresh_applies_current_generation(self):
        """Current generation results must be applied."""
        ws = self.app.workspace
        gen = ws.start_refresh()
        result = {"generation": gen, "assoc": {"/fake": [{"addr": "0x1"}]}, "projects": []}
        ws.apply(result)
        self.assertIn("/fake", ws._ephemeral_active)

    def test_collect_does_not_mutate_instance(self):
        """collect() must not touch _ephemeral_active or other mutable state."""
        ws = self.app.workspace
        original = dict(ws._ephemeral_active)
        projects = [{"path": "/tmp/test"}]
        with mock.patch("projectdock.workspace.hyprland.clients", return_value=[]):
            result = ws.collect(projects)
        self.assertEqual(dict(ws._ephemeral_active), original)
        self.assertIn("generation", result)
        self.assertIn("assoc", result)


# ---------------------------------------------------------------------------
# Phase 3 — tool:* action correctness
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class ToolActionTest(unittest.TestCase):
    """Verify tool:* actions launch the exact tool, not open_default."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "cfg"),
            "XDG_STATE_HOME": os.path.join(self.tmp.name, "state"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = DockApp()
        self.project = {"path": "/tmp/fake-project", "name": "fake", "kind": "node"}

    def test_tool_zed_calls_launch_tool(self):
        """tool:zed must call launch_tool, not open_default."""
        with mock.patch.object(self.app, "launch_tool", return_value=True) as launch, \
             mock.patch.object(self.app, "open_default") as open_def, \
             mock.patch.object(self.app, "_touch"), \
             mock.patch.object(self.app, "_record_workspace"):
            self.app.run_action("tool:zed", self.project)
            launch.assert_called_once_with(self.project, "zed")
            open_def.assert_not_called()

    def test_tool_unknown_falls_back_safely(self):
        """tool:does-not-exist should fail safely, no crash."""
        with mock.patch.object(self.app, "launch_tool", return_value=False) as launch, \
             mock.patch.object(self.app, "open_default") as open_def, \
             mock.patch.object(self.app, "_touch"), \
             mock.patch.object(self.app, "_record_workspace"):
            # Should not raise
            self.app.run_action("tool:does-not-exist", self.project)
            launch.assert_called_once_with(self.project, "does-not-exist")
            # Falls back to open_default when launch_tool returns False
            open_def.assert_called_once_with(self.project)

    def test_tool_vscode_calls_launch_tool(self):
        """tool:vscode must call launch_tool with exact id."""
        with mock.patch.object(self.app, "launch_tool", return_value=True) as launch, \
             mock.patch.object(self.app, "open_default") as open_def, \
             mock.patch.object(self.app, "_touch"), \
             mock.patch.object(self.app, "_record_workspace"):
            self.app.run_action("tool:vscode", self.project)
            launch.assert_called_once_with(self.project, "vscode")
            open_def.assert_not_called()

    def test_tool_action_does_not_reach_open_default_directly(self):
        """tool:* must NEVER fall through to open_default without going through launch_tool."""
        with mock.patch.object(self.app, "open_default") as open_def, \
             mock.patch.object(self.app, "_touch"), \
             mock.patch.object(self.app, "_record_workspace"), \
             mock.patch.object(self.app, "launch_tool", return_value=True):
            self.app.run_action("tool:zed", self.project)
            open_def.assert_not_called()

    def test_editor_action_still_works(self):
        """editor:* actions should still work as before."""
        import subprocess as _subprocess
        with mock.patch.object(self.app, "_touch"), \
             mock.patch.object(self.app, "_record_workspace"), \
             mock.patch.object(self.app, "_track_editor"), \
             mock.patch.object(_subprocess, "Popen") as popen_mock:
            popen_mock.return_value = mock.Mock(pid=12345)
            self.app.run_action("editor:nvim", self.project)
            popen_mock.assert_called_once()


@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class OpenDefaultToolPriorityTest(unittest.TestCase):
    """Verify open_default checks preferred tool before default editor."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "cfg"),
            "XDG_STATE_HOME": os.path.join(self.tmp.name, "state"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = DockApp()
        self.project = {"path": "/tmp/fake-project", "name": "fake", "kind": "node"}

    def test_open_default_uses_preferred_tool(self):
        """open_default should use preferred tool when available."""
        fake_tool = tools.Tool("zed", "Zed", "editor",
                               probe=("zeditor", "zed"),
                               args=("{exe}", "{path}"))
        with mock.patch.object(self.app, "preferred_tool_for", return_value=fake_tool), \
             mock.patch.object(self.app, "launch_tool", return_value=True) as launch, \
             mock.patch.object(self.app, "_touch"), \
             mock.patch.object(self.app, "_record_workspace"), \
             mock.patch("projectdock.app.actions.open_in_editor") as open_ed:
            self.app.open_default(self.project)
            launch.assert_called_once_with(self.project, "zed")
            open_ed.assert_not_called()

    def test_open_default_falls_back_without_preferred_tool(self):
        """open_default should fall back to editor when no preferred tool."""
        with mock.patch.object(self.app, "preferred_tool_for", return_value=None), \
             mock.patch.object(self.app, "_touch"), \
             mock.patch.object(self.app, "_record_workspace"), \
             mock.patch("projectdock.app.actions.open_in_editor") as open_ed:
            self.app.open_default(self.project)
            open_ed.assert_called_once()


# ---------------------------------------------------------------------------
# Phase 4 — Search debounce & annotation cache
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class AnnotationCacheTest(unittest.TestCase):
    """Verify annotation cache invalidation and reuse."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "cfg"),
            "XDG_STATE_HOME": os.path.join(self.tmp.name, "state"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = DockApp()
        self.project = {"path": "/tmp/fake", "name": "fake", "kind": "node"}

    def test_annotation_cache_reused_for_same_query(self):
        """Same query should reuse cached annotated list."""
        self.app.state.projects = [self.project]
        # First call builds cache
        r1 = self.app.projects_for_query("fak")
        # Second call should hit cache
        r2 = self.app.projects_for_query("fak")
        self.assertEqual(len(r1), len(r2))

    def test_annotation_cache_invalidated_on_pin(self):
        """Pin/unpin must invalidate annotation cache."""
        self.app.state.projects = [{"path": "/tmp/a", "name": "a", "kind": "node"}]
        # Build cache
        self.app.projects_for_query("")
        old_ver = self.app._annotation_version
        self.app.toggle_pin(self.project)
        self.assertGreater(self.app._annotation_version, old_ver)

    def test_annotation_cache_invalidated_on_touch(self):
        """Touch (recents change) must invalidate cache."""
        self.app.state.projects = []
        old_ver = self.app._annotation_version
        self.app._touch(self.project)
        self.assertGreater(self.app._annotation_version, old_ver)

    def test_invalidate_annotation_bumps_version(self):
        """invalidate_annotation must bump version and clear cache."""
        self.app._annotation_cache = (1, [])
        old_ver = self.app._annotation_version
        self.app.invalidate_annotation()
        self.assertGreater(self.app._annotation_version, old_ver)
        self.assertIsNone(self.app._annotation_cache)


@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class SearchDebounceTest(unittest.TestCase):
    """Verify search debounce behavior on LauncherWindow."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "cfg"),
            "XDG_STATE_HOME": os.path.join(self.tmp.name, "state"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = DockApp()
        self.app._ensure_window()

    def test_search_changed_schedules_rebuild(self):
        """Typing should schedule a debounced rebuild, not call rebuild directly."""
        with mock.patch.object(self.app.window, "rebuild") as rebuild, \
             mock.patch("projectdock.ui.GLib.timeout_add", return_value=42) as timeout_add:
            self.app.window._on_search_changed(self.app.window.entry)
            # Should NOT call rebuild directly
            rebuild.assert_not_called()
            # Should schedule via GLib.timeout_add
            timeout_add.assert_called()

    def test_debounce_cancels_previous(self):
        """Rapid keystrokes should cancel previous timeout."""
        with mock.patch("projectdock.ui.GLib.timeout_add", return_value=42) as timeout_add, \
             mock.patch("projectdock.ui.GLib.source_remove") as source_remove:
            self.app.window._schedule_search_rebuild()
            self.assertEqual(self.app.window._rebuild_timeout, 42)
            # Second call should cancel the first
            timeout_add.return_value = 99
            self.app.window._schedule_search_rebuild()
            source_remove.assert_called_with(42)
            self.assertEqual(self.app.window._rebuild_timeout, 99)

    def test_cancel_search_rebuild_clears_timeout(self):
        """_cancel_search_rebuild should clear the timeout ID."""
        self.app.window._rebuild_timeout = 42
        with mock.patch("projectdock.ui.GLib.source_remove") as source_remove:
            self.app.window._cancel_search_rebuild()
            source_remove.assert_called_with(42)
            self.assertIsNone(self.app.window._rebuild_timeout)

    def test_rebuild_not_called_when_hidden(self):
        """Scheduled rebuild should not fire when window is hidden."""
        self.app.window._rebuild_timeout = None
        with mock.patch.object(self.app.window, "get_visible", return_value=False), \
             mock.patch.object(self.app.window, "rebuild") as rebuild:
            result = self.app.window._run_scheduled_rebuild()
            rebuild.assert_not_called()
            # Should still return SOURCE_REMOVE to stop the timeout
            from gi.repository import GLib
            self.assertEqual(result, GLib.SOURCE_REMOVE)


# ---------------------------------------------------------------------------
# Workspace collect/apply unit tests
# ---------------------------------------------------------------------------

class WorkspaceCollectApplyTest(unittest.TestCase):
    """Test collect/apply split without GTK dependency."""

    def setUp(self):
        self.ws = WorkspaceStore()

    def test_collect_with_no_clients(self):
        with mock.patch("projectdock.workspace.hyprland.clients", return_value=[]):
            result = self.ws.collect([])
        self.assertEqual(result["assoc"], {})

    def test_apply_filters_to_known_projects(self):
        projects = [{"path": "/a"}, {"path": "/b"}]
        self.ws._refresh_generation = 1
        # collect() already filters assoc by known_paths
        with mock.patch("projectdock.workspace.hyprland.clients",
                         return_value=[{"address": "0x1", "pid": 1},
                                       {"address": "0x2", "pid": 2}]), \
             mock.patch("projectdock.workspace.hyprland.associate_clients_to_projects",
                         return_value={"/a": [{"addr": "0x1"}],
                                      "/unknown": [{"addr": "0x2"}]}):
            result = self.ws.collect(projects)
        self.ws.apply(result)
        self.assertIn("/a", self.ws._ephemeral_active)
        self.assertNotIn("/unknown", self.ws._ephemeral_active)

    def test_stale_generation_not_applied(self):
        self.ws._refresh_generation = 5
        result = {
            "generation": 3,
            "assoc": {"/a": [{"addr": "0x1"}]},
            "projects": [],
        }
        self.ws.apply(result)
        self.assertEqual(self.ws._ephemeral_active, {})

    def test_start_refresh_increments_generation(self):
        g1 = self.ws.start_refresh()
        g2 = self.ws.start_refresh()
        self.assertEqual(g2, g1 + 1)

    def test_start_refresh_sets_in_flight(self):
        self.ws._refresh_in_flight = False
        self.ws.start_refresh()
        self.assertTrue(self.ws._refresh_in_flight)

    def test_apply_clears_in_flight(self):
        self.ws._refresh_in_flight = True
        gen = self.ws.start_refresh()
        result = {"generation": gen, "assoc": {}, "projects": []}
        self.ws.apply(result)
        self.assertFalse(self.ws._refresh_in_flight)


if __name__ == "__main__":
    unittest.main()
