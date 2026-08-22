import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from projectdock.app import DockApp
    from projectdock import commands
    _HAS_GTK = True
except ImportError:
    _HAS_GTK = False


@unittest.skipUnless(_HAS_GTK, "GTK4/PyGObject not available")
class RunActionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": os.path.join(self.tmp.name, "cfg"),
            "XDG_STATE_HOME": os.path.join(self.tmp.name, "state"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = DockApp()
        self.project = {"path": "/tmp/fake-project", "name": "fake", "kind": "node"}

    def test_known_command_executes(self):
        with mock.patch.object(commands, "discover",
                               return_value=[("dev", "npm run dev")]), \
             mock.patch("projectdock.app.actions.open_in_terminal") as opened:
            self.app.run_action("cmd:npm run dev", self.project)
            opened.assert_called_once_with(
                "/tmp/fake-project", self.app.cfg, command="npm run dev")

    def test_unknown_command_is_rejected(self):
        with mock.patch.object(commands, "discover",
                               return_value=[("dev", "npm run dev")]), \
             mock.patch("projectdock.app.actions.open_in_terminal") as opened:
            self.app.run_action("cmd:rm -rf /", self.project)
            opened.assert_not_called()

    def test_unknown_command_does_not_touch_recents(self):
        with mock.patch.object(commands, "discover",
                               return_value=[("dev", "npm run dev")]), \
             mock.patch("projectdock.app.actions.open_in_terminal"):
            self.app.run_action("cmd:$(reboot)", self.project)
            self.assertEqual(self.app.state.recents, [])

    def test_command_suffix_collision_is_rejected(self):
        # A command that merely has a known command as a prefix must not run.
        with mock.patch.object(commands, "discover",
                               return_value=[("dev", "npm run dev")]), \
             mock.patch("projectdock.app.actions.open_in_terminal") as opened:
            self.app.run_action("cmd:npm run dev; rm -rf /", self.project)
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
