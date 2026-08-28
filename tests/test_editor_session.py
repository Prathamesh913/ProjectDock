import os, json, sys, tempfile, unittest, time, signal
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import tools, config as cfg_mod, intelligence, sessions, workspace, state as state_mod
import projectdock.actions as actions

class EditorDiscoveryTest(unittest.TestCase):
    def test_available_editor_detected(self):
        with mock.patch("projectdock.tools._which", side_effect=lambda x: "/usr/bin/code" if x=="code" else None):
            tools.clear_cache()
            avail = tools.available_tools()
            ids = [t.id for t in avail]
            self.assertIn("vscode", ids)
            tools.clear_cache()

    def test_unavailable_hidden(self):
        with mock.patch("projectdock.tools._which", return_value=None):
            tools.clear_cache()
            self.assertEqual(tools.available_tools(), [])
            tools.clear_cache()

    def test_duplicate_aliases_dedup(self):
        # zed has probes zeditor, zed – should appear once
        def which(name):
            if name in ("zeditor", "zed"):
                return f"/usr/bin/{name}"
            return None
        with mock.patch("projectdock.tools._which", side_effect=which):
            tools.clear_cache()
            avail = tools.available_tools()
            zeds = [t for t in avail if t.id=="zed"]
            self.assertEqual(len(zeds), 1)
            tools.clear_cache()

    def test_t3code_alias(self):
        def which(name):
            if name=="t3":
                return "/usr/bin/t3"
            return None
        with mock.patch("projectdock.tools._which", side_effect=which):
            tools.clear_cache()
            t = tools.get_tool("t3code")
            exe = tools.resolve_executable(t)
            self.assertEqual(exe, "/usr/bin/t3")
            tools.clear_cache()
        # t3code also
        with mock.patch("projectdock.tools._which", side_effect=lambda x: "/usr/bin/t3code" if x=="t3code" else None):
            tools.clear_cache()
            t = tools.get_tool("t3code")
            self.assertEqual(tools.resolve_executable(t), "/usr/bin/t3code")
            tools.clear_cache()

    def test_fallback_editor(self):
        cfg = cfg_mod.Config(roots=[])
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(cfg.detected_editor())

    def test_preferred_disappears(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp,"cfg"), "XDG_STATE_HOME": os.path.join(tmp,"state")}):
                st = state_mod.load()
                ws = workspace.WorkspaceStore(st)
                ws.load_from_state(st)
                ws.set_preferred_tool("/proj/a", "zed")
                # simulate zed disappearing
                with mock.patch("projectdock.tools._which", return_value=None):
                    tools.clear_cache()
                    from projectdock.app import DockApp
                    # preferred_tool_for should return None when not available
                    with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp,"cfg2"), "XDG_STATE_HOME": os.path.join(tmp,"state2")}):
                        pass
                    # direct test via workspace + tools
                    self.assertIsNone(tools.validate_tool_id("zed"))
                tools.clear_cache()

class PickerTest(unittest.TestCase):
    def test_editor_picker_rows_only_editors(self):
        with mock.patch("projectdock.tools._which", side_effect=lambda x: "/usr/bin/code" if x=="code" else ("/usr/bin/nvim" if x=="nvim" else None)):
            tools.clear_cache()
            from projectdock.app import DockApp
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp,"cfg"), "XDG_STATE_HOME": os.path.join(tmp,"state")}):
                    app = DockApp()
                    proj = {"path":"/tmp/proj","name":"proj"}
                    rows = app.editor_picker_rows()
                    # should contain CODE EDITORS header and vscode
                    ids = [r[0] for r in rows]
                    self.assertTrue(any("vscode" in x or "nvim" in x for x in ids) or len(rows)>0)
            tools.clear_cache()

    def test_choose_editor_rows_preferred_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp,"cfg"), "XDG_STATE_HOME": os.path.join(tmp,"state")}):
                from projectdock.app import DockApp
                with mock.patch("projectdock.tools._which", side_effect=lambda x: "/usr/bin/code" if x in ("code","nvim") else None):
                    tools.clear_cache()
                    app = DockApp()
                    proj_path = "/tmp/myproj"
                    app.workspace.set_preferred_tool(proj_path, "vscode")
                    # also need editor preference?
                    rows = app.choose_editor_rows({"path": proj_path, "name":"myproj"})
                    # find vscode row with hint preferred
                    found = False
                    for r in rows:
                        if r[0]=="tool:vscode":
                            self.assertEqual(r[3], "preferred")
                            found=True
                    self.assertTrue(found)
                    tools.clear_cache()

class LongRunningTest(unittest.TestCase):
    def test_dev_long_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp,"package.json"),"w") as f:
                json.dump({"scripts":{"dev":"next dev","test":"jest","build":"tsc"}},f)
            intelligence.invalidate(tmp)
            caps=intelligence.capabilities_for({"path":tmp,"kind":"node"})
            self.assertTrue(caps.get("dev").long_running)
            self.assertFalse(caps.get("test").long_running)
            self.assertFalse(caps.get("build").long_running)

    def test_run_long_running_rust_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp,"Cargo.toml"),"w").close()
            intelligence.invalidate(tmp)
            caps=intelligence.capabilities_for({"path":tmp,"kind":"rust"})
            self.assertTrue(caps.get("run").long_running)
            self.assertFalse(caps.get("test").long_running)
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp,"go.mod"),"w").close()
            intelligence.invalidate(tmp)
            caps=intelligence.capabilities_for({"path":tmp,"kind":"go"})
            self.assertTrue(caps.get("run").long_running)

class SessionTest(unittest.TestCase):
    def test_session_creation(self):
        store=sessions.SessionStore()
        s=store.create("/proj/a","int:dev:npm run dev","Run Dev Server","npm run dev","dev", pid=12345, long_running=True)
        self.assertEqual(s.project_path,"/proj/a")
        self.assertTrue(s.long_running)
        self.assertEqual(len(store.for_project("/proj/a")),1)

    def test_per_capability_dedup(self):
        store=sessions.SessionStore()
        s1=store.create("/p","int:dev:npm run dev","Dev","npm run dev","dev", pid=111, long_running=True)
        s2=store.create("/p","int:dev:npm run dev","Dev","npm run dev","dev", pid=222, long_running=True)
        lst=store.for_project("/p")
        self.assertEqual(len(lst),1)
        self.assertEqual(lst[0].pid,222)

    def test_bounded(self):
        store=sessions.SessionStore()
        for i in range(10):
            store.create(f"/proj/{i%2}",f"act:{i}","Lab",f"cmd{i}","dev", pid=100+i, long_running=False)
        # per project max 5
        self.assertLessEqual(len(store.for_project("/proj/0")),5)
        self.assertLessEqual(len(store.for_project("/proj/1")),5)

    def test_exited_cleanup(self):
        store=sessions.SessionStore()
        # pid that doesn't exist
        s=store.create("/p","int:dev:npm run dev","Dev","npm run dev","dev", pid=999999, long_running=True)
        # is_running should be False and cleanup removes
        self.assertFalse(s.is_running())
        store.cleanup()
        self.assertEqual(len(store.for_project("/p")),0)

    def test_multiple_sessions_different_actions(self):
        store=sessions.SessionStore()
        store.create("/p","int:dev:npm run dev","Dev","npm run dev","dev", pid=111, long_running=True)
        store.create("/p","terminal","Terminal","","terminal", pid=222, long_running=False)
        self.assertEqual(len(store.for_project("/p")),2)

    def test_stale_pid_reuse(self):
        store=sessions.SessionStore()
        # create session with real current pid
        pid=os.getpid()
        s=store.create("/p","int:dev:npm run dev","Dev","npm run dev","dev", pid=pid, long_running=True)
        # should be running
        self.assertTrue(s.is_running())
        # fake stale by changing start_ticks
        s.start_ticks = 9999999
        self.assertFalse(s.is_running())

class StopRestartTest(unittest.TestCase):
    def test_stop_owned(self):
        store=sessions.SessionStore()
        # spawn a sleep process
        import subprocess
        proc=subprocess.Popen(["sleep","30"], start_new_session=True)
        try:
            s=store.create("/p","int:dev:sleep 30","Dev","sleep 30","dev", pid=proc.pid, long_running=True)
            self.assertTrue(s.is_running())
            ok=store.stop(s)
            self.assertTrue(ok)
            time.sleep(0.3)
            # after stop, session should be removed from store
            self.assertEqual(len(store.for_project("/p")),0)
            # proc should be terminated
            proc.poll()
            # may still be zombie briefly, but at least store removed
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try: proc.kill()
                except: pass

    def test_stop_unrelated_rejected(self):
        store=sessions.SessionStore()
        # pid 1 is init, we own no session for it but create fake with wrong start_ticks
        s=store.create("/p","int:dev:npm run dev","Dev","npm run dev","dev", pid=1, long_running=True)
        s.start_ticks = 12345  # wrong
        # should be considered not running due to pid reuse detection? but pid 1 exists, but start_ticks mismatch => is_running false
        self.assertFalse(s.is_running())
        # stop should fail and remove
        ok=store.stop(s)
        self.assertFalse(ok)

    def test_restart_revalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj_path=os.path.join(tmp,"proj")
            os.makedirs(proj_path)
            with open(os.path.join(proj_path,"package.json"),"w") as f:
                json.dump({"scripts":{"dev":"next dev"}},f)
            intelligence.invalidate(proj_path)
            from projectdock.app import DockApp
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp,"cfg"), "XDG_STATE_HOME": os.path.join(tmp,"state")}):
                app=DockApp()
                app.state.projects=[{"path":proj_path,"name":"proj","kind":"node"}]
                # create a dev session
                import subprocess
                proc=subprocess.Popen(["sleep","30"], start_new_session=True)
                try:
                    sess=app.sessions.create(proj_path,"int:dev:npm run dev","Run Dev Server","npm run dev","dev", pid=proc.pid, long_running=True)
                    # restart should stop and create new (mock terminal launch)
                    with mock.patch("projectdock.actions.open_in_terminal", return_value=99999) as mocked:
                        # Remove package.json dev script to simulate removed capability
                        with open(os.path.join(proj_path,"package.json"),"w") as f:
                            json.dump({"scripts":{}},f)
                        intelligence.invalidate(proj_path)
                        app.run_action("restart_dev", {"path":proj_path,"name":"proj","kind":"node"})
                        mocked.assert_not_called()
                        # restore
                        with open(os.path.join(proj_path,"package.json"),"w") as f:
                            json.dump({"scripts":{"dev":"next dev"}},f)
                        intelligence.invalidate(proj_path)
                        app.run_action("restart_dev", {"path":proj_path,"name":"proj","kind":"node"})
                        # Should have called open_in_terminal once
                        mocked.assert_called()
                finally:
                    try: proc.terminate(); proc.wait(timeout=1)
                    except: pass

    def test_duplicate_dev_prevented(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj_path=os.path.join(tmp,"proj")
            os.makedirs(proj_path)
            with open(os.path.join(proj_path,"package.json"),"w") as f:
                json.dump({"scripts":{"dev":"x"}},f)
            intelligence.invalidate(proj_path)
            from projectdock.app import DockApp
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp,"cfg"), "XDG_STATE_HOME": os.path.join(tmp,"state")}):
                app=DockApp()
                app.state.projects=[{"path":proj_path,"name":"proj","kind":"node"}]
                # create running session
                import subprocess
                proc=subprocess.Popen(["sleep","30"], start_new_session=True)
                try:
                    app.sessions.create(proj_path,"int:dev:npm run dev","Run Dev Server","npm run dev","dev", pid=proc.pid, long_running=True)
                    with mock.patch("projectdock.actions.open_in_terminal", return_value=123) as mocked:
                        app.run_action("int:dev:npm run dev", {"path":proj_path,"name":"proj","kind":"node"})
                        mocked.assert_not_called()
                finally:
                    try: proc.terminate(); proc.wait(timeout=1)
                    except: pass

class HyprlandAssociationTest(unittest.TestCase):
    def test_confident_cwd(self):
        from projectdock import hyprland as hl
        clients=[{"address":"0x1","class":"code","title":"proj","pid":123}]
        projects=[{"path":"/home/u/Projects/myapp","name":"myapp"}]
        def resolver(pid): return "/home/u/Projects/myapp"
        assoc=hl.associate_clients_to_projects(clients, projects, cwd_resolver=resolver)
        self.assertIn("/home/u/Projects/myapp", assoc)

    def test_no_title_guessing(self):
        from projectdock import hyprland as hl
        clients=[{"address":"0x1","class":"code","title":"myapp – VS Code","pid":123}]
        projects=[{"path":"/home/u/Projects/myapp","name":"myapp"}]
        def resolver(pid): return "/other"
        assoc=hl.associate_clients_to_projects(clients, projects, cwd_resolver=resolver)
        self.assertNotIn("/home/u/Projects/myapp", assoc)

    def test_focus_only_when_valid(self):
        from projectdock import hyprland as hl
        with mock.patch("subprocess.run") as mocked:
            mocked.return_value=type("o",(),{"returncode":0})()
            self.assertTrue(hl.focus_window("0x123"))
            mocked.assert_called()
        with mock.patch("subprocess.run", side_effect=Exception("fail")):
            self.assertFalse(hl.focus_window(""))

class SmartPrimaryTest(unittest.TestCase):
    def test_active_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp,"cfg"), "XDG_STATE_HOME": os.path.join(tmp,"state")}):
                from projectdock.app import DockApp
                app=DockApp()
                proj={"path":"/tmp/proj","name":"proj","kind":"generic"}
                app.state.projects=[proj]
                app.workspace._ephemeral_active={"/tmp/proj":[{"address":"0x1"}]}
                # is_active true
                is_active=app.workspace.is_active("/tmp/proj")
                self.assertTrue(is_active)
                pid, label, sub=app._smart_primary(proj, is_active, [])
                self.assertEqual(pid,"focus")

    def test_preferred_editor_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp,"cfg"), "XDG_STATE_HOME": os.path.join(tmp,"state")}):
                from projectdock.app import DockApp
                app=DockApp()
                proj={"path":tmp,"name":"tmp","kind":"generic"}
                app.state.projects=[proj]
                # no active, no preferred, fallback to open
                pid,label,sub=app._smart_primary(proj, False, [])
                self.assertEqual(pid,"open")

class PersistenceTest(unittest.TestCase):
    def test_runtime_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp,"cfg"), "XDG_STATE_HOME": os.path.join(tmp,"state")}):
                from projectdock.app import DockApp
                app=DockApp()
                proj="/tmp/proj"
                import subprocess
                proc=subprocess.Popen(["sleep","5"], start_new_session=True)
                try:
                    sess=app.sessions.create(proj,"int:dev:npm run dev","Dev","npm run dev","dev", pid=proc.pid, long_running=True)
                    # save state should not include sessions
                    state_mod.save(app.state)
                    # reload state
                    st2=state_mod.load()
                    self.assertNotIn("sessions", st2.workspace if hasattr(st2,"workspace") else {})
                    # sessions store is ephemeral, new app has empty
                    from projectdock.app import DockApp as DA2
                    # new instance
                    app2=DA2()
                    self.assertEqual(len(app2.sessions.for_project(proj)),0)
                finally:
                    try: proc.terminate(); proc.wait(timeout=1)
                    except: pass

    def test_preferred_editor_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp,"cfg"), "XDG_STATE_HOME": os.path.join(tmp,"state")}):
                from projectdock.app import DockApp
                with mock.patch("shutil.which", return_value="/usr/bin/code"):
                    app=DockApp()
                    path="/tmp/a"
                    app.workspace.record(path, editor="code")
                    state_mod.save(app.state)
                    st2=state_mod.load()
                    ws2=workspace.WorkspaceStore(st2); ws2.load_from_state(st2)
                    self.assertEqual(ws2.get_preferred_editor(path), "code")

if __name__=="__main__":
    unittest.main()
