"""Connection validation for the Foresight Claude Code plugin."""

from urllib.parse import urlparse


def get_api_url(config: dict, debug_fn=None, allow_daemon_start: bool = False) -> str:
    """Return the configured Foresight API URL.

    Foresight's personal knowledge and solution APIs are served by the deployed
    Foresight backend, so this plugin does not start a local service.

    ``allow_daemon_start`` is retained in the signature for compatibility with
    existing hook callers; it has no effect.
    """
    del allow_daemon_start

    api_url = str(config.get("foresightApiUrl") or "").strip().rstrip("/")
    if not api_url:
        raise RuntimeError(
            "Foresight API URL is required. Set foresightApiUrl in "
            "~/.foresight/claude-code.json or set FORESIGHT_API_URL."
        )

    parsed = urlparse(api_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise RuntimeError(f"Foresight API URL must be a valid http(s) URL: {api_url!r}")

    if debug_fn:
        debug_fn(f"Using configured Foresight API: {api_url}")
    return api_url
