"""Project visual identity: real artwork when present, deterministic fallback.

ProjectDock is a dense launcher, so each project gets a compact square
"cover" in the slot the old per-language glyph used to occupy. Discovery is
bounded and fast: we only look for a handful of well-known branding filenames
at the project root and one level of obvious asset directories (public/,
assets/, ...). We never walk the whole repository hunting for arbitrary
images.

When no artwork exists we render a deterministic fallback: the project's
initials over a subtle, name-derived tint. The same project always produces
the same identity, so scanning the list feels intentional rather than
decorative.
"""

import hashlib
import os
import re

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico")
_BRAND_NAMES = (
    "logo", "favicon", "icon", "appicon", "cover", "banner", "thumbnail",
)
# Root plus a short, bounded list of likely asset directories. Generic code
# directories ("src", "app", ...) are deliberately excluded: their logo.png
# files are usually component artwork, not project branding.
_ASSET_DIRS = ("", "public", "assets", "static", "images")

_SCAN_CACHE = {}


def discover_cover(project):
    """Return a local image path for the project, or None.

    Bounded: only checks a fixed set of branding filenames under a small set
    of likely directories. Never recurses through the project tree.
    """
    path = project.get("path", "") if isinstance(project, dict) else str(project)
    if not path:
        return None
    cached = _SCAN_CACHE.get(path)
    if cached is not None:
        return cached[0] if isinstance(cached, tuple) else cached
    result = _scan(path)
    _SCAN_CACHE[path] = (result,)
    return result


def _scan(root):
    if not os.path.isdir(root):
        return None
    for sub in _ASSET_DIRS:
        base = os.path.join(root, sub) if sub else root
        for name in _BRAND_NAMES:
            for ext in _IMAGE_EXTS:
                candidate = os.path.join(base, name + ext)
                if os.path.isfile(candidate) and _valid_image(candidate):
                    return candidate
    return None


def _valid_image(path):
    """Accept common raster/image signatures without an image dependency."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size < 16:
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    if head.startswith(b"\xff\xd8\xff"):
        return True
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    if head[:4] == b"\x00\x00\x01\x00":
        return True
    if head[:5] in (b"<?xml", b"<svg") or head.startswith(b"<?XML"):
        return True
    return False


def identity_initials(name):
    """Deterministic 1-2 character initials from a project name."""
    words = re.split(r"[^A-Za-z0-9]+", name or "")
    words = [w for w in words if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def identity_colors(name, palette):
    """Subtle, name-derived cover background/foreground as CSS color strings.

    The tint is muted (low saturation, translucent) so it reads as an Omarchy
    foreground-derived accent rather than a bright SaaS avatar. Lightness
    follows the theme mode so the mark stays visible on dark and light
    backgrounds alike, and the foreground is taken from the active theme so
    the initials stay legible. Same name -> same colors, always.
    """
    digest = hashlib.md5((name or "").lower().encode("utf-8")).hexdigest()
    hue = int(digest, 16) % 360
    fg = palette.get("foreground", "#e6e9f2")
    if palette.get("mode") == "light":
        bg = "hsla(%d, 28%%, 42%%, 0.20)" % hue
    else:
        bg = "hsla(%d, 26%%, 60%%, 0.17)" % hue
    return bg, fg
