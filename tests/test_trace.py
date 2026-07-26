"""Per-agent time and token accounting, read from the subagent transcripts.

This parses a harness-internal format that is not ours to control, so the
tests that matter most are the degradation ones: a missing meta file, a
truncated line, an unknown directory. A tracing tool must never be the thing
that fails a run.
"""

import json

from src.eval.trace import (project_slug, read_agent, render_traces,
                            session_dirs, trace_session)


def agent_lines(model="claude-opus-5"):
    return [
        {"timestamp": "2026-07-26T10:00:00.000Z", "type": "user",
         "message": {"role": "user", "content": "go"}},
        {"timestamp": "2026-07-26T10:00:30.000Z", "type": "assistant",
         "message": {"model": model, "content": [
             {"type": "tool_use", "name": "mcp__horizon-draft__run_sql"},
             {"type": "tool_use", "name": "Read"}],
             "usage": {"input_tokens": 100,
                       "cache_read_input_tokens": 9000,
                       "cache_creation_input_tokens": 900,
                       "output_tokens": 250}}},
        {"timestamp": "2026-07-26T10:02:00.000Z", "type": "assistant",
         "message": {"model": model, "content": [
             {"type": "tool_use",
              "name": "mcp__horizon-draft__precheck_candidate"}],
             "usage": {"input_tokens": 50, "cache_read_input_tokens": 11000,
                       "output_tokens": 150}}},
    ]


def make_session(tmp_path, agents=("a1",), meta=True):
    session = tmp_path / "session-uuid"
    subagents = session / "subagents"
    subagents.mkdir(parents=True)
    for name in agents:
        (subagents / f"agent-{name}.jsonl").write_text(
            "\n".join(json.dumps(o) for o in agent_lines()), encoding="utf-8")
        if meta:
            (subagents / f"agent-{name}.meta.json").write_text(
                json.dumps({"agentType": "corpus-explorer",
                            "description": f"Map slice {name}",
                            "spawnDepth": 1}), encoding="utf-8")
    return session


def test_project_slug_dashes_every_non_alphanumeric_character():
    """Claude Code maps each character, not each run: C:\\horizon-scout is
    C--horizon-scout. Collapsing runs points at a directory that never
    exists, which is how this was originally wrong."""
    from pathlib import Path
    assert project_slug(Path(r"C:\horizon-scout")) == "C--horizon-scout"


def test_agent_totals_and_tool_counts(tmp_path):
    session = make_session(tmp_path)
    (trace,) = trace_session(session)
    assert trace.agent_type == "corpus-explorer"
    assert trace.description == "Map slice a1"
    assert trace.turns == 2
    assert trace.output == 400
    assert trace.input_fresh == 150
    assert trace.cache_read == 20000
    assert trace.cache_creation == 900
    assert trace.input_total == 21050
    assert trace.seconds == 120.0        # first message to last
    assert trace.tools["mcp__horizon-draft__run_sql"] == 1
    assert trace.mcp_calls == 2          # run_sql + precheck_candidate, not Read
    assert sum(trace.tools.values()) == 3


def test_since_separates_one_run_from_an_earlier_one(tmp_path):
    session = make_session(tmp_path)
    assert trace_session(session, since="2026-07-26T09:00:00.000Z")
    assert trace_session(session, since="2026-07-27T00:00:00.000Z") == []


def test_since_accepts_local_time_against_utc_transcripts(tmp_path):
    """Transcripts are UTC; a --since typed from `date` is local. Comparing
    them as strings drops every agent on a machine ahead of UTC, which is how
    the first live run traced empty."""
    session = make_session(tmp_path)          # agent ends 10:02:00Z
    from datetime import datetime, timezone
    local_before = (datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)
                    .astimezone().replace(tzinfo=None).isoformat())
    local_after = (datetime(2026, 7, 26, 23, 0, tzinfo=timezone.utc)
                   .astimezone().replace(tzinfo=None).isoformat())
    assert trace_session(session, since=local_before)
    assert trace_session(session, since=local_after) == []


def test_a_missing_meta_file_degrades_to_a_row(tmp_path):
    session = make_session(tmp_path, meta=False)
    (trace,) = trace_session(session)
    assert trace.agent_type == "?" and trace.turns == 2


def test_a_truncated_transcript_line_is_skipped_not_raised(tmp_path):
    session = make_session(tmp_path)
    path = session / "subagents" / "agent-a1.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n{\"half-writ",
                    encoding="utf-8")
    assert read_agent(path).turns == 2


def test_an_empty_transcript_is_not_reported_as_an_agent(tmp_path):
    session = make_session(tmp_path)
    (session / "subagents" / "agent-empty.jsonl").write_text(
        "", encoding="utf-8")
    assert len(trace_session(session)) == 1


def test_session_discovery_ignores_directories_without_subagents(tmp_path):
    make_session(tmp_path)
    (tmp_path / "not-a-session").mkdir()
    found = session_dirs(path=tmp_path.parent, projects=tmp_path.parent)
    assert all((d / "subagents").is_dir() for d in found)


def test_render_is_readable_and_states_what_wall_means(tmp_path):
    session = make_session(tmp_path, agents=("a1", "a2"))
    rendered = render_traces(trace_session(session))
    assert "| corpus-explorer |" in rendered
    assert "2 subagent(s)" in rendered
    assert "Map slice a1" in rendered
    # The caveat has to travel with the number: for the orchestrator row this
    # is session lifetime, not work.
    assert "first-to-last message timestamp" in rendered


def test_render_says_so_when_there_is_nothing_to_trace():
    assert "No agent transcripts found" in render_traces([])
