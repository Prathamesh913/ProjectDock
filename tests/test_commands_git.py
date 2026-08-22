import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import commands, gitinfo


class CommandsTest(unittest.TestCase):
    def test_npm_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                fh.write('{"scripts": {"dev": "next dev", "build": "next build", "test": "vitest"}}')
            found = commands.discover({"path": tmp, "kind": "node"})
            names = [name for name, _ in found]
            self.assertEqual(names, ["dev", "build", "test"])
            self.assertTrue(all(cmd.startswith("npm run ") for _, cmd in found))

    def test_bun_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                fh.write('{"scripts": {"dev": "next dev"}}')
            open(os.path.join(tmp, "bun.lock"), "w").close()
            found = commands.discover({"path": tmp, "kind": "node"})
            self.assertEqual(found[0][1], "bun run dev")

    def test_pnpm_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                fh.write('{"scripts": {"build": "tsc"}}')
            open(os.path.join(tmp, "pnpm-lock.yaml"), "w").close()
            found = commands.discover({"path": tmp, "kind": "node"})
            self.assertEqual(found[0][1], "pnpm run build")

    def test_cargo_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "Cargo.toml"), "w").close()
            found = commands.discover({"path": tmp, "kind": "rust"})
            self.assertEqual([c for _, c in found],
                             ["cargo run", "cargo build", "cargo test"])

    def test_go_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            found = commands.discover({"path": tmp, "kind": "go"})
            self.assertEqual([c for _, c in found],
                             ["go run .", "go build ./...", "go test ./..."])

    def test_missing_project(self):
        self.assertEqual(commands.discover({"path": "/nonexistent", "kind": "node"}), [])

    def test_broken_package_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "package.json"), "w") as fh:
                fh.write("{oops")
            self.assertEqual(commands.discover({"path": tmp, "kind": "node"}), [])

    def test_unknown_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(commands.discover({"path": tmp, "kind": "generic"}), [])

    def test_makefile_for_generic(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "Makefile"), "w").close()
            found = commands.discover({"path": tmp, "kind": "generic"})
            self.assertTrue(any(c == "make" for _, c in found))


class GitInfoTest(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git not installed")

    def test_non_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(gitinfo.info(tmp))

    def test_clean_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp, check=True)
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                            "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp, check=True)
            branch, dirty = gitinfo.info(tmp)
            self.assertEqual(branch, "main")
            self.assertFalse(dirty)

    def test_dirty_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp, check=True)
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                            "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp, check=True)
            open(os.path.join(tmp, "file.txt"), "w").close()
            branch, dirty = gitinfo.info(tmp)
            self.assertEqual(branch, "main")
            self.assertTrue(dirty)

    def test_cache_and_invalidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(gitinfo.info(tmp))
            gitinfo.invalidate(tmp)
            self.assertIsNone(gitinfo.info(tmp))

    def test_missing_dir(self):
        self.assertIsNone(gitinfo.info("/nonexistent/xyz"))


if __name__ == "__main__":
    unittest.main()
