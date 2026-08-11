import io
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import session_start


def test_session_start_injects_solution_protocol(monkeypatch) -> None:
    monkeypatch.setattr(
        session_start,
        "load_config",
        lambda: {
            "autoRecall": True,
            "autoRetain": True,
            "enableKnowledgeTools": True,
            "debug": False,
            "foresightApiKey": "hsk_test-key",
        },
    )
    monkeypatch.setattr(session_start, "get_api_url", lambda *args, **kwargs: "http://api.test")
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"source": "startup"})))
    monkeypatch.setattr(sys, "stdout", stdout)

    session_start.main()

    output = json.loads(stdout.getvalue())
    context = output["hookSpecificOutput"]["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "<foresight_solution_protocol>" in context
    assert "在调用 Skill、Bash、TaskCreate 或其他行动工具前" in context
    assert "不要只根据候选摘要或 solution-like facts 执行完整工作流" in context


def test_session_start_skips_protocol_when_tools_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        session_start,
        "load_config",
        lambda: {
            "autoRecall": True,
            "autoRetain": True,
            "enableKnowledgeTools": False,
            "debug": False,
            "foresightApiKey": "hsk_test-key",
        },
    )
    monkeypatch.setattr(session_start, "get_api_url", lambda *args, **kwargs: "http://api.test")

    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(sys, "stdout", stdout)

    session_start.main()

    assert stdout.getvalue() == ""
