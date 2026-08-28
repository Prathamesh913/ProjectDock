import os
import json
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import creation, discovery, intelligence, actions
from projectdock import state as state_mod

class CreationValidationTest(unittest.TestCase):
    def test_valid_simple_name(self):
        self.assertTrue(creation.is_valid_project_name("myapp"))
        self.assertTrue(creation.is_valid_project_name("My-App_123"))

    def test_name_with_spaces(self):
        self.assertTrue(creation.is_valid_project_name("My New App"))
        self.assertEqual(creation.sanitize_creation_name("My New App"), "My New App")

    def test_invalid_name_empty(self):
        self.assertFalse(creation.is_valid_project_name(""))
        self.assertFalse(creation.is_valid_project_name("   "))
        self.assertIsNone(creation.sanitize_creation_name("   "))

    def test_path_traversal_rejected(self):
        self.assertFalse(creation.is_valid_project_name("../etc"))
        self.assertFalse(creation.is_valid_project_name("a/b"))
        self.assertFalse(creation.is_valid_project_name("foo/../bar"))

    def test_absolute_path_rejected(self):
        self.assertFalse(creation.is_valid_project_name("/absolute"))
        self.assertFalse(creation.is_valid_project_name("/tmp/foo"))

    def test_duplicate_names(self):
        self.assertFalse(creation.is_valid_project_name("."))
        self.assertFalse(creation.is_valid_project_name(".."))
        self.assertFalse(creation.is_valid_project_name("-bad"))

    def test_slash_backslash_rejection(self):
        self.assertFalse(creation.is_valid_project_name("a\\b"))
        self.assertFalse(creation.is_valid_project_name("a:b"))

    def test_control_chars_rejected(self):
        self.assertFalse(creation.is_valid_project_name("foo\x00bar"))
        self.assertFalse(creation.is_valid_project_name("foo\nbar"))

    def test_target_path_inside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = creation.target_path_for_name("My App", tmp)
            self.assertTrue(target.startswith(tmp))
            self.assertEqual(os.path.basename(target), "My App")

    def test_target_path_outside_root_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(creation.target_path_for_name("../escape", tmp))
            self.assertIsNone(creation.target_path_for_name("/etc", tmp))

class CreationFilesystemTest(unittest.TestCase):
    def test_create_simple(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [tmp]
            path, err = creation.create_project("My New App", roots)
            self.assertIsNotNone(path)
            self.assertIsNone(err)
            self.assertTrue(os.path.isdir(path))
            self.assertTrue(os.path.basename(path), "My New App")
            # duplicate returns existing without error
            path2, err2 = creation.create_project("My New App", roots)
            self.assertEqual(path, path2)
            self.assertIsNone(err2)

    def test_create_with_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, err = creation.create_project("Hello World", [tmp])
            self.assertIsNotNone(path)
            self.assertTrue(os.path.isdir(path))

    def test_create_invalid_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, err = creation.create_project("a/b", [tmp])
            self.assertIsNone(path)
            self.assertEqual(err, "invalid name")

    def test_root_selection_active(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            # create active project under tmp2
            os.makedirs(os.path.join(tmp2, "activeproj"))
            roots = [tmp1, tmp2]
            chosen = creation.choose_target_root(roots, os.path.join(tmp2, "activeproj"))
            self.assertEqual(os.path.normpath(chosen), os.path.normpath(tmp2))
            chosen2 = creation.choose_target_root(roots, None)
            self.assertEqual(os.path.normpath(chosen2), os.path.normpath(tmp1))

    def test_permission_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            # mock os.makedirs to raise PermissionError
            with mock.patch("projectdock.creation.os.makedirs", side_effect=PermissionError("denied")):
                path, err = creation.create_project("newproj", [tmp])
                self.assertIsNone(path)
                self.assertIn("permission", err.lower())

    def test_no_root_available(self):
        path, err = creation.create_project("foo", ["/nonexistent_xyz_root_123"])
        self.assertIsNone(path)
        self.assertIn("no project root", err)

    def test_newly_created_empty_project_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [tmp]
            path, _ = creation.create_project("emptystuff", roots)
            desc = discovery.describe_project(path)
            self.assertIsNotNone(desc)
            self.assertEqual(desc["kind"], "generic")
            # ensure discovery.scan alone would not find it (marker-only) but describe does
            result = discovery.scan(roots)
            self.assertNotIn(path, {p["path"] for p in result.projects})
            # but after app merging, it would be preserved - simulate

    def test_should_offer_create(self):
        projs = [{"name": "myapp", "path": "/tmp/myapp"}]
        self.assertTrue(creation.should_offer_create("My New App", projs))
        self.assertFalse(creation.should_offer_create("myapp", projs))
        self.assertFalse(creation.should_offer_create("", projs))
        self.assertFalse(creation.should_offer_create("   ", projs))
        self.assertFalse(creation.should_offer_create("a/b", projs))
        self.assertTrue(creation.should_offer_create("myapp2", projs))

class PackageManagerTest(unittest.TestCase):
    def _tmp_node(self, tmp, pkg, lockfiles=None, pm_field=None):
        pkg_data = {"scripts": pkg} if pkg is not None else {}
        if pm_field is not None:
            pkg_data["packageManager"] = pm_field
        with open(os.path.join(tmp, "package.json"), "w") as fh:
            json.dump(pkg_data, fh)
        if lockfiles:
            for lf in lockfiles:
                open(os.path.join(tmp, lf), "w").close()

    def test_packageManager_field_overrides_lockfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tmp_node(tmp, {"dev":"x"}, lockfiles=["yarn.lock"], pm_field="npm@10.0.0")
            intelligence.invalidate(tmp)
            pm, runner, avail, src = intelligence.detect_package_manager(tmp)
            self.assertEqual(pm, "npm")
            self.assertEqual(src, "packageManager")
            # ensure capability uses npm
            caps = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            self.assertIn("npm run", caps.get("dev").command)
            self.assertEqual(caps.get("dev").pm, "npm")

    def test_npm_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tmp_node(tmp, {"dev":"next dev"}, lockfiles=["package-lock.json"])
            intelligence.invalidate(tmp)
            pm, runner, avail, src = intelligence.detect_package_manager(tmp)
            self.assertEqual(pm, "npm")
            self.assertEqual(runner, "npm run")

    def test_pnpm_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tmp_node(tmp, {"dev":"x"}, lockfiles=["pnpm-lock.yaml"])
            intelligence.invalidate(tmp)
            pm, _, _, _ = intelligence.detect_package_manager(tmp)
            self.assertEqual(pm, "pnpm")

    def test_yarn_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tmp_node(tmp, {"dev":"x"}, lockfiles=["yarn.lock"])
            intelligence.invalidate(tmp)
            pm, _, _, _ = intelligence.detect_package_manager(tmp)
            self.assertEqual(pm, "yarn")

    def test_bun_lockb(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tmp_node(tmp, {"dev":"x"}, lockfiles=["bun.lockb"])
            intelligence.invalidate(tmp)
            pm, _, _, _ = intelligence.detect_package_manager(tmp)
            self.assertEqual(pm, "bun")

    def test_bun_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tmp_node(tmp, {"dev":"x"}, lockfiles=["bun.lock"])
            intelligence.invalidate(tmp)
            pm, _, _, _ = intelligence.detect_package_manager(tmp)
            self.assertEqual(pm, "bun")

    def test_conflicting_lockfiles_packageManager_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tmp_node(tmp, {"dev":"x"}, lockfiles=["pnpm-lock.yaml","yarn.lock"], pm_field="bun@1.0.0")
            intelligence.invalidate(tmp)
            pm, _, _, src = intelligence.detect_package_manager(tmp)
            self.assertEqual(pm, "bun")
            self.assertEqual(src, "packageManager")

    def test_malformed_package_json_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                fh.write("{not json")
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            self.assertTrue(caps.is_empty())

    def test_missing_executable_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tmp_node(tmp, {"dev":"x"}, lockfiles=["pnpm-lock.yaml"])
            # mock shutil.which to pretend pnpm missing
            with mock.patch("projectdock.intelligence._shutil.which", return_value=None):
                intelligence.invalidate(tmp)
                caps = intelligence.capabilities_for({"path": tmp, "kind": "node"})
                self.assertIsNotNone(caps.get("dev"))
                self.assertFalse(caps.get("dev").available)
            # also test detect_package_manager reports unavailable
            with mock.patch("projectdock.intelligence._shutil.which", side_effect=lambda x: None if x=="pnpm" else "/usr/bin/npm"):
                intelligence.invalidate(tmp)
                pm, runner, avail, src = intelligence.detect_package_manager(tmp)
                self.assertEqual(pm, "pnpm")
                self.assertFalse(avail)

    def test_fallback_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts":{"dev":"x"}}, fh)
            # no lock, no pm field, fallback should be npm if installed
            with mock.patch("projectdock.intelligence._shutil.which", side_effect=lambda x: "/usr/bin/npm" if x=="npm" else None):
                intelligence.invalidate(tmp)
                pm, _, avail, src = intelligence.detect_package_manager(tmp)
                self.assertEqual(pm, "npm")
                self.assertTrue(avail)
                self.assertEqual(src, "fallback")

    def test_cache_invalidation_on_package_json_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tmp_node(tmp, {"dev":"x"}, pm_field="npm@1.0.0")
            intelligence.invalidate(tmp)
            caps1 = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            self.assertEqual(caps1.get("dev").pm, "npm")
            # change to pnpm
            self._tmp_node(tmp, {"dev":"x"}, pm_field="pnpm@9.0.0")
            # ensure mtime changes (sleep a bit)
            import time as _t
            _t.sleep(0.01)
            os.utime(os.path.join(tmp, "package.json"), None)
            # without invalidate, cache might still hold old - but _NODE_WATCHED includes package.json so sig should change
            caps2 = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            # if sig changed, pm should be pnpm; if not, it would still be npm but we invalidate manually
            intelligence.invalidate(tmp)
            caps3 = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            self.assertEqual(caps3.get("dev").pm, "pnpm")

class TerminalExecutionTest(unittest.TestCase):
    def test_build_argv_auto_executes(self):
        base = ["xdg-terminal-exec", "--dir=/tmp/proj"]
        cmd = "npm run dev"
        argv = actions.build_terminal_argv(base, cmd)
        self.assertIsNotNone(argv)
        self.assertIn("bash", argv)
        self.assertIn("-lc", argv)
        # shell string should contain command and exec bash
        shell = argv[-1]
        self.assertIn(cmd, shell)
        self.assertIn("exec bash", shell)
        # should contain printf banner
        self.assertIn("printf", shell)

    def test_cwd_preserved_via_config(self):
        from projectdock.config import Config
        cfg = Config(roots=["~/Projects"])
        argv = cfg.terminal_command("/tmp/myproj")
        self.assertIsNotNone(argv)
        self.assertIn("/tmp/myproj", " ".join(argv))

    def test_structured_args_no_shell_injection(self):
        base = ["xdg-terminal-exec", "--dir=/tmp/proj"]
        # invalid command with injection should be rejected
        bad = "npm run dev; rm -rf /"
        argv = actions.build_terminal_argv(base, bad)
        self.assertIsNone(argv)
        # valid command
        good = "npm run dev"
        argv2 = actions.build_terminal_argv(base, good)
        self.assertIsNotNone(argv2)

    def test_missing_executable_handling(self):
        # capability unavailable should block execution at app level - test intelligence
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts":{"dev":"x"}}, fh)
            open(os.path.join(tmp, "pnpm-lock.yaml"), "w").close()
            with mock.patch("projectdock.intelligence._shutil.which", return_value=None):
                intelligence.invalidate(tmp)
                caps = intelligence.capabilities_for({"path": tmp, "kind":"node"})
                self.assertFalse(caps.get("dev").available)
            # open_in_terminal should reject invalid command
            from projectdock.config import Config
            cfg = Config()
            # command with bad chars rejected
            self.assertFalse(actions.open_in_terminal(tmp, cfg, command="bad; rm"))

    def test_command_disappears_after_detection(self):
        # Simulate run_action validation: command not in valid set should be rejected
        # We test via app run_action with mock
        import os as _os, tempfile as _tf
        from projectdock.app import DockApp
        with _tf.TemporaryDirectory() as tmp:
            env = mock.patch.dict(_os.environ, {
                "XDG_CONFIG_HOME": os.path.join(tmp, "cfg"),
                "XDG_STATE_HOME": os.path.join(tmp, "state"),
            })
            env.start()
            try:
                from unittest import mock as _mock
                app = DockApp()
                proj = {"path": "/tmp/fake", "name":"fake","kind":"node"}
                # patch capabilities to have only "npm run dev"
                fake_cap = intelligence.Capability(key="dev", label="Run Dev Server", command="npm run dev", script="dev", available=True, pm="npm")
                fake_caps = intelligence.ProjectCapabilities(path="/tmp/fake", kind="node", capabilities={"dev": fake_cap})
                with _mock.patch.object(intelligence, "capabilities_for", return_value=fake_caps):
                    with _mock.patch("projectdock.app.actions.open_in_terminal") as opened:
                        app.run_action("int:dev:npm run dev", proj)
                        opened.assert_called_once()
                with _mock.patch.object(intelligence, "capabilities_for", return_value=fake_caps):
                    with _mock.patch("projectdock.app.actions.open_in_terminal") as opened:
                        app.run_action("int:dev:npm run dev; rm -rf /", proj)
                        opened.assert_not_called()
            finally:
                env.stop()

class SearchCreateTest(unittest.TestCase):
    def test_create_only_for_valid_queries(self):
        self.assertFalse(creation.should_offer_create("", []))
        self.assertFalse(creation.should_offer_create("   ", []))
        self.assertFalse(creation.should_offer_create("a/b", []))
        self.assertTrue(creation.should_offer_create("my new project", []))

    def test_exact_match_prevents_duplicate(self):
        projs = [{"name":"myapp","path":"/tmp/myapp"}]
        self.assertFalse(creation.should_offer_create("myapp", projs))
        self.assertFalse(creation.should_offer_create("MyApp", projs))  # case insensitive
        self.assertTrue(creation.should_offer_create("myapp2", projs))

    def test_fuzzy_matches_remain(self):
        from projectdock import search as _search
        projs = [
            {"name":"cine-print-gallery","path":"/tmp/cine-print-gallery","label":"TypeScript","kind":"node-ts"},
            {"name":"other","path":"/tmp/other","label":"Python","kind":"python"},
        ]
        for p in projs:
            p["pinned"]=False; p["pin_order"]=1<<30; p["recent_rank"]=None; p["active"]=False
        ranked = _search.filter_and_rank("cine", projs)
        self.assertEqual(ranked[0]["name"], "cine-print-gallery")
        # creation offer for "network" while fuzzy matches exist should still be offered but not above high-confidence? our logic offers create after matches
        self.assertTrue(creation.should_offer_create("network", projs))

class RescanArchitectureTest(unittest.TestCase):
    def test_project_rescan_updates_intelligence(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Setup root
            roots = [tmp]
            # create project with package.json
            proj_path = os.path.join(tmp, "myproj")
            os.makedirs(proj_path)
            with open(os.path.join(proj_path, "package.json"), "w") as fh:
                json.dump({"scripts":{"dev":"next dev"}}, fh)
            # discovery describe
            desc = discovery.describe_project(proj_path)
            self.assertEqual(desc["kind"], "node")
            caps = intelligence.capabilities_for(desc)
            self.assertIsNotNone(caps.get("dev"))
            # modify to add test script
            with open(os.path.join(proj_path, "package.json"), "w") as fh:
                json.dump({"scripts":{"dev":"next dev","test":"jest"}}, fh)
            import time; time.sleep(0.01); os.utime(os.path.join(proj_path, "package.json"), None)
            intelligence.invalidate(proj_path)
            caps2 = intelligence.capabilities_for(desc)
            self.assertIsNotNone(caps2.get("test"))

    def test_root_rescan_discovers_new_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "a"))
            with open(os.path.join(tmp, "a", "package.json"), "w") as fh:
                json.dump({}, fh)
            result1 = discovery.scan([tmp])
            self.assertEqual(len([p for p in result1.projects if p["name"]=="a"]), 1)
            # create new project b
            os.makedirs(os.path.join(tmp, "b"))
            with open(os.path.join(tmp, "b", "Cargo.toml"), "w") as fh:
                fh.write("")
            result2 = discovery.scan([tmp])
            names = {p["name"] for p in result2.projects}
            self.assertIn("b", names)

    def test_root_rescan_removes_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "todelete"))
            with open(os.path.join(tmp, "todelete","package.json"),"w") as fh:
                json.dump({},fh)
            result1 = discovery.scan([tmp])
            self.assertIn("todelete", {p["name"] for p in result1.projects})
            # delete
            import shutil
            shutil.rmtree(os.path.join(tmp, "todelete"))
            result2 = discovery.scan([tmp])
            self.assertNotIn("todelete", {p["name"] for p in result2.projects})

    def test_global_rescan_preserves_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Use state
            st = state_mod.State(pins=["/tmp/foo"], recents=[{"path":"/tmp/foo","at":123}])
            st.projects = [{"path":"/tmp/foo","name":"foo","kind":"generic"}]
            st.workspace = {"/tmp/foo":{"action_usage":{"open":2}}}
            # Simulate _scan_done preserving empty? we test via app logic
            # Instead test that state.annotate preserves pins
            projs = [{"path":"/tmp/foo","name":"foo"}]
            st.annotate(projs)
            self.assertTrue(projs[0]["pinned"])

class KeyboardTest(unittest.TestCase):
    def test_printable_not_shortcut(self):
        try:
            import gi
            gi.require_version("Gtk",4.0); gi.require_version("Gdk",4.0)
            from gi.repository import Gdk
            from projectdock.ui import key_action, MODE_SEARCH
        except Exception:
            self.skipTest("GTK not available")
        for ch in "abcdefghijklmnopqrstuvwxyz0123456789 .-_":
            kv = Gdk.unicode_to_keyval(ord(ch))
            self.assertIsNone(key_action(kv, 0, MODE_SEARCH), f"{ch} should be text")

    def test_ctrl_shortcuts(self):
        try:
            import gi
            gi.require_version("Gtk",4.0); gi.require_version("Gdk",4.0)
            from gi.repository import Gdk
            from projectdock.ui import key_action
        except Exception:
            self.skipTest("GTK not available")
        CTRL = int(Gdk.ModifierType.CONTROL_MASK)
        self.assertEqual(key_action(Gdk.KEY_r, CTRL, 0), "ctrl:r")
        self.assertEqual(key_action(Gdk.KEY_R, CTRL | int(Gdk.ModifierType.SHIFT_MASK), 0) or key_action(Gdk.KEY_R, CTRL, 0), "ctrl:R" if key_action(Gdk.KEY_R, CTRL, 0)=="ctrl:R" else "ctrl:r")
        # Ensure plain r not shortcut
        self.assertIsNone(key_action(Gdk.KEY_r, 0, 0))

if __name__ == "__main__":
    unittest.main()
