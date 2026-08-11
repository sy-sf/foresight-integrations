"""Configuration management for the Foresight Claude Code plugin.

Loads built-in defaults, stable user configuration, and environment overrides.
"""

import json
import os
import sys

DEFAULTS = {
    # Recall
    "autoRecall": True,
    "recallBudget": "mid",
    "recallMaxTokens": 1024,
    "recallTypes": ["observation", "solution"],
    "recallSolutionDetail": "candidate",
    "recallContextTurns": 1,
    "recallMaxQueryChars": 800,
    "recallRoles": ["user", "assistant"],
    "recallPromptPreamble": (
        "以下是从过往对话中召回的相关记忆（冲突时优先近期内容）。仅使用对继续本次对话有直接帮助的记忆，忽略其余："
    ),
    # Retain
    "autoRetain": True,
    # Connection
    "foresightApiUrl": None,
    "foresightApiKey": None,
    "requestTimeoutSeconds": None,
    # Personal knowledge
    "personalKnowledgeMission": "",
    "retainMission": None,
    # Misc
    "enableKnowledgeTools": True,
    "debug": False,
}

# Map env var names to config keys and their types
ENV_OVERRIDES = {
    "FORESIGHT_API_URL": ("foresightApiUrl", str),
    "FORESIGHT_API_KEY": ("foresightApiKey", str),
    "FORESIGHT_AUTO_RECALL": ("autoRecall", bool),
    "FORESIGHT_AUTO_RETAIN": ("autoRetain", bool),
    "FORESIGHT_RECALL_BUDGET": ("recallBudget", str),
    "FORESIGHT_RECALL_SOLUTION_DETAIL": ("recallSolutionDetail", str),
    "FORESIGHT_RECALL_MAX_TOKENS": ("recallMaxTokens", int),
    "FORESIGHT_RECALL_MAX_QUERY_CHARS": ("recallMaxQueryChars", int),
    "FORESIGHT_RECALL_CONTEXT_TURNS": ("recallContextTurns", int),
    "FORESIGHT_REQUEST_TIMEOUT_SECONDS": ("requestTimeoutSeconds", int),
    "FORESIGHT_PERSONAL_KNOWLEDGE_MISSION": ("personalKnowledgeMission", str),
    "FORESIGHT_DEBUG": ("debug", bool),
}


def _cast_env(value: str, typ):
    """Cast environment variable string to target type. Returns None on failure."""
    try:
        if typ is bool:
            return value.lower() in ("true", "1", "yes")
        if typ is int:
            return int(value)
        return value
    except (ValueError, AttributeError):
        return None


def _load_config_file(path: str, config: dict) -> None:
    """Merge a JSON configuration file in-place. Silently skip if missing."""
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            file_config = json.load(f)
        config.update({k: v for k, v in file_config.items() if v is not None})
    except (json.JSONDecodeError, OSError) as e:
        debug_log(config, f"Failed to load {path}: {e}")


def load_config() -> dict:
    """Load plugin configuration from user settings and env overrides.

    Loading order (later entries win):
      1. Built-in defaults
      2. User config (~/.foresight/claude-code.json)
      3. Environment variable overrides

    ~/.foresight/claude-code.json is the recommended place to configure the
    plugin — same convention as ~/.openclaw/openclaw.json. It is stable across
    plugin updates and marketplace changes.
    """
    config = dict(DEFAULTS)

    # User config — stable and version-independent.
    user_config_path = os.path.join(os.path.expanduser("~"), ".foresight", "claude-code.json")
    _load_config_file(user_config_path, config)

    # Apply environment variable overrides
    for env_name, (key, typ) in ENV_OVERRIDES.items():
        val = os.environ.get(env_name)
        if val is not None:
            cast_val = _cast_env(val, typ)
            if cast_val is not None:
                config[key] = cast_val

    return config


def debug_log(config: dict, *args):
    """Log to stderr if debug mode is enabled."""
    if config.get("debug"):
        print("[Foresight]", *args, file=sys.stderr)
