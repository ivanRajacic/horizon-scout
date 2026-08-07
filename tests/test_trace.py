"""Per-agent time and token accounting, read from the subagent transcripts.

This parses a harness-internal format that is not ours to control, so the
tests that matter most are the degradation ones: a missing meta file, a
truncated line, an unknown directory. A tracing tool must never be the thing
that fails a run.
"""

import json

from src.eval.trace import (instruction_text, project_slug, read_agent,
                            render_traces, session_dirs, slot_of,
                            trace_session)


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
    # The caveat has to travel with the number: active is work, span is
    # lifetime, and for the orchestrator row span is not work at all.
    assert "**active** sums the steps" in rendered
    assert "**span** is first-to-last" in rendered


# --- steps: what a warm agent did under each instruction -------------------

RELAY = ("The coordinator sent a message while you were working: "
         "Rectification round for hyb-09.")


def warm_lines():
    """A drafter that drafts, waits ten minutes, then gets a fix round.

    The relay carries `isMeta` AND `origin.kind == "coordinator"` - the real
    shape from the 2026-07-25 batch, and the reason the meta flag alone must
    not be the filter.
    """
    return [
        {"timestamp": "2026-07-26T10:00:00.000Z", "type": "user",
         "message": {"role": "user", "content": "Draft exactly one question."}},
        {"timestamp": "2026-07-26T10:01:00.000Z", "type": "assistant",
         "message": {"model": "claude-opus-5", "content": [
             {"type": "tool_use", "name": "mcp__horizon-draft__run_sql"}],
             "usage": {"input_tokens": 10, "output_tokens": 100}}},
        {"timestamp": "2026-07-26T10:01:05.000Z", "type": "user",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "content": "rows"}]}},
        {"timestamp": "2026-07-26T10:02:00.000Z", "type": "assistant",
         "message": {"model": "claude-opus-5", "content": [
             {"type": "text", "text": "RECORD ..."}],
             "usage": {"input_tokens": 20, "output_tokens": 200}}},
        # ten idle minutes: warm, waiting on the bus, doing nothing
        {"timestamp": "2026-07-26T10:12:00.000Z", "type": "user",
         "isMeta": True, "origin": {"kind": "coordinator"},
         "message": {"role": "user", "content": RELAY}},
        {"timestamp": "2026-07-26T10:13:00.000Z", "type": "assistant",
         "message": {"model": "claude-opus-5", "content": [
             {"type": "text", "text": "fixed"}],
             "usage": {"input_tokens": 30, "output_tokens": 300}}},
    ]


def warm_session(tmp_path, description="Draft hyb-09 musicology MSCA"):
    session = tmp_path / "warm"
    subagents = session / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-w1.jsonl").write_text(
        "\n".join(json.dumps(o) for o in warm_lines()), encoding="utf-8")
    (subagents / "agent-w1.meta.json").write_text(
        json.dumps({"agentType": "question-drafter",
                    "description": description}), encoding="utf-8")
    return session


def test_a_relayed_message_opens_a_step_despite_being_meta(tmp_path):
    """The trap this exists to avoid: relays are flagged isMeta, so filtering
    on that flag collapses a warm agent to one step and hides every round
    after the first."""
    (trace,) = trace_session(warm_session(tmp_path))
    assert len(trace.steps) == 2
    assert trace.steps[1].label.startswith("Rectification round")
    assert "coordinator sent a message" not in trace.steps[1].label


def test_active_time_excludes_the_wait_between_rounds(tmp_path):
    (trace,) = trace_session(warm_session(tmp_path))
    assert trace.seconds == 780.0            # 10:00:00 -> 10:13:00, idle in it
    assert trace.active_seconds == 180.0     # 2m drafting + 1m fixing
    assert [s.seconds for s in trace.steps] == [120.0, 60.0]


def test_a_step_carries_its_own_tokens_and_tool_calls(tmp_path):
    (trace,) = trace_session(warm_session(tmp_path))
    first, second = trace.steps
    assert (first.spend.output, second.spend.output) == (300, 300)
    assert first.spend.tools == {"mcp__horizon-draft__run_sql": 1}
    assert second.spend.tools == {}
    # The agent total is still the sum of its steps.
    assert trace.output == first.spend.output + second.spend.output


def test_tool_results_do_not_open_steps(tmp_path):
    """A tool result is a user entry too. Treating it as an instruction would
    make every tool call its own 'step' and the breakdown meaningless."""
    assert instruction_text(
        {"type": "user",
         "message": {"content": [{"type": "tool_result", "content": "x"}]}}) is None


def test_an_injected_note_that_is_not_a_relay_opens_no_step():
    assert instruction_text(
        {"type": "user", "isMeta": True,
         "message": {"content": "<system-reminder>be good</system-reminder>"}}
    ) is None


def test_slot_id_is_read_from_the_spawn_description(tmp_path):
    (trace,) = trace_session(warm_session(tmp_path))
    assert slot_of(trace) == "hyb-09"
    (other,) = trace_session(
        warm_session(tmp_path / "b", description="Attack draft SQL-18 round 2"))
    assert slot_of(other) == "sql-18"


def test_no_slot_id_is_not_a_slot(tmp_path):
    (trace,) = trace_session(
        warm_session(tmp_path, description="Explore slice s01"))
    assert slot_of(trace) is None


def test_scratch_slot_id_with_letter_infix_is_traced(tmp_path):
    # The Sonnet probe's scratch ids (`vec-s38`) must roll up by slot like
    # any other - dropping them silently removed all 28 probe agents from
    # the by-slot table.
    (trace,) = trace_session(
        warm_session(tmp_path, description="Draft vec-s38 candidate 1"))
    assert slot_of(trace) == "vec-s38"


def test_rollups_add_up_the_agents_that_made_one_question(tmp_path):
    session = warm_session(tmp_path)
    rendered = render_traces(trace_session(session), steps=True)
    assert "By slot" in rendered and "| hyb-09 |" in rendered
    assert "## Per step" in rendered
    assert "Rectification round" in rendered


def test_steps_are_off_by_default(tmp_path):
    assert "## Per step" not in render_traces(
        trace_session(warm_session(tmp_path)))


def test_render_says_so_when_there_is_nothing_to_trace():
    assert "No agent transcripts found" in render_traces([])
