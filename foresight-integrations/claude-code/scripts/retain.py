#!/usr/bin/env python3
"""Auto-retain hook for Stop event.

Port of: agent_end handler in Openclaw index.js
Adapted for Claude Code hooks (ephemeral process, JSON stdin/stdout).

Flow:
  1. Read hook input from stdin (session_id, transcript_path, cwd)
  2. Read conversation transcript from transcript_path
  3. Apply chunked retention logic (retainEveryNTurns + overlap window)
  4. Resolve API URL (external, existing local, or auto-start daemon)
  5. Resolve personal knowledge space and ensure mission
  6. Format transcript (strip memory tags, filter roles)
  7. Upsert the session snapshot to the Foresight document API

Exit codes:
  0 — always (graceful degradation on any error)
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.bank import derive_bank_id, ensure_bank_mission
from lib.client import HindsightClient
from lib.config import debug_log, load_config
from lib.connection import get_api_url
from lib.content import (
    prepare_retention_transcript,
)
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

    Claude stores tool results as Anthropic user-role messages. Tag true user
    prompts from the outer JSONL wrapper so retainEveryNTurns and chunk windows
    do not treat tool_result rows as conversation turns.
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
                            if entry.get("permissionMode") is not None:
                                msg["_is_user_prompt"] = True
                            elif (
                                entry.get("isMeta")
                                or entry.get("toolUseResult") is not None
                                or entry.get("interruptedMessageId")
                            ):
                                msg["_is_user_prompt"] = False
                            messages.append(msg)
                    # Flat format (testing / future compatibility)
                    elif "role" in entry and "content" in entry:
                        messages.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return messages


def _is_real_user_prompt(msg: dict) -> bool:
    """Check whether a message is a real user prompt, not a tool result."""
    if msg.get("role") != "user":
        return False
    if msg.get("_is_user_prompt"):
        return True
    if "_is_user_prompt" in msg:
        return False

    # Fallback for flat-format tests and older transcripts without the outer
    # Claude wrapper. Keep it conservative so tool_result messages never become
    # turn boundaries just because Anthropic represents them as role=user.
    content = msg.get("content", "")
    if isinstance(content, str):
        if content.lstrip().startswith("<"):
            return False
        if content.strip().startswith("[Request interrupted"):
            return False
        return True
    if isinstance(content, list):
        if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
            return False
        if all(isinstance(block, dict) and block.get("type") == "text" for block in content):
            text = " ".join(block.get("text", "") for block in content if isinstance(block, dict))
            if text.strip().startswith("[Request interrupted"):
                return False
        return True
    return False


def _count_user_prompts(messages: list) -> int:
    """Count real user prompts, excluding tool results and meta messages."""
    return sum(1 for message in messages if _is_real_user_prompt(message))


def _slice_by_user_prompts(messages: list, turns: int) -> list:
    """Slice to the last N real user prompt turns.

    Claude tool_result rows use role=user, so the generic role-based slicer can
    drop the actual task prompts and leave only the latest tool calls.
    """
    if not messages or turns <= 0:
        return []

    user_prompts_seen = 0
    start_index = -1
    for index in range(len(messages) - 1, -1, -1):
        if _is_real_user_prompt(messages[index]):
            user_prompts_seen += 1
            if user_prompts_seen >= turns:
                start_index = index
                break

    if start_index == -1:
        return list(messages)
    return messages[start_index:]


def run_retain(hook_input: dict, force: bool = False) -> None:
    config = load_config()

    if not config.get("autoRetain"):
        debug_log(config, "Auto-retain disabled, exiting")
        return

    debug_log(config, f"Retain hook_input keys: {list(hook_input.keys())} force={force}")

    session_id = hook_input.get("session_id", "unknown")
    transcript_path = hook_input.get("transcript_path", "")

    # Read full transcript
    all_messages = read_transcript(transcript_path)
    if not all_messages:
        debug_log(config, "No messages in transcript, skipping retain")
        return

    debug_log(config, f"Read {len(all_messages)} messages from transcript")

    # Retention mode: full session (default) or chunked (legacy)
    retain_mode = config.get("retainMode", "full-session")
    retain_every_n = max(1, config.get("retainEveryNTurns", 1))
    retain_full_window = False
    messages_to_retain = all_messages

    # Respect retainEveryNTurns in both modes, unless force=True (SessionEnd final retain).
    # Count real user prompts in the full transcript rather than Stop hook
    # invocations; a single user turn can trigger several Stop hooks around tool
    # use, permission prompts, and assistant continuations.
    if retain_every_n > 1 and not force:
        user_turn_count = _count_user_prompts(all_messages)
        if user_turn_count % retain_every_n != 0:
            next_at = ((user_turn_count // retain_every_n) + 1) * retain_every_n
            debug_log(
                config,
                f"User turn {user_turn_count}/{retain_every_n}, skipping retain (next at turn {next_at})",
            )
            return
        debug_log(config, f"User turn {user_turn_count}/{retain_every_n}, retain firing")

    if retain_mode == "chunked" and retain_every_n > 1:
        # Sliding window: N turns + configured overlap
        overlap_turns = config.get("retainOverlapTurns", 0)
        window_turns = retain_every_n + overlap_turns
        messages_to_retain = _slice_by_user_prompts(all_messages, window_turns)
        retain_full_window = True
        debug_log(
            config,
            f"Chunked retain firing (window: {window_turns} turns, {len(messages_to_retain)} messages)",
        )
    else:
        # Full session mode: retain all messages, always as full window
        retain_full_window = True
        debug_log(config, f"Full session retain: {len(all_messages)} messages")

    # Format transcript
    retain_roles = config.get("retainRoles", ["user", "assistant"])
    include_tool_calls = config.get("retainToolCalls", True)
    transcript, message_count = prepare_retention_transcript(
        messages_to_retain, retain_roles, retain_full_window, include_tool_calls=include_tool_calls
    )

    if not transcript:
        debug_log(config, "Empty transcript after formatting, skipping retain")
        return

    # Resolve API URL
    def _dbg(*a):
        debug_log(config, *a)

    try:
        api_url = get_api_url(config, debug_fn=_dbg, allow_daemon_start=True)
    except RuntimeError as e:
        print(f"[Foresight] {e}", file=sys.stderr)
        return

    api_key = config.get("hindsightApiKey")
    try:
        client = HindsightClient(
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

    # One stable document per session/compaction segment. Even the legacy
    # chunked trigger mode updates this full-snapshot source instead of creating
    # timestamped documents for overlapping windows.
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

    # Resolve template variables in tags and metadata.
    # Supported variables: {session_id}, {bank_id}, {timestamp}, {user_id}
    template_vars = {
        "session_id": session_id,
        "bank_id": bank_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user_id": os.environ.get("HINDSIGHT_USER_ID", ""),
    }

    def _resolve_template(value: str) -> str:
        for k, v in template_vars.items():
            value = value.replace(f"{{{k}}}", v)
        return value

    # Tags from config with template resolution.
    # Drop tags whose resolved form ends in an empty namespace part (e.g. "user:"
    # when HINDSIGHT_USER_ID is unset). Tags without ':' are preserved as-is.
    raw_tags = config.get("retainTags", [])
    if raw_tags:
        tags = []
        for original in raw_tags:
            resolved = _resolve_template(original)
            if ":" in resolved and resolved.split(":", 1)[1] == "":
                debug_log(config, f"Dropping tag '{original}' -> '{resolved}' (empty content after ':')")
                continue
            tags.append(resolved)
        if not tags:
            tags = None
    else:
        tags = None

    # Metadata: merge built-in defaults with user-configured extras
    metadata = {
        "message_count": str(len(all_messages)),
        "session_id": session_id,
    }
    opened_state = read_state(OPENED_SOLUTIONS_STATE, {})
    opened_solutions = opened_solutions_for_session(opened_state, session_id)
    if opened_solutions:
        metadata["hindsight_opened_solutions_json"] = json.dumps(opened_solutions, ensure_ascii=False)
    for k, v in config.get("retainMetadata", {}).items():
        metadata[k] = _resolve_template(str(v))

    debug_log(
        config,
        f"Retaining to personal knowledge space, doc '{document_id}', "
        f"{len(all_messages)} messages, {len(transcript)} selected chars",
    )
    if tags:
        debug_log(config, f"Tags: {tags}")

    # The document stores the complete structured snapshot. Formatting above is
    # still used for message-count/empty checks, while extraction selects the
    # parser through source_format.
    source_content = json.dumps(all_messages, ensure_ascii=False, separators=(",", ":"))

    try:
        response = client.upsert_document(
            bank_id=bank_id,
            document_id=document_id,
            content=source_content,
            source_session_id=session_id,
            source_segment_index=source_segment_index,
            context=config.get("retainContext", "claude-code"),
            metadata=metadata,
            tags=tags,
            process_now=force,
            timeout=15,
        )
        debug_log(config, f"Document upsert response: {json.dumps(response)[:200]}")
        # Record successful persistence for diagnostics; SessionEnd still sends
        # an idempotent process_now snapshot to bypass the idle window.
        mark_session_retained(session_id)
        if force and opened_solutions:
            write_state(OPENED_SOLUTIONS_STATE, clear_opened_solution_session(opened_state, session_id))
    except Exception as e:
        print(f"[Foresight] Document upsert failed: {e}", file=sys.stderr)


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        print("[Foresight] Failed to read hook input", file=sys.stderr)
        return
    run_retain(hook_input, force=False)


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
