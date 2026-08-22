import os, sys, json, tempfile, time, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import workspace, hyprland, state, search
from projectdock.workspace import WorkspaceStore

class HyprlandTest(unittest.TestCase):
    def test_valid_clients_json(self):
        fake = [{"address":"0xabc","pid":123,"title":"foo","class":"code"}]
        with mock.patch("projectdock.hyprland.subprocess.run") as m:
            m.return_value = mock.Mock(returncode=0, stdout=json.dumps(fake))
            with mock.patch("projectdock.hyprland._has_hyprctl", return_value=True):
                clients = hyprland.clients()
                self.assertEqual(len(clients),1)
                self.assertEqual(clients[0]["address"],"0xabc")

    def test_malformed_json(self):
        with mock.patch("projectdock.hyprland.subprocess.run") as m:
            m.return_value = mock.Mock(returncode=0, stdout="not json")
            with mock.patch("projectdock.hyprland._has_hyprctl", return_value=True):
                self.assertEqual(hyprland.clients(), [])

    def test_hyprctl_unavailable(self):
        with mock.patch("projectdock.hyprland._has_hyprctl", return_value=False):
            self.assertEqual(hyprland.clients(), [])

    def test_empty_client_list(self):
        with mock.patch("projectdock.hyprland.subprocess.run") as m:
            m.return_value = mock.Mock(returncode=0, stdout="[]")
            with mock.patch("projectdock.hyprland._has_hyprctl", return_value=True):
                self.assertEqual(hyprland.clients(), [])

    def test_focus_structured(self):
        with mock.patch("projectdock.hyprland.subprocess.run") as m:
            m.return_value = mock.Mock(returncode=0, stdout="")
            with mock.patch("projectdock.hyprland._has_hyprctl", return_value=True):
                self.assertTrue(hyprland.focus_window("0x123ab"))
                m.assert_called_with(["hyprctl","dispatch","focuswindow","address:0x123ab"], capture_output=True, text=True, timeout=1.5)

    def test_focus_rejects_bad_address(self):
        with mock.patch("projectdock.hyprland._has_hyprctl", return_value=True):
            self.assertFalse(hyprland.focus_window("bad"))
            self.assertFalse(hyprland.focus_window("0xZZZ"))
            self.assertFalse(hyprland.focus_window(""))

    def test_stale_missing_window(self):
        self.assertFalse(hyprland.focus_window(None))
        self.assertFalse(hyprland.focus_window("0x"))

    def test_cwd_for_pid_invalid(self):
        self.assertIsNone(hyprland.cwd_for_pid("notanint"))
        self.assertIsNone(hyprland.cwd_for_pid(None))

    def test_cwd_for_pid_inaccessible(self):
        with mock.patch("os.readlink", side_effect=OSError("perm")):
            self.assertIsNone(hyprland.cwd_for_pid(999999))

class AssociationTest(unittest.TestCase):
    def test_exact_cwd_inside_project(self):
        projs=[{"path":"/home/user/Projects/foo"},{"path":"/home/user/Projects/bar"}]
        clients=[{"address":"0x1","pid":100}]
        with mock.patch("projectdock.hyprland.cwd_for_pid", return_value="/home/user/Projects/foo/src"):
            assoc=hyprland.associate_clients_to_projects(clients, projs)
            self.assertIn("/home/user/Projects/foo", assoc)

    def test_cwd_equal_to_root(self):
        projs=[{"path":"/tmp/proj"}]
        clients=[{"address":"0x1","pid":1}]
        with mock.patch("projectdock.hyprland.cwd_for_pid", return_value="/tmp/proj"):
            assoc=hyprland.associate_clients_to_projects(clients, projs)
            self.assertIn("/tmp/proj", assoc)

    def test_cwd_outside(self):
        projs=[{"path":"/tmp/proj"}]
        clients=[{"address":"0x1","pid":1}]
        with mock.patch("projectdock.hyprland.cwd_for_pid", return_value="/tmp/other"):
            assoc=hyprland.associate_clients_to_projects(clients, projs)
            self.assertEqual(assoc, {})

    def test_nested_precedence(self):
        projs=[{"path":"/tmp/outer"},{"path":"/tmp/outer/inner"}]
        clients=[{"address":"0x1","pid":1}]
        with mock.patch("projectdock.hyprland.cwd_for_pid", return_value="/tmp/outer/inner/src"):
            assoc=hyprland.associate_clients_to_projects(clients, projs)
            self.assertIn("/tmp/outer/inner", assoc)
            self.assertNotIn("/tmp/outer", assoc)

    def test_false_positive_prevention(self):
        projs=[{"path":"/tmp/foo"}]
        clients=[{"address":"0x1","pid":1}]
        # cwd is /tmp/foobar should not match /tmp/foo
        with mock.patch("projectdock.hyprland.cwd_for_pid", return_value="/tmp/foobar"):
            assoc=hyprland.associate_clients_to_projects(clients, projs)
            self.assertEqual(assoc, {})

    def test_inaccessible_proc(self):
        projs=[{"path":"/tmp/proj"}]
        clients=[{"address":"0x1","pid":1}]
        with mock.patch("projectdock.hyprland.cwd_for_pid", return_value=None):
            assoc=hyprland.associate_clients_to_projects(clients, projs)
            self.assertEqual(assoc, {})

class WorkspaceSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = state.State()
        self.ws = WorkspaceStore(self.state)
        self.ws.load_from_state(self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_activity(self):
        self.ws.record("/tmp/proj", action="editor", editor="zed")
        self.assertIn("/tmp/proj", self.state.workspace)
        self.assertEqual(self.state.workspace["/tmp/proj"]["last_action"], "editor")
        self.assertEqual(self.state.workspace["/tmp/proj"]["editor"], "zed")

    def test_update_timestamps(self):
        self.ws.record("/tmp/proj", action="open")
        t1 = self.state.workspace["/tmp/proj"]["last_active_at"]
        time.sleep(0.01)
        self.ws.record("/tmp/proj", action="terminal")
        t2 = self.state.workspace["/tmp/proj"]["last_active_at"]
        self.assertGreater(t2, t1)

    def test_last_editor_memory(self):
        self.ws.record("/tmp/proj", action="open", editor="code")
        self.assertEqual(self.ws.last_editor_for("/tmp/proj"), "code")

    def test_corrupt_state_handling(self):
        self.state.workspace = {"bad": "not a dict for entry", "/tmp/proj": {"last_active_at": time.time()}}
        # is_active should not crash
        self.ws.is_active("/tmp/proj")
        self.ws.annotate_projects([{"path":"/tmp/proj","name":"proj"}])

    def test_stale_session_cleanup(self):
        projs=[{"path":"/tmp/a"},{"path":"/tmp/b"}]
        self.ws._ephemeral_active={"/tmp/a":[{"address":"0x1"}]}
        self.ws.cleanup_stale(projs)
        self.assertIn("/tmp/a", self.ws._ephemeral_active)
        self.ws._ephemeral_active["/tmp/stale"]=[{"address":"0x2"}]
        self.ws.cleanup_stale(projs)
        self.assertNotIn("/tmp/stale", self.ws._ephemeral_active)

    def test_active_vs_historical_separation(self):
        # record recent
        self.ws.record("/tmp/proj", action="open")
        # without hyprland refresh, recent should be considered active via fallback window? We test window vs historical
        # After refresh with no clients, ephemeral cleared, but historical remains
        self.ws._ephemeral_active = {}
        # is_active with fallback should still be true within 30min if no refresh? After refresh, should be false
        self.ws._last_refresh = time.time()
        self.assertFalse(self.ws.is_active("/tmp/proj"))
        self.assertIn("/tmp/proj", self.state.workspace)  # historical retained

    def test_is_active_with_window(self):
        self.state.workspace["/tmp/proj"]={"last_active_at": time.time(), "path":"/tmp/proj"}
        self.ws._ephemeral_active["/tmp/proj"]=[{"address":"0x1","pid":1}]
        self.ws._last_refresh = time.time()
        self.assertTrue(self.ws.is_active("/tmp/proj"))
        self.assertEqual(len(self.ws.windows_for("/tmp/proj")),1)
        self.assertIsNotNone(self.ws.most_recent_window("/tmp/proj"))

    def test_windows_for_empty(self):
        self.assertEqual(self.ws.windows_for("/tmp/nonexist"), [])

    def test_annotate_projects(self):
        self.ws.record("/tmp/a", action="open")
        self.ws._ephemeral_active["/tmp/a"]=[{"address":"0x1"}]
        self.ws._last_refresh=time.time()
        projs=[{"path":"/tmp/a","name":"a"},{"path":"/tmp/b","name":"b"}]
        self.ws.annotate_projects(projs)
        self.assertTrue(projs[0]["active"])
        self.assertFalse(projs[1]["active"])
        self.assertEqual(projs[0]["active_windows"],1)

class ActiveSectionTest(unittest.TestCase):
    def test_no_duplicate_across_sections(self):
        # simulate search.sorted_by_activity with active
        projs=[
            {"path":"/a","name":"a","pinned":True,"pin_order":0},
            {"path":"/b","name":"b","active":True,"active_rank":0},
            {"path":"/c","name":"c","recent_rank":0},
            {"path":"/d","name":"d"},
        ]
        ordered = search.sorted_by_activity(projs)
        paths=[p["path"] for p in ordered]
        self.assertEqual(len(set(paths)), len(paths))
        # precedence pinned->active->recent->projects
        self.assertEqual(paths, ["/a","/b","/c","/d"])

    def test_active_rank_order(self):
        projs=[
            {"path":"/b","name":"b","active":True,"active_rank":1},
            {"path":"/a","name":"a","active":True,"active_rank":0},
        ]
        ordered = search.sorted_by_activity(projs)
        self.assertEqual(ordered[0]["path"], "/a")

    def test_search_boost_active(self):
        projs=[
            {"path":"/a","name":"foo","active":True,"active_rank":0},
            {"path":"/b","name":"foo","active":False},
        ]
        # query foo should rank active higher
        from projectdock.search import score
        s_active = score("foo", projs[0])
        s_plain = score("foo", projs[1])
        self.assertGreater(s_active, s_plain)

class ActionMenuTest(unittest.TestCase):
    def test_focus_action_present_when_active(self):
        from projectdock.app import DockApp
        from unittest import mock
        app = mock.Mock(spec=DockApp)
        app.cfg = mock.Mock()
        app.cfg.editor_label.return_value="code"
        app.state = state.State()
        app.workspace = WorkspaceStore(app.state)
        app.workspace.load_from_state(app.state)
        app.workspace._ephemeral_active["/tmp/proj"]=[{"address":"0x1"}]
        app.workspace._last_refresh=time.time()
        # need real actions_for logic: we test via DockApp instance with mocked state
        # create minimal DockApp without Gtk
        with mock.patch("projectdock.app.DockApp.__init__", lambda self: None):
            # manually set attributes
            inst = DockApp.__new__(DockApp)
            inst.cfg = app.cfg
            inst.state = app.state
            inst.workspace = app.workspace
            # import actual method
            from projectdock.app import DockApp as Real
            inst.actions_for = Real.actions_for.__get__(inst, DockApp)
            proj={"path":"/tmp/proj","kind":"generic"}
            rows = inst.actions_for(proj)
            ids=[r[0] for r in rows if not r[0].startswith("header:")]
            self.assertIn("focus", ids)
            self.assertEqual(ids[0], "focus")

    def test_primary_is_focus_when_active(self):
        # same as above but check primary label
        from projectdock.app import DockApp
        from unittest import mock
        inst = DockApp.__new__(DockApp)
        inst.cfg = mock.Mock()
        inst.cfg.editor_label.return_value="code"
        inst.state = state.State()
        inst.workspace = WorkspaceStore(inst.state)
        inst.workspace.load_from_state(inst.state)
        inst.workspace._ephemeral_active["/tmp/proj"]=[{"address":"0x1"}]
        inst.workspace._last_refresh=time.time()
        from projectdock.app import DockApp as Real
        inst.actions_for = Real.actions_for.__get__(inst, DockApp)
        rows = inst.actions_for({"path":"/tmp/proj","kind":"generic"})
        filtered=[r for r in rows if not r[0].startswith("header:")]
        self.assertEqual(filtered[0][1], "Focus Project")

    def test_fallback_to_open_when_not_active(self):
        from projectdock.app import DockApp
        from unittest import mock
        inst = DockApp.__new__(DockApp)
        inst.cfg = mock.Mock()
        inst.cfg.editor_label.return_value="code"
        inst.state = state.State()
        inst.workspace = WorkspaceStore(inst.state)
        inst.workspace.load_from_state(inst.state)
        from projectdock.app import DockApp as Real
        inst.actions_for = Real.actions_for.__get__(inst, DockApp)
        rows = inst.actions_for({"path":"/tmp/proj","kind":"generic"})
        ids=[r[0] for r in rows]
        self.assertNotIn("focus", ids)
        self.assertTrue(any(r[0]=="open" for r in rows))

    def test_open_default_focuses_when_active(self):
        from projectdock.app import DockApp
        from unittest import mock
        inst = DockApp.__new__(DockApp)
        inst.cfg = mock.Mock()
        inst.cfg.editor_label.return_value="code"
        inst.cfg.detected_editor.return_value=["code"]
        inst.state = state.State()
        inst.workspace = WorkspaceStore(inst.state)
        inst.workspace.load_from_state(inst.state)
        inst.workspace._ephemeral_active["/tmp/proj"]=[{"address":"0x123"}]
        inst.workspace._last_refresh=time.time()
        inst._touch = mock.Mock()
        inst._record_workspace = mock.Mock()
        with mock.patch("projectdock.hyprland.focus_window", return_value=True) as foc:
            with mock.patch("projectdock.actions.open_in_editor") as editor:
                inst.open_default = type(inst).open_default.__get__(inst, DockApp)
                inst.open_default({"path":"/tmp/proj","name":"proj"})
                foc.assert_called()
                editor.assert_not_called()

class IndicatorTest(unittest.TestCase):
    def test_active_dot_present(self):
        from projectdock import ui, theme
        from unittest import mock
        import types
        palette=theme.DEFAULT_PALETTE
        dummy=mock.Mock()
        dummy.palette=palette
        dummy._cover_cache={}
        dummy._cover_for=lambda p: None
        dummy._apply_cover_style=lambda b,bg,fg: None
        dummy._cover_widget=types.MethodType(ui.LauncherWindow._cover_widget, dummy)
        dummy._project_row=types.MethodType(ui.LauncherWindow._project_row, dummy)
        proj={"name":"foo","path":"/tmp/foo","label":"Python","active":True,"pinned":False}
        row=dummy._project_row(proj)
        box=row.get_child()
        # find dot via class
        found=False
        # walk
        def walk(w):
            nonlocal found
            if hasattr(w, "get_css_classes") and "active-dot" in (w.get_css_classes() or []):
                found=True
            child=w.get_first_child()
            while child:
                walk(child)
                child=child.get_next_sibling()
        walk(box)
        self.assertTrue(found)

if __name__=="__main__":
    unittest.main()
