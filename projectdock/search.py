"""Instant project filtering with fuzzy ranking.

Matching runs against the project name, folder name and full path. Space-
separated query tokens must all match somewhere. Scoring favors:

    prefix > word-boundary > substring > scattered subsequence

with bonuses for contiguous runs and penalties for gaps, then applies small
boosts so pinned and recently-opened projects float above equally-matched
ones.
"""

import os
import re

_WORD_BOUNDARY = re.compile(r"[^a-z0-9]", re.IGNORECASE)


def score(query, project):
    """Return a match score >= 0, or None when the project does not match."""
    query = query.strip().lower()
    if not query:
        return 0.0

    name = project.get("name", "")
    path = project.get("path", "")
    folder = os.path.basename(os.path.normpath(path))
    label = project.get("label", "")
    kind = project.get("kind", "")

    tokens = [t for t in query.split() if t]
    if not tokens:
        return 0.0

    total = 0.0
    for token in tokens:
        token_score = max(
            _score_field(token, name),
            _score_field(token, folder) * 0.95,
            _score_field(token, path) * 0.55,
            _score_field(token, label) * 0.50,
            _score_field(token, kind) * 0.50,
        )
        if token_score <= 0:
            return None
        total += token_score

    avg = total / len(tokens)

    pinned = bool(project.get("pinned"))
    active = bool(project.get("active"))
    recent_rank = project.get("recent_rank")
    usage = int(project.get("usage_count", 0) or 0)
    if pinned:
        avg *= 1.30
    elif active:
        rank = project.get("active_rank", 0)
        avg *= 1.20 + max(0.0, 0.05 - (rank or 0) * 0.01)
    elif recent_rank is not None:
        avg *= 1.10 + max(0.0, 0.10 - recent_rank * 0.01)
    # tiny usage tie-breaker (never outranks exact/prefix quality)
    if usage > 3:
        avg *= 1.02 + min(0.03, (usage - 3) * 0.005)

    return round(avg, 4)


def _score_field(token, text):
    text = text.lower()
    if not text:
        return 0.0
    if token == text:
        return 100.0
    if text.startswith(token):
        return 80.0 + min(len(token), 12)

    idx = text.find(token)
    if idx >= 0:
        base = 55.0 + min(len(token), 10)
        if idx > 0 and _WORD_BOUNDARY.match(text[idx - 1]):
            base += 12.0
        if token == os.path.basename(text) or text.startswith(f"{token}."):
            base += 10.0
        return base

    sub = _subsequence_score(token, text)
    return sub


def _subsequence_score(token, text):
    ti = 0
    score = 0.0
    run = 0
    prev_idx = -2
    for i, ch in enumerate(text):
        if ti < len(token) and ch == token[ti]:
            ti += 1
            if i == 0 or _WORD_BOUNDARY.match(text[i - 1]):
                score += 8.0
            elif i == prev_idx + 1:
                run += 1
                score += 3.0 + min(run, 4)
            else:
                run = 0
                score += 1.0
            prev_idx = i
        else:
            score -= 0.35
    if ti < len(token):
        return 0.0
    return max(1.0, score)


def filter_and_rank(query, projects):
    """Filter projects by query and return them ranked best-first."""
    ranked = []
    for project in projects:
        s = score(query, project)
        if s is not None:
            ranked.append((s, project))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["name"].lower()))
    return [p for _, p in ranked]


def sorted_by_activity(projects):
    """Ordering used when the search query is empty:
    pinned → active → recent → rest alphabetically.
    Active is ephemeral (window association) + recent workspace launches.
    """
    pinned = []
    active = []
    recent = []
    rest = []
    for project in projects:
        if project.get("pinned"):
            pinned.append(project)
        elif project.get("active"):
            active.append(project)
        elif project.get("recent_rank") is not None:
            recent.append(project)
        else:
            rest.append(project)
    pinned.sort(key=lambda p: p.get("pin_order", 0))
    active.sort(key=lambda p: p.get("active_rank", 0) if p.get("active_rank") is not None else 1 << 30)
    recent.sort(key=lambda p: p["recent_rank"])
    rest.sort(key=lambda p: p["name"].lower())
    return pinned + active + recent + rest
