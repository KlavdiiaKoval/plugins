"""Read the plugin's off switches: an old CLAUDE_<NAME>_OFF env var, or the matching /config option."""

from __future__ import annotations

import os

ON = {"true", "1", "yes"}


def is_off(*names: str) -> bool:
    """Report whether any named switch (GATE, GUARD, REVIEW, TESTS) is on.

    A switch is on when CLAUDE_<NAME>_OFF is set to anything, or when the plugin option
    <name>_off is true, which Claude Code exports as CLAUDE_PLUGIN_OPTION_<NAME>_OFF.
    """
    return any(
        os.environ.get(f"CLAUDE_{name}_OFF") or os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name}_OFF", "").lower() in ON
        for name in names
    )
