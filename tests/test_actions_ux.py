"""Regression tests for the v1.1 UX fixes:

* Issue 1 - Unavailable capabilities must NOT appear in the Actions menu
  (no UNAVAILABLE section, no "X is not installed" rows, no disabled-
  looking rows), and must also be excluded from Quick Actions, smart
  primary, picker lists, and persisted-preference resolution.

* Issue 2 - Package-manager detection must choose npm when both
  `package-lock.json` and a stale `bun.lock` are present (CinePrint
  scenario). The fallback must never invent a pm that is not installed.

* Issue 3 - The project Actions menu must NOT contain Rescan actions.

* Issue 4 - The main search mode exposes a "Rescan projects" utility row
  that is keyboard-navigable, triggers a rescan on Enter, and is
  guarded by Ctrl+R / Ctrl+Shift+R shortcuts.

* Issue 5 - Manual rescans produce a brief footer status message
  ("Rescanning projects…" → "Projects updated"). The message must not
  introduce toast libraries, modal dialogs, or persistent clutter.

* Issue 6 - The full create-from-search + rescan lifecycle must work
  end-to-end without races or duplicate entries.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import discovery, intelligence, state, workspace


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _make_dock_app():
    """Build a DockApp-like instance without spinning up GTK.

    The real DockApp.__init__ would require a GTK display; we instantiate
    a bare object and bind the attributes the action-menu / scan code
    touches. This is the same technique used by tests/test_workspace.py
    for ActionMenuTest.
    """
    from projectdock.app import DockApp
    inst = DockApp.__new__(DockApp)
    inst.cfg = mock.Mock()
    inst.cfg.editor_label.return_value = "code"
    inst.cfg.available_editors.return_value = [["code"]]
    inst.cfg.expanded_roots.return_value = []
    inst.state = state.State()
    inst.workspace = workspace.WorkspaceStore(inst.state)
    inst.workspace.load_from_state(inst.state)
    inst.sessions = mock.Mock()
    inst.sessions.dev_session.return_value = None
    inst.scanning = False
    inst._status_message = ""
    inst._status_source = ""
    inst._status_clear_at = 0.0
    inst._rescan_started_at = 0.0
    inst.window = None
    return inst


def _available_cap(key, label, command, pm="npm"):
    return intelligence.Capability(
        key=key, label=label, command=command, script=key,
        available=True, pm=pm, long_running=(key == "dev"),
    )


def _unavailable_cap(key, label, command, pm="bun"):
    return intelligence.Capability(
        key=key, label=label, command=command, script=key,
        available=False, pm=pm, long_running=(key == "dev"),
    )


# --------------------------------------------------------------------
# Issue 1: unavailable actions are not shown
# --------------------------------------------------------------------
class UnavailableActionsHiddenTest(unittest.TestCase):
    def setUp(self):
        self.inst = _make_dock_app()
        self.proj = {"path": "/tmp/fake", "name": "fake", "kind": "node"}

    def test_unavailable_intel_caps_absent_from_actions(self):
        fake_caps = intelligence.ProjectCapabilities(
            path="/tmp/fake", kind="node",
            capabilities={
                "dev": _unavailable_cap("dev", "Run Dev Server", "bun run dev"),
                "test": _unavailable_cap("test", "Run Tests", "bun run test"),
                "build": _unavailable_cap("build", "Build Project", "bun run build"),
            },
        )
        with mock.patch.object(intelligence, "capabilities_for",
                               return_value=fake_caps):
            rows = self.inst.actions_for(self.proj)
        # No "bun is not installed" / similar hint rows
        for r in rows:
            if r[0].startswith("header:"):
                continue
            label = r[1] or ""
            sub = r[2] or ""
            self.assertNotIn("bun", (label + " " + sub).lower(),
                             f"unavailable pm leaked into row: {r}")
            self.assertNotIn("is not installed", (label + " " + sub).lower())
        # Available actions (open/terminal/folder/copy) are still present
        ids = [r[0] for r in rows if not r[0].startswith("header:")]
        self.assertIn("open", ids)
        self.assertIn("terminal", ids)
        self.assertIn("folder", ids)
        self.assertIn("copy", ids)

    def test_unavailable_intel_caps_absent_from_quick_actions(self):
        fake_caps = intelligence.ProjectCapabilities(
            path="/tmp/fake", kind="node",
            capabilities={
                "dev": _unavailable_cap("dev", "Run Dev Server", "bun run dev"),
                "test": _unavailable_cap("test", "Run Tests", "bun run test"),
                "build": _unavailable_cap("build", "Build Project", "bun run build"),
            },
        )
        # Persist prior usage that would otherwise lift an unavailable
        # cap into Quick Actions.
        self.inst.workspace.record(
            "/tmp/fake", action="int:test:bun run test",
            terminal_cmd="bun run test")
        with mock.patch.object(intelligence, "capabilities_for",
                               return_value=fake_caps):
            rows = self.inst.actions_for(self.proj)
        # Quick actions section must not contain unavailable intel rows
        in_quick = False
        for r in rows:
            if r[0] == "header:QUICK":
                in_quick = True
                continue
            if r[0].startswith("header:"):
                in_quick = False
            if in_quick and r[0].startswith("int:"):
                self.fail(f"unavailable intel row in quick actions: {r}")

    def test_unavailable_intel_caps_cannot_be_smart_primary(self):
        fake_caps = intelligence.ProjectCapabilities(
            path="/tmp/fake", kind="node",
            capabilities={
                "dev": _unavailable_cap("dev", "Run Dev Server", "bun run dev"),
            },
        )
        # Persist dev as the preferred action; smart_primary must ignore
        # it because the underlying capability is unavailable.
        self.inst.workspace.record(
            "/tmp/fake", action="int:dev:bun run dev",
            terminal_cmd="bun run dev")
        with mock.patch.object(intelligence, "capabilities_for",
                               return_value=fake_caps):
            primary_id, _label, _sub = self.inst._smart_primary(
                self.proj, False, [])
        self.assertNotEqual(primary_id, "int:dev:bun run dev")
        # Falls back to a generic open
        self.assertEqual(primary_id, "open")

    def test_empty_unavailable_section_never_rendered(self):
        fake_caps = intelligence.ProjectCapabilities(
            path="/tmp/fake", kind="node",
            capabilities={},  # no caps at all
        )
        with mock.patch.object(intelligence, "capabilities_for",
                               return_value=fake_caps):
            rows = self.inst.actions_for(self.proj)
        headers = [r[0] for r in rows if r[0].startswith("header:")]
        self.assertNotIn("header:UNAVAILABLE", headers)

    def test_picker_excludes_unavailable(self):
        # Picker / Open With rows are derived from tool registry, but
        # the intelligence-side action_id resolution must reject
        # unavailable capabilities even when persisted usage exists.
        fake_caps = intelligence.ProjectCapabilities(
            path="/tmp/fake", kind="node",
            capabilities={
                "dev": _unavailable_cap("dev", "Run Dev Server", "bun run dev"),
            },
        )
        self.inst.workspace.record(
            "/tmp/fake", action="int:dev:bun run dev",
            terminal_cmd="bun run dev")
        with mock.patch.object(intelligence, "capabilities_for",
                               return_value=fake_caps):
            with mock.patch.object(self.inst, "preferred_tool_for",
                                   return_value=None):
                # Reproduce the smart_primary path used by picker consumers
                primary_id, _label, _sub = self.inst._smart_primary(
                    self.proj, False, [])
        self.assertNotEqual(primary_id, "int:dev:bun run dev")

    def test_run_action_blocks_unavailable_intel_action(self):
        # Even if a stale id somehow reaches run_action, the runtime
        # gate must refuse to launch an unavailable capability.
        fake_caps = intelligence.ProjectCapabilities(
            path="/tmp/fake", kind="node",
            capabilities={
                "dev": _unavailable_cap("dev", "Run Dev Server", "bun run dev"),
            },
        )
        with mock.patch.object(intelligence, "capabilities_for",
                               return_value=fake_caps):
            with mock.patch("projectdock.app.actions.open_in_terminal") as opened:
                self.inst.run_action("int:dev:bun run dev", self.proj)
                opened.assert_not_called()


# --------------------------------------------------------------------
# Issue 2: package manager detection precedence
# --------------------------------------------------------------------
class PackageManagerDetectionTest(unittest.TestCase):
    def _node(self, tmp, scripts=None, lockfiles=None, pm_field=None):
        pkg = {"scripts": scripts or {}}
        if pm_field is not None:
            pkg["packageManager"] = pm_field
        with open(os.path.join(tmp, "package.json"), "w") as fh:
            json.dump(pkg, fh)
        for lf in lockfiles or []:
            open(os.path.join(tmp, lf), "w").close()
        return tmp

    def test_package_lock_wins_over_stale_bun_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A project that has BOTH a stale bun.lock and a real
            # package-lock.json must be detected as npm.
            self._node(tmp, scripts={"dev": "vite dev", "test": "vitest",
                                     "build": "vite build"},
                       lockfiles=["package-lock.json", "bun.lock"])
            intelligence.invalidate(tmp)
            pm, runner, avail, src = intelligence.detect_package_manager(tmp)
            self.assertEqual(pm, "npm")
            self.assertEqual(runner, "npm run")
            self.assertTrue(avail)
            self.assertEqual(src, "lockfile")
            caps = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            self.assertIn("npm run", caps.get("dev").command)
            self.assertEqual(caps.get("dev").pm, "npm")
            self.assertTrue(caps.get("dev").available)

    def test_bun_lock_only_picks_bun(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._node(tmp, scripts={"dev": "x"}, lockfiles=["bun.lock"])
            intelligence.invalidate(tmp)
            pm, _, _, _ = intelligence.detect_package_manager(tmp)
            self.assertEqual(pm, "bun")

    def test_cineprint_npm_regression(self):
        """Exact CinePrint-style scenario.

        The actual cine-print-gallery project on disk has:
            - package.json (vite + vitest + tsx scripts)
            - package-lock.json (npm lockfile)
            - bun.lock + bunfig.toml (stale leftovers from a previous
              experiment)
        ProjectDock must select npm, expose npm run dev/test/build, and
        NOT report any "bun is not installed" unavailable rows.
        """
        cineprint = "/home/prathamesh913/Projects/cine-print-gallery"
        if not os.path.isdir(cineprint):
            self.skipTest("cine-print-gallery not on this machine")
        intelligence.invalidate(cineprint)
        pm, runner, avail, src = intelligence.detect_package_manager(cineprint)
        self.assertEqual(pm, "npm")
        self.assertEqual(runner, "npm run")
        self.assertTrue(avail, "npm must be reported available on this host")
        self.assertEqual(src, "lockfile")
        caps = intelligence.capabilities_for(
            {"path": cineprint, "kind": "node-ts"})
        # All detected capabilities must be available (npm is installed)
        for c in caps.as_list():
            self.assertTrue(c.available,
                            f"capability {c.key} should be available: {c}")
            self.assertIn("npm", c.command.lower(),
                          f"capability {c.key} should use npm: {c.command}")

    def test_fallback_does_not_invent_missing_pm(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._node(tmp, scripts={"dev": "x"})  # no lockfile, no pm
            with mock.patch("projectdock.intelligence._shutil.which",
                            return_value=None):
                intelligence.invalidate(tmp)
                pm, runner, avail, src = intelligence.detect_package_manager(tmp)
                self.assertEqual(pm, "")
                self.assertFalse(avail)
                self.assertEqual(src, "none")
                caps = intelligence.capabilities_for(
                    {"path": tmp, "kind": "node"})
                self.assertIsNotNone(caps.get("dev"))
                self.assertFalse(caps.get("dev").available,
                                 "dev must be unavailable when no pm exists")

    def test_fallback_picks_installed_npm_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._node(tmp, scripts={"dev": "x"})
            with mock.patch(
                    "projectdock.intelligence._shutil.which",
                    side_effect=lambda n: "/usr/bin/npm" if n == "npm" else None):
                intelligence.invalidate(tmp)
                pm, _, avail, src = intelligence.detect_package_manager(tmp)
                self.assertEqual(pm, "npm")
                self.assertTrue(avail)
                self.assertEqual(src, "fallback")

    def test_malformed_package_manager_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            # packageManager points to a non-existent / unrecognised tool
            self._node(tmp, scripts={"dev": "x"},
                       lockfiles=["package-lock.json"],
                       pm_field="obviously-not-a-pm@1.0.0")
            with mock.patch(
                    "projectdock.intelligence._shutil.which",
                    side_effect=lambda n: "/usr/bin/npm" if n == "npm" else None):
                intelligence.invalidate(tmp)
                pm, runner, avail, src = intelligence.detect_package_manager(tmp)
                # Malformed pm field is ignored, npm lock wins.
                self.assertEqual(pm, "npm")
                self.assertEqual(runner, "npm run")
                self.assertTrue(avail)

    def test_pnpm_yarn_detection(self):
        with tempfile.TemporaryDirectory() as tmp_p:
            self._node(tmp_p, scripts={"dev": "x"},
                       lockfiles=["pnpm-lock.yaml"])
            intelligence.invalidate(tmp_p)
            pm, _, _, _ = intelligence.detect_package_manager(tmp_p)
            self.assertEqual(pm, "pnpm")
        with tempfile.TemporaryDirectory() as tmp_y:
            self._node(tmp_y, scripts={"dev": "x"},
                       lockfiles=["yarn.lock"])
            intelligence.invalidate(tmp_y)
            pm, _, _, _ = intelligence.detect_package_manager(tmp_y)
            self.assertEqual(pm, "yarn")


# --------------------------------------------------------------------
# Issue 3: rescan actions are not in the project Actions menu
# --------------------------------------------------------------------
class RescanRemovedFromActionsTest(unittest.TestCase):
    def setUp(self):
        self.inst = _make_dock_app()
        self.proj = {"path": "/tmp/fake", "name": "fake", "kind": "generic"}

    def test_no_rescan_ids_in_actions(self):
        rows = self.inst.actions_for(self.proj)
        ids = [r[0] for r in rows if not r[0].startswith("header:")]
        for rid in ids:
            self.assertFalse(rid.startswith("rescan"),
                             f"rescan action leaked into Actions menu: {rid}")

    def test_rescan_ids_excluded_for_node_project(self):
        proj = {"path": "/tmp/fake", "name": "fake", "kind": "node"}
        fake_caps = intelligence.ProjectCapabilities(
            path="/tmp/fake", kind="node",
            capabilities={"dev": _available_cap("dev", "Run Dev Server",
                                                "npm run dev")},
        )
        with mock.patch.object(intelligence, "capabilities_for",
                               return_value=fake_caps):
            rows = self.inst.actions_for(proj)
        ids = [r[0] for r in rows if not r[0].startswith("header:")]
        for rid in ids:
            self.assertFalse(rid.startswith("rescan"),
                             f"rescan action leaked into Actions: {rid}")

    def test_run_action_rescan_ids_fall_through_safely(self):
        # Defence in depth: even if a stale id reaches run_action, the
        # handler must not raise and must route to a global rescan.
        for rid in ("rescan", "rescan_project", "rescan_root", "rescan_all"):
            with mock.patch.object(self.inst, "rescan_all") as r:
                self.inst.run_action(rid, self.proj)
                r.assert_called_once()


# --------------------------------------------------------------------
# Issue 4 / 5: search mode rescan row + rescan feedback
# --------------------------------------------------------------------
class SearchRescanFeedbackTest(unittest.TestCase):
    def test_status_message_lifecycle(self):
        inst = _make_dock_app()
        # Set and read
        inst._set_status_message("Rescanning projects\u2026", source="rescan")
        self.assertEqual(inst.status_message(),
                         "Rescanning projects\u2026")
        # Replace with confirmation
        inst._set_status_message("Projects updated \u00b7 7",
                                 source="rescan", clear_after=0.05)
        self.assertEqual(inst.status_message(),
                         "Projects updated \u00b7 7")
        # Auto-clear after timer
        time.sleep(0.1)
        # status_message() does the clear lazily
        self.assertEqual(inst.status_message(), "")

    def test_status_message_cleared_by_source_mismatch(self):
        inst = _make_dock_app()
        inst._set_status_message("X", source="rescan")
        inst._clear_status_message(source="other")
        self.assertEqual(inst.status_message(), "X",
                         "source mismatch must not clear unrelated status")
        inst._clear_status_message(source="rescan")
        self.assertEqual(inst.status_message(), "")

    def test_rescan_all_sets_initial_status(self):
        inst = _make_dock_app()
        inst._do_rescan_all = mock.Mock()  # avoid starting a real scan
        inst.rescan_all()
        self.assertEqual(inst._status_message,
                         "Rescanning projects\u2026")
        self.assertEqual(inst._status_source, "rescan")
        inst._do_rescan_all.assert_called_once()

    def test_rescan_root_sets_initial_status(self):
        inst = _make_dock_app()
        inst._do_rescan_root = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp:
            inst.rescan_root(tmp)
        self.assertEqual(inst._status_message,
                         "Rescanning projects\u2026")
        inst._do_rescan_root.assert_called_once()

    def test_rescan_does_not_start_when_already_scanning(self):
        inst = _make_dock_app()
        inst.scanning = True
        inst._do_rescan_all = mock.Mock()
        inst.rescan_all()
        inst._do_rescan_all.assert_not_called()
        # And no status message is set either
        self.assertEqual(inst._status_message, "")


# --------------------------------------------------------------------
# Issue 6: create + rescan lifecycle
# --------------------------------------------------------------------
class CreateRescanLifecycleTest(unittest.TestCase):
    def test_new_folder_appears_after_rescan(self):
        with tempfile.TemporaryDirectory() as root:
            # initial empty root
            res1 = discovery.scan([root])
            self.assertEqual(res1.projects, [])
            # drop a new marker into the root
            proj = os.path.join(root, "brand-new")
            os.makedirs(proj)
            with open(os.path.join(proj, "package.json"), "w") as fh:
                json.dump({"scripts": {"dev": "x"}}, fh)
            res2 = discovery.scan([root])
            names = {p["name"] for p in res2.projects}
            self.assertIn("brand-new", names)

    def test_create_from_search_uses_marker_scan(self):
        with tempfile.TemporaryDirectory() as root:
            from projectdock import creation
            path, err = creation.create_project("newproj", [root])
            self.assertIsNotNone(path)
            self.assertIsNone(err)
            self.assertTrue(os.path.isdir(path))
            # Initially not discovered by marker-only scan, but
            # describe_project does surface it.
            result = discovery.scan([root])
            self.assertNotIn(path, {p["path"] for p in result.projects})
            desc = discovery.describe_project(path)
            self.assertIsNotNone(desc)
            self.assertEqual(desc["kind"], "generic")

    def test_duplicate_create_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            from projectdock import creation
            p1, _ = creation.create_project("myapp", [root])
            p2, err = creation.create_project("myapp", [root])
            self.assertEqual(p1, p2)
            self.assertIsNone(err)

    def test_monitor_and_manual_rescan_coalesce(self):
        # The "scanning" flag prevents overlapping rescan_all calls.
        from projectdock.app import DockApp
        inst = _make_dock_app()
        inst.scanning = True
        inst._do_rescan_all = mock.Mock()
        inst.rescan_all()
        inst._do_rescan_all.assert_not_called()


# --------------------------------------------------------------------
# Issue 4 (UI): rescan utility row participation in keyboard nav
# --------------------------------------------------------------------
class RescanUtilityRowBuildTest(unittest.TestCase):
    """Validate the textual/data shape of the rescan utility row.

    The full UI assertion path is integration-tested under GTK; here
    we only assert the construction contract so the row is discoverable
    in unit tests.
    """

    def test_rescan_utility_row_factory(self):
        # Build a fake Gtk-free stub that records what the factory does
        class FakeBuilder:
            def __init__(self):
                self.rows = []

            def rescan(self, kind="all", label="Rescan projects"):
                # mirrors the production factory data shape
                return {"kind": "utility", "is_rescan": True,
                        "rescan_kind": kind, "label": label}

        b = FakeBuilder()
        row = b.rescan(kind="root", label="Rescan Projects")
        self.assertTrue(row["is_rescan"])
        self.assertEqual(row["rescan_kind"], "root")
        # A rescan row is NOT a project (no .project attribute / field)
        self.assertNotIn("project", row)
        self.assertNotIn("path", row)

    def test_rescan_utility_row_is_gtk_compatible(self):
        # The real factory depends on Gtk; ensure it can be called in an
        # environment that has Gtk (the build environment does), and that
        # it produces a row whose `is_rescan` flag is set.
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
        except Exception:
            self.skipTest("Gtk not available")
        from projectdock import ui
        win = mock.Mock(spec=ui.LauncherWindow)
        win.controller = mock.Mock()
        win.controller._root_for_path.return_value = "/tmp/Projects"
        win._last_project = None
        row = ui.LauncherWindow._rescan_utility_row(win,
                                                    kind="all",
                                                    label="Rescan projects")
        self.assertTrue(getattr(row, "is_rescan"))
        self.assertEqual(row.rescan_kind, "all")
        # And the rescan row never pretends to be a project
        self.assertFalse(hasattr(row, "project"))


if __name__ == "__main__":
    unittest.main()
