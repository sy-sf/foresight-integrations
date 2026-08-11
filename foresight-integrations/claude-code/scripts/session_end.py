#!/usr/bin/env python3
"""SessionEnd hook: synchronize the final trajectory snapshot.

Fires once when a Claude Code session terminates. The snapshot remains subject
to the server-side idle window; SessionEnd does not force knowledge extraction.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.config import debug_log, load_config


def main():
    config = load_config()

    # Consume stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    debug_log(config, f"SessionEnd hook, reason: {hook_input.get('reason', 'unknown')}")

    # Send one final idempotent snapshot without bypassing the server-side idle
    # window. This also clears session-scoped opened-solution state after a
    # successful upload.
    if config.get("autoRetain") and hook_input.get("transcript_path"):
        try:
            from retain import run_retain

            run_retain(hook_input, final=True)
        except Exception as e:
            print(f"[Foresight] SessionEnd final retain error: {e}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[Foresight] SessionEnd error: {e}", file=sys.stderr)
        sys.exit(0)
