import io
import json
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PLUGIN_DIR / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import retain
import session_end
from lib.solution_candidates import OPENED_SOLUTIONS_STATE


def _write_transcript(tmp_path: Path) -> Path:
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps({"role": "user", "content": "first request"}),
                json.dumps({"role": "assistant", "content": "first answer"}),
            ]
        ),
        encoding="utf-8",
    )
    return transcript_path


def _document_config(**overrides):
    config = {
        "autoRetain": True,
        "requestTimeoutSeconds": 10,
    }
    config.update(overrides)
    return config


def test_retain_submits_full_session_document_snapshot(monkeypatch, tmp_path) -> None:
    transcript_path = _write_transcript(tmp_path)
    captured = {}
    marked = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def upsert_document(self, **kwargs):
            captured.update(kwargs)
            return {"processing_status": "scheduled"}

    monkeypatch.setattr(retain, "load_config", lambda: _document_config())
    monkeypatch.setattr(retain, "debug_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "get_api_url", lambda *args, **kwargs: "http://api.test")
    monkeypatch.setattr(retain, "derive_bank_id", lambda *args, **kwargs: "bank-a")
    monkeypatch.setattr(retain, "ensure_bank_mission", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "ForesightClient", FakeClient)
    monkeypatch.setattr(retain, "track_retention", lambda session_id, count: (0, False))
    monkeypatch.setattr(retain, "read_state", lambda name, default=None: default)
    monkeypatch.setattr(retain, "mark_session_retained", lambda session_id: marked.append(session_id))

    retain.run_retain(
        {
            "session_id": "session-1",
            "transcript_path": str(transcript_path),
            "cwd": str(tmp_path),
        }
    )

    assert captured["bank_id"] == "bank-a"
    assert captured["document_id"] == "session-1"
    assert captured["source_session_id"] == "session-1"
    assert json.loads(captured["content"])[0]["content"] == "first request"
    assert captured["metadata"]["message_count"] == "2"
    assert "retained_at" not in captured["metadata"]
    assert captured["tags"] is None
    assert captured["process_now"] is False
    assert marked == ["session-1"]


def test_every_stop_upserts_latest_snapshot_without_forcing_processing(monkeypatch, tmp_path) -> None:
    transcript_path = _write_transcript(tmp_path)
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def upsert_document(self, **kwargs):
            calls.append(kwargs)
            return {"processing_status": "scheduled"}

    monkeypatch.setattr(retain, "load_config", lambda: _document_config())
    monkeypatch.setattr(retain, "debug_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "get_api_url", lambda *args, **kwargs: "http://api.test")
    monkeypatch.setattr(retain, "derive_bank_id", lambda *args, **kwargs: "bank-a")
    monkeypatch.setattr(retain, "ensure_bank_mission", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "ForesightClient", FakeClient)
    monkeypatch.setattr(retain, "track_retention", lambda session_id, count: (0, False))
    monkeypatch.setattr(retain, "read_state", lambda name, default=None: default)
    monkeypatch.setattr(retain, "mark_session_retained", lambda session_id: None)

    hook_input = {"session_id": "session-1", "transcript_path": str(transcript_path)}
    retain.run_retain(hook_input)

    with transcript_path.open("a", encoding="utf-8") as transcript:
        transcript.write("\n" + json.dumps({"role": "user", "content": "second request"}))
        transcript.write("\n" + json.dumps({"role": "assistant", "content": "second answer"}))
    retain.run_retain(hook_input)

    assert len(calls) == 2
    assert [call["document_id"] for call in calls] == ["session-1", "session-1"]
    assert [call["process_now"] for call in calls] == [False, False]
    assert len(json.loads(calls[0]["content"])) == 2
    assert len(json.loads(calls[1]["content"])) == 4


def test_final_retain_keeps_idle_processing_and_clears_opened_solution_state(monkeypatch, tmp_path) -> None:
    transcript_path = _write_transcript(tmp_path)
    captured = {}
    marked = []
    writes = {}
    opened_record = {
        "session_id": "session-1",
        "title": "Debug report",
        "solution_id": "solution-1",
    }
    states = {OPENED_SOLUTIONS_STATE: {"sessions": {"session-1": [opened_record]}}}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def upsert_document(self, **kwargs):
            captured.update(kwargs)
            return {"processing_status": "scheduled"}

    monkeypatch.setattr(retain, "load_config", lambda: _document_config())
    monkeypatch.setattr(retain, "debug_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "get_api_url", lambda *args, **kwargs: "http://api.test")
    monkeypatch.setattr(retain, "derive_bank_id", lambda *args, **kwargs: "bank-a")
    monkeypatch.setattr(retain, "ensure_bank_mission", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "ForesightClient", FakeClient)
    monkeypatch.setattr(retain, "track_retention", lambda session_id, count: (0, False))
    monkeypatch.setattr(retain, "read_state", lambda name, default=None: states.get(name, default))
    monkeypatch.setattr(retain, "write_state", lambda name, state: writes.__setitem__(name, state))
    monkeypatch.setattr(retain, "mark_session_retained", lambda session_id: marked.append(session_id))

    retain.run_retain(
        {
            "session_id": "session-1",
            "transcript_path": str(transcript_path),
            "cwd": str(tmp_path),
        },
        final=True,
    )

    opened = json.loads(captured["metadata"]["hindsight_opened_solutions_json"])
    assert opened == [opened_record]
    assert captured["process_now"] is False
    assert marked == ["session-1"]
    assert writes[OPENED_SOLUTIONS_STATE] == {"sessions": {}}


def test_retain_upserts_full_stable_compaction_snapshot(monkeypatch, tmp_path) -> None:
    transcript_path = tmp_path / "chunked.jsonl"
    messages = [
        {"role": role, "content": f"message-{index}"}
        for index, role in enumerate(["user", "assistant"] * 4)
    ]
    transcript_path.write_text(
        "\n".join(json.dumps(message) for message in messages),
        encoding="utf-8",
    )
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def upsert_document(self, **kwargs):
            captured.update(kwargs)
            return {"processing_status": "scheduled"}

    monkeypatch.setattr(retain, "load_config", lambda: _document_config())
    monkeypatch.setattr(retain, "debug_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "get_api_url", lambda *args, **kwargs: "http://api.test")
    monkeypatch.setattr(retain, "derive_bank_id", lambda *args, **kwargs: "bank-a")
    monkeypatch.setattr(retain, "ensure_bank_mission", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "ForesightClient", FakeClient)
    monkeypatch.setattr(retain, "track_retention", lambda session_id, count: (3, False))
    monkeypatch.setattr(retain, "read_state", lambda name, default=None: default)
    monkeypatch.setattr(retain, "mark_session_retained", lambda session_id: None)

    retain.run_retain(
        {
            "session_id": "session-1",
            "transcript_path": str(transcript_path),
            "cwd": str(tmp_path),
        }
    )

    assert captured["document_id"] == "session-1-c3"
    assert captured["source_segment_index"] == 3
    assert json.loads(captured["content"]) == messages
    assert captured["metadata"]["message_count"] == "8"


def test_session_end_submits_final_document_without_forcing_processing(monkeypatch) -> None:
    calls = []
    config = {"autoRetain": True}

    monkeypatch.setattr(session_end, "load_config", lambda: config)
    monkeypatch.setattr(session_end, "debug_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(retain, "run_retain", lambda hook_input, final=False: calls.append((hook_input, final)))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "session-1", "transcript_path": "/tmp/session.jsonl"})),
    )

    session_end.main()

    assert calls == [({"session_id": "session-1", "transcript_path": "/tmp/session.jsonl"}, True)]
