import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from lib.content import format_memories, format_recall_context, format_solution_candidates, strip_memory_tags


def _solution_candidate() -> dict:
    return {
        "id": "solution-1",
        "title": "MQuantum root-cause analysis",
        "task_intent": "分析指定广告账户的投放异常并输出可验证的根因结论。",
        "original_user_input": "请分析指定广告账户为什么消耗突然下降，并给我一份根因报告。",
        "matched_query": "How do I diagnose ad delivery anomalies?",
        "sub_queries": ["定位消耗下降发生在哪个渠道", "验证根因是否解释关键指标变化"],
        "relevance": 0.9234,
        "approach": "FULL APPROACH SHOULD NOT BE INJECTED",
        "metadata": {
            "skill_refs": ["mquantum-report"],
            "applicability": "广告投放数据归因、异常定位、根因分析",
            "failure_modes": ["Do not use for creative upload tasks"],
            "organization_slug": "ad-ops",
        },
        "tags": ["ads"],
        "layer": "center",
        "source_bank_id": "center-bank",
    }


def test_format_recall_context_includes_facts_and_solution_candidates_without_approach() -> None:
    memories = format_memories(
        [
            {
                "text": "量子平台消耗字段是 cost。",
                "type": "observation",
                "mentioned_at": "2026-06-20T12:00:00Z",
            }
        ]
    )
    candidates = format_solution_candidates([_solution_candidate()])

    context = format_recall_context("preamble", "2026-06-23 10:00 UTC", memories, candidates)

    assert "<foresight_memories>" in context
    assert "<foresight_solution_candidates>" in context
    assert "量子平台消耗字段是 cost。" in context
    assert "Foresight Solution 使用协议：" in context
    assert "在调用 Skill、Bash、TaskCreate 或其他行动工具前" in context
    assert "分析指定广告账户的投放异常" in context
    assert 'agent_knowledge_open_solution(title="MQuantum root-cause analysis")' in context
    assert "如果候选可能对当前任务有帮助" in context
    assert "打开只读取知识，不会执行实际操作" in context
    assert "solution-1" not in context
    assert "center-bank" not in context
    assert "ad-ops" not in context
    assert "请分析指定广告账户为什么消耗突然下降" not in context
    assert "定位消耗下降发生在哪个渠道" not in context
    assert "mquantum-report" not in context
    assert "failure_modes" not in context
    assert "matched_query" not in context
    assert "relevance" not in context
    assert "layer:" not in context
    assert "FULL APPROACH SHOULD NOT BE INJECTED" not in context


def test_format_recall_context_allows_only_solution_candidates() -> None:
    context = format_recall_context(
        "preamble",
        "2026-06-23 10:00 UTC",
        "",
        format_solution_candidates([_solution_candidate()]),
    )

    assert "<foresight_memories>" not in context
    assert "<foresight_solution_candidates>" in context
    assert "MQuantum root-cause analysis" in context
    assert "solution-1" not in context
    assert "FULL APPROACH SHOULD NOT BE INJECTED" not in context


def test_format_recall_context_allows_only_facts() -> None:
    context = format_recall_context(
        "preamble",
        "2026-06-23 10:00 UTC",
        format_memories([{"text": "Existing fact", "type": "observation"}]),
        "",
    )

    assert "Existing fact" in context
    assert "<foresight_solution_candidates>" not in context


def test_format_recall_context_allows_empty_results() -> None:
    context = format_recall_context("preamble", "2026-06-23 10:00 UTC", "", "")

    assert context == ""


def test_format_solution_candidates_skips_invalid_items_and_uses_task_description() -> None:
    assert format_solution_candidates(["not-a-candidate"]) == ""
    assert format_solution_candidates([{"title": "Missing ID"}]) == ""

    formatted = format_solution_candidates(
        [
            {
                "id": "solution-2",
                "title": "No boundary metadata",
                "task_intent": "调试 Solution 召回。",
                "original_user_input": "帮我调试 Solution 召回。",
                "matched_query": "How do I debug recall?",
                "relevance": 0.8,
                "metadata": {},
            }
        ]
    )

    assert "<foresight_solution_candidates>" in formatted
    assert "description: 调试 Solution 召回。" in formatted
    assert "帮我调试 Solution 召回" not in formatted


def test_strip_memory_tags_removes_solution_candidate_blocks() -> None:
    content = (
        "before\n"
        "<foresight_solution_protocol>\n"
        "solution loading protocol\n"
        "</foresight_solution_protocol>\n"
        "<foresight_solution_candidates>\n"
        "agent_knowledge_open_solution(title=\"Example\")\n"
        "</foresight_solution_candidates>\n"
        "after"
    )

    stripped = strip_memory_tags(content).strip()
    assert "solution loading protocol" not in stripped
    assert "agent_knowledge_open_solution" not in stripped
    assert stripped.startswith("before")
    assert stripped.endswith("after")
