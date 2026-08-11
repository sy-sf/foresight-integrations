#!/usr/bin/env python3
"""Auto-recall hook for UserPromptSubmit.

Port of: before_prompt_build handler in Openclaw index.js
Adapted for Claude Code hooks (ephemeral process, JSON stdin/stdout).

Flow:
  1. Read hook input from stdin (prompt, session_id, transcript_path, cwd)
  2. Resolve the configured Foresight API URL
  3. Resolve the canonical personal knowledge space
  4. Ensure personal knowledge mission is set (first use only)
  5. Compose multi-turn query if recallContextTurns > 1
  6. Truncate to recallMaxQueryChars
  7. Call the Foresight recall API
  8. Format memories and output hookSpecificOutput.additionalContext
  9. Save last recall to state (for PostCompact re-injection)

Exit codes:
  0 — always (graceful degradation on any error)
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.bank import derive_bank_id, ensure_bank_mission
from lib.client import ForesightClient
from lib.config import debug_log, load_config
from lib.connection import get_api_url
from lib.content import (
    compose_recall_query,
    format_current_time,
    format_memories,
    format_recall_context,
    format_solution_candidates,
    truncate_recall_query,
)
from lib.solution_candidates import (
    SOLUTION_CANDIDATES_STATE,
    build_solution_candidate_routes,
    serialize_solution_candidate_state,
)
from lib.state import write_state

LAST_RECALL_STATE = "last_recall.json"


def read_transcript_messages(transcript_path: str) -> list:
    """Read messages from a JSONL transcript file for multi-turn context.

    Claude Code transcript format nests messages:
      {type: "user", message: {role: "user", content: "..."}, uuid: "...", ...}
    Also supports flat format for testing:
      {role: "user", content: "..."}
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return []
    messages = []
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    # Claude Code nested format: {type: "user", message: {role, content}}
                    if entry.get("type") in ("user", "assistant"):
                        msg = entry.get("message", {})
                        if isinstance(msg, dict) and msg.get("role"):
                            messages.append(msg)
                    # Flat format (testing / future compatibility)
                    elif "role" in entry and "content" in entry:
                        messages.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return messages


def main():
    config = load_config()

    if not config.get("autoRecall"):
        debug_log(config, "Auto-recall disabled, exiting")
        return

    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        print("[Foresight] Failed to read hook input", file=sys.stderr)
        return

    debug_log(config, f"Hook input keys: {list(hook_input.keys())}")

    # Extract user query — hooks-reference.md documents "prompt", but some
    # Claude Code sources reference "user_prompt". Accept both defensively.
    prompt = (hook_input.get("prompt") or hook_input.get("user_prompt") or "").strip()
    if not prompt or len(prompt) < 5:
        debug_log(config, "Prompt too short for recall, skipping")
        return

    # Resolve the required external Foresight API URL.
    def _dbg(*a):
        debug_log(config, *a)

    try:
        api_url = get_api_url(config, debug_fn=_dbg, allow_daemon_start=False)
    except RuntimeError as e:
        print(f"[Foresight] {e}", file=sys.stderr)
        return

    api_key = config.get("foresightApiKey")
    try:
        client = ForesightClient(
            api_url,
            api_key,
            request_timeout_override=config.get("requestTimeoutSeconds"),
        )
    except ValueError as e:
        print(f"[Foresight] Invalid connection configuration: {e}", file=sys.stderr)
        return

    # Resolve the canonical personal knowledge space.
    bank_id = derive_bank_id(hook_input, config)
    session_id = str(hook_input.get("session_id") or "")
    saved_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_state(
        SOLUTION_CANDIDATES_STATE,
        serialize_solution_candidate_state([], bank_id=bank_id, saved_at=saved_at, session_id=session_id),
    )

    # Set personal knowledge mission on first use.
    ensure_bank_mission(client, bank_id, config, debug_fn=_dbg)

    # Multi-turn query composition
    recall_context_turns = config.get("recallContextTurns", 1)
    recall_max_query_chars = config.get("recallMaxQueryChars", 800)
    recall_roles = config.get("recallRoles", ["user", "assistant"])

    if recall_context_turns > 1:
        transcript_path = hook_input.get("transcript_path", "")
        messages = read_transcript_messages(transcript_path)
        debug_log(config, f"Multi-turn context: {recall_context_turns} turns, {len(messages)} messages from transcript")
        query = compose_recall_query(prompt, messages, recall_context_turns, recall_roles)
    else:
        query = prompt

    query = truncate_recall_query(query, prompt, recall_max_query_chars)

    # Final defensive cap (mirrors Openclaw)
    if len(query) > recall_max_query_chars:
        query = query[:recall_max_query_chars]

    debug_log(config, f"Recalling from personal knowledge space, query length: {len(query)}")
    recall_max_tokens = config.get("recallMaxTokens", 1024)
    recall_budget = config.get("recallBudget", "mid")
    recall_types = config.get("recallTypes")
    recall_solution_detail = config.get("recallSolutionDetail", "candidate")

    # Call the Foresight recall API.
    try:
        response = client.recall(
            bank_id=bank_id,
            query=query,
            current_user_input=prompt,
            max_tokens=recall_max_tokens,
            budget=recall_budget,
            types=recall_types,
            solution_detail=recall_solution_detail,
            timeout=10,
        )
    except Exception as e:
        print(f"[Foresight] Recall failed: {e}", file=sys.stderr)
        return

    results = response.get("results") or []
    solutions = response.get("solutions") or []

    solution_routes = build_solution_candidate_routes(solutions)
    memories_formatted = format_memories(results)
    solution_candidates_formatted = format_solution_candidates(solutions)

    if not memories_formatted and not solution_candidates_formatted:
        debug_log(config, "No injectable memories or solution candidates found")
        return

    debug_log(config, f"Injecting {len(results)} memories and {len(solutions)} solution candidates")

    # Format context message with direct facts plus progressive solution candidates.
    preamble = config.get("recallPromptPreamble", "")
    current_time = format_current_time()

    context_message = format_recall_context(
        preamble=preamble,
        current_time=current_time,
        memories_formatted=memories_formatted,
        solution_candidates_formatted=solution_candidates_formatted,
    )

    write_state(
        SOLUTION_CANDIDATES_STATE,
        serialize_solution_candidate_state(solution_routes, bank_id=bank_id, saved_at=saved_at, session_id=session_id),
    )

    # Save last recall to state for diagnostics
    write_state(
        LAST_RECALL_STATE,
        {
            "context": context_message,
            "saved_at": saved_at,
            "bank_id": bank_id,
            "session_id": session_id,
            "result_count": len(results),
            "solution_count": len(solutions),
            "solution_route_count": len(solution_routes),
        },
    )

    # Output JSON for Claude Code hook system
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context_message,
        }
    }
    json.dump(output, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[Foresight] Unexpected error in recall: {e}", file=sys.stderr)
        # Exit 2 in debug mode surfaces errors to Claude; 0 degrades silently
        try:
            from lib.config import load_config

            sys.exit(2 if load_config().get("debug") else 0)
        except Exception:
            sys.exit(0)
