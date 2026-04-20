"""Tests for the state dict — the core data structure."""

import pytest
from src.state import (
    create_empty_state,
    make_data_point,
    get_missing_data,
    get_missing_entities,
    get_unfilled_steps,
    is_data_complete,
    render_state_for_prompt,
    validate_state_update,
)


class TestStateCreation:
    def test_empty_state_has_required_keys(self):
        state = create_empty_state("Test plan")
        assert state["plan"] == "Test plan"
        assert state["data_needed"] == {}
        assert state["entities"] == {}
        assert state["calculation_steps"] == []
        assert state["answer"]["value"] is None

    def test_make_data_point(self):
        dp = make_data_point("USD millions", "Revenue FY2024")
        assert dp["value"] is None
        assert dp["unit"] == "USD millions"
        assert dp["attempts"] == []


class TestMissingData:
    def test_get_missing_data(self):
        state = {
            "data_needed": {
                "revenue": {"value": 100, "unit": "B"},
                "cogs": {"value": None, "unit": "B"},
                "inventory": {"value": None, "unit": "M"},
            }
        }
        missing = get_missing_data(state)
        assert "cogs" in missing
        assert "inventory" in missing
        assert "revenue" not in missing

    def test_is_data_complete(self):
        state = {"data_needed": {"a": {"value": 1}, "b": {"value": 2}}}
        assert is_data_complete(state)

        state["data_needed"]["c"] = {"value": None}
        assert not is_data_complete(state)

    def test_get_missing_entities(self):
        state = {
            "entities": {
                "AAPL": {
                    "revenue": {
                        "2022": {"value": 394, "source": "10-K"},
                        "2023": None,
                        "2024": {"value": 391, "source": "10-K"},
                    }
                }
            }
        }
        missing = get_missing_entities(state)
        assert ("AAPL", "revenue", "2023") in missing
        assert len(missing) == 1


class TestRenderState:
    def test_render_shows_found_and_missing(self):
        state = {
            "plan": "Calculate something",
            "clarifications": {"company": "Apple"},
            "data_needed": {
                "revenue": {"value": 391, "unit": "B", "source": "10-K", "confidence": "high", "attempts": []},
                "cogs": {"value": None, "unit": "B", "source": None, "confidence": None, "attempts": []},
            },
            "entities": {},
            "calculation_steps": [
                {"step": "margin", "formula": "revenue - cogs", "result": None},
            ],
            "answer": {"value": None},
        }
        rendered = render_state_for_prompt(state)
        assert "[FOUND] revenue" in rendered
        assert "[MISSING] cogs" in rendered
        assert "[TODO] margin" in rendered

    def test_render_shows_completed_steps(self):
        state = {
            "plan": "Test",
            "clarifications": {},
            "data_needed": {},
            "entities": {},
            "calculation_steps": [
                {"step": "result", "formula": "1 + 1", "result": 2},
            ],
            "answer": {"value": 2},
        }
        rendered = render_state_for_prompt(state)
        assert "[DONE] result = 2" in rendered


class TestStateUpdate:
    def test_validate_merges_data(self):
        current = {
            "data_needed": {
                "revenue": {"value": None, "unit": "B", "source": None, "confidence": None, "attempts": []},
            },
            "entities": {},
            "calculation_steps": [],
            "answer": {"value": None, "formatted": None, "sources": [], "work_shown": None},
            "clarifications": {},
        }
        update = {
            "data_needed": {
                "revenue": {"value": 391, "source": "Apple 10-K", "confidence": "high"},
            }
        }
        merged = validate_state_update(current, update)
        assert merged["data_needed"]["revenue"]["value"] == 391
        assert merged["data_needed"]["revenue"]["source"] == "Apple 10-K"
        assert merged["data_needed"]["revenue"]["unit"] == "B"  # Preserved from original

    def test_validate_merges_entities(self):
        current = {
            "data_needed": {},
            "entities": {"AAPL": {"revenue": {"2024": None}}},
            "calculation_steps": [],
            "answer": {"value": None, "formatted": None, "sources": [], "work_shown": None},
            "clarifications": {},
        }
        update = {
            "entities": {"AAPL": {"revenue": {"2024": {"value": 391, "source": "10-K"}}}},
        }
        merged = validate_state_update(current, update)
        assert merged["entities"]["AAPL"]["revenue"]["2024"]["value"] == 391

    def test_validate_merges_calc_results(self):
        current = {
            "data_needed": {},
            "entities": {},
            "calculation_steps": [{"step": "margin", "formula": "x/y", "result": None}],
            "answer": {"value": None, "formatted": None, "sources": [], "work_shown": None},
            "clarifications": {},
        }
        update = {"calculation_steps": [{"step": "margin", "result": 0.35}]}
        merged = validate_state_update(current, update)
        assert merged["calculation_steps"][0]["result"] == 0.35
