"""Content processing utilities.

Faithful port of Openclaw plugin's content processing: memory tag stripping,
query composition/truncation, transcript formatting, and memory formatting.

Source: reference/openclaw-source/index.js — stripMemoryTags, composeRecallQuery,
truncateRecallQuery, sliceLastTurnsByUserBoundary, prepareRetentionTranscript,
formatMemories.
"""

import json
import re
from datetime import datetime, timezone
from typing import Any

from lib.solution_candidates import normalize_solution_title

SOLUTION_RECALL_PROTOCOL_LINES = [
    "Foresight Solution 使用协议：",
    "- 将 <hindsight_solution_candidates> 视为类似 Skill 描述的简短、可渐进打开的方法论入口。",
    "- 每次处理用户请求时，在调用 Skill、Bash、TaskCreate 或其他行动工具前，先扫描 <hindsight_solution_candidates>。",
    "- 候选只展示 title 和 description，不包含完整流程、适用边界或失败模式。",
    "- 不要只根据候选摘要或 solution-like facts 执行完整工作流。",
    '- 如果候选可能对当前任务有帮助，优先调用其 open 行中的 agent_knowledge_open_solution(title="...") 查看详情；打开只读取知识，不会执行实际操作。',
    "- 打开后先按 Skill 编排选择/加载所需 Skill，再按执行流程、适用边界和失败模式完成任务。",
    "- 如果没有相关候选，正常继续即可，不需要解释跳过原因。",
    "- 默认最多打开 1 个候选；只有复杂任务才最多打开 2 个。",
]


def format_solution_recall_protocol() -> str:
    """Return the fixed solution loading protocol for session-level context."""
    return "\n".join(
        ["<hindsight_solution_protocol>", *SOLUTION_RECALL_PROTOCOL_LINES, "</hindsight_solution_protocol>"]
    )


# ---------------------------------------------------------------------------
# Memory tag stripping (anti-feedback-loop)
# ---------------------------------------------------------------------------


def strip_channel_envelope(content: str) -> str:
    """Strip Claude Code channel XML wrappers from user messages.

    Claude Code wraps incoming channel messages in XML:
      <channel source="plugin:telegram:telegram" chat_id="..." ...>
      actual message text
      </channel>

    This is the Claude Code equivalent of Openclaw's stripMetadataEnvelopes().
    Extracts the inner text, preserving the actual user message while removing
    transport metadata that Hindsight doesn't need.
    """
    # Match <channel ...>content</channel> — extract inner text
    match = re.search(r"<channel\b[^>]*>([\s\S]*?)</channel>", content)
    if match:
        return match.group(1).strip()
    return content


def strip_memory_tags(content: str) -> str:
    """Remove Hindsight recall-injected blocks.

    Prevents retain feedback loop — these were injected during recall and
    should not be re-stored.

    Port of: stripMemoryTags() in index.js
    """
    content = re.sub(r"<hindsight_memories>[\s\S]*?</hindsight_memories>", "", content)
    content = re.sub(r"<hindsight_solution_protocol>[\s\S]*?</hindsight_solution_protocol>", "", content)
    content = re.sub(r"<hindsight_solution_candidates>[\s\S]*?</hindsight_solution_candidates>", "", content)
    content = re.sub(r"<relevant_memories>[\s\S]*?</relevant_memories>", "", content)
    return content


# ---------------------------------------------------------------------------
# Recall: query composition and truncation
# ---------------------------------------------------------------------------


def compose_recall_query(
    latest_query: str,
    messages: list,
    recall_context_turns: int,
    recall_roles: list = None,
) -> str:
    """Compose a multi-turn recall query from conversation history.

    Port of: composeRecallQuery() in index.js

    When recallContextTurns > 1, includes prior context from the transcript
    above the latest user query. Format:

        Prior context:

        user: ...
        assistant: ...

        <latest query>
    """
    latest = latest_query.strip()
    if recall_context_turns <= 1 or not isinstance(messages, list) or not messages:
        return latest

    allowed_roles = set(recall_roles or ["user", "assistant"])
    contextual_messages = slice_last_turns_by_user_boundary(messages, recall_context_turns)

    context_lines = []
    for msg in contextual_messages:
        role = msg.get("role")
        if role not in allowed_roles:
            continue

        content = _extract_text_content(msg.get("content", ""), role=role)
        content = strip_channel_envelope(content)
        content = strip_memory_tags(content).strip()
        if not content:
            continue

        # Skip if this is the same as the latest query (avoid duplication)
        if role == "user" and content == latest:
            continue

        context_lines.append(f"{role}: {content}")

    if not context_lines:
        return latest

    return "\n\n".join(
        [
            "Prior context:",
            "\n".join(context_lines),
            latest,
        ]
    )


def truncate_recall_query(query: str, latest_query: str, max_chars: int) -> str:
    """Truncate a composed recall query to max_chars.

    Port of: truncateRecallQuery() in index.js

    Preserves the latest user message. When the query contains "Prior context:",
    drops oldest context lines first (from the top) to fit within the limit.
    """
    if max_chars <= 0:
        return query

    latest = latest_query.strip()
    if len(query) <= max_chars:
        return query

    # If even the latest alone is too long, hard-truncate it
    latest_only = latest[:max_chars] if len(latest) > max_chars else latest

    if "Prior context:" not in query:
        return latest_only

    context_marker = "Prior context:\n\n"
    marker_index = query.find(context_marker)
    if marker_index == -1:
        return latest_only

    suffix_marker = "\n\n" + latest
    suffix_index = query.rfind(suffix_marker)
    if suffix_index == -1:
        return latest_only

    suffix = query[suffix_index:]  # \n\n<latest>
    if len(suffix) >= max_chars:
        return latest_only

    context_body = query[marker_index + len(context_marker) : suffix_index]
    context_lines = [line for line in context_body.split("\n") if line]

    # Add context lines from newest (bottom) to oldest (top), stop when exceeding
    kept = []
    for i in range(len(context_lines) - 1, -1, -1):
        kept.insert(0, context_lines[i])
        candidate = f"{context_marker}{chr(10).join(kept)}{suffix}"
        if len(candidate) > max_chars:
            kept.pop(0)
            break

    if kept:
        return f"{context_marker}{chr(10).join(kept)}{suffix}"
    return latest_only


# ---------------------------------------------------------------------------
# Turn slicing
# ---------------------------------------------------------------------------


def slice_last_turns_by_user_boundary(messages: list, turns: int) -> list:
    """Slice messages to the last N turns, where a turn starts at a user message.

    Port of: sliceLastTurnsByUserBoundary() in index.js

    Walks backward counting user messages as turn boundaries. Returns
    messages from the Nth user boundary to the end.
    """
    if not isinstance(messages, list) or not messages or turns <= 0:
        return []

    user_turns_seen = 0
    start_index = -1

    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_turns_seen += 1
            if user_turns_seen >= turns:
                start_index = i
                break

    if start_index == -1:
        return list(messages)

    return messages[start_index:]


# ---------------------------------------------------------------------------
# Memory formatting (recall results → context string)
# ---------------------------------------------------------------------------


def format_memories(results: list) -> str:
    """Format recall results into human-readable text.

    Port of: formatMemories() in index.js
    Format: - <text> [<type>] (<mentioned_at>)
    """
    if not results:
        return ""
    lines = []
    for r in results:
        text = r.get("text", "")
        mem_type = r.get("type", "")
        mentioned_at = r.get("mentioned_at", "")
        type_str = f" [{mem_type}]" if mem_type else ""
        date_str = f" ({mentioned_at})" if mentioned_at else ""
        lines.append(f"- {text}{type_str}{date_str}")
    return "\n\n".join(lines)


def _truncate_recall_field(value: object, max_chars: int = 240) -> str:
    """Convert a recall field to compact single-line text for prompt injection."""
    if value is None:
        return ""
    if isinstance(value, list):
        text = ", ".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        text = ", ".join(f"{key}: {val}" for key, val in value.items() if val not in (None, "", []))
    else:
        text = str(value).strip()

    text = re.sub(r"\s+", " ", text)
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


def format_solution_candidates(solutions: list[object]) -> str:
    """Format solution recall results as skill-like progressive-loading candidates.

    The full `approach` methodology is deliberately not included here. Agents
    load a candidate by its visible title after comparing its task intent with
    the current user request.
    """
    if not solutions:
        return ""

    header = [
        "<hindsight_solution_candidates>",
        *SOLUTION_RECALL_PROTOCOL_LINES,
        "",
        "候选 Solution：",
    ]
    candidate_lines = []
    for solution in solutions:
        if not isinstance(solution, dict):
            continue
        title = normalize_solution_title(solution.get("title"))
        solution_id = normalize_solution_title(solution.get("id"))
        if not title or not solution_id:
            continue
        open_call = f"agent_knowledge_open_solution(title={json.dumps(title, ensure_ascii=False)})"

        candidate_lines.extend([f"- title: {title}"])
        description = _truncate_recall_field(
            solution.get("task_intent") or solution.get("matched_query") or title,
            400,
        )
        candidate_lines.extend(
            [
                f"  description: {description}",
                f"  open: {open_call}",
            ]
        )
    if not candidate_lines:
        return ""
    return "\n".join(header + candidate_lines + ["</hindsight_solution_candidates>"])


def format_recall_context(
    preamble: str,
    current_time: str,
    memories_formatted: str = "",
    solution_candidates_formatted: str = "",
) -> str:
    """Build recall context with facts and solution candidates separated."""
    sections = []
    if memories_formatted:
        body_parts = []
        if preamble:
            body_parts.append(preamble)
        body_parts.append(f"当前时间 - {current_time}")
        body_parts.append(memories_formatted)
        sections.append("<hindsight_memories>\n" + "\n\n".join(body_parts) + "\n</hindsight_memories>")
    if solution_candidates_formatted:
        sections.append(solution_candidates_formatted)
    return "\n\n".join(sections)


def format_current_time() -> str:
    """Format current UTC time for recall context.

    The "UTC" suffix is explicit so client LLMs do not misread the
    value as local time when reasoning about wall-clock context.

    Port of: formatCurrentTimeForRecall() in index.js
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# Retention transcript formatting
# ---------------------------------------------------------------------------


def _extract_message_blocks(content, role: str = "") -> list:
    """Extract structured content blocks from a message for JSON retention.

    Returns a list of dicts, each representing a content block:
      - {"type": "text", "text": "..."} for text blocks
      - {"type": "tool_use", "name": "...", "input": {...}} for tool calls
      - Channel message tool_use blocks get their text extracted inline.
    """
    if isinstance(content, str):
        cleaned = strip_channel_envelope(strip_memory_tags(content)).strip()
        return [{"type": "text", "text": cleaned}] if cleaned else []

    if not isinstance(content, list):
        return []

    blocks = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type", "")

        if block_type == "text":
            text = strip_channel_envelope(strip_memory_tags(block.get("text", ""))).strip()
            if text:
                blocks.append({"type": "text", "text": text})

        elif block_type == "tool_use" and role == "assistant":
            if _is_channel_message_tool(block):
                # Channel messages: extract the outgoing text
                tool_input = block.get("input", {})
                for field in _MESSAGE_TEXT_FIELDS:
                    val = tool_input.get(field)
                    if isinstance(val, str) and val.strip():
                        blocks.append({"type": "text", "text": val.strip()})
                        break
            else:
                name = block.get("name", "unknown")
                inp = block.get("input", {})
                # Skip Hindsight MCP tools to avoid feedback loops
                if name.startswith("mcp__") and _OPERATIONAL_TOOL_PATTERN.search(name.split("__")[-1]):
                    continue
                blocks.append({"type": "tool_use", "name": name, "input": inp})

        elif block_type == "tool_result":
            # Include tool results for context.
            # content can be a plain string or a list of content blocks
            # (e.g. [{"type": "text", "text": "..."}] for Agent results).
            result_content = block.get("content", "")
            if isinstance(result_content, list):
                # Extract text from content blocks
                parts = []
                for item in result_content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        t = item.get("text", "").strip()
                        if t:
                            parts.append(t)
                result_content = "\n".join(parts)
            if isinstance(result_content, str) and result_content.strip():
                text = result_content.strip()
                # Truncate very long results
                if len(text) > 2000:
                    text = text[:2000] + "... (truncated)"
                blocks.append({"type": "tool_result", "tool_use_id": block.get("tool_use_id", ""), "content": text})

    return blocks


def prepare_retention_transcript(
    messages: list,
    retain_roles: list = None,
    retain_full_window: bool = False,
    include_tool_calls: bool = False,
) -> tuple:
    """Format messages into a retention transcript.

    When include_tool_calls is True, outputs JSON with full message structure
    including tool calls and their inputs. Otherwise outputs the legacy
    text format with [role: ...]...[role:end] markers.

    Args:
        messages: List of message dicts with 'role' and 'content'.
        retain_roles: Roles to include (default: ['user', 'assistant']).
        retain_full_window: If True, retain all messages (chunked mode).
            If False, retain only the last turn (last user msg + responses).
        include_tool_calls: If True, output JSON format with full tool call data.

    Returns:
        (transcript_text, message_count) or (None, 0) if nothing to retain.
    """
    if not messages:
        return None, 0

    if retain_full_window:
        target_messages = messages
    else:
        # Default: retain only the last turn
        last_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx == -1:
            return None, 0
        target_messages = messages[last_user_idx:]

    allowed_roles = set(retain_roles or ["user", "assistant"])

    if include_tool_calls:
        return _prepare_json_transcript(target_messages, allowed_roles)
    return _prepare_text_transcript(target_messages, allowed_roles)


def _prepare_json_transcript(messages: list, allowed_roles: set) -> tuple:
    """Format messages as JSON with full tool call data."""
    import json

    structured_messages = []
    for msg in messages:
        role = msg.get("role", "unknown")
        if role not in allowed_roles:
            continue

        blocks = _extract_message_blocks(msg.get("content", ""), role=role)
        if not blocks:
            continue

        structured_messages.append({"role": role, "content": blocks})

    if not structured_messages:
        return None, 0

    transcript = json.dumps(structured_messages, indent=None, ensure_ascii=False)
    if len(transcript.strip()) < 10:
        return None, 0

    return transcript, len(structured_messages)


def _prepare_text_transcript(messages: list, allowed_roles: set) -> tuple:
    """Format messages as legacy text with [role:]...[role:end] markers."""
    parts = []

    for msg in messages:
        role = msg.get("role", "unknown")
        if role not in allowed_roles:
            continue

        content = _extract_text_content(msg.get("content", ""), role=role)
        content = strip_channel_envelope(content)
        content = strip_memory_tags(content).strip()

        if not content:
            continue

        parts.append(f"[role: {role}]\n{content}\n[{role}:end]")

    if not parts:
        return None, 0

    transcript = "\n\n".join(parts)
    if len(transcript.strip()) < 10:
        return None, 0

    return transcript, len(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Fields in tool_use input that carry the outgoing message text.
# Ordered by likelihood — first match wins.
_MESSAGE_TEXT_FIELDS = ("text", "body", "message", "content")

# MCP tool name suffixes that are operational, not conversational.
# Checked against the last segment of the tool name (after the last __).
import re as _re

_OPERATIONAL_TOOL_PATTERN = _re.compile(
    r"(?:recall|retain|reflect|search|extract|create_|delete_|update_|get_|list_)",
    _re.IGNORECASE,
)


def _is_channel_message_tool(block: dict) -> bool:
    """Detect if a tool_use block is a channel message (reply/send).

    Uses a structural approach rather than name-matching for robustness:
      1. Must be an MCP tool (name starts with "mcp__")
      2. Must NOT match known operational patterns (recall, search, CRUD)
      3. Must have a text-like field in input (text, body, message, content)

    This catches any channel plugin (Telegram, Slack, Discord, Matrix,
    future channels) without hardcoding tool names. Built-in tools (Bash,
    Read, Write) don't start with mcp__. MCP tools for non-messaging
    purposes (hindsight recall, search) are excluded by pattern and by
    lacking text/body fields.
    """
    name = block.get("name", "")
    if not name.startswith("mcp__"):
        return False

    # Exclude operational MCP tools (check only the tool suffix, not server name)
    tool_suffix = name.split("__")[-1]
    if _OPERATIONAL_TOOL_PATTERN.search(tool_suffix):
        return False

    tool_input = block.get("input", {})
    if not isinstance(tool_input, dict):
        return False

    # Must have a text-carrying field with actual content
    return any(isinstance(tool_input.get(f), str) and tool_input[f].strip() for f in _MESSAGE_TEXT_FIELDS)


def _extract_text_content(content, role: str = "") -> str:
    """Extract text from message content (string or content blocks array).

    For user messages: extracts from plain strings (channel XML wrappers
    are stripped separately by strip_channel_envelope).

    For assistant messages: extracts from:
      - {type: "text"} blocks — terminal output/narration
      - {type: "tool_use"} blocks detected as channel messages — the agent's
        actual responses to the user. Detection is structural (MCP tool with
        text-like input field), not name-based, for channel-agnosticism.

    Excludes:
      - {type: "thinking"} — internal reasoning
      - {type: "tool_use"} for operational tools — Bash, Read, Write, recall, etc.
      - {type: "tool_result"} — operational results, not conversation
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")

            # Text blocks: terminal output / narration
            if block_type == "text":
                text = block.get("text", "").strip()
                if text:
                    texts.append(text)

            # Tool use blocks: extract channel messages
            elif block_type == "tool_use" and role == "assistant":
                if _is_channel_message_tool(block):
                    tool_input = block.get("input", {})
                    for field in _MESSAGE_TEXT_FIELDS:
                        val = tool_input.get(field)
                        if isinstance(val, str) and val.strip():
                            texts.append(val.strip())
                            break

        return "\n".join(texts)
    return ""
