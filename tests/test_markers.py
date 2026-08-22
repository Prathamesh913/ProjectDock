import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import markers


class MarkerTest(unittest.TestCase):
    def test_every_type_has_icon_and_label(self):
        for kind in markers.MARKERS:
            self.assertTrue(kind.label)
            self.assertTrue(kind.icon)
            self.assertTrue(kind.id)

    def test_node_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "package.json"), "w").close()
            kind = markers.detect(tmp)
            self.assertEqual(kind.id, "node")

    def test_typescript_detection_via_tsconfig(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                fh.write("{}")
            open(os.path.join(tmp, "tsconfig.json"), "w").close()
            kind = markers.detect(tmp)
            self.assertEqual(kind.id, "node-ts")

    def test_typescript_detection_via_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                fh.write('{"devDependencies": {"typescript": "^5"}}')
            kind = markers.detect(tmp)
            self.assertEqual(kind.id, "node-ts")

    def test_rust_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "Cargo.toml"), "w").close()
            self.assertEqual(markers.detect(tmp).id, "rust")

    def test_python_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "pyproject.toml"), "w").close()
            self.assertEqual(markers.detect(tmp).id, "python")
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "requirements.txt"), "w").close()
            self.assertEqual(markers.detect(tmp).id, "python")

    def test_git_only_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, ".git"))
            self.assertEqual(markers.detect(tmp).id, "git")

    def test_missing_dir(self):
        kind = markers.detect("/nonexistent/path/xyz")
        self.assertEqual(kind.id, "generic")

    def test_broken_package_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                fh.write("{not json")
            kind = markers.detect(tmp)
            self.assertEqual(kind.id, "node")

    def test_dotnet_csproj_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "MyApp.csproj"), "w").close()
            self.assertEqual(markers.detect(tmp).id, "dotnet")

    def test_dotnet_sln_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "Solution.sln"), "w").close()
            self.assertEqual(markers.detect(tmp).id, "dotnet")

    def test_haskell_cabal_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "mypackage.cabal"), "w").close()
            self.assertEqual(markers.detect(tmp).id, "haskell")

    def test_haskell_stack_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "stack.yaml"), "w").close()
            self.assertEqual(markers.detect(tmp).id, "haskell")

    def test_name_is_marker(self):
        self.assertTrue(markers.name_is_marker(".git"))
        self.assertTrue(markers.name_is_marker("Cargo.toml"))
        self.assertTrue(markers.name_is_marker("package.json"))
        self.assertTrue(markers.name_is_marker("MyApp.csproj"))
        self.assertTrue(markers.name_is_marker("foo.cabal"))
        self.assertFalse(markers.name_is_marker("README.md"))
        self.assertFalse(markers.name_is_marker("index.html"))

    def test_dotnet_priority_over_git(self):
        # A .csproj + .git dir should still be typed as .NET, not generic git.
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, ".git"))
            open(os.path.join(tmp, "App.csproj"), "w").close()
            self.assertEqual(markers.detect(tmp).id, "dotnet")

    def test_ignored_dirs(self):
        self.assertTrue(markers.is_ignored_dir("node_modules"))
        self.assertTrue(markers.is_ignored_dir(".git"))
        self.assertTrue(markers.is_ignored_dir(".venv"))
        self.assertTrue(markers.is_ignored_dir("my.egg-info"))
        self.assertFalse(markers.is_ignored_dir("src"))
        self.assertFalse(markers.is_ignored_dir("my-app"))


if __name__ == "__main__":
    unittest.main()
