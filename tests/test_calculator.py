"""Tests for the calculator — deterministic Python execution."""

import pytest
from src.calculator import execute_calculations, _to_number, _eval_formula


class TestToNumber:
    def test_int(self):
        assert _to_number(100) == 100.0

    def test_float(self):
        assert _to_number(3.14) == 3.14

    def test_string_number(self):
        assert _to_number("100") == 100.0

    def test_string_with_commas(self):
        assert _to_number("14,060") == 14060.0

    def test_string_with_dollar(self):
        assert _to_number("$14,060") == 14060.0

    def test_string_billions(self):
        assert _to_number("391B") == 391e9

    def test_string_millions(self):
        assert _to_number("14M") == 14e6

    def test_string_percent(self):
        assert _to_number("55.8%") == 55.8

    def test_string_non_numeric_returns_none(self):
        """P83: non-numeric strings should return None, not crash."""
        assert _to_number("FY2024") is None
        assert _to_number("not found") is None
        assert _to_number("N/A") is None
        assert _to_number("") is None
        assert _to_number(None) is None

    def test_none_does_not_crash_calculator(self):
        """P83: calculator should skip keys with non-numeric values."""
        state = {
            "data_needed": {
                "revenue": {"value": 1000},
                "bad_value": {"value": "FY2024"},  # String that can't convert
            },
            "entities": {},
            "calculation_steps": [
                {"step": "test", "formula": "revenue * 2",
                 "inputs": ["revenue"], "result": None},
            ],
            "answer": {"value": None, "formatted": None, "sources": [], "work_shown": None},
        }
        # Should not crash
        state = execute_calculations(state)
        assert state["calculation_steps"][0]["result"] == 2000.0


class TestEvalFormula:
    def test_simple_division(self):
        data = {"cogs": 14060, "avg_inventory": 2168}
        result = _eval_formula("cogs / avg_inventory", data)
        assert abs(result - 6.487) < 0.01

    def test_average(self):
        data = {"inv_start": 2000, "inv_end": 2336}
        result = _eval_formula("(inv_start + inv_end) / 2", data)
        assert result == 2168.0

    def test_cagr(self):
        # "3-year CAGR" → N = 3 (the label number, not years of growth)
        data = {"rev_start": 1906, "rev_end": 2866}
        result = _eval_formula("(rev_end / rev_start) ** (1/3) - 1", data)
        assert abs(result - 0.1456) < 0.01

    def test_bps_calculation(self):
        data = {"actual_margin": 0.116, "guided_margin": 0.109}
        result = _eval_formula("(actual_margin - guided_margin) * 10000", data)
        assert abs(result - 70) < 1

    def test_no_dangerous_builtins(self):
        with pytest.raises(Exception):
            _eval_formula("__import__('os').system('ls')", {})


class TestExecuteCalculations:
    def test_inventory_turnover(self):
        # FAB convention: Inventory Turnover = COGS / Ending Inventory (not average)
        state = {
            "data_needed": {
                "cogs_2024": {"value": 14060},
                "inventory_2024": {"value": 2168},
            },
            "entities": {},
            "calculation_steps": [
                {
                    "step": "turnover",
                    "formula": "cogs_2024 / inventory_2024",
                    "inputs": ["cogs_2024", "inventory_2024"],
                    "result": None,
                },
            ],
            "answer": {"value": None, "formatted": None, "sources": [], "work_shown": None},
        }
        state = execute_calculations(state)
        assert abs(state["calculation_steps"][0]["result"] - 6.49) < 0.01
        assert state["answer"]["value"] is not None
        assert state["answer"]["work_shown"] is not None

    def test_skips_when_inputs_missing(self):
        state = {
            "data_needed": {
                "revenue": {"value": 100},
                # cogs is missing
            },
            "entities": {},
            "calculation_steps": [
                {
                    "step": "margin",
                    "formula": "revenue - cogs",
                    "inputs": ["revenue", "cogs"],
                    "result": None,
                },
            ],
            "answer": {"value": None, "formatted": None, "sources": [], "work_shown": None},
        }
        state = execute_calculations(state)
        assert state["calculation_steps"][0]["result"] is None

    def test_chained_calculations(self):
        state = {
            "data_needed": {
                "a": {"value": 10},
                "b": {"value": 5},
            },
            "entities": {},
            "calculation_steps": [
                {"step": "sum_ab", "formula": "a + b", "inputs": ["a", "b"], "result": None},
                {"step": "doubled", "formula": "sum_ab * 2", "inputs": ["sum_ab"], "result": None},
            ],
            "answer": {"value": None, "formatted": None, "sources": [], "work_shown": None},
        }
        state = execute_calculations(state)
        assert state["calculation_steps"][0]["result"] == 15.0
        assert state["calculation_steps"][1]["result"] == 30.0
