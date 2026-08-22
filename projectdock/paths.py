"""XDG-compatible filesystem locations for ProjectDock."""

import os


def _xdg(env_var, fallback):
    value = os.environ.get(env_var)
    if value:
        return value
    home = os.path.expanduser("~")
    return os.path.join(home, fallback)


def config_dir():
    return _xdg("XDG_CONFIG_HOME", ".config")


def state_dir():
    return _xdg("XDG_STATE_HOME", ".local/state")


def config_file():
    return os.path.join(config_dir(), "projectdock", "config.toml")


def state_file():
    return os.path.join(state_dir(), "projectdock", "state.json")


def cache_file():
    return os.path.join(state_dir(), "projectdock", "cache.json")


def ensure_dirs():
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    os.makedirs(os.path.dirname(state_file()), exist_ok=True)
