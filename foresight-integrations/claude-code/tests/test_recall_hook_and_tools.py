import importlib.util
import io
import json
import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PLUGIN_DIR / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import recall
import retain
from lib.client import ForesightClient
from lib.solution_candidates import OPENED_SOLUTIONS_STATE, SOLUTION_CANDIDATES_STATE


def test_recall_hook_formats_solution_candidates_without_approach(monkeypatch) -> None:
    captured_calls = []
    captured_states = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def recall(self, **kwargs):
            captured_calls.append(kwargs)
            return {
                "results": [{"text": "Use cost for spend.", "type": "observation"}],
                "solutions": [
                    {
                        "id": "solution-1",
                        "title": "MQuantum root-cause analysis",
                        "task_intent": "分析广告投放异常并输出可验证的根因结论。",
                        "original_user_input": "请分析投放异常并给出根因报告。",
                        "matched_query": "How to debug ad spend anomalies?",
                        "relevance": 0.91,
                        "approach": "FULL APPROACH SHOULD NOT LEAK",
                        "metadata": {
                            "skill_refs": ["mquantum-report"],
                            "applicability": "广告投放异常根因分析",
                            "failure_modes": ["Not for upload operations"],
                        },
                        "layer": "user",
                        "source_bank_id": "claude_code",
                    }
                ],
            }

    config = {
        "autoRecall": True,
        "recallMaxTokens": 1024,
        "recallBudget": "mid",
        "recallTypes": ["observation", "solution"],
        "recallSolutionDetail": "candidate",
        "recallContextTurns": 1,
        "recallMaxQueryChars": 800,
        "recallRoles": ["user", "assistant"],
        "recallPromptPreamble": "preamble",
    }

    monkeypatch.setattr(recall, "load_config", lambda: config)
    monkeypatch.setattr(recall, "get_api_url", lambda *args, **kwargs: "http://api.test")
    monkeypatch.setattr(recall, "derive_bank_id", lambda *args, **kwargs: "claude_code")
    monkeypatch.setattr(recall, "ensure_bank_mission", lambda *args, **kwargs: None)
    monkeypatch.setattr(recall, "ForesightClient", FakeClient)
    monkeypatch.setattr(recall, "write_state", lambda name, state: captured_states.__setitem__(name, state))

    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": "请分析投放异常"})))
    monkeypatch.setattr(sys, "stdout", stdout)

    recall.main()

    output = json.loads(stdout.getvalue())
    context = output["hookSpecificOutput"]["additionalContext"]
    assert captured_calls[0]["types"] == ["observation", "solution"]
    assert captured_calls[0]["solution_detail"] == "candidate"
    assert captured_calls[0]["current_user_input"] == "请分析投放异常"
    assert captured_states[recall.LAST_RECALL_STATE]["result_count"] == 1
    assert captured_states[recall.LAST_RECALL_STATE]["solution_count"] == 1
    assert captured_states[recall.LAST_RECALL_STATE]["solution_route_count"] == 1
    assert captured_states[SOLUTION_CANDIDATES_STATE]["candidates"] == [
        {
            "title": "MQuantum root-cause analysis",
            "solution_id": "solution-1",
            "source_bank_id": "claude_code",
            "organization_slug": "",
            "relevance": 0.91,
            "layer": "user",
        }
    ]
    assert "Use cost for spend." in context
    assert "<foresight_solution_candidates>" in context
    assert "在调用 Skill、Bash、TaskCreate 或其他行动工具前" in context
    assert "如果候选可能对当前任务有帮助" in context
    assert "打开只读取知识，不会执行实际操作" in context
    assert "分析广告投放异常并输出可验证的根因结论" in context
    assert 'agent_knowledge_open_solution(title="MQuantum root-cause analysis")' in context
    assert "solution-1" not in context
    assert "请分析投放异常并给出根因报告" not in context
    assert "How to debug ad spend anomalies?" not in context
    assert "mquantum-report" not in context
    assert "Not for upload operations" not in context
    assert "FULL APPROACH SHOULD NOT LEAK" not in context


def test_client_recall_sends_types_and_solution_detail() -> None:
    captured = {}
    client = ForesightClient("http://api.test", "hsk_test-key")

    def fake_request(method, path, body=None, timeout=10):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        captured["timeout"] = timeout
        return {"results": [], "solutions": []}

    client.request = fake_request

    client.recall(
        bank_id="bank/a",
        query="debug ad spend",
        current_user_input="Please debug ad spend and produce a diagnosis.",
        types=["observation", "solution"],
        solution_detail="candidate",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/me/knowledge/recall"
    assert captured["body"]["types"] == ["observation", "solution"]
    assert captured["body"]["solution_detail"] == "candidate"
    assert captured["body"]["current_user_input"] == "Please debug ad spend and produce a diagnosis."


def test_client_upsert_document_uses_canonical_document_endpoint() -> None:
    captured = {}
    client = ForesightClient("http://api.test", "hsk_test-key")

    def fake_request(method, path, body=None, timeout=10):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        captured["timeout"] = timeout
        return {"queued": True}

    client.request = fake_request

    client.upsert_document(
        bank_id="bank/a",
        document_id="session/1",
        content='[{"role":"user","content":"hello"}]',
        source_session_id="session-1",
        metadata={"project": "demo"},
        tags=["session-1"],
        process_now=True,
    )

    assert captured["method"] == "PUT"
    assert captured["path"] == "/v1/me/knowledge/documents/session%2F1"
    assert captured["body"]["source_harness"] == "claude-code"
    assert captured["body"]["source_session_id"] == "session-1"
    assert captured["body"]["source_format"] == "claude_code_transcript"
    assert json.loads(captured["body"]["content"])[0]["content"] == "hello"
    assert captured["body"]["process_now"] is True


def test_client_get_solution_sends_agent_detail() -> None:
    captured = {}
    client = ForesightClient("http://api.test", "hsk_test-key")

    def fake_request(method, path, body=None, timeout=10):
        captured["method"] = method
        captured["path"] = path
        captured["timeout"] = timeout
        return {}

    client.request = fake_request

    client.get_solution("bank/a", "solution/1", detail="agent")
    assert captured["method"] == "GET"
    assert captured["path"] == "/v1/me/knowledge/solutions/solution%2F1?detail=agent"


def test_client_get_center_solution_sends_agent_detail() -> None:
    captured = {}
    client = ForesightClient("http://api.test", "hsk_test-key")

    def fake_request(method, path, body=None, timeout=10):
        captured["method"] = method
        captured["path"] = path
        captured["timeout"] = timeout
        return {}

    client.request = fake_request

    client.get_center_solution("solution/1", "ad ops", detail="agent")
    assert captured["method"] == "GET"
    assert captured["path"] == "/v1/default/center/solutions/solution%2F1?organization_slug=ad+ops&detail=agent"


def _load_mcp_server_with_fake_dependencies(monkeypatch):
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")

    class FakeFastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self):
            def decorator(func):
                return func

            return decorator

        def run(self, transport="stdio"):
            return None

    fastmcp_module.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp", types.ModuleType("mcp"))
    monkeypatch.setitem(sys.modules, "mcp.server", types.ModuleType("mcp.server"))
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    import lib.bank
    import lib.client
    import lib.config
    import lib.connection

    class FakeClient:
        calls = []

        def __init__(self, *args, **kwargs):
            pass

        def recall(self, **kwargs):
            self.calls.append(("recall", kwargs))
            return {"results": [], "solutions": []}

        def get_solution(self, **kwargs):
            self.calls.append(("get_solution", kwargs))
            if kwargs["solution_id"] == "missing":
                raise RuntimeError("HTTP 404 from /solutions/missing")
            return {
                "id": kwargs["solution_id"],
                "title": "Loaded solution",
                "approach": "full methodology",
                "queries": [{"text": "debug ad spend"}],
                "metadata": {
                    "applicability": "use for ad anomaly analysis",
                    "failure_modes": ["do not skip field discovery"],
                    "skill_refs": ["mquantum-report"],
                    "evidence": "validated by prior trajectory",
                    "internal_episodes": "SHOULD NOT LEAK",
                },
                "source_document_ids": ["internal-doc-id"],
                "content_hash": "internal-hash",
                "tags": ["ads"],
                "confidence": 0.9,
                "layer": "user",
                "source_bank_id": None,
                "organization_slug": None,
            }

        def get_center_solution(self, **kwargs):
            self.calls.append(("get_center_solution", kwargs))
            return {
                "id": kwargs["solution_id"],
                "organization_slug": kwargs["organization_slug"],
                "approach": "center methodology",
                "metadata": {"internal_episodes": "SHOULD NOT LEAK"},
            }

    monkeypatch.setattr(
        lib.config,
        "load_config",
        lambda: {
            "enableKnowledgeTools": True,
            "foresightApiUrl": "http://api.test",
            "foresightApiKey": "hsk_test-key",
        },
    )
    monkeypatch.setattr(lib.config, "debug_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(lib.connection, "get_api_url", lambda *args, **kwargs: "http://api.test")
    monkeypatch.setattr(lib.bank, "derive_bank_id", lambda *args, **kwargs: "default-bank")
    monkeypatch.setattr(lib.client, "ForesightClient", FakeClient)

    module_path = SCRIPT_DIR / "mcp_server.py"
    spec = importlib.util.spec_from_file_location("mcp_server_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module._client


def test_mcp_get_solution_routes_personal_center_and_not_found(monkeypatch) -> None:
    module, fake_client = _load_mcp_server_with_fake_dependencies(monkeypatch)

    user_result = json.loads(module.agent_knowledge_get_solution("solution-1"))
    center_result = json.loads(module.agent_knowledge_get_solution("solution-2", organization_slug="ad-ops"))
    missing_result = json.loads(module.agent_knowledge_get_solution("missing"))

    assert user_result["approach"] == "full methodology"
    assert center_result["organization_slug"] == "ad-ops"
    assert "HTTP 404" in missing_result["error"]
    assert fake_client.calls[0] == (
        "get_solution",
        {"bank_id": "default-bank", "solution_id": "solution-1", "detail": "agent", "timeout": 10},
    )
    assert fake_client.calls[1] == (
        "get_center_solution",
        {"solution_id": "solution-2", "organization_slug": "ad-ops", "detail": "agent", "timeout": 10},
    )


def test_mcp_open_solution_by_title_routes_personal_space(monkeypatch) -> None:
    module, fake_client = _load_mcp_server_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(
        module,
        "read_state",
        lambda name, default=None: {
            "candidates": [
                {
                    "title": "MQuantum root-cause analysis",
                    "solution_id": "solution-1",
                    "source_bank_id": "bank-a",
                    "organization_slug": "",
                    "relevance": 0.9,
                    "layer": "user",
                }
            ]
        },
    )

    result = json.loads(module.agent_knowledge_open_solution("MQuantum root-cause analysis"))

    assert result["approach"] == "full methodology"
    assert result["queries"] == [{"text": "debug ad spend"}]
    assert result["metadata"] == {
        "applicability": "use for ad anomaly analysis",
        "failure_modes": ["do not skip field discovery"],
        "skill_refs": ["mquantum-report"],
        "evidence": "validated by prior trajectory",
    }
    assert "internal_episodes" not in json.dumps(result)
    assert "source_document_ids" not in result
    assert "content_hash" not in result
    assert fake_client.calls[-1] == (
        "get_solution",
        {"bank_id": "default-bank", "solution_id": "solution-1", "detail": "agent", "timeout": 10},
    )


def test_mcp_open_solution_by_title_records_opened_state(monkeypatch) -> None:
    module, _fake_client = _load_mcp_server_with_fake_dependencies(monkeypatch)
    states = {
        SOLUTION_CANDIDATES_STATE: {
            "session_id": "session-1",
            "candidates": [
                {
                    "title": "MQuantum root-cause analysis",
                    "solution_id": "solution-1",
                    "source_bank_id": "bank-a",
                    "organization_slug": "",
                    "relevance": 0.9,
                    "layer": "user",
                }
            ],
        },
        OPENED_SOLUTIONS_STATE: {},
    }
    monkeypatch.setattr(module, "read_state", lambda name, default=None: states.get(name, default))
    monkeypatch.setattr(module, "write_state", lambda name, state: states.__setitem__(name, state))

    result = json.loads(module.agent_knowledge_open_solution("MQuantum root-cause analysis"))

    assert result["approach"] == "full methodology"
    opened = states[OPENED_SOLUTIONS_STATE]["sessions"]["session-1"][0]
    assert opened["title"] == "MQuantum root-cause analysis"
    assert opened["solution_id"] == "solution-1"
    assert opened["source_bank_id"] == "bank-a"
    assert opened["layer"] == "user"
    assert opened["opened_at"]


def test_mcp_open_solution_by_title_routes_center_candidate(monkeypatch) -> None:
    module, fake_client = _load_mcp_server_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(
        module,
        "read_state",
        lambda name, default=None: {
            "candidates": [
                {
                    "title": "Center playbook",
                    "solution_id": "solution-center",
                    "source_bank_id": "center-bank",
                    "organization_slug": "ad-ops",
                    "relevance": 0.88,
                    "layer": "center",
                }
            ]
        },
    )

    result = json.loads(module.agent_knowledge_open_solution("Center playbook"))

    assert result["approach"] == "center methodology"
    assert result["metadata"] == {}
    assert "internal_episodes" not in json.dumps(result)
    assert fake_client.calls[-1] == (
        "get_center_solution",
        {"solution_id": "solution-center", "organization_slug": "ad-ops", "detail": "agent", "timeout": 10},
    )


def test_mcp_open_solution_by_title_selects_highest_relevance_duplicate(monkeypatch) -> None:
    module, fake_client = _load_mcp_server_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(
        module,
        "read_state",
        lambda name, default=None: {
            "candidates": [
                {
                    "title": "Duplicate title",
                    "solution_id": "low",
                    "source_bank_id": "bank-low",
                    "organization_slug": "",
                    "relevance": 0.4,
                    "layer": "user",
                },
                {
                    "title": "Duplicate title",
                    "solution_id": "high",
                    "source_bank_id": "bank-high",
                    "organization_slug": "",
                    "relevance": 0.95,
                    "layer": "user",
                },
            ]
        },
    )

    result = json.loads(module.agent_knowledge_open_solution("Duplicate title"))

    assert result["approach"] == "full methodology"
    assert fake_client.calls[-1] == (
        "get_solution",
        {"bank_id": "default-bank", "solution_id": "high", "detail": "agent", "timeout": 10},
    )


def test_mcp_open_solution_by_title_handles_empty_and_missing_state(monkeypatch) -> None:
    module, _fake_client = _load_mcp_server_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(module, "read_state", lambda name, default=None: {"candidates": []})

    empty_result = json.loads(module.agent_knowledge_open_solution("Missing"))

    monkeypatch.setattr(
        module,
        "read_state",
        lambda name, default=None: {
            "candidates": [
                {
                    "title": "Available title",
                    "solution_id": "solution-1",
                    "source_bank_id": "bank-a",
                    "organization_slug": "",
                    "relevance": 0.9,
                    "layer": "user",
                }
            ]
        },
    )
    missing_result = json.loads(module.agent_knowledge_open_solution("Missing"))

    assert empty_result["error"] == "当前提示词中没有可加载的 solution 候选"
    assert missing_result["error"] == "当前 solution 候选中没有匹配该标题的条目"
    assert missing_result["available_titles"] == ["Available title"]


def test_mcp_recall_accepts_solution_type(monkeypatch) -> None:
    module, fake_client = _load_mcp_server_with_fake_dependencies(monkeypatch)

    result = json.loads(module.agent_knowledge_recall("debug ad spend", types="observation,solution"))

    assert result == {"results": [], "solutions": []}
    assert fake_client.calls[-1] == (
        "recall",
        {
            "bank_id": "default-bank",
            "query": "debug ad spend",
            "current_user_input": "debug ad spend",
            "max_tokens": 1024,
            "budget": "mid",
            "types": ["observation", "solution"],
            "timeout": 10,
        },
    )


def test_retain_keeps_opened_solutions_until_final_snapshot(monkeypatch, tmp_path) -> None:
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps({"role": "user", "content": "debug ad spend"}),
                json.dumps({"role": "assistant", "content": "used solution"}),
            ]
        ),
        encoding="utf-8",
    )
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def upsert_document(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    opened_record = {
        "session_id": "session-1",
        "title": "MQuantum root-cause analysis",
        "solution_id": "solution-1",
        "source_bank_id": "bank-a",
        "organization_slug": "",
        "relevance": 0.91,
        "layer": "user",
        "opened_at": "2026-06-24T01:02:03Z",
    }
    states = {OPENED_SOLUTIONS_STATE: {"sessions": {"session-1": [opened_record]}}}
    config = {
        "autoRetain": True,
        "requestTimeoutSeconds": 10,
    }

    monkeypatch.setattr(retain, "load_config", lambda: config)
    monkeypatch.setattr(retain, "debug_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "get_api_url", lambda *args, **kwargs: "http://api.test")
    monkeypatch.setattr(retain, "derive_bank_id", lambda *args, **kwargs: "bank-a")
    monkeypatch.setattr(retain, "ensure_bank_mission", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "ForesightClient", FakeClient)
    monkeypatch.setattr(retain, "track_retention", lambda *args, **kwargs: (0, False))
    monkeypatch.setattr(retain, "mark_session_retained", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "read_state", lambda name, default=None: states.get(name, default))
    monkeypatch.setattr(retain, "write_state", lambda name, state: states.__setitem__(name, state))

    retain.run_retain(
        {
            "session_id": "session-1",
            "transcript_path": str(transcript_path),
            "cwd": str(tmp_path),
        }
    )

    metadata = captured["metadata"]
    opened = json.loads(metadata["hindsight_opened_solutions_json"])
    assert opened == [opened_record]
    assert captured["process_now"] is False
    assert states[OPENED_SOLUTIONS_STATE] == {"sessions": {"session-1": [opened_record]}}

    retain.run_retain(
        {
            "session_id": "session-1",
            "transcript_path": str(transcript_path),
            "cwd": str(tmp_path),
        },
        final=True,
    )

    assert captured["process_now"] is False
    assert states[OPENED_SOLUTIONS_STATE] == {"sessions": {}}
