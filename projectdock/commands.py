"""Discover the few well-known commands a project exposes.

Kept deliberately conservative: only commands that can be inferred with high
confidence are surfaced (package.json scripts, Cargo, Go). Returns a list of
(display, shell_command) tuples.
"""

import json
import os

_COMMON_SCRIPTS = (
    ("dev", "dev"),
    ("start", "start"),
    ("build", "build"),
    ("test", "test"),
    ("lint", "lint"),
)

_LOCKFILES = (
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
)

# Import intelligence package manager resolver to keep consistent; avoid circular import at runtime
def _pm_runner_for_path(path):
    try:
        from . import intelligence as _intel
        return _intel.detect_package_manager(path)[1]
    except Exception:
        # Fallback conservative: check lockfiles then npm
        for lockfile, runner in _LOCKFILES:
            if os.path.exists(os.path.join(path, lockfile)):
                return f"{runner} run"
        if os.path.exists(os.path.join(path, "package-lock.json")) or os.path.exists(os.path.join(path, "npm-shrinkwrap.json")):
            return "npm run"
        return "npm run"


def discover(project):
    """Return up to a few (label, command) tuples for the project."""
    path = project.get("path", "")
    kind = project.get("kind", "")
    if not os.path.isdir(path):
        return []

    if kind in ("node", "node-ts", "deno"):
        return _node_commands(path, kind)
    if kind == "rust":
        return _rust_commands()
    if kind == "go":
        return _go_commands()
    if kind == "cmake":
        return _cmake_commands()
    if kind == "generic" and os.path.exists(os.path.join(path, "Makefile")):
        return _make_commands()
    return []


def _node_commands(path, kind):
    if kind == "deno":
        scripts = _read_scripts(os.path.join(path, "deno.json"), path)
        if not scripts:
            scripts = _read_scripts(os.path.join(path, "deno.jsonc"), path)
        runner = "deno task"
    else:
        scripts = _read_scripts(os.path.join(path, "package.json"), path)
        runner = _pm_runner_for_path(path)

    out = []
    for name, script in scripts:
        if not script or len(out) >= 4:
            break
        out.append((f"{name}", f"{runner} {name}"))
    return out


def _package_runner(path):
    return _pm_runner_for_path(path)


def _read_scripts(pkg_path, path):
    try:
        with open(pkg_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    scripts = data.get("scripts") or data.get("tasks")
    if not isinstance(scripts, dict):
        return []
    out = []
    for name, _ in _COMMON_SCRIPTS:
        if name in scripts:
            out.append((name, scripts[name]))
    return out


def _rust_commands():
    return [
        ("run", "cargo run"),
        ("build", "cargo build"),
        ("test", "cargo test"),
    ]


def _go_commands():
    return [
        ("run", "go run ."),
        ("build", "go build ./..."),
        ("test", "go test ./..."),
    ]


def _cmake_commands():
    return [
        ("build", "cmake --build build"),
        ("test", "ctest --test-dir build"),
    ]


def _make_commands():
    return [
        ("make", "make"),
        ("build", "make build"),
        ("test", "make test"),
    ]
