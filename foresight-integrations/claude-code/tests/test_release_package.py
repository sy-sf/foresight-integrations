import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
MARKETPLACE_DIR = PLUGIN_DIR.parents[1]
sys.path.insert(0, str(PLUGIN_DIR / "scripts"))


def test_release_manifests_use_foresight_identity() -> None:
    plugin = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads(
        (MARKETPLACE_DIR / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )

    assert plugin["name"] == "foresight-memory"
    assert re.fullmatch(r"\d+\.\d+\.\d+", plugin["version"])
    changelog = (PLUGIN_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {plugin['version']}" in changelog
    assert marketplace["name"] == "foresight"
    relative_plugin_path = f"./{PLUGIN_DIR.relative_to(MARKETPLACE_DIR).as_posix()}"
    assert marketplace["plugins"] == [
        {
            "name": "foresight-memory",
            "description": "Trajectory collection and reusable knowledge for Claude Code via Foresight",
            "source": relative_plugin_path,
        }
    ]


def test_release_registers_foresight_mcp_server() -> None:
    mcp = json.loads((PLUGIN_DIR / ".mcp.json").read_text(encoding="utf-8"))

    assert mcp == {
        "mcpServers": {
            "foresight": {
                "command": "bash",
                "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/run_mcp.sh"],
            }
        }
    }

    assert (PLUGIN_DIR / "requirements.txt").read_text(encoding="utf-8").strip() == "mcp==1.25.0"


def test_defaults_have_one_source_and_require_external_foresight() -> None:
    from lib.config import DEFAULTS

    assert not (PLUGIN_DIR / "settings.json").exists()
    assert DEFAULTS["foresightApiUrl"] is None
    assert DEFAULTS["foresightApiKey"] is None
    for removed_key in (
        "apiPort",
        "daemonIdleTimeout",
        "embedVersion",
        "embedPackagePath",
        "llmProvider",
        "llmModel",
        "llmApiKeyEnv",
        "retainMode",
        "retainEveryNTurns",
        "retainOverlapTurns",
        "retainRoles",
        "retainToolCalls",
        "retainContext",
        "retainTags",
        "retainMetadata",
    ):
        assert removed_key not in DEFAULTS


def test_config_loads_foresight_user_path_and_environment(monkeypatch, tmp_path) -> None:
    from lib.config import load_config

    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".foresight"
    config_dir.mkdir()
    (config_dir / "claude-code.json").write_text(
        json.dumps(
            {
                "foresightApiUrl": "https://user.example.com",
                "foresightApiKey": "hsk_user-key",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FORESIGHT_API_URL", "https://env.example.com")

    config = load_config()

    assert config["foresightApiUrl"] == "https://env.example.com"
    assert config["foresightApiKey"] == "hsk_user-key"


def test_connection_requires_deployed_foresight_api() -> None:
    from lib.connection import get_api_url

    with pytest.raises(RuntimeError, match="Foresight API URL is required"):
        get_api_url({})

    with pytest.raises(RuntimeError, match=r"valid http\(s\) URL"):
        get_api_url({"foresightApiUrl": "file:///tmp/foresight"})

    assert get_api_url({"foresightApiUrl": "https://foresight.example.com/"}) == (
        "https://foresight.example.com"
    )


def test_client_requires_personal_hsk_api_key() -> None:
    from lib.client import ForesightClient

    with pytest.raises(ValueError, match="API key is required"):
        ForesightClient("https://foresight.example.com")

    with pytest.raises(ValueError, match="hsk_ API key"):
        ForesightClient("https://foresight.example.com", "legacy-token")

    client = ForesightClient("https://foresight.example.com", "hsk_test-key")
    assert client._headers()["Authorization"] == "Bearer hsk_test-key"
