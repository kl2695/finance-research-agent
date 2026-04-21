"""Replay tests — run extraction + calculation on recorded eval data.

Loads saved tool_log from a previous eval run and verifies that the extraction
pipeline + calculator produces the expected answers. NO API calls needed.

Usage:
    pytest tests/test_replay.py                    # Uses latest eval recording
    pytest tests/test_replay.py --recording FILE   # Uses specific recording

To create/update recordings:
    python eval.py --indices 0 1 2 ...             # Run eval with tool_log saving
    cp results/eval_TIMESTAMP.json tests/fixtures/eval_recording.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.extractor import extract_from_tool_log, _parse_filing_text, llm_match_facts_to_keys
from src.calculator import execute_calculations


FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_DIR.mkdir(exist_ok=True)


def get_recording_path() -> Path:
    """Find the most recent eval recording to use as fixture."""
    # First check for a dedicated fixture file
    fixture_path = FIXTURE_DIR / "eval_recording.json"
    if fixture_path.exists():
        return fixture_path

    # Fall back to the most recent eval result
    results_dir = Path(__file__).parent.parent / "results"
    eval_files = sorted(results_dir.glob("eval_*.json"), reverse=True)
    if eval_files:
        return eval_files[0]

    pytest.skip("No eval recording found. Run: python eval.py first")


def load_recording() -> list[dict]:
    """Load the eval recording results."""
    path = get_recording_path()
    with open(path) as f:
        data = json.load(f)
    return data.get("results", [])


class TestReplayExtraction:
    """Replay the extraction pipeline on recorded tool_logs.

    For each question that PASSED in the eval, verify that:
    1. The extraction pipeline fills the expected values from the tool_log
    2. The calculator produces results consistent with the final answer
    """

    @pytest.fixture(scope="class")
    def recording(self):
        return load_recording()

    def test_passing_questions_have_tool_logs(self, recording):
        """Verify the recording has tool_logs for replay."""
        passing = [r for r in recording if r.get("pass") and r.get("tool_log")]
        if len(passing) == 0:
            pytest.skip("No tool_logs in recording — re-run eval.py to capture them")

    def test_xbrl_extraction_replay(self, recording):
        """XBRL extraction should produce the same values on replay."""
        for r in recording:
            if not r.get("pass") or not r.get("tool_log"):
                continue

            # Get XBRL entries from tool_log
            xbrl_log = [t for t in r["tool_log"] if t.get("tool") == "sec_edgar_financials"]
            if not xbrl_log:
                continue

            # Rebuild state from recorded data
            state = r.get("state", {})
            if not state.get("data_needed"):
                continue

            # Clear values and replay extraction
            replay_state = json.loads(json.dumps(state))
            for dp in replay_state.get("data_needed", {}).values():
                if isinstance(dp, dict):
                    dp["value"] = None
                    dp["source"] = None

            replay_state = extract_from_tool_log(xbrl_log, replay_state)

            # Check that at least some values were extracted
            filled = sum(1 for dp in replay_state["data_needed"].values()
                        if isinstance(dp, dict) and dp.get("value") is not None)
            if filled > 0:
                # Verify extracted values match the recorded state
                for key, dp in replay_state["data_needed"].items():
                    if isinstance(dp, dict) and dp.get("value") is not None:
                        recorded_val = state["data_needed"].get(key, {})
                        if isinstance(recorded_val, dict) and recorded_val.get("source", "").startswith("SEC EDGAR XBRL"):
                            assert dp["value"] == recorded_val["value"], \
                                f"[{r['idx']}] {key}: replay={dp['value']} != recorded={recorded_val['value']}"

    def test_calculator_replay(self, recording):
        """Calculator should produce the same results on replay."""
        for r in recording:
            if not r.get("pass"):
                continue

            state = r.get("state", {})
            calc_steps = state.get("calculation_steps", [])
            if not calc_steps:
                continue

            # Only replay if all inputs are available
            data_needed = state.get("data_needed", {})
            all_inputs_filled = all(
                isinstance(data_needed.get(inp), dict) and data_needed[inp].get("value") is not None
                for step in calc_steps
                for inp in step.get("inputs", [])
                if inp in data_needed  # Skip computed intermediate values
            )

            if not all_inputs_filled:
                continue

            # Clear results and replay
            replay_state = json.loads(json.dumps(state))
            for step in replay_state["calculation_steps"]:
                step["result"] = None
            replay_state["answer"] = {"value": None, "formatted": None, "sources": [], "work_shown": None}

            replay_state = execute_calculations(replay_state)

            # Verify results match
            for orig_step, replay_step in zip(calc_steps, replay_state["calculation_steps"]):
                if orig_step.get("result") is not None and replay_step.get("result") is not None:
                    assert abs(orig_step["result"] - replay_step["result"]) < 0.01, \
                        f"[{r['idx']}] {orig_step['step']}: {orig_step['result']} != {replay_step['result']}"


class TestReplayRegression:
    """Detect regressions by comparing extraction results to the recording."""

    @pytest.fixture(scope="class")
    def recording(self):
        return load_recording()

    def test_no_extraction_regressions(self, recording):
        """Any question that extracted values before should still extract them."""
        regressions = []
        for r in recording:
            if not r.get("tool_log"):
                continue

            state = r.get("state", {})
            data_needed = state.get("data_needed", {})

            # Count how many keys had values from XBRL
            xbrl_filled = sum(1 for dp in data_needed.values()
                             if isinstance(dp, dict)
                             and (dp.get("source") or "").startswith("SEC EDGAR XBRL"))

            if xbrl_filled == 0:
                continue

            # Replay XBRL extraction
            xbrl_log = [t for t in r["tool_log"] if t.get("tool") == "sec_edgar_financials"]
            if not xbrl_log:
                continue

            replay_state = json.loads(json.dumps(state))
            for dp in replay_state.get("data_needed", {}).values():
                if isinstance(dp, dict):
                    dp["value"] = None
                    dp["source"] = None

            replay_state = extract_from_tool_log(xbrl_log, replay_state)

            replay_filled = sum(1 for dp in replay_state["data_needed"].values()
                               if isinstance(dp, dict) and dp.get("value") is not None)

            if replay_filled < xbrl_filled:
                regressions.append(f"[{r['idx']}] XBRL: was {xbrl_filled} filled, now {replay_filled}")

        assert not regressions, f"Extraction regressions:\n" + "\n".join(regressions)
