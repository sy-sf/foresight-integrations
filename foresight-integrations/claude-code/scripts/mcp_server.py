#!/usr/bin/env python3
"""Foresight MCP server for Claude Code plugin.

Runs as a stdio subprocess managed by the plugin system.
Exposes knowledge tools (list/get/create/update/delete pages, recall, ingest).
Reuses the existing plugin config chain and client.

Tools use the authenticated user's personal knowledge space. The primary
solution loader opens the current recall candidates by visible title; routing
metadata stays in local hook state instead of the agent prompt.
"""

import json
import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Launched via scripts/run_mcp.sh which execs the venv's interpreter, so
# `mcp` and friends resolve from ${CLAUDE_PLUGIN_DATA}/venv/site-packages.
from lib.bank import derive_bank_id
from lib.client import ForesightClient
from lib.config import debug_log, load_config
from lib.connection import get_api_url
from lib.solution_candidates import (
    OPENED_SOLUTIONS_STATE,
    SOLUTION_CANDIDATES_STATE,
    SolutionCandidateRoute,
    append_opened_solution_state,
    normalize_solution_title,
    opened_solution_record,
    parse_solution_candidate_state,
    select_solution_route_by_title,
    session_id_from_candidate_state,
)
from lib.state import read_state, write_state
from mcp.server.fastmcp import FastMCP

# ── Server setup ────────────────────────────────────────

mcp = FastMCP("foresight")

# Resolve config at startup
_config = load_config()


def _dbg(*args: object) -> None:
    debug_log(_config, *args)


if not _config.get("enableKnowledgeTools"):
    # Knowledge tools are opt-out. When disabled we must NOT exit: the plugin
    # registers this server unconditionally in .mcp.json, and Claude Code treats
    # a process that exits at startup as a crashed server — retrying it and
    # surfacing a `-32000` reconnect error on every prompt. Instead, stay alive
    # as an empty MCP server that advertises no tools. The tool definitions
    # below are skipped entirely because mcp.run() blocks here.
    _dbg("Knowledge tools disabled (enableKnowledgeTools=false) — running empty MCP server")
    mcp.run(transport="stdio")
    sys.exit(0)

try:
    _api_url = get_api_url(_config, debug_fn=_dbg, allow_daemon_start=True)
    _client = ForesightClient(
        _api_url,
        _config.get("foresightApiKey"),
        request_timeout_override=_config.get("requestTimeoutSeconds"),
    )
except Exception as e:
    print(f"[Foresight MCP] Invalid connection configuration: {e}", file=sys.stderr)
    sys.exit(1)

_hook_input = {"cwd": os.getcwd(), "session_id": ""}
_default_bank_id = derive_bank_id(_hook_input, _config)

PERSONAL_KNOWLEDGE_BASE = "/v1/me/knowledge"

_dbg(f"MCP server starting — API: {_api_url}, personal knowledge space")


def _parse_recall_types(types: object) -> list[str] | None:
    """Normalize MCP recall types from comma-separated text or a JSON list."""
    if types is None:
        return None
    if isinstance(types, list):
        parsed = [str(item).strip() for item in types if str(item).strip()]
        return parsed or None
    if isinstance(types, str):
        parsed = [item.strip() for item in types.split(",") if item.strip()]
        return parsed or None
    return None


# ── Mental model defaults ───────────────────────────────

PAGE_DEFAULTS = {
    "mode": "delta",
    "refresh_after_consolidation": True,
    "fact_types": ["observation"],
    "exclude_mental_models": True,
}

# ── Tools ───────────────────────────────────────────────
# Most tools use the canonical personal knowledge space. Solution title loading
# uses routing metadata saved by the latest UserPromptSubmit recall hook.


@mcp.tool()
def agent_knowledge_get_current_space() -> str:
    """Get the current knowledge space used for conversation retention and pages."""
    return json.dumps({"space": "personal"})


@mcp.tool()
def agent_knowledge_list_pages() -> str:
    """List all your knowledge pages (IDs and names only). Use agent_knowledge_get_page to read the full content of a specific page."""
    # The API defaults to detail=full, which returns synthesized content +
    # reflect_response for every page. The docstring above promises "IDs and
    # names only", so request the metadata projection explicitly. This keeps
    # list_pages payloads small at realistic agent scales (tens of pages,
    # each up to ~100 KB content).
    resp = _client.request(
        "GET",
        f"{PERSONAL_KNOWLEDGE_BASE}/mental-models?detail=metadata",
        timeout=10,
    )
    return json.dumps(resp, indent=2)


@mcp.tool()
def agent_knowledge_get_page(page_id: str) -> str:
    """Read a specific knowledge page by its ID. Returns the full synthesized content."""
    # detail=content returns the synthesized `content` plus metadata; detail=full
    # additionally includes `reflect_response`, the internal trace metadata used
    # to build the page. Empirically reflect_response is 70-95% of the response
    # bytes and the docstring promises only "synthesized content" — full payloads
    # at this scale (200+ KB per page) blow past the MCP host's per-tool-result
    # token cap and force the result to spill to disk, where the agent can't
    # consume it inline.
    resp = _client.request(
        "GET",
        f"{PERSONAL_KNOWLEDGE_BASE}/mental-models/{page_id}?detail=content",
        timeout=10,
    )
    return json.dumps(resp, indent=2)


@mcp.tool()
def agent_knowledge_create_page(page_id: str, name: str, source_query: str) -> str:
    """Create a new knowledge page. The source_query is a question the system re-asks after each consolidation to rebuild the page from conversation observations. Pages auto-update as you have more conversations."""
    resp = _client.request(
        "POST",
        f"{PERSONAL_KNOWLEDGE_BASE}/mental-models",
        body={
            "id": page_id,
            "name": name,
            "source_query": source_query,
            "max_tokens": 4096,
            "trigger": PAGE_DEFAULTS,
        },
        timeout=15,
    )
    return json.dumps(resp, indent=2)


@mcp.tool()
def agent_knowledge_update_page(page_id: str, name: str = "", source_query: str = "") -> str:
    """Update a page's name or source query. The content will re-synthesize on next consolidation."""
    body = {}
    if name:
        body["name"] = name
    if source_query:
        body["source_query"] = source_query
    if not body:
        return json.dumps({"error": "请提供 name 或 source_query 以便更新"}, ensure_ascii=False)
    resp = _client.request(
        "PATCH",
        f"{PERSONAL_KNOWLEDGE_BASE}/mental-models/{page_id}",
        body=body,
        timeout=10,
    )
    return json.dumps(resp, indent=2)


@mcp.tool()
def agent_knowledge_delete_page(page_id: str) -> str:
    """Permanently delete a knowledge page."""
    resp = _client.request(
        "DELETE",
        f"{PERSONAL_KNOWLEDGE_BASE}/mental-models/{page_id}",
        timeout=10,
    )
    return json.dumps(resp, indent=2)


@mcp.tool()
def agent_knowledge_recall(query: str, max_tokens: int = 1024, types: str = "") -> str:
    """Search retained knowledge for facts/details not covered by pages. Optional types is a comma-separated list such as observation,solution; default favors fact recall."""
    resp = _client.recall(
        bank_id=_default_bank_id,
        query=query,
        current_user_input=query,
        max_tokens=max_tokens,
        budget="mid",
        types=_parse_recall_types(types),
        timeout=10,
    )
    return json.dumps(resp, indent=2)


def _load_solution_route(route: SolutionCandidateRoute) -> dict[str, Any]:
    """Fetch the full solution for a selected current candidate route."""
    if route.organization_slug:
        return _client.get_center_solution(
            solution_id=route.solution_id,
            organization_slug=route.organization_slug,
            detail="agent",
            timeout=10,
        )
    return _client.get_solution(
        bank_id=_default_bank_id,
        solution_id=route.solution_id,
        detail="agent",
        timeout=10,
    )


def _project_agent_solution(solution: dict[str, Any]) -> dict[str, Any]:
    """Return only the solution fields useful to an agent applying the method."""
    metadata = solution.get("metadata") if isinstance(solution.get("metadata"), dict) else {}
    projected_metadata = {
        key: metadata[key]
        for key in ("applicability", "failure_modes", "skill_refs", "skills", "evidence")
        if key in metadata
    }
    return {
        "title": solution.get("title", ""),
        "original_user_input": solution.get("original_user_input"),
        "approach": solution.get("approach", ""),
        "queries": solution.get("queries", []),
        "metadata": projected_metadata,
        "tags": solution.get("tags", []),
        "confidence": solution.get("confidence"),
        "layer": solution.get("layer", "user"),
        "organization_slug": solution.get("organization_slug"),
    }


@mcp.tool()
def agent_knowledge_open_solution(title: str) -> str:
    """Open a full solution methodology by the exact title shown in <foresight_solution_candidates>."""
    normalized_title = normalize_solution_title(title)
    if not normalized_title:
        return json.dumps({"error": "必须提供 title"}, ensure_ascii=False, indent=2)

    candidate_state = read_state(SOLUTION_CANDIDATES_STATE, {})
    routes = parse_solution_candidate_state(candidate_state)
    if not routes:
        return json.dumps({"error": "当前提示词中没有可加载的 solution 候选"}, ensure_ascii=False, indent=2)

    route = select_solution_route_by_title(routes, normalized_title)
    if route is None:
        return json.dumps(
            {
                "error": "当前 solution 候选中没有匹配该标题的条目",
                "title": normalized_title,
                "available_titles": [candidate.title for candidate in routes],
            },
            ensure_ascii=False,
            indent=2,
        )

    try:
        resp = _load_solution_route(route)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False, indent=2)

    session_id = session_id_from_candidate_state(candidate_state)
    if session_id:
        opened_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        opened_state = append_opened_solution_state(
            read_state(OPENED_SOLUTIONS_STATE, {}),
            opened_solution_record(route, session_id=session_id, opened_at=opened_at),
        )
        write_state(OPENED_SOLUTIONS_STATE, opened_state)
    return json.dumps(_project_agent_solution(resp), ensure_ascii=False, indent=2)


@mcp.tool()
def agent_knowledge_get_solution(solution_id: str, organization_slug: str = "") -> str:
    """Load the full methodology for a solution candidate. Pass organization_slug for center candidates."""
    if not solution_id.strip():
        return json.dumps({"error": "必须提供 solution_id"}, ensure_ascii=False)

    try:
        if organization_slug.strip():
            resp = _client.get_center_solution(
                solution_id=solution_id.strip(),
                organization_slug=organization_slug.strip(),
                detail="agent",
                timeout=10,
            )
        else:
            resp = _client.get_solution(
                bank_id=_default_bank_id,
                solution_id=solution_id.strip(),
                detail="agent",
                timeout=10,
            )
    except Exception as e:
        return json.dumps({"error": str(e)})
    return json.dumps(_project_agent_solution(resp), ensure_ascii=False, indent=2)


@mcp.tool()
def agent_knowledge_ingest(title: str, content: str) -> str:
    """Upload text content into your personal knowledge space. Pass the full raw content; the title becomes the document ID."""
    doc_id = title.lower().replace(" ", "-")
    resp = _client.retain(bank_id=_default_bank_id, content=content, document_id=doc_id, timeout=15)
    return json.dumps(resp, indent=2)


@mcp.tool()
def agent_knowledge_ingest_file(file_path: str) -> str:
    """Ingest a file from disk into your personal knowledge space. The filename becomes the document ID."""
    import os

    if not os.path.isfile(file_path):
        return json.dumps({"error": f"File not found: {file_path}"})

    content = open(file_path, encoding="utf-8").read()
    if not content.strip():
        return json.dumps({"error": f"File is empty: {file_path}"})

    doc_id = os.path.basename(file_path).rsplit(".", 1)[0].lower().replace(" ", "-")
    resp = _client.retain(bank_id=_default_bank_id, content=content, document_id=doc_id, timeout=15)
    return json.dumps(resp, indent=2)


# ── Entry point ─────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
