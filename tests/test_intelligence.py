import os
import json
import tempfile
import unittest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import intelligence, gitinfo

class NodeTest(unittest.TestCase):
    def _proj(self, tmp, kind="node"):
        return {"path": tmp, "kind": kind}

    def test_valid_package_json_dev_test_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts": {"dev": "next dev", "test": "vitest", "build": "next build"}}, fh)
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            self.assertIsNotNone(caps.get("dev"))
            self.assertEqual(caps.get("dev").command, "npm run dev")
            self.assertIsNotNone(caps.get("test"))
            self.assertEqual(caps.get("test").command, "npm run test")
            self.assertIsNotNone(caps.get("build"))

    def test_malformed_package_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                fh.write("{oops")
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            self.assertTrue(caps.is_empty())

    def test_dev_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts": {"start": "node server.js", "dev": "next dev"}}, fh)
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            self.assertEqual(caps.get("dev").script, "dev")

    def test_start_serve_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts": {"start": "node app.js"}}, fh)
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            self.assertIsNotNone(caps.get("dev"))
            self.assertEqual(caps.get("dev").script, "start")
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts": {"serve": "serve dist"}}, fh)
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            self.assertEqual(caps.get("dev").script, "serve")

    def test_test_detection_with_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts": {"test:unit": "vitest run", "build": "tsc"}}, fh)
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            self.assertIsNotNone(caps.get("test"))
            self.assertIn("test:unit", caps.get("test").script)

    def test_build_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts": {"build": "tsc", "test": "jest"}}, fh)
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            self.assertIsNotNone(caps.get("build"))

    def test_multiple_scripts_prioritized(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts = {f"script{i}": f"echo {i}" for i in range(20)}
            scripts.update({"dev": "dev", "test": "t", "build": "b"})
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts": scripts}, fh)
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            self.assertEqual(len(caps.as_list()), 3)

    def test_no_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({}, fh)
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            self.assertTrue(caps.is_empty())

    def test_dangerous_scripts_not_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts": {"preinstall": "rm -rf /", "dev": "next dev"}}, fh)
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            # only dev should be exposed, preinstall ignored
            self.assertIsNotNone(caps.get("dev"))
            # preinstall not in any capability
            for cap in caps.as_list():
                self.assertNotIn("preinstall", cap.script)

class PythonTest(unittest.TestCase):
    def _proj(self, tmp):
        return {"path": tmp, "kind": "python"}

    def test_pyproject_with_pytest(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "pyproject.toml"), "w") as fh:
                fh.write('[tool.pytest.ini_options]\naddopts=""\n[build-system]\nrequires=["setuptools"]\n')
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            self.assertIsNotNone(caps.get("test"))
            self.assertEqual(caps.get("test").command, "pytest")
            self.assertIsNotNone(caps.get("build"))

    def test_malformed_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "pyproject.toml"), "w") as fh:
                fh.write("not toml [[[[")
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            # should not crash, may be empty
            self.assertIsInstance(caps.is_empty(), bool)

    def test_requirements_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "requirements.txt"), "w").close()
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            # requirements-only no strong test/build evidence -> empty
            self.assertTrue(caps.is_empty())

    def test_manage_py(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "manage.py"), "w").close()
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            self.assertIsNotNone(caps.get("dev"))
            self.assertIn("manage.py runserver", caps.get("dev").command)
            self.assertIsNotNone(caps.get("test"))

    def test_meaningful_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            # empty pyproject without pytest/build
            with open(os.path.join(tmp, "pyproject.toml"), "w") as fh:
                fh.write('[project]\nname="foo"\n')
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for(self._proj(tmp))
            # no test/build without evidence
            self.assertIsNone(caps.get("test"))
            self.assertIsNone(caps.get("build"))

class RustTest(unittest.TestCase):
    def test_cargo(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "Cargo.toml"), "w").close()
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for({"path": tmp, "kind": "rust"})
            self.assertIsNotNone(caps.get("run"))
            self.assertIsNotNone(caps.get("test"))
            self.assertIsNotNone(caps.get("build"))

class GoTest(unittest.TestCase):
    def test_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "go.mod"), "w").close()
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for({"path": tmp, "kind": "go"})
            self.assertEqual(caps.get("run").command, "go run .")
            self.assertEqual(caps.get("test").command, "go test ./...")
            self.assertEqual(caps.get("build").command, "go build ./...")

class MakeTest(unittest.TestCase):
    def test_make_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "Makefile"), "w") as fh:
                fh.write("run:\n\techo hi\ntest:\n\techo test\nbuild:\n\techo build\n")
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for({"path": tmp, "kind": "generic"})
            self.assertIsNotNone(caps.get("run"))
            self.assertIsNotNone(caps.get("test"))
            self.assertIsNotNone(caps.get("build"))

    def test_make_conservative(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "Makefile"), "w") as fh:
                fh.write("all:\n\techo all\nweird-target!\n\techo\n")
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for({"path": tmp, "kind": "generic"})
            self.assertTrue(caps.is_empty() or caps.get("run") is None)

    def test_makefile_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "makefile"), "w") as fh:
                fh.write("dev:\n\techo dev\n")
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for({"path": tmp, "kind": "generic"})
            self.assertIsNotNone(caps.get("dev"))

class GeneralTest(unittest.TestCase):
    def test_unknown_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for({"path": tmp, "kind": "generic"})
            self.assertTrue(caps.is_empty())

    def test_unreadable_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            # no files, should not crash
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for({"path": "/nonexistent_xyz", "kind": "node"})
            self.assertTrue(caps.is_empty())

    def test_malformed_metadata_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                fh.write("not json")
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            self.assertTrue(caps.is_empty())

    def test_caching(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts": {"dev": "a"}}, fh)
            intelligence.invalidate(tmp)
            caps1 = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            caps2 = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            self.assertIs(caps1, caps2)  # cached object identity
            # after invalidate should recompute
            intelligence.invalidate(tmp)
            caps3 = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            self.assertIsNot(caps1, caps3)

    def test_does_not_mutate_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = {"path": tmp, "kind": "node", "name": "foo"}
            orig = dict(proj)
            intelligence.invalidate(tmp)
            intelligence.capabilities_for(proj)
            self.assertEqual(proj, orig)

    def test_actions_only_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts": {"dev": "next dev"}}, fh)
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            for cap in caps.as_list():
                self.assertRegex(cap.command, r"^[A-Za-z0-9 _./:\-@]+$")

    def test_no_arbitrary_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                json.dump({"scripts": {"dev": "next dev; rm -rf /"}}, fh)
            intelligence.invalidate(tmp)
            caps = intelligence.capabilities_for({"path": tmp, "kind": "node"})
            # command is constructed as "npm run dev", not the script content
            if not caps.is_empty():
                self.assertNotIn("rm -rf", caps.get("dev").command)

class GitHealthTest(unittest.TestCase):
    def setUp(self):
        import shutil
        if shutil.which("git") is None:
            self.skipTest("git not installed")

    def test_clean_repo(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp, check=True)
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp, check=True)
            gitinfo.invalidate(tmp)
            h = gitinfo.health(tmp)
            self.assertIsNotNone(h)
            self.assertEqual(h.branch, "main")
            self.assertTrue(h.clean)
            self.assertFalse(h.dirty)
            self.assertEqual(h.untracked, 0)

    def test_modified_repo(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp, check=True)
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp, check=True)
            open(os.path.join(tmp, "file.txt"), "w").close()
            gitinfo.invalidate(tmp)
            h = gitinfo.health(tmp)
            self.assertTrue(h.dirty or h.untracked>0)

    def test_untracked(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp, check=True)
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp, check=True)
            open(os.path.join(tmp, "untracked.txt"), "w").close()
            gitinfo.invalidate(tmp)
            h = gitinfo.health(tmp)
            self.assertEqual(h.untracked, 1)

    def test_no_remote(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp, check=True)
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp, check=True)
            gitinfo.invalidate(tmp)
            h = gitinfo.health(tmp)
            self.assertEqual(h.ahead, 0)
            self.assertEqual(h.behind, 0)

    def test_failures_not_crash(self):
        self.assertIsNone(gitinfo.health("/nonexistent"))
        self.assertIsNone(gitinfo.info("/nonexistent"))

if __name__ == "__main__":
    unittest.main()
