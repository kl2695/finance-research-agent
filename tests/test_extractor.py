"""Tests for the data extractor — parsing tool results into state."""

import pytest
from src.extractor import (
    _parse_xbrl_output,
    _parse_filing_text,
    _match_fact_to_key,
    extract_from_tool_log,
)


class TestParseXBRL:
    def test_parses_standard_format(self):
        output = "InventoryNet (USD): 2168000000 (period: ? to 2024-12-31, filed: 2025-01-31)"
        facts = _parse_xbrl_output(output)
        assert len(facts) == 1
        assert facts[0]["concept"] == "InventoryNet"
        assert facts[0]["value"] == 2168000000
        assert facts[0]["period_end"] == "2024-12-31"

    def test_parses_multiple_entries(self):
        output = """SEC EDGAR XBRL data for US Steel:
InventoryNet (USD): 2168000000 (period: ? to 2024-12-31, filed: 2025-01-31)
InventoryNet (USD): 2039000000 (period: ? to 2023-12-31, filed: 2024-02-02)
CostOfGoodsAndServicesSold (USD): 14060000000 (period: 2024-01-01 to 2024-12-31, filed: 2025-01-31)"""
        facts = _parse_xbrl_output(output)
        assert len(facts) == 3

    def test_handles_commas_in_values(self):
        output = "Revenues (USD): 15,640,000,000 (period: 2024-01-01 to 2024-12-31, filed: 2025-01-31)"
        facts = _parse_xbrl_output(output)
        assert len(facts) == 1
        assert facts[0]["value"] == 15640000000


class TestParseFilingText:
    def test_finds_dollar_amounts(self):
        output = "Revenue was $15,640 million for the year ended December 31, 2024."
        facts = _parse_filing_text(output)
        assert len(facts) >= 1
        assert any(f["value"] == 15640e6 for f in facts)

    def test_handles_billions(self):
        output = "Total assets of $4.2 billion as of December 31, 2024."
        facts = _parse_filing_text(output)
        assert len(facts) >= 1
        assert any(f["value"] == 4.2e9 for f in facts)


class TestMatchFactToKey:
    def test_matches_inventory_ending(self):
        facts = [
            {"concept": "InventoryNet", "value": 2168e6, "period_end": "2024-12-31", "source": "XBRL"},
            {"concept": "InventoryNet", "value": 2039e6, "period_end": "2023-12-31", "source": "XBRL"},
        ]
        matches = _match_fact_to_key("inventory_ending_fy2024", "Ending inventory FY2024", facts, "FY2024")
        assert len(matches) >= 1
        assert matches[0]["value"] == 2168e6  # Should prefer 2024

    def test_matches_inventory_beginning(self):
        facts = [
            {"concept": "InventoryNet", "value": 2168e6, "period_end": "2024-12-31", "source": "XBRL"},
            {"concept": "InventoryNet", "value": 2039e6, "period_end": "2023-12-31", "source": "XBRL"},
        ]
        # "beginning fy2024" should match 2023 ending (prior year)
        matches = _match_fact_to_key("inventory_beginning_fy2024", "Beginning inventory FY2024", facts, "FY2024")
        assert len(matches) >= 1
        assert matches[0]["value"] == 2039e6  # Should pick 2023

    def test_matches_cogs(self):
        facts = [
            {"concept": "CostOfGoodsAndServicesSold", "value": 14060e6, "period_end": "2024-12-31", "source": "XBRL"},
            {"concept": "Revenues", "value": 15640e6, "period_end": "2024-12-31", "source": "XBRL"},
        ]
        matches = _match_fact_to_key("cogs_fy2024", "COGS FY2024", facts, "FY2024")
        assert len(matches) >= 1
        assert matches[0]["value"] == 14060e6


class TestPressReleaseExtraction:
    """Programmatic extraction from press release text — no LLM, no rounding."""

    def test_extracts_gross_bookings_precisely(self):
        """Must extract $4,278.9M exactly — not round to $4,300M."""
        output = "Gross Bookings of $4,278.9 million, up 15% year over year."
        facts = _parse_filing_text(output)
        values = [f["value"] for f in facts]
        assert any(abs(v - 4278.9e6) < 1 for v in values), f"Expected ~4278.9M, got {values}"

    def test_extracts_adjusted_ebitda_precisely(self):
        output = "Adjusted EBITDA of $112.8 million compared to $71.1 million"
        facts = _parse_filing_text(output)
        values = [f["value"] for f in facts]
        assert 112.8e6 in values

    def test_context_keywords_gross_bookings(self):
        output = "Record Gross Bookings of $4,278.9 million in Q4"
        facts = _parse_filing_text(output)
        assert len(facts) >= 1
        assert "gross_bookings" in facts[0].get("context_keywords", []) or "bookings" in facts[0].get("context_keywords", [])

    def test_context_keywords_adjusted_ebitda(self):
        output = "Adjusted EBITDA of $112.8 million for the quarter"
        facts = _parse_filing_text(output)
        assert len(facts) >= 1
        kw = facts[0].get("context_keywords", [])
        assert "adjusted_ebitda" in kw or "ebitda" in kw

    def test_matches_bookings_to_key_via_context(self):
        """Context keywords should match press release data to data_needed keys."""
        facts = [
            {"value": 4278.9e6, "context": "Gross Bookings of $4,278.9 million",
             "context_keywords": ["gross_bookings", "bookings"], "source": "SEC filing text"},
            {"value": 112.8e6, "context": "Adjusted EBITDA of $112.8 million",
             "context_keywords": ["adjusted_ebitda", "ebitda"], "source": "SEC filing text"},
        ]
        matches = _match_fact_to_key("q4_2024_gross_bookings", "Q4 2024 Gross Bookings", facts, "")
        assert len(matches) >= 1
        assert matches[0]["value"] == 4278.9e6

    def test_matches_ebitda_to_key_via_context(self):
        facts = [
            {"value": 4278.9e6, "context": "Gross Bookings of $4,278.9 million",
             "context_keywords": ["gross_bookings", "bookings"], "source": "SEC filing text"},
            {"value": 112.8e6, "context": "Adjusted EBITDA of $112.8 million",
             "context_keywords": ["adjusted_ebitda", "ebitda"], "source": "SEC filing text"},
        ]
        matches = _match_fact_to_key("q4_2024_adjusted_ebitda", "Q4 2024 Adjusted EBITDA", facts, "")
        assert len(matches) >= 1
        assert matches[0]["value"] == 112.8e6

    def test_preserves_decimal_precision(self):
        """$4,278.9 must stay as 4278.9, not become 4279 or 4280 or 4300."""
        output = "$4,278.9 million"
        facts = _parse_filing_text(output)
        val = facts[0]["value"]
        # Must be within $1 of 4278.9M (floating point tolerance)
        assert abs(val - 4278.9e6) < 1, f"Value {val} is not precise enough"
        # Must NOT be rounded to nearest 10M, 100M, etc.
        assert abs(val - 4280e6) > 1e5, "Rounded to 4280M"
        assert abs(val - 4300e6) > 1e6, "Rounded to 4300M"


class TestTableUnitDetection:
    """Tests for detecting '(in millions)' table headers and applying multipliers."""

    def test_table_unit_millions(self):
        """Bare dollar amounts after '(in millions)' should get the multiplier."""
        output = "(in millions, except for percentages)\nGross Bookings $ 4,278.9 $ 4,108.4"
        facts = _parse_filing_text(output)
        bookings = [f for f in facts if abs(f["value"] - 4278.9e6) < 1]
        assert len(bookings) >= 1, f"Expected 4278.9M, got {[f['value'] for f in facts]}"
        assert bookings[0]["from_table"] is True

    def test_inline_unit_overrides_table(self):
        """'$4.3 billion' should NOT be double-multiplied even after '(in millions)'."""
        output = "(in millions)\nGross Bookings of $4.3 billion in Q4"
        facts = _parse_filing_text(output)
        billions = [f for f in facts if abs(f["value"] - 4.3e9) < 1e6]
        assert len(billions) == 1, f"Expected 4.3B, got {[f['value'] for f in facts]}"

    def test_table_values_beat_prose_for_same_concept(self):
        """Table $4,278.9M should rank above prose $4.3B for the same key."""
        output = (
            "Gross Bookings of $4.3 billion, up 15%\n"
            "(in millions)\n"
            "Gross Bookings $ 4,278.9 $ 4,108.4"
        )
        facts = _parse_filing_text(output)
        matches = _match_fact_to_key("q4_2024_gross_bookings", "Q4 2024 Gross Bookings", facts, "")
        assert matches[0]["value"] == pytest.approx(4278.9e6, rel=1e-6), \
            f"Expected table value 4278.9M first, got {matches[0]['value']:,.0f}"


class TestProgrammaticOverLLMPrinciple:
    """Tests that verify the principle: programmatic extraction is always preferred
    over LLM extraction for precision-critical financial data."""

    def test_xbrl_data_never_needs_llm(self):
        """If XBRL has the data, the structured extractor must find it."""
        tool_log = [{
            "tool": "sec_edgar_financials",
            "input": {"ticker": "X", "metric": "InventoryNet"},
            "output": "InventoryNet (USD): 2168000000 (period: ? to 2024-12-31, filed: 2025-01-31)",
        }]
        state = {
            "clarifications": {"period": "FY2024"},
            "data_needed": {
                "inventory_fy2024": {"value": None, "unit": "USD", "source": None,
                                     "confidence": None, "attempts": [], "label": "Inventory FY2024"},
            },
        }
        state = extract_from_tool_log(tool_log, state)
        assert state["data_needed"]["inventory_fy2024"]["value"] == 2168000000
        assert state["data_needed"]["inventory_fy2024"]["source"] != "LLM extraction"

    def test_press_release_data_extracted_programmatically(self):
        """Press release dollar amounts should be parsed, not LLM-extracted."""
        tool_log = [{
            "tool": "sec_edgar_earnings",
            "input": {"ticker": "LYFT", "quarter": "Q4 2024"},
            "output": "EARNINGS PRESS RELEASE for LYFT Q4 2024\nGross Bookings of $4,278.9 million, up 15%\nAdjusted EBITDA of $112.8 million",
        }]
        state = {
            "clarifications": {"period": "Q4 2024"},
            "data_needed": {
                "q4_gross_bookings": {"value": None, "unit": "USD millions", "source": None,
                                      "confidence": None, "attempts": [], "label": "Q4 Gross Bookings"},
                "q4_adjusted_ebitda": {"value": None, "unit": "USD millions", "source": None,
                                       "confidence": None, "attempts": [], "label": "Q4 Adjusted EBITDA"},
            },
        }
        state = extract_from_tool_log(tool_log, state)
        # At least one should be extracted programmatically
        bookings = state["data_needed"]["q4_gross_bookings"]
        ebitda = state["data_needed"]["q4_adjusted_ebitda"]
        extracted_count = sum(1 for dp in [bookings, ebitda] if dp["value"] is not None)
        assert extracted_count >= 1, "Press release data should be extracted programmatically"


class TestExtractFromToolLog:
    def test_fills_us_steel_data(self):
        tool_log = [
            {
                "tool": "sec_edgar_financials",
                "input": {"ticker": "X", "metric": "CostOfGoodsAndServicesSold"},
                "output": "SEC EDGAR XBRL data for United States Steel Corp:\nCostOfGoodsAndServicesSold (USD): 14060000000 (period: 2024-01-01 to 2024-12-31, filed: 2025-01-31)",
            },
            {
                "tool": "sec_edgar_financials",
                "input": {"ticker": "X", "metric": "InventoryNet"},
                "output": "SEC EDGAR XBRL data for United States Steel Corp:\nInventoryNet (USD): 2168000000 (period: ? to 2024-12-31, filed: 2025-01-31)\nInventoryNet (USD): 2039000000 (period: ? to 2023-12-31, filed: 2024-02-02)",
            },
        ]
        state = {
            "clarifications": {"period": "FY2024 ending Dec 31, 2024"},
            "data_needed": {
                "cogs_fy2024": {"value": None, "unit": "USD", "source": None, "confidence": None, "attempts": [], "label": "COGS FY2024"},
                "inventory_ending_fy2024": {"value": None, "unit": "USD", "source": None, "confidence": None, "attempts": [], "label": "Ending inventory Dec 31, 2024"},
                "inventory_beginning_fy2024": {"value": None, "unit": "USD", "source": None, "confidence": None, "attempts": [], "label": "Beginning inventory Dec 31, 2023"},
            },
            "calculation_steps": [],
            "answer": {"value": None},
        }

        state = extract_from_tool_log(tool_log, state)

        assert state["data_needed"]["cogs_fy2024"]["value"] == 14060000000
        assert state["data_needed"]["inventory_ending_fy2024"]["value"] == 2168000000
        assert state["data_needed"]["inventory_beginning_fy2024"]["value"] == 2039000000
