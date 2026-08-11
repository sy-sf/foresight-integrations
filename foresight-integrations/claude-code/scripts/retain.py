#!/usr/bin/env python3
"""Synchronize the latest Claude session snapshot on every Stop event.

Flow:
  1. Read hook input from stdin (session_id, transcript_path, cwd)
  2. Read conversation transcript from transcript_path
  3. Resolve the configured Foresight API
  4. Resolve personal knowledge space and ensure mission
  5. Upsert the complete structured session snapshot

The server owns idle-time debouncing and knowledge extraction. Every changed
snapshot is therefore sent with ``process_now=false`` so a new message resets
the server-side idle window.

Exit codes:
  0 — always (graceful degradation on any error)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.bank import derive_bank_id, ensure_bank_mission
from lib.client import ForesightClient
from lib.config import debug_log, load_config
from lib.connection import get_api_url
from lib.solution_candidates import (
    OPENED_SOLUTIONS_STATE,
    clear_opened_solution_session,
    opened_solutions_for_session,
)
from lib.state import mark_session_retained, read_state, track_retention, write_state


def read_transcript(transcript_path: str) -> list:
    """Read a JSONL transcript file and return list of message dicts.

    Claude Code transcript format nests messages:
      {type: "user", message: {role: "user", content: "..."}, uuid: "...", ...}
    Also supports flat format for testing:
      {role: "user", content: "..."}

    The complete structured message payload is preserved because the Foresight
    server selects the ``claude_code_transcript`` parser for this source.
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


def run_retain(hook_input: dict, final: bool = False) -> None:
    config = load_config()

    if not config.get("autoRetain"):
        debug_log(config, "Auto-retain disabled, exiting")
        return

    debug_log(config, f"Retain hook_input keys: {list(hook_input.keys())} final={final}")

    session_id = hook_input.get("session_id", "unknown")
    transcript_path = hook_input.get("transcript_path", "")

    # Read full transcript
    all_messages = read_transcript(transcript_path)
    if not all_messages:
        debug_log(config, "No messages in transcript, skipping retain")
        return

    debug_log(config, f"Full session snapshot: {len(all_messages)} messages")

    # Resolve API URL
    def _dbg(*a):
        debug_log(config, *a)

    try:
        api_url = get_api_url(config, debug_fn=_dbg, allow_daemon_start=True)
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

    # Resolve the canonical personal knowledge space and ensure mission.
    bank_id = derive_bank_id(hook_input, config)
    ensure_bank_mission(client, bank_id, config, debug_fn=_dbg)

    # One stable document per session/compaction segment.
    chunk_index, compacted = track_retention(session_id, len(all_messages))
    if compacted:
        debug_log(
            config,
            f"Compaction detected for session {session_id}: transcript shrank, "
            f"advancing to chunk {chunk_index} to preserve prior document",
        )
    # chunk 0 → plain session_id (backwards compatible with existing docs)
    document_id = session_id if chunk_index == 0 else f"{session_id}-c{chunk_index}"
    source_segment_index = chunk_index

    # Session identity is canonical source metadata, not user configuration.
    metadata = {
        "message_count": str(len(all_messages)),
        "session_id": session_id,
    }
    opened_state = read_state(OPENED_SOLUTIONS_STATE, {})
    opened_solutions = opened_solutions_for_session(opened_state, session_id)
    if opened_solutions:
        # Server contract: keep this metadata key until the deployed API changes.
        metadata["hindsight_opened_solutions_json"] = json.dumps(opened_solutions, ensure_ascii=False)

    debug_log(
        config,
        f"Retaining to personal knowledge space, doc '{document_id}', "
        f"{len(all_messages)} messages",
    )

    # The server selects its transcript parser through source_format.
    source_content = json.dumps(all_messages, ensure_ascii=False, separators=(",", ":"))

    try:
        response = client.upsert_document(
            bank_id=bank_id,
            document_id=document_id,
            content=source_content,
            source_session_id=session_id,
            source_segment_index=source_segment_index,
            context="claude-code",
            metadata=metadata,
            tags=None,
            process_now=False,
            timeout=15,
        )
        debug_log(config, f"Document upsert response: {json.dumps(response)[:200]}")
        # Each changed source revision resets the server-side idle window.
        mark_session_retained(session_id)
        if final and opened_solutions:
            write_state(OPENED_SOLUTIONS_STATE, clear_opened_solution_session(opened_state, session_id))
    except Exception as e:
        print(f"[Foresight] Document upsert failed: {e}", file=sys.stderr)


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        print("[Foresight] Failed to read hook input", file=sys.stderr)
        return
    run_retain(hook_input)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[Foresight] Unexpected error in retain: {e}", file=sys.stderr)
        try:
            from lib.config import load_config

            sys.exit(2 if load_config().get("debug") else 0)
        except Exception:
            sys.exit(0)
