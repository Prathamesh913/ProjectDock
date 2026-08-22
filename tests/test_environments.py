import os, sys, json, tempfile, time, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import state, workspace
from projectdock.workspace import WorkspaceStore
from projectdock import config

class ProfilePersistenceTest(unittest.TestCase):
    def test_empty_profile(self):
        st = state.State()
        st.workspace = {}
        ws = WorkspaceStore(st)
        ws.load_from_state(st)
        self.assertEqual(ws.get_preferred_editor("/tmp/proj"), "")
        self.assertIsNone(ws.get_preferred_primary("/tmp/proj", ["open"]))

    def test_migration_old_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp, "cfg"), "XDG_STATE_HOME": os.path.join(tmp, "state")}):
                # create old state without workspace
                s = state.State()
                s.pins = ["/a"]
                state.save(s)
                # reload
                s2 = state.load()
                self.assertIsInstance(s2.workspace, dict)
                self.assertEqual(s2.pins, ["/a"])

    def test_corrupt_profile(self):
        st = state.State()
        st.workspace = {"/tmp/proj": "not a dict", "/tmp/bad": {"action_usage": "not dict"}}
        ws = WorkspaceStore(st)
        ws.load_from_state(st)
        # should not crash
        self.assertEqual(ws.get_preferred_editor("/tmp/proj"), "")
        ws.annotate_projects([{"path":"/tmp/proj","name":"proj"}])

    def test_unknown_fields_ignored(self):
        st = state.State()
        st.workspace = {"/tmp/proj": {"last_active_at": time.time(), "unknown_field": "foo", "action_usage": {"open": 1}}}
        ws = WorkspaceStore(st)
        ws.load_from_state(st)
        # unknown field should remain but not affect
        self.assertIn("unknown_field", st.workspace["/tmp/proj"])
        self.assertEqual(ws.get_preferred_primary("/tmp/proj", ["open"]), "open")

    def test_stale_nonexistent_project(self):
        st = state.State()
        st.workspace = {"/nonexistent/path": {"last_active_at": time.time(), "path": "/nonexistent/path"}}
        ws = WorkspaceStore(st)
        ws.load_from_state(st)
        ws.cleanup_stale([{"path":"/real"}])
        # stale still in workspace but pruned if >100, not here
        self.assertIn("/nonexistent/path", st.workspace)

    def test_cap_pruning(self):
        st = state.State()
        ws = WorkspaceStore(st)
        ws.load_from_state(st)
        # create 101 entries
        for i in range(101):
            st.workspace[f"/tmp/proj{i}"] = {"last_active_at": float(i), "path": f"/tmp/proj{i}"}
        ws.cleanup_stale([{"path": f"/tmp/proj{i}"} for i in range(101)])
        self.assertLessEqual(len(st.workspace), 100)
        # oldest pruned
        self.assertNotIn("/tmp/proj0", st.workspace)

class ActionUsageTest(unittest.TestCase):
    def setUp(self):
        self.st = state.State()
        self.ws = WorkspaceStore(self.st)
        self.ws.load_from_state(self.st)

    def test_first_action(self):
        self.ws.record("/tmp/proj", action="open")
        self.assertEqual(self.st.workspace["/tmp/proj"]["action_usage"]["open"], 1)

    def test_repeated_action(self):
        for _ in range(5):
            self.ws.record("/tmp/proj", action="terminal")
        self.assertEqual(self.st.workspace["/tmp/proj"]["action_usage"]["terminal"], 5)

    def test_bounded_counts(self):
        for _ in range(25):
            self.ws.record("/tmp/proj", action="open")
        self.assertLessEqual(self.st.workspace["/tmp/proj"]["action_usage"]["open"], 20)

    def test_bounded_history_distinct(self):
        for i in range(15):
            self.ws.record("/tmp/proj", action=f"act{i}")
        usage = self.st.workspace["/tmp/proj"]["action_usage"]
        self.assertLessEqual(len(usage), 10)

    def test_ranking_behavior(self):
        self.ws.record("/tmp/proj", action="open")
        self.ws.record("/tmp/proj", action="terminal")
        self.ws.record("/tmp/proj", action="terminal")
        pref = self.ws.get_preferred_primary("/tmp/proj", ["open","terminal"])
        self.assertEqual(pref, "terminal")

    def test_corrupt_action_data(self):
        self.st.workspace["/tmp/proj"] = {"action_usage": "bad", "action_last_used": "bad"}
        pref = self.ws.get_preferred_primary("/tmp/proj", ["open"])
        self.assertIsNone(pref)

    def test_deterministic_tie_break(self):
        # same count, earlier timestamp should tie break by recency
        now = time.time()
        self.st.workspace["/tmp/proj"] = {
            "action_usage": {"open":2, "terminal":2},
            "action_last_used": {"open": now-10, "terminal": now}
        }
        pref = self.ws.get_preferred_primary("/tmp/proj", ["open","terminal"])
        self.assertEqual(pref, "terminal")

class PrimaryActionTest(unittest.TestCase):
    def setUp(self):
        self.st = state.State()
        self.ws = WorkspaceStore(self.st)
        self.ws.load_from_state(self.st)

    def test_active_focus(self):
        # active project should have focus as primary
        self.ws._ephemeral_active["/tmp/proj"]=[{"address":"0x1"}]
        self.ws._last_refresh=time.time()
        # need app logic
        from projectdock.app import DockApp
        inst = DockApp.__new__(DockApp)
        inst.cfg = mock.Mock()
        inst.cfg.editor_label.return_value="code"
        inst.cfg.available_editors.return_value=[]
        inst.state=self.st
        inst.workspace=self.ws
        from projectdock.app import DockApp as Real
        inst._smart_primary = Real._smart_primary.__get__(inst, DockApp)
        pid, label, sub = inst._smart_primary({"path":"/tmp/proj","kind":"generic"}, True, [])
        self.assertEqual(pid, "focus")

    def test_preferred_valid(self):
        self.ws.record("/tmp/proj", action="terminal")
        self.ws.record("/tmp/proj", action="terminal")
        # need valid ids
        valid = ["open","terminal","int:dev:npm run dev"]
        pref = self.ws.get_preferred_primary("/tmp/proj", valid)
        self.assertEqual(pref, "terminal")

    def test_preferred_stale_fallback(self):
        self.ws.record("/tmp/proj", action="int:dev:npm run dev")
        # but capability no longer exists (valid does not contain it)
        pref = self.ws.get_preferred_primary("/tmp/proj", ["open","terminal"])
        self.assertIsNone(pref)  # stale not returned

    def test_no_preference_fallback(self):
        pref = self.ws.get_preferred_primary("/tmp/proj", ["open"])
        self.assertIsNone(pref)

    def test_unsafe_persisted_rejected(self):
        self.st.workspace["/tmp/proj"]={"action_usage": {"int:dev:; rm -rf /": 5}, "action_last_used": {"int:dev:; rm -rf /": time.time()}}
        # valid ids do not contain malicious, so pref should be None even though usage high
        pref = self.ws.get_preferred_primary("/tmp/proj", ["open","terminal"])
        self.assertIsNone(pref)

    def test_unavailable_editor_fallback(self):
        self.st.workspace["/tmp/proj"]={"preferred_editor": "nonexistent_editor_xyz", "editor": "nonexistent_editor_xyz"}
        self.assertEqual(self.ws.get_preferred_editor("/tmp/proj"), "")

class QuickActionsTest(unittest.TestCase):
    def setUp(self):
        self.st = state.State()
        self.ws = WorkspaceStore(self.st)
        self.ws.load_from_state(self.st)

    def test_frequent_appears(self):
        for _ in range(5):
            self.ws.record("/tmp/proj", action="terminal")
        ranked = self.ws.ranked_quick_candidates("/tmp/proj", ["open","terminal","folder"])
        self.assertEqual(ranked[0], "terminal")

    def test_duplicates_removed(self):
        # simulate actions_for dedup
        from projectdock.app import DockApp
        inst = DockApp.__new__(DockApp)
        inst.cfg = mock.Mock()
        inst.cfg.editor_label.return_value="code"
        inst.cfg.available_editors.return_value=[]
        inst.state=self.st
        inst.workspace=self.ws
        inst.workspace.load_from_state(inst.state)
        # mock intelligence to return dev cap
        with mock.patch("projectdock.intelligence.capabilities_for") as m:
            from projectdock.intelligence import ProjectCapabilities, Capability
            caps = ProjectCapabilities(path="/tmp/proj", capabilities={"dev": Capability("dev","Run Dev Server","npm run dev")})
            m.return_value=caps
            from projectdock.app import DockApp as Real
            inst.actions_for = Real.actions_for.__get__(inst, DockApp)
            rows = inst.actions_for({"path":"/tmp/proj","kind":"node"})
            ids=[r[0] for r in rows if not r[0].startswith("header:")]
            self.assertEqual(len(ids), len(set(ids)))

    def test_intelligence_preserved(self):
        from projectdock.app import DockApp
        inst = DockApp.__new__(DockApp)
        inst.cfg = mock.Mock()
        inst.cfg.editor_label.return_value="code"
        inst.cfg.available_editors.return_value=[]
        inst.state=self.st
        inst.workspace=self.ws
        inst.workspace.load_from_state(inst.state)
        with mock.patch("projectdock.intelligence.capabilities_for") as m:
            from projectdock.intelligence import ProjectCapabilities, Capability
            caps = ProjectCapabilities(path="/tmp/proj", capabilities={"dev": Capability("dev","Run Dev Server","npm run dev")})
            m.return_value=caps
            from projectdock.app import DockApp as Real
            inst.actions_for = Real.actions_for.__get__(inst, DockApp)
            rows = inst.actions_for({"path":"/tmp/proj","kind":"node"})
            ids=[r[0] for r in rows]
            self.assertTrue(any("int:dev" in i for i in ids))

    def test_empty_categories_hidden(self):
        from projectdock.app import DockApp
        inst = DockApp.__new__(DockApp)
        inst.cfg = mock.Mock()
        inst.cfg.editor_label.return_value="code"
        inst.cfg.available_editors.return_value=[]
        inst.state=self.st
        inst.workspace=self.ws
        inst.workspace.load_from_state(inst.state)
        with mock.patch("projectdock.intelligence.capabilities_for") as m:
            from projectdock.intelligence import ProjectCapabilities
            m.return_value=ProjectCapabilities(path="/tmp/proj", capabilities={})
            from projectdock.app import DockApp as Real
            inst.actions_for = Real.actions_for.__get__(inst, DockApp)
            rows = inst.actions_for({"path":"/tmp/proj","kind":"generic"})
            # should not have PROJECT header when no intel
            headers=[r[1] for r in rows if r[0].startswith("header:")]
            self.assertNotIn("PROJECT", headers)

    def test_deterministic_ordering(self):
        for _ in range(3):
            self.ws.record("/tmp/proj", action="terminal")
        for _ in range(1):
            self.ws.record("/tmp/proj", action="open")
        ranked = self.ws.ranked_quick_candidates("/tmp/proj", ["open","terminal"])
        self.assertEqual(ranked, ["terminal","open"])

class EditorMemoryTest(unittest.TestCase):
    def test_editor_remembered(self):
        st=state.State()
        ws=WorkspaceStore(st)
        ws.load_from_state(st)
        with mock.patch("shutil.which", side_effect=lambda x: x in ["zed","code"]):
            ws.record("/tmp/proj", action="open", editor="zed")
            self.assertIn("zed", ws.get_preferred_editor("/tmp/proj"))

    def test_unavailable_fallback(self):
        st=state.State()
        ws=WorkspaceStore(st)
        ws.load_from_state(st)
        st.workspace["/tmp/proj"]={"preferred_editor":"nope_xyz","editor":"nope_xyz"}
        self.assertEqual(ws.get_preferred_editor("/tmp/proj"), "")

    def test_different_projects(self):
        st=state.State()
        ws=WorkspaceStore(st)
        ws.load_from_state(st)
        with mock.patch("shutil.which", side_effect=lambda x: x in ["zed","code"]):
            ws.record("/tmp/a", action="open", editor="zed")
            ws.record("/tmp/b", action="open", editor="code")
            self.assertEqual(ws.get_preferred_editor("/tmp/a"), "zed")
            self.assertEqual(ws.get_preferred_editor("/tmp/b"), "code")

class SafetyTest(unittest.TestCase):
    def test_malicious_state_not_executed(self):
        from projectdock.app import DockApp
        inst = DockApp.__new__(DockApp)
        inst.cfg = mock.Mock()
        inst.cfg.editor_label.return_value="code"
        inst.cfg.available_editors.return_value=[]
        inst.state = state.State()
        inst.workspace = WorkspaceStore(inst.state)
        inst.workspace.load_from_state(inst.state)
        # malicious persisted usage
        inst.state.workspace["/tmp/proj"]={"action_usage": {"int:dev:; rm -rf /": 10}, "action_last_used": {"int:dev:; rm -rf /": time.time()}}
        with mock.patch("projectdock.intelligence.capabilities_for") as m:
            from projectdock.intelligence import ProjectCapabilities, Capability
            m.return_value=ProjectCapabilities(path="/tmp/proj", capabilities={"dev": Capability("dev","Run Dev Server","npm run dev")})
            with mock.patch("projectdock.actions.open_in_terminal") as term:
                from projectdock.app import DockApp as Real
                inst.run_action = Real.run_action.__get__(inst, DockApp)
                inst._touch = mock.Mock()
                inst._record_workspace = mock.Mock()
                inst.run_action("int:dev:; rm -rf /", {"path":"/tmp/proj","kind":"node"})
                term.assert_not_called()

    def test_stale_capability_not_executed(self):
        from projectdock.app import DockApp
        inst = DockApp.__new__(DockApp)
        inst.cfg = mock.Mock()
        inst.state = state.State()
        inst.workspace = WorkspaceStore(inst.state)
        inst.workspace.load_from_state(inst.state)
        with mock.patch("projectdock.intelligence.capabilities_for") as m:
            from projectdock.intelligence import ProjectCapabilities
            m.return_value=ProjectCapabilities(path="/tmp/proj", capabilities={})
            with mock.patch("projectdock.actions.open_in_terminal") as term:
                from projectdock.app import DockApp as Real
                inst.run_action = Real.run_action.__get__(inst, DockApp)
                inst._touch = mock.Mock()
                inst._record_workspace = mock.Mock()
                inst.run_action("int:dev:npm run dev", {"path":"/tmp/proj","kind":"node"})
                term.assert_not_called()

    def test_arbitrary_not_quick(self):
        st=state.State()
        ws=WorkspaceStore(st)
        ws.load_from_state(st)
        # inject arbitrary action
        st.workspace["/tmp/proj"]={"action_usage": {"evil:cmd": 10}, "action_last_used": {"evil:cmd": time.time()}}
        ranked=ws.ranked_quick_candidates("/tmp/proj", ["open","terminal"])
        self.assertNotIn("evil:cmd", ranked)

if __name__=="__main__":
    unittest.main()
