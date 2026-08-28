"""Lightweight ProjectDock-owned session model.

Only tracks sessions launched by ProjectDock. Runtime-only, ephemeral,
bounded, never persisted. Provides safe stop/restart with ownership
validation via /proc start-time.

No system-wide scanning, no tmux, no daemon polling.
"""
import os
import signal
import time
import uuid

# bounded
MAX_SESSIONS_TOTAL = 50
MAX_SESSIONS_PER_PROJECT = 5

def _proc_start_ticks(pid):
    """Return start ticks from /proc/<pid>/stat field 22, or None."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        # stat: pid (comm) state ... starttime is 22nd field (index 21)
        # comm may contain spaces/parens, find last ')'
        end = data.rfind(b")")
        if end == -1:
            return None
        rest = data[end+2:].split()
        # fields after comm: state (0), ppid (1), ... starttime is 19th after split (since we removed pid/comm)
        # Actually stat fields: 1 pid, 2 comm, 3 state, 4 ppid, ... 22 starttime
        # After splitting post-comm, starttime is at index 19
        if len(rest) < 20:
            return None
        return int(rest[19])
    except Exception:
        return None

def _pid_exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False

class ProjectSession:
    __slots__ = ("id","project_path","action_id","label","command","type","pid","pgid","started_at","start_ticks","long_running","state")
    def __init__(self, project_path, action_id, label, command, type_, pid=None, pgid=None, long_running=False):
        self.id = uuid.uuid4().hex[:8]
        self.project_path = project_path
        self.action_id = action_id
        self.label = label
        self.command = command
        self.type = type_  # editor, terminal, dev, test, build, run
        self.pid = pid
        self.pgid = pgid
        self.started_at = time.time()
        self.start_ticks = _proc_start_ticks(pid) if pid else None
        self.long_running = bool(long_running)
        self.state = "running" if pid else "unknown"

    def is_running(self):
        if self.pid is None:
            return False
        if not _pid_exists(self.pid):
            self.state = "exited"
            return False
        cur = _proc_start_ticks(self.pid)
        if cur is None:
            # fallback: pid exists but can't read
            return True
        if self.start_ticks is not None and cur != self.start_ticks:
            # PID reused
            self.state = "exited"
            return False
        return True

    def age_str(self):
        secs = int(time.time() - self.started_at)
        if secs < 60:
            return f"{secs}s ago"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m ago"
        hrs = mins // 60
        return f"{hrs}h ago"

class SessionStore:
    def __init__(self):
        self._sessions = {}  # project_path -> list[ProjectSession]
        self._all = []  # ordered by start time

    def create(self, project_path, action_id, label, command, type_, pid=None, pgid=None, long_running=False):
        sess = ProjectSession(project_path, action_id, label, command, type_, pid, pgid, long_running)
        lst = self._sessions.setdefault(project_path, [])
        lst.append(sess)
        self._all.append(sess)
        # dedup per capability: keep only latest for same action_id if long_running
        if long_running:
            # remove older duplicates for same action_id
            same = [s for s in lst if s.action_id == action_id and s.id != sess.id]
            for old in same:
                # if old is still running, we keep it but will prevent duplicate launch elsewhere
                # For now keep bounded: remove oldest if more than 1 per capability
                if len([x for x in lst if x.action_id == action_id]) > 1:
                    # keep newest only
                    lst.remove(old)
                    if old in self._all:
                        self._all.remove(old)
        # bound per project
        if len(lst) > MAX_SESSIONS_PER_PROJECT:
            oldest = lst.pop(0)
            if oldest in self._all:
                self._all.remove(oldest)
        # global bound
        if len(self._all) > MAX_SESSIONS_TOTAL:
            oldest = self._all.pop(0)
            proj_list = self._sessions.get(oldest.project_path, [])
            if oldest in proj_list:
                proj_list.remove(oldest)
        return sess

    def for_project(self, project_path):
        lst = self._sessions.get(project_path, [])
        # cleanup exited lazily? keep but mark
        return list(lst)

    def active_for(self, project_path):
        return [s for s in self.for_project(project_path) if s.is_running()]

    def dev_session(self, project_path):
        """Return running dev/long_running session for project, or None."""
        for s in self.for_project(project_path):
            if s.long_running and s.is_running():
                return s
        return None

    def find_by_action(self, project_path, action_id):
        for s in self.for_project(project_path):
            if s.action_id == action_id and s.is_running():
                return s
        return None

    def stop(self, session):
        """Safe stop: SIGTERM to pid/pgid if owned and still running."""
        if session is None:
            return False
        if not session.is_running():
            self._remove(session)
            return False
        pid = session.pid
        if pid is None:
            self._remove(session)
            return False
        # validate ownership again
        if not _pid_exists(pid):
            self._remove(session)
            return False
        cur = _proc_start_ticks(pid)
        if cur is not None and session.start_ticks is not None and cur != session.start_ticks:
            self._remove(session)
            return False
        try:
            # prefer pgid if we created new session
            if session.pgid and session.pgid != pid:
                try:
                    os.killpg(session.pgid, signal.SIGTERM)
                except Exception:
                    os.kill(pid, signal.SIGTERM)
            else:
                # try pgid == pid
                try:
                    os.killpg(pid, signal.SIGTERM)
                except Exception:
                    os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            self._remove(session)
            return False
        except PermissionError:
            return False
        except Exception:
            return False
        # give brief time for exit, mark exited lazily
        # we don't SIGKILL automatically; caller may poll
        session.state = "exited"
        # keep for history briefly then remove? For V1, remove after stop
        self._remove(session)
        return True

    def _remove(self, session):
        try:
            lst = self._sessions.get(session.project_path, [])
            if session in lst:
                lst.remove(session)
            if session in self._all:
                self._all.remove(session)
            if not lst:
                self._sessions.pop(session.project_path, None)
        except Exception:
            pass

    def cleanup(self):
        """Remove exited sessions (non-running)."""
        for sess in list(self._all):
            if not sess.is_running():
                # keep terminated briefly? For now remove unknown/exited that are old
                # Keep only running
                if sess.state == "exited":
                    self._remove(sess)

    def clear_all(self):
        self._sessions.clear()
        self._all.clear()
