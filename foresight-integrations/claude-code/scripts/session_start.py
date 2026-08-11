#!/usr/bin/env python3
"""SessionStart hook: configuration check + session logging.

Fires once when a Claude Code session begins. Uses additionalContext
(supported on SessionStart) to inject an initial system note if
Foresight is configured.

Verify the Foresight connection configuration before the first prompt.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.client import ForesightClient
from lib.config import debug_log, load_config
from lib.connection import get_api_url
from lib.content import format_solution_recall_protocol


def main():
    config = load_config()

    if not config.get("autoRecall") and not config.get("autoRetain"):
        debug_log(config, "Both autoRecall and autoRetain disabled, skipping session start")
        return

    additional_context = ""
    if config.get("autoRecall") and config.get("enableKnowledgeTools"):
        additional_context = format_solution_recall_protocol()

    # Consume stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    debug_log(config, f"SessionStart hook, source: {hook_input.get('source', 'unknown')}")

    # Validate the required API URL early so misconfiguration is visible before
    # the first recall or retain hook fires.
    def _dbg(*a):
        debug_log(config, *a)

    try:
        api_url = get_api_url(config, debug_fn=_dbg, allow_daemon_start=False)
        ForesightClient(
            api_url,
            config.get("foresightApiKey"),
            request_timeout_override=config.get("requestTimeoutSeconds"),
        )
        debug_log(config, f"Foresight API configured at {api_url}")
    except (RuntimeError, ValueError) as e:
        print(f"[Foresight] {e}", file=sys.stderr)

    if additional_context:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": additional_context,
                }
            },
            sys.stdout,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[Foresight] SessionStart error: {e}", file=sys.stderr)
        sys.exit(0)
