"""Command-line entry point.

    projectdock            show / toggle the launcher
    projectdock toggle     toggle the launcher
    projectdock show       show the launcher
    projectdock hide       hide the launcher
    projectdock quit       stop the daemon
    projectdock rescan     rescan project roots
    projectdock --version  print the version

gtk4-layer-shell must be loaded before libwayland-client, or its layer
surfaces fail to initialize (see https://github.com/wmww/gtk4-layer-shell
linking notes). PyGObject cannot control link order, so we dlopen the
library with RTLD_GLOBAL here, before anything imports GObject-
introspection. Doing it in-process (rather than LD_PRELOAD) keeps the
environment clean for every child process the daemon later spawns.
"""

import os
import sys

_LAYER_LIB_CANDIDATES = (
    "/usr/lib64/libgtk4-layer-shell.so",
    "/usr/lib/libgtk4-layer-shell.so",
    "/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so",
)


def _preload_layer_shell():
    """dlopen libgtk4-layer-shell with RTLD_GLOBAL before gi is imported."""
    if not sys.platform.startswith("linux"):
        return
    import ctypes
    for cand in _LAYER_LIB_CANDIDATES:
        if os.path.exists(cand):
            try:
                ctypes.CDLL(cand, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass
            return


def main(argv=None):
    _preload_layer_shell()
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv or "-v" in argv:
        from . import __version__
        print(f"projectdock {__version__}")
        return 0
    from .app import main as app_main
    return app_main()


if __name__ == "__main__":
    sys.exit(main())
