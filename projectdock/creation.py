"""Safe project creation.

Default creation targets an appropriate configured project root, never outside
roots, with sanitized folder names and no shell interpolation.
"""

import os
import re

# Reject control chars, null, and problematic filesystem characters.
# Allow typical human names with spaces, hyphens, underscores, dots.
# Reject path separators "/" "\\" and absolute-path leading "/" etc.

_MAX_NAME_LEN = 255
_INVALID_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED_NAMES = {".", "..", ""}

# Characters that would imply path traversal or absolute path.
_PATH_SPLITTERS = ("/", "\\")


def is_valid_project_name(name):
    """Return True if `name` is a safe single folder name.

    Rules:
    - non-empty after stripping
    - length 1..255
    - not "." or ".."
    - no "/" or "\\" or ":" (drive) or NUL or control
    - no leading "-" (avoids flag confusion) - but allow? we reject "-foo"
    - not absolute path (starts with "/" )
    - no ".." segment
    - no trailing spaces/dots? Windows-style but keep simple: reject names that after strip are empty
    """
    if not isinstance(name, str):
        return False
    stripped = name.strip()
    if not stripped:
        return False
    if len(stripped) > _MAX_NAME_LEN:
        return False
    if stripped in _RESERVED_NAMES:
        return False
    if stripped.startswith("-"):
        return False
    if any(sep in stripped for sep in _PATH_SPLITTERS):
        return False
    if ":" in stripped:
        # reject Windows drive patterns and colon misuse
        return False
    if _INVALID_PATTERN.search(stripped):
        return False
    # Reject names with NUL explicitly (already in control range but double-check)
    if "\x00" in stripped:
        return False
    # Avoid names that are only dots or spaces
    if all(ch in " ." for ch in stripped):
        return False
    return True


def sanitize_creation_name(query):
    """Return trimmed name if valid, else None."""
    if not isinstance(query, str):
        return None
    name = query.strip()
    # Collapse internal whitespace? Keep single-space collapsed? Keep as typed but trim ends.
    # Remove leading/trailing spaces done, keep internal as is.
    # Also collapse multiple spaces to single? Preserve user intent, but normalize.
    # For safety, keep exactly trimmed version.
    if not is_valid_project_name(name):
        return None
    return name


def choose_target_root(roots, active_project_path=None):
    """Pick target root for creation.

    Priority:
    1. Root containing active_project_path (if any)
    2. First expanded root that exists (default root)
    3. None if no roots.
    """
    if not roots:
        return None
    # Filter to existing dirs
    expanded = []
    for r in roots:
        # roots may already be expanded; handle both tilde and absolute
        exp = os.path.expanduser(r)
        # normalize
        exp = os.path.normpath(exp)
        if exp and os.path.isdir(exp):
            expanded.append(exp)
    if not expanded:
        return None
    if active_project_path:
        try:
            active = os.path.normpath(os.path.expanduser(active_project_path))
            # longest prefix match
            best = None
            best_len = -1
            for root in expanded:
                # ensure root is prefix with separator boundary
                if active == root or active.startswith(root + os.sep):
                    if len(root) > best_len:
                        best = root
                        best_len = len(root)
            if best:
                return best
        except Exception:
            pass
    return expanded[0]


def target_path_for_name(name, root):
    """Return absolute path for `name` inside `root`, or None if invalid."""
    if not is_valid_project_name(name):
        return None
    if not root or not isinstance(root, str):
        return None
    # Ensure root is absolute and exists
    root = os.path.expanduser(root)
    root = os.path.normpath(root)
    if not os.path.isdir(root):
        return None
    # Join safely - name has no separators, so join is just one level
    target = os.path.join(root, name.strip())
    # Validate that target is inside root (no traversal)
    try:
        # normpath target
        norm_target = os.path.normpath(target)
        # Must start with root + sep or equal root (but we append name so longer)
        # Allow root == dirname
        if not (norm_target == root or norm_target.startswith(root + os.sep)):
            return None
        # Also ensure basename equals sanitized name
        if os.path.basename(norm_target) != name.strip():
            return None
    except Exception:
        return None
    return norm_target


def create_project(query, roots, active_project_path=None):
    """Attempt to create a project folder for `query`.

    Returns (path, error) where path is created directory path on success,
    or None on failure with error string.
    Side effects: creates directory via os.makedirs(exist_ok=False) atomically.

    Never uses shell. Never creates outside roots. Handles spaces correctly.
    """
    name = sanitize_creation_name(query)
    if name is None:
        return (None, "invalid name")
    root = choose_target_root(roots, active_project_path)
    if root is None:
        return (None, "no project root available")
    target = target_path_for_name(name, root)
    if target is None:
        return (None, "invalid target")
    # Check duplicate: if exists, return existing without error (treat as success)
    if os.path.exists(target):
        if os.path.isdir(target):
            return (target, None)
        return (None, "path exists and is not a directory")
    try:
        os.makedirs(target, exist_ok=False)
    except FileExistsError:
        # race: now exists
        if os.path.isdir(target):
            return (target, None)
        return (None, "path exists")
    except PermissionError:
        return (None, "permission denied")
    except OSError as e:
        return (None, str(e) or "creation failed")
    # Verify it is inside root after creation (defense)
    try:
        real_target = os.path.realpath(target)
        real_root = os.path.realpath(root)
        if not (real_target == real_root or real_target.startswith(real_root + os.sep)):
            # unexpected - remove? Just report error
            return (None, "invalid target")
    except Exception:
        pass
    return (target, None)


def is_exact_match(query, projects):
    """Return True if query exactly matches an existing project name/path.

    Used to decide whether to offer create row. Exact match means no creation
    duplicate. Comparison is case-insensitive on name and path basename.
    """
    if not query or not projects:
        return False
    q = query.strip().lower()
    if not q:
        return False
    for p in projects:
        name = (p.get("name") or "").strip().lower()
        path = (p.get("path") or "")
        base = os.path.basename(os.path.normpath(path)).lower()
        if q == name or q == base or q == path.lower():
            return True
    return False


def should_offer_create(query, projects):
    """Whether create row should be offered for this query."""
    if not query or not query.strip():
        return False
    if not is_valid_project_name(query.strip()):
        return False
    if is_exact_match(query, projects):
        return False
    return True
