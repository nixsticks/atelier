"""Locate the `claude` CLI binary.

The `claude` CLI is typically installed at `~/.local/bin/claude` (the
self-installer's default). That directory is normally added to PATH by
`.zshrc` / `.bash_profile`, but those only run for *interactive* shells —
so if uvicorn is launched from VS Code's debugger, a launchd plist, a
fresh non-interactive subshell, or any other context that didn't source
the user's rc file, the subprocess can't find `claude`.

This module resolves the binary to an absolute path at server startup
by checking the current PATH plus the most common install locations.
Both vision.py and coaching.py use the result so neither has to
re-discover it on every request.
"""

import os
import shutil

# Common locations where `claude` might live, in priority order. We
# check these in addition to whatever's in the inherited PATH.
_EXTRA_PATHS = [
    "~/.local/bin",
    "~/.npm-global/bin",
    "~/.volta/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
]


def find_claude_cli() -> str | None:
    """Return absolute path to the `claude` binary, or None if not found."""
    # Try the inherited PATH first
    found = shutil.which("claude")
    if found:
        return found

    # Augment PATH with common install locations and try again
    expanded = [os.path.expanduser(p) for p in _EXTRA_PATHS]
    augmented = os.environ.get("PATH", "") + os.pathsep + os.pathsep.join(expanded)
    return shutil.which("claude", path=augmented)
