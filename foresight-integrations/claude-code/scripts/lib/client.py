"""Foresight REST API client.

Communicates with a Foresight server via HTTP using only the Python standard
library. The class name remains compatible with the original integration.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

DEFAULT_TIMEOUT = 15  # seconds
HEALTH_CHECK_RETRIES = 3
HEALTH_CHECK_DELAY = 2  # seconds


def _plugin_version() -> str:
    """Read the plugin version from plugin.json (single source of truth)."""
    manifest = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text()).get("version", "0.0.0")
    except (OSError, ValueError):
        return "0.0.0"


# Sent on every request so self-hosted deployments behind Cloudflare (or any
# reverse proxy with UA-based bot filtering) don't block the stdlib default
# "Python-urllib/X.Y", which trips Cloudflare error 1010.
USER_AGENT = f"foresight-claude-code/{_plugin_version()}"
PERSONAL_KNOWLEDGE_BASE = "/v1/me/knowledge"


def _validate_api_url(url: str) -> str:
    """Validate and normalize the API URL. Reject non-HTTP schemes."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Foresight API URL must use http or https, got: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError(f"Foresight API URL has no hostname: {url!r}")
    return url.rstrip("/")


class HindsightClient:
    """HTTP client for the Foresight API."""

    def __init__(
        self,
        api_url: str,
        api_key: Optional[str] = None,
        request_timeout_override: Optional[int] = None,
    ):
        self.api_url = _validate_api_url(api_url)
        normalized_api_key = (api_key or "").strip()
        if not normalized_api_key:
            raise ValueError(
                "Foresight API key is required. Create a personal API key and set hindsightApiKey or HINDSIGHT_API_KEY."
            )
        if not normalized_api_key.startswith("hsk_"):
            raise ValueError("Foresight credential must be a long-lived hsk_ API key")
        self.api_key = normalized_api_key
        self.request_timeout_override = request_timeout_override

    def _resolve_timeout(self, timeout: int) -> int:
        """Return the override if configured, otherwise the caller's timeout."""
        return self.request_timeout_override if self.request_timeout_override is not None else timeout

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def request(self, method: str, path: str, body: Optional[dict] = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
        timeout = self._resolve_timeout(timeout)
        url = f"{self.api_url}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode()
            except Exception:
                pass
            raise RuntimeError(f"HTTP {e.code} from {url}: {body_text}") from e

    def health_check(self, timeout: int = 5) -> bool:
        """Check if the Foresight server is reachable.

        Mirrors Openclaw's checkExternalApiHealth: retries up to 3 times
        with 2s delay between attempts.
        """
        import time

        for attempt in range(1, HEALTH_CHECK_RETRIES + 1):
            try:
                url = f"{self.api_url}/health"
                req = urllib.request.Request(url, headers=self._headers(), method="GET")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                pass
            if attempt < HEALTH_CHECK_RETRIES:
                time.sleep(HEALTH_CHECK_DELAY)
        return False

    def recall(
        self,
        bank_id: str,
        query: str,
        current_user_input: Optional[str] = None,
        max_tokens: int = 1024,
        budget: str = "mid",
        types: Optional[list] = None,
        solution_detail: Optional[str] = None,
        timeout: int = 10,
    ) -> dict:
        """Recall memories from the personal knowledge space.

        Returns the raw API response dict with 'results' and optional virtual knowledge lists.
        """
        path = f"{PERSONAL_KNOWLEDGE_BASE}/recall"
        body = {
            "query": query,
            "max_tokens": max_tokens,
        }
        if current_user_input:
            body["current_user_input"] = current_user_input
        if budget:
            body["budget"] = budget
        if types:
            body["types"] = types
        if solution_detail:
            body["solution_detail"] = solution_detail
        return self.request("POST", path, body, timeout=timeout)

    def get_solution(self, bank_id: str, solution_id: str, detail: Optional[str] = None, timeout: int = 10) -> dict:
        """Fetch a full reusable solution from the personal knowledge space."""
        path = f"{PERSONAL_KNOWLEDGE_BASE}/solutions/{urllib.parse.quote(solution_id, safe='')}"
        if detail:
            path += "?" + urllib.parse.urlencode({"detail": detail})
        return self.request("GET", path, timeout=timeout)

    def get_center_solution(
        self,
        solution_id: str,
        organization_slug: str,
        detail: Optional[str] = None,
        timeout: int = 10,
    ) -> dict:
        """Fetch a full reusable solution from an organization center."""
        encoded_solution = urllib.parse.quote(solution_id, safe="")
        query_params = {"organization_slug": organization_slug}
        if detail:
            query_params["detail"] = detail
        query = urllib.parse.urlencode(query_params)
        path = f"/v1/default/center/solutions/{encoded_solution}?{query}"
        return self.request("GET", path, timeout=timeout)

    def retain(
        self,
        bank_id: str,
        content: str,
        document_id: str = "conversation",
        context: Optional[str] = None,
        metadata: Optional[dict] = None,
        tags: Optional[list] = None,
        timeout: int = 15,
    ) -> dict:
        """Retain content into the personal knowledge space.

        Posts with async=true so the server processes in the background.
        The context field helps Foresight cluster memories by provenance
        (e.g. "claude-code" vs manual retains).
        """
        path = f"{PERSONAL_KNOWLEDGE_BASE}/memories"
        item = {
            "content": content,
            "document_id": document_id,
            "metadata": metadata or {},
        }
        if context:
            item["context"] = context
        if tags:
            item["tags"] = tags
        body = {
            "items": [item],
            "async": True,
        }
        return self.request("POST", path, body, timeout=timeout)

    def upsert_document(
        self,
        bank_id: str,
        document_id: str,
        content: str,
        *,
        source_session_id: str,
        source_segment_index: int = 0,
        context: Optional[str] = None,
        metadata: Optional[dict] = None,
        tags: Optional[list] = None,
        process_now: bool = False,
        timeout: int = 15,
    ) -> dict:
        """Save a full Claude session snapshot as the canonical document source."""
        encoded_document_id = urllib.parse.quote(document_id, safe="")
        path = f"{PERSONAL_KNOWLEDGE_BASE}/documents/{encoded_document_id}"
        body = {
            "content": content,
            "source_kind": "harness",
            "source_harness": "claude-code",
            "source_session_id": source_session_id,
            "source_segment_index": source_segment_index,
            "source_format": "claude_code_transcript",
            "context": context or "claude-code",
            "metadata": metadata or {},
            "tags": tags or [],
            "process_now": bool(process_now),
        }
        return self.request("PUT", path, body, timeout=timeout)

    def set_personal_knowledge_mission(
        self, bank_id: str, mission: str, retain_mission: Optional[str] = None, timeout: int = 15
    ) -> dict:
        """Set the personal knowledge mission/persona."""
        path = f"{PERSONAL_KNOWLEDGE_BASE}/settings"
        updates = {"reflect_mission": mission}
        if retain_mission:
            updates["retain_mission"] = retain_mission
        return self.request("PATCH", path, {"updates": updates}, timeout=timeout)

    def get_personal_knowledge_settings(self, bank_id: str, timeout: int = 10) -> dict:
        """Fetch the resolved personal knowledge settings from the server.

        Returns the config dict which may contain recall_prompt_preamble,
        retain_mission, reflect_mission, etc.
        Returns empty dict on failure.
        """
        path = f"{PERSONAL_KNOWLEDGE_BASE}/settings"
        try:
            return self.request("GET", path, timeout=timeout)
        except Exception:
            return {}
