"""Project intelligence: lightweight, safe capability detection.

Detects useful development actions from project files without executing
anything. Each project maps to structured ``ProjectCapabilities`` with
``Capability`` entries (dev/test/build/run). Detection is pure file
inspection, never spawns installers.

Caching is in-memory per path + mtime of inspected files; malformed files
never propagate exceptions.

Extensible: add a new ecosystem by adding a detector function and
registering it.
"""

import json
import os
import re
import time
from dataclasses import dataclass, field

# ------------------------------------------------------------
@dataclass(frozen=True)
class Capability:
    """A single runnable project action."""
    key: str  # dev | test | build | run
    label: str  # human label shown in menu
    command: str  # shell command to run in terminal cwd
    script: str = ""  # underlying script name for tracing
    available: bool = True  # whether required executable is installed
    pm: str = ""  # package manager for node caps (npm/pnpm/yarn/bun) or ""
    long_running: bool = False  # dev/serve/start/run are typically long-running

@dataclass
class ProjectCapabilities:
    path: str
    kind: str = ""
    capabilities: dict = field(default_factory=dict)  # key -> Capability

    def get(self, key):
        return self.capabilities.get(key)

    def as_list(self):
        # deterministic order: dev, test, build, run, extras
        order = ["dev", "test", "build", "run"]
        out = []
        for k in order:
            if k in self.capabilities:
                out.append(self.capabilities[k])
        # any extras not in order
        for k, v in self.capabilities.items():
            if k not in order:
                out.append(v)
        return out

    def is_empty(self):
        return not self.capabilities

# ------------------------------------------------------------
# Cache: path -> (mtime_signature, ProjectCapabilities)
_CACHE = {}
_CACHE_MTIME_SIG = {}

def _sig_for(path, files):
    """Simple mtime signature for listed files; 0 if missing."""
    sig = []
    for fname in files:
        p = os.path.join(path, fname)
        try:
            sig.append(os.stat(p).st_mtime)
        except OSError:
            sig.append(0)
    return tuple(sig)

def _cached_or_compute(path, kind, files, compute):
    sig = _sig_for(path, files)
    key = path
    cached = _CACHE.get(key)
    old_sig = _CACHE_MTIME_SIG.get(key)
    if cached is not None and old_sig == sig:
        # ensure kind still matches (type may have changed)
        if cached.kind == kind:
            return cached
    # compute fresh
    caps = compute(path)
    caps.path = path
    caps.kind = kind
    _CACHE[key] = caps
    _CACHE_MTIME_SIG[key] = sig
    return caps

def invalidate(path=None):
    if path is None:
        _CACHE.clear()
        _CACHE_MTIME_SIG.clear()
    else:
        _CACHE.pop(path, None)
        _CACHE_MTIME_SIG.pop(path, None)

# ------------------------------------------------------------
# Node
_NODE_WATCHED = [
    "package.json", "deno.json", "deno.jsonc",
    "package-lock.json", "npm-shrinkwrap.json",
    "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock",
]

_DEV_PRIORITY = ["dev", "start:dev", "serve", "start"]
_TEST_PRIORITY = ["test", "test:unit", "test:e2e", "e2e"]
_BUILD_PRIORITY = ["build", "build:prod", "compile"]

_DANGEROUS_SCRIPTS = {"preinstall", "postinstall", "preuninstall", "postuninstall", "install"}

# Package manager detection priority as per spec
_KNOWN_PMS = ("npm", "pnpm", "yarn", "bun")

_LOCK_PM_MAP = {
    "package-lock.json": "npm",
    "npm-shrinkwrap.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "bun.lockb": "bun",
    "bun.lock": "bun",
}

# Lock check order for conflicting evidence: deterministic.
# Order rationale: when multiple lockfiles coexist (a stale bun.lock left
# over from a previous experiment, but the project actually runs with npm),
# the authoritative npm lockfile wins, and stale bun.lock files do not
# dominate. We then prefer non-npm pm lockfiles over the npm fallback, but
# never over package-lock.json itself.
_LOCK_CHECK_ORDER = [
    "package-lock.json", "npm-shrinkwrap.json",
    "pnpm-lock.yaml", "yarn.lock",
    "bun.lockb", "bun.lock",
]

import shutil as _shutil

def _package_manager_from_field(path):
    """Return pm string from packageManager field if valid, else None."""
    try:
        with open(os.path.join(path, "package.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    pm = data.get("packageManager")
    if not isinstance(pm, str) or not pm.strip():
        return None
    # packageManager format e.g. "npm@10.2.3" "pnpm@9.0.0" "yarn@4.0.0" "bun@1.0.0"
    pm = pm.strip().lower()
    # Extract before @
    base = pm.split("@")[0].strip()
    # Some projects use "yarn@..." or "yarn classic"? Keep simple
    # Validate base is known pm; also handle "yarn" vs "yarn@berry"
    for known in _KNOWN_PMS:
        if base == known or base.startswith(known):
            return known
    return None

def _package_manager_from_lockfiles(path):
    for lock in _LOCK_CHECK_ORDER:
        if os.path.exists(os.path.join(path, lock)):
            return _LOCK_PM_MAP[lock]
    return None

def _fallback_package_manager():
    """Pick the most preferred package manager that is actually installed.

    Precedence is npm > pnpm > yarn > bun, restricted to executables that
    exist on the current system. Crucially: this routine MUST NOT silently
    invent a pm that is not installed. A stale `bun.lock` (or no evidence at
    all) on a machine without `npm` should not blindly pick `bun`; we only
    return a pm whose executable is on PATH.
    """
    for pm in ("npm", "pnpm", "yarn", "bun"):
        if _shutil.which(pm) is not None:
            return pm
    # Nothing installed: explicitly signal "no pm". Callers that need a
    # default (capability detection) should mark capabilities unavailable
    # rather than fabricate a non-existent runtime.
    return None

def _pm_runner(pm):
    if pm in ("bun", "pnpm", "yarn"):
        return f"{pm} run"
    return "npm run"

def _is_pm_available(pm):
    if not pm:
        return False
    try:
        return _shutil.which(pm) is not None
    except Exception:
        return False

def detect_package_manager(path):
    """Public: detect package manager for a node project.

    Returns (pm_name, runner, available_bool, source).
    source is one of "packageManager", "lockfile", "fallback", or "none".
    pm_name is "" when no safe determination is possible.
    """
    pm_field = _package_manager_from_field(path)
    if pm_field:
        return (pm_field, _pm_runner(pm_field), _is_pm_available(pm_field), "packageManager")
    pm_lock = _package_manager_from_lockfiles(path)
    if pm_lock:
        return (pm_lock, _pm_runner(pm_lock), _is_pm_available(pm_lock), "lockfile")
    pm_fb = _fallback_package_manager()
    if pm_fb:
        return (pm_fb, _pm_runner(pm_fb), True, "fallback")
    return ("", "npm run", False, "none")

def _package_runner(path):
    pm, runner, _, _ = detect_package_manager(path)
    return runner

def _read_scripts_safe(path, pkg_name):
    try:
        with open(os.path.join(path, pkg_name), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    scripts = data.get("scripts") or data.get("tasks")
    if not isinstance(scripts, dict):
        return {}
    return {k: v for k, v in scripts.items() if isinstance(k, str)}

def _pick_script(scripts, priorities):
    # exact priority first
    for name in priorities:
        if name in scripts and name not in _DANGEROUS_SCRIPTS:
            return name
    # wildcard fallback for test/build: test:* or build:*
    # e.g., if priorities is test list, allow test:* second pass
    for name in priorities:
        # already handled exact
        pass
    # for test/build, try prefix matches if no exact found
    # only for test:/build:
    prefix_candidates = []
    for cand in scripts:
        if cand in _DANGEROUS_SCRIPTS:
            continue
        # test:xxx matches if any priority is test
        if cand.startswith("test:") and any(p.startswith("test") for p in priorities):
            prefix_candidates.append(cand)
        elif cand.startswith("build:") and any(p.startswith("build") for p in priorities):
            prefix_candidates.append(cand)
    if prefix_candidates:
        prefix_candidates.sort()
        return prefix_candidates[0]
    return None

def _detect_node(path):
    scripts = _read_scripts_safe(path, "package.json")
    if not scripts:
        # also try deno.json fallback if package.json absent but deno kind handled separately
        return ProjectCapabilities(path=path)
    pm, runner, available, _src = detect_package_manager(path)
    caps = {}
    dev_name = _pick_script(scripts, _DEV_PRIORITY)
    if dev_name:
        caps["dev"] = Capability(key="dev", label="Run Dev Server", command=f"{runner} {dev_name}", script=dev_name, available=available, pm=pm, long_running=True)
    test_name = _pick_script(scripts, _TEST_PRIORITY)
    if test_name:
        caps["test"] = Capability(key="test", label="Run Tests", command=f"{runner} {test_name}", script=test_name, available=available, pm=pm, long_running=False)
    build_name = _pick_script(scripts, _BUILD_PRIORITY)
    if build_name:
        caps["build"] = Capability(key="build", label="Build Project", command=f"{runner} {build_name}", script=build_name, available=available, pm=pm, long_running=False)
    # limit: at most dev/test/build
    return ProjectCapabilities(path=path, capabilities=caps)

def _detect_deno(path):
    for fname in ("deno.json", "deno.jsonc"):
        scripts = _read_scripts_safe(path, fname)
        if scripts:
            avail = _has_executable("deno")
            caps = {}
            dev_name = _pick_script(scripts, _DEV_PRIORITY)
            if dev_name:
                caps["dev"] = Capability(key="dev", label="Run Dev Server", command=f"deno task {dev_name}", script=dev_name, available=avail, long_running=True)
            test_name = _pick_script(scripts, _TEST_PRIORITY)
            if test_name:
                caps["test"] = Capability(key="test", label="Run Tests", command=f"deno task {test_name}", script=test_name, available=avail, long_running=False)
            build_name = _pick_script(scripts, _BUILD_PRIORITY)
            if build_name:
                caps["build"] = Capability(key="build", label="Build Project", command=f"deno task {build_name}", script=build_name, available=avail, long_running=False)
            return ProjectCapabilities(path=path, capabilities=caps)
    return ProjectCapabilities(path=path)

# ------------------------------------------------------------
# Python
_PY_WATCHED = ["pyproject.toml", "requirements.txt", "setup.py", "manage.py", "Pipfile"]

def _has_executable(name):
    try:
        return _shutil.which(name) is not None
    except Exception:
        return False

def _detect_python(path):
    caps = {}
    has_manage = os.path.exists(os.path.join(path, "manage.py"))
    if has_manage:
        avail = _has_executable("python") or _has_executable("python3")
        # Django project - strong evidence
        caps["dev"] = Capability(key="dev", label="Run Dev Server", command="python manage.py runserver", script="manage.py runserver", available=avail, long_running=True)
        caps["test"] = Capability(key="test", label="Run Tests", command="python manage.py test", script="manage.py test", available=avail, long_running=False)
        return ProjectCapabilities(path=path, capabilities=caps)

    # pyproject inspection (safe, no toml dep: read as text heuristics)
    pyproject_path = os.path.join(path, "pyproject.toml")
    has_pyproject = os.path.exists(pyproject_path)
    has_requirements = os.path.exists(os.path.join(path, "requirements.txt"))
    text = ""
    if has_pyproject:
        try:
            with open(pyproject_path, encoding="utf-8") as fh:
                text = fh.read(8192)
        except OSError:
            text = ""

    if has_pyproject and text:
        # very lightweight heuristic: look for pytest markers
        lower = text.lower()
        if "pytest" in lower or "[tool.pytest" in lower or "[tool.pytest.ini_options]" in lower:
            avail = _has_executable("pytest") or _has_executable("python") or _has_executable("python3")
            caps["test"] = Capability(key="test", label="Run Tests", command="pytest", script="pytest", available=avail, long_running=False)
        elif has_requirements or has_pyproject:
            # if we can't detect pytest but it's a python project, we still don't invent test
            pass
        if "[build-system]" in text or "build-system" in lower:
            avail = _has_executable("python") or _has_executable("python3")
            caps["build"] = Capability(key="build", label="Build Package", command="python -m build", script="build", available=avail, long_running=False)
        elif "setup.py" in lower or has_pyproject:
            # don't add build unless build-system present - keep conservative
            pass
    elif has_requirements:
        # requirements-only project: no strong test/build evidence, keep empty
        pass

    # If no test derived but pyproject exists with pytest-like content missing, keep empty
    return ProjectCapabilities(path=path, capabilities=caps)

# ------------------------------------------------------------
# Rust / Go / Make / CMake
_RUST_WATCHED = ["Cargo.toml"]
_GO_WATCHED = ["go.mod"]
_MAKE_WATCHED = ["Makefile", "makefile"]
_CMAKE_WATCHED = ["CMakeLists.txt", "meson.build"]

def _detect_rust(path):
    if not os.path.exists(os.path.join(path, "Cargo.toml")):
        return ProjectCapabilities(path=path)
    avail = _has_executable("cargo")
    return ProjectCapabilities(path=path, capabilities={
        "run": Capability(key="run", label="Run Project", command="cargo run", script="cargo run", available=avail, long_running=True),
        "test": Capability(key="test", label="Run Tests", command="cargo test", script="cargo test", available=avail, long_running=False),
        "build": Capability(key="build", label="Build Project", command="cargo build", script="cargo build", available=avail, long_running=False),
    })

def _detect_go(path):
    if not os.path.exists(os.path.join(path, "go.mod")):
        return ProjectCapabilities(path=path)
    avail = _has_executable("go")
    return ProjectCapabilities(path=path, capabilities={
        "run": Capability(key="run", label="Run Project", command="go run .", script="go run .", available=avail, long_running=True),
        "test": Capability(key="test", label="Run Tests", command="go test ./...", script="go test ./...", available=avail, long_running=False),
        "build": Capability(key="build", label="Build Project", command="go build ./...", script="go build ./...", available=avail, long_running=False),
    })

_MAKE_SAFE = {"run", "test", "build", "dev"}
_MAKE_RE = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9_.-]*)\s*:[^=\n]*$", re.MULTILINE)

def _detect_make(path):
    mk = None
    for cand in ("Makefile", "makefile"):
        p = os.path.join(path, cand)
        if os.path.exists(p):
            mk = p
            break
    if mk is None:
        return ProjectCapabilities(path=path)
    try:
        with open(mk, encoding="utf-8", errors="ignore") as fh:
            content = fh.read(20000)
    except OSError:
        return ProjectCapabilities(path=path)
    found = set(_MAKE_RE.findall(content))
    avail = _has_executable("make")
    caps = {}
    if "run" in found:
        caps["run"] = Capability(key="run", label="Run Project", command="make run", script="make run", available=avail, long_running=True)
    if "test" in found:
        caps["test"] = Capability(key="test", label="Run Tests", command="make test", script="make test", available=avail, long_running=False)
    if "build" in found:
        caps["build"] = Capability(key="build", label="Build Project", command="make build", script="make build", available=avail, long_running=False)
    if "dev" in found and "dev" not in caps:
        # dev as run dev server alternative
        if "run" not in caps:
            caps["dev"] = Capability(key="dev", label="Run Dev Server", command="make dev", script="make dev", available=avail, long_running=True)
    # if only generic 'make' without specific targets, expose a conservative 'make'?
    # Keep conservative: only if above none found, don't expose bare 'make' to avoid side effects
    return ProjectCapabilities(path=path, capabilities=caps)

def _detect_cmake(path):
    if not (os.path.exists(os.path.join(path, "CMakeLists.txt")) or os.path.exists(os.path.join(path, "meson.build"))):
        return ProjectCapabilities(path=path)
    avail = _has_executable("cmake")
    return ProjectCapabilities(path=path, capabilities={
        "build": Capability(key="build", label="Build Project", command="cmake --build build", script="cmake --build build", available=avail, long_running=False),
    })

# ------------------------------------------------------------
def capabilities_for(project):
    """Return ProjectCapabilities for a project dict or path.

    Safe, cached, never raises. Handles malformed files internally.
    """
    try:
        if isinstance(project, dict):
            path = project.get("path", "")
            kind = project.get("kind", "")
        else:
            path = str(project)
            kind = ""
        if not path or not os.path.isdir(path):
            return ProjectCapabilities(path=path, kind=kind)
        # dispatch based on kind + file existence; extensible
        # Node/TS/Deno
        if kind in ("node", "node-ts"):
            return _cached_or_compute(path, kind, _NODE_WATCHED, _detect_node)
        if kind == "deno":
            return _cached_or_compute(path, kind, _NODE_WATCHED, _detect_deno)
        if kind == "python":
            return _cached_or_compute(path, kind, _PY_WATCHED, _detect_python)
        if kind == "rust":
            return _cached_or_compute(path, kind, _RUST_WATCHED, _detect_rust)
        if kind == "go":
            return _cached_or_compute(path, kind, _GO_WATCHED, _detect_go)
        if kind == "cmake":
            return _cached_or_compute(path, kind, _CMAKE_WATCHED, _detect_cmake)
        # generic fallback checks: makefile may appear for any kind
        # try make detection for generic or any project with Makefile
        # Check for makefile existence regardless, but only if not already handled
        if os.path.exists(os.path.join(path, "Makefile")) or os.path.exists(os.path.join(path, "makefile")):
            # for generic kind, provide make; for other kinds, also provide make if specific file exists
            # to avoid shadowing language-specific, only add if no capabilities yet
            # we check make separately
            mc = _cached_or_compute(path, kind, _MAKE_WATCHED, _detect_make)
            if not mc.is_empty():
                return mc
        # also python detection for files even if kind != python (e.g., generic with manage.py)
        # conservative: only if manage.py present
        if os.path.exists(os.path.join(path, "manage.py")):
            return _cached_or_compute(path, kind, _PY_WATCHED, _detect_python)
        # unknown -> empty
        return ProjectCapabilities(path=path, kind=kind)
    except Exception:
        # never crash
        try:
            p = project.get("path", "") if isinstance(project, dict) else str(project)
        except Exception:
            p = ""
        return ProjectCapabilities(path=p)
