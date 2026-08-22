"""Project markers and type detection.

A directory is considered a project when it contains one of the marker files
listed below (or a .git directory / file). The first matching type in the
MARKERS list wins; `.git` alone maps to the generic "git" type.

Icons are Nerd Font glyphs verified against the fonts installed on Omarchy
(they degrade to nothing if the font is missing, so a glyph is always paired
with a visible label).
"""

import json
import os


class Type:
    __slots__ = ("id", "label", "icon", "color", "markers")

    def __init__(self, id_, label, icon, color, markers):
        self.id = id_
        self.label = label
        self.icon = icon
        self.color = color
        self.markers = markers


GIT_TYPE = Type("git", "Git", "\U0000f02a2", "#8aadf4", ())

MARKERS = [
    Type("rust", "Rust", "\U0000f1617", "#e5b48d", ("Cargo.toml",)),
    Type("node", "Node.js", "\U0000f0338", "#a6da95", ("package.json",)),
    Type("python", "Python", "\U0000f030b", "#8aadf4", ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile")),
    Type("go", "Go", "\U0000f07d3", "#8bd5ca", ("go.mod",)),
    Type("zig", "Zig", "\U0000f1237", "#eed49f", ("build.zig", "build.zig.zon")),
    Type("elixir", "Elixir", "\U0000f062d", "#c6a0f6", ("mix.exs",)),
    Type("ruby", "Ruby", "\U0000f0d2d", "#ed8796", ("Gemfile",)),
    Type("php", "PHP", "\U0000f0317", "#b7bdf8", ("composer.json",)),
    Type("java", "Java", "\U0000f0ac3", "#f5a97f", ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle")),
    Type("dotnet", ".NET", "\U0000f0aa0", "#7dc4e4", ("global.json",)),
    Type("haskell", "Haskell", "\U0000f0732", "#eed49f", ("stack.yaml", "package.yaml")),
    Type("dart", "Flutter/Dart", "\U0000f0798", "#8bd5ca", ("pubspec.yaml",)),
    Type("nix", "Nix", "\U0000f0905", "#b7bdf8", ("flake.nix", "shell.nix")),
    Type("deno", "Deno", "\U0000f07c0", "#a6da95", ("deno.json", "deno.jsonc")),
    Type("cmake", "C/C++", "\U0000f0308", "#91d7e3", ("CMakeLists.txt", "meson.build")),
    Type("crystal", "Crystal", "\U0000f07c0", "#f5bde6", ("shard.yml",)),
    Type("julia", "Julia", "\U0000f07e0", "#a6da95", ("Project.toml", "Manifest.toml")),
    Type("terraform", "Terraform", "\U0000f07c0", "#b7bdf8", ("main.tf",)),
    GIT_TYPE,
]

GENERIC_TYPE = Type("generic", "Project", "\U0000f07c0", "#a5adcb", ())

# Filename suffixes that identify a project type (e.g. `MyApp.csproj`).
_SUFFIX_PATTERNS = (
    (".csproj", "dotnet"),
    (".fsproj", "dotnet"),
    (".sln", "dotnet"),
    (".cabal", "haskell"),
)

_TYPE_BY_ID = {t.id: t for t in MARKERS}

# Directories that are never descended into and never considered projects.
IGNORE_DIRS = {
    "node_modules", "target", "dist", "build", "out", "vendor", "deps",
    ".venv", "venv", "env", "__pycache__", ".cache", ".cargo", ".next",
    ".nuxt", ".output", ".parcel-cache", ".turbo", "coverage",
    ".pytest_cache", ".tox", ".mypy_cache", ".ruff_cache", ".idea",
    ".vscode", ".vs", ".svn", ".hg", "bower_components", ".gradle",
    ".dart_tool", ".stack-work", "_build", ".terraform", ".svelte-kit",
    ".angular", ".nx", ".yarn", ".pnpm-store", ".eslintcache",
    "dist-newstyle", "zig-cache", ".zig-cache", "__MACOSX", "miniconda3",
    ".godot", ".elixir_ls", ".history", ".nix-profile", "result",
    ".ruff", ".pre-commit", ".pixi", ".conda", ".uv", "site-packages",
    ".DS_Store", ".Trash-1000",
}

_IGNORE_SUFFIXES = (".egg-info",)


def _type_by_id(type_id):
    return _TYPE_BY_ID.get(type_id, GENERIC_TYPE)


def name_is_marker(name):
    """True when a single filename identifies its directory as a project."""
    if name == ".git":
        return True
    for kind in MARKERS:
        for marker in kind.markers:
            if name == marker:
                return True
    return any(name.endswith(suffix) for suffix, _ in _SUFFIX_PATTERNS)


def _match_type(names):
    """Return the Type for a directory given its entry names, or None."""
    nameset = set(names)
    for kind in MARKERS:
        for marker in kind.markers:
            if marker in nameset:
                return kind
    for name in nameset:
        for suffix, type_id in _SUFFIX_PATTERNS:
            if name.endswith(suffix):
                return _type_by_id(type_id)
    if ".git" in nameset:
        return GIT_TYPE
    return None


def detect(path, is_dir_scanned=False):
    """Return the project Type for a directory (GENERIC_TYPE fallback)."""
    if not os.path.isdir(path):
        return GENERIC_TYPE
    try:
        names = set(os.listdir(path))
    except OSError:
        return GENERIC_TYPE

    kind = _match_type(names)
    if kind is None:
        return GENERIC_TYPE
    if kind.id == "node":
        return _node_type(path)
    return kind


def _node_type(path):
    """package.json project: TypeScript if the project uses it."""
    node = _type_by_id("node")
    pkg_path = os.path.join(path, "package.json")
    try:
        with open(pkg_path, encoding="utf-8") as fh:
            pkg = json.load(fh)
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "typescript" in deps or os.path.exists(os.path.join(path, "tsconfig.json")):
            return Type("node-ts", "TypeScript", "\U0000f0338", "#7dc4e4", ("package.json",))
    except (OSError, ValueError, AttributeError):
        pass
    return node


def is_ignored_dir(name):
    name = name.lower()
    if name in IGNORE_DIRS:
        return True
    if name.startswith("."):
        return True
    return name.endswith(_IGNORE_SUFFIXES)
