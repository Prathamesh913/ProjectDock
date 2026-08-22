import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import discovery


def make(tmp, *paths):
    for p in paths:
        os.makedirs(os.path.join(tmp, p), exist_ok=True)


def touch(tmp, *paths):
    for p in paths:
        d = os.path.dirname(os.path.join(tmp, p))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(tmp, p), "w") as fh:
            fh.write("")


class DiscoveryTest(unittest.TestCase):
    def test_missing_root(self):
        result = discovery.scan(["/definitely/not/a/real/root"])
        self.assertEqual(result.projects, [])

    def test_root_is_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "afile")
            open(f, "w").close()
            result = discovery.scan([f])
            self.assertEqual(result.projects, [])

    def test_basic_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            make(tmp, "web", "rusty", "data")
            touch(tmp, "web/package.json", "rusty/Cargo.toml")
            result = discovery.scan([tmp])
            paths = {p["name"]: p for p in result.projects}
            self.assertEqual(set(paths), {"web", "rusty"})
            self.assertEqual(paths["web"]["kind"], "node")
            self.assertEqual(paths["rusty"]["kind"], "rust")
            self.assertEqual(paths["web"]["is_git"], False)

    def test_git_dir_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            make(tmp, "proj/.git")
            result = discovery.scan([tmp])
            self.assertEqual(len(result.projects), 1)
            self.assertEqual(result.projects[0]["name"], "proj")
            self.assertTrue(result.projects[0]["is_git"])

    def test_nested_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            touch(tmp, "mono/package.json", "mono/packages/a/package.json",
                  "mono/packages/b/Cargo.toml")
            result = discovery.scan([tmp])
            names = {p["name"] for p in result.projects}
            self.assertEqual(names, {"mono"})

    def test_ignored_dirs_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            touch(tmp, "proj/package.json",
                  "proj/node_modules/pkg/package.json",
                  "proj/target/x/Cargo.toml",
                  "proj/.venv/y/pyproject.toml",
                  "proj/dist/z/package.json")
            result = discovery.scan([tmp])
            self.assertEqual(len(result.projects), 1)
            self.assertEqual(result.projects[0]["name"], "proj")

    def test_project_root_stops_descent(self):
        with tempfile.TemporaryDirectory() as tmp:
            touch(tmp, "proj/package.json",
                  "proj/sub/package.json",
                  "proj/sub/deep/Cargo.toml")
            result = discovery.scan([tmp])
            names = {p["name"] for p in result.projects}
            self.assertEqual(names, {"proj"})

    def test_depth_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            touch(tmp, "a/b/c/d/package.json", "a/b/package.json")
            result = discovery.scan([tmp], max_depth=2)
            names = {p["name"] for p in result.projects}
            self.assertEqual(names, {"b"})

    def test_hidden_dirs_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            touch(tmp, ".hidden/package.json", "ok/package.json")
            result = discovery.scan([tmp])
            self.assertEqual({p["name"] for p in result.projects}, {"ok"})

    def test_symlink_loop_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            make(tmp, "real/loop")
            touch(tmp, "real/package.json")
            os.symlink(os.path.join(tmp, "real"),
                       os.path.join(tmp, "real", "loop", "back"))
            result = discovery.scan([tmp])
            self.assertEqual(len(result.projects), 1)

    def test_project_sorting(self):
        with tempfile.TemporaryDirectory() as tmp:
            touch(tmp, "zeta/package.json", "alpha/package.json", "mid/package.json")
            result = discovery.scan([tmp])
            names = [p["name"] for p in result.projects]
            self.assertEqual(names, ["alpha", "mid", "zeta"])

    def test_roots_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            make(tmp, "proj")
            touch(tmp, "proj/package.json")
            roots = [tmp]
            mtimes = discovery.root_mtimes(roots)
            self.assertFalse(discovery.roots_changed(roots, mtimes))
            self.assertTrue(discovery.roots_changed(roots, {}))
            touch(tmp, "proj2/Cargo.toml")
            self.assertTrue(discovery.roots_changed(roots, mtimes))

    def test_suffix_marker_project_typed(self):
        with tempfile.TemporaryDirectory() as tmp:
            touch(tmp, "dotnet-app/App.csproj", "hs-lib/foo.cabal")
            result = discovery.scan([tmp])
            by_name = {p["name"]: p for p in result.projects}
            self.assertEqual(by_name["dotnet-app"]["kind"], "dotnet")
            self.assertEqual(by_name["hs-lib"]["kind"], "haskell")

    def test_many_dirs_budget_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(300):
                make(tmp, f"d{i}/a/b/c")
            touch(tmp, "d0/a/b/c/package.json")
            result = discovery.scan([tmp])
            self.assertEqual(len(result.projects), 1)


if __name__ == "__main__":
    unittest.main()
