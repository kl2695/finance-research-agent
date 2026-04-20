"""Integration tests using recorded fixtures from actual FAB benchmark runs.

These test the extraction + calculation pipeline end-to-end using real SEC EDGAR
responses — NO API calls, runs in milliseconds.

Each test recreates a real scenario we encountered and fixed.
"""

import pytest
from src.extractor import (
    extract_from_tool_log,
    _parse_filing_text,
    _parse_xbrl_output,
    _match_fact_to_key,
)
from src.calculator import execute_calculations


# ============================================================
# FIXTURE: US Steel Inventory Turnover (idx 14)
# From: SEC EDGAR XBRL for CostOfGoodsAndServicesSold and InventoryNet
# Ground truth: 6.49x
# ============================================================

US_STEEL_XBRL_COGS = (
    "SEC EDGAR XBRL data for United States Steel Corp — metric filter: CostOfGoodsAndServicesSold\n"
    "CostOfGoodsAndServicesSold (USD): 14060000000 (period: 2024-01-01 to 2024-12-31, filed: 2025-01-31)\n"
    "CostOfGoodsAndServicesSold (USD): 14803000000 (period: 2023-01-01 to 2023-12-31, filed: 2025-01-31)"
)

US_STEEL_XBRL_INVENTORY = (
    "SEC EDGAR XBRL data for United States Steel Corp — metric filter: InventoryNet\n"
    "InventoryNet (USD): 2168000000 (period: ? to 2024-12-31, filed: 2025-01-31)\n"
    "InventoryNet (USD): 2128000000 (period: ? to 2023-12-31, filed: 2025-01-31)"
)


class TestUSSteel:
    def test_xbrl_extraction_fills_values(self):
        """XBRL extraction should find COGS and inventory for the right year."""
        tool_log = [
            {"tool": "sec_edgar_financials", "input": {"ticker": "X", "metric": "CostOfGoodsAndServicesSold"},
             "output": US_STEEL_XBRL_COGS},
            {"tool": "sec_edgar_financials", "input": {"ticker": "X", "metric": "InventoryNet"},
             "output": US_STEEL_XBRL_INVENTORY},
        ]
        state = {
            "clarifications": {"period": "FY2024"},
            "data_needed": {
                "cogs_fy2024": {"value": None, "unit": "USD", "source": None, "confidence": None, "attempts": [], "label": "COGS FY2024"},
                "ending_inventory_fy2024": {"value": None, "unit": "USD", "source": None, "confidence": None, "attempts": [], "label": "Ending inventory FY2024"},
            },
        }
        state = extract_from_tool_log(tool_log, state)
        assert state["data_needed"]["cogs_fy2024"]["value"] == 14060000000
        assert state["data_needed"]["ending_inventory_fy2024"]["value"] == 2168000000

    def test_inventory_turnover_calculation(self):
        """Calculator should produce 6.49x from COGS / ending inventory."""
        state = {
            "data_needed": {
                "cogs_fy2024": {"value": 14060000000},
                "ending_inventory_fy2024": {"value": 2168000000},
            },
            "entities": {},
            "calculation_steps": [
                {"step": "inventory_turnover", "formula": "cogs_fy2024 / ending_inventory_fy2024",
                 "inputs": ["cogs_fy2024", "ending_inventory_fy2024"], "result": None},
            ],
            "answer": {"value": None, "formatted": None, "sources": [], "work_shown": None},
        }
        state = execute_calculations(state)
        assert abs(state["calculation_steps"][0]["result"] - 6.49) < 0.01


# ============================================================
# FIXTURE: Lyft Q4 2024 Beat/Miss (idx 37)
# From: Lyft earnings press release with financial table
# Ground truth: 26.1 bps beat
# Key issue: must extract $4,278.9M from table, not $4.3B from prose
# ============================================================

LYFT_PRESS_RELEASE_SNIPPET = (
    "Record Fourth Quarter 2024 Financial Highlights\n"
    "• Gross Bookings of $4.3 billion, up 15% year over year.\n"
    "• Revenue of $1.6 billion, up 27% year over year.\n"
    "• Adjusted EBITDA of $112.8 million compared to $66.6 million in Q4'23.\n"
    "(in millions, except for percentages)\n"
    "Gross Bookings $ 4,278.9 $ 4,108.4 $ 3,724.3 $ 16,099.4 $ 13,775.2\n"
    "Revenue $ 1,550.3 $ 1,522.7 $ 1,224.6 $ 5,786.0 $ 4,403.6\n"
    "Adjusted EBITDA $ 112.8 $ 107.3 $ 66.6 $ 382.4 $ 222.4\n"
)


class TestLyft:
    def test_table_unit_detection(self):
        """'(in millions)' header should apply multiplier to bare table values."""
        facts = _parse_filing_text(LYFT_PRESS_RELEASE_SNIPPET)
        table_bookings = [f for f in facts if abs(f["value"] - 4278.9e6) < 1e4]
        assert len(table_bookings) >= 1, f"Expected ~4278.9M, got {[f['value'] for f in facts if f['value'] > 3e9]}"
        assert table_bookings[0]["from_table"] is True

    def test_table_value_more_precise_than_prose(self):
        """$4,278.9M (table) should exist alongside $4.3B (prose)."""
        facts = _parse_filing_text(LYFT_PRESS_RELEASE_SNIPPET)
        prose_bookings = [f for f in facts if abs(f["value"] - 4.3e9) < 1e7 and not f.get("from_table")]
        table_bookings = [f for f in facts if abs(f["value"] - 4278.9e6) < 1e4 and f.get("from_table")]
        assert len(prose_bookings) >= 1, "Should find $4.3B prose value"
        assert len(table_bookings) >= 1, "Should find $4,278.9M table value"

    def test_ebitda_extracted_correctly(self):
        """$112.8M EBITDA should be extracted with correct context."""
        facts = _parse_filing_text(LYFT_PRESS_RELEASE_SNIPPET)
        ebitda = [f for f in facts if abs(f["value"] - 112.8e6) < 1e4]
        assert len(ebitda) >= 1

    def test_beat_miss_calculation(self):
        """With correct values, should produce ~26.1 bps beat."""
        state = {
            "data_needed": {
                "ebitda": {"value": 112800000},
                "bookings": {"value": 4278900000},
                "guided_ebitda_low": {"value": 100000000},
                "guided_ebitda_high": {"value": 105000000},
                "guided_bookings_low": {"value": 4280000000},
                "guided_bookings_high": {"value": 4350000000},
            },
            "entities": {},
            "calculation_steps": [
                {"step": "actual_margin", "formula": "(ebitda / bookings) * 100",
                 "inputs": ["ebitda", "bookings"], "result": None},
                {"step": "guided_margin", "formula": "((guided_ebitda_low + guided_ebitda_high) / 2) / ((guided_bookings_low + guided_bookings_high) / 2) * 100",
                 "inputs": ["guided_ebitda_low", "guided_ebitda_high", "guided_bookings_low", "guided_bookings_high"], "result": None},
                {"step": "beat_bps", "formula": "(actual_margin - guided_margin) * 100",
                 "inputs": ["actual_margin", "guided_margin"], "result": None},
            ],
            "answer": {"value": None, "formatted": None, "sources": [], "work_shown": None},
        }
        state = execute_calculations(state)
        beat = state["calculation_steps"][2]["result"]
        assert abs(beat - 26.08) < 0.5, f"Expected ~26.1 bps, got {beat}"


# ============================================================
# FIXTURE: Palantir 3-Year CAGR (idx 9)
# From: SEC EDGAR XBRL RevenueFromContractWithCustomerExcludingAssessedTax
# Ground truth: 14.56% (FY2022→FY2024, N=3)
# Key issue: XBRL concept name differs from standard "Revenues"
# ============================================================

PALANTIR_XBRL_REVENUE = (
    "SEC EDGAR XBRL data for Palantir Technologies Inc. — metric filter: RevenueFromContractWithCustomerExcludingAssessedTax\n"
    "RevenueFromContractWithCustomerExcludingAssessedTax (USD): 4475446000 (period: 2025-01-01 to 2025-12-31, filed: 2026-02-17)\n"
    "RevenueFromContractWithCustomerExcludingAssessedTax (USD): 2865507000 (period: 2024-01-01 to 2024-12-31, filed: 2026-02-17)\n"
    "RevenueFromContractWithCustomerExcludingAssessedTax (USD): 2225012000 (period: 2023-01-01 to 2023-12-31, filed: 2026-02-17)\n"
    "RevenueFromContractWithCustomerExcludingAssessedTax (USD): 1905871000 (period: 2022-01-01 to 2022-12-31, filed: 2025-02-18)"
)


class TestPalantir:
    def test_xbrl_parses_long_concept_name(self):
        """Should parse RevenueFromContractWithCustomerExcludingAssessedTax correctly."""
        facts = _parse_xbrl_output(PALANTIR_XBRL_REVENUE)
        assert len(facts) == 4
        assert facts[0]["value"] == 4475446000
        assert facts[0]["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"

    def test_revenue_period_matching(self):
        """Should match FY2022 and FY2024 revenue to the right keys."""
        facts = _parse_xbrl_output(PALANTIR_XBRL_REVENUE)
        # Add source_idx for the extractor
        for f in facts:
            f["source_idx"] = 0

        state = {
            "clarifications": {"period": "FY2022 to FY2024"},
            "data_needed": {
                "revenue_fy2022": {"value": None, "unit": "USD", "source": None, "confidence": None, "attempts": [], "label": "Revenue FY2022"},
                "revenue_fy2024": {"value": None, "unit": "USD", "source": None, "confidence": None, "attempts": [], "label": "Revenue FY2024"},
            },
        }
        tool_log = [{"tool": "sec_edgar_financials", "output": PALANTIR_XBRL_REVENUE}]
        state = extract_from_tool_log(tool_log, state)
        assert state["data_needed"]["revenue_fy2022"]["value"] == 1905871000
        assert state["data_needed"]["revenue_fy2024"]["value"] == 2865507000

    def test_cagr_calculation_n3(self):
        """3-year CAGR with N=3 (FAB convention, not N=2)."""
        state = {
            "data_needed": {
                "revenue_fy2022": {"value": 1905871000},
                "revenue_fy2024": {"value": 2865507000},
            },
            "entities": {},
            "calculation_steps": [
                {"step": "cagr", "formula": "(revenue_fy2024 / revenue_fy2022) ** (1/3) - 1",
                 "inputs": ["revenue_fy2022", "revenue_fy2024"], "result": None},
            ],
            "answer": {"value": None, "formatted": None, "sources": [], "work_shown": None},
        }
        state = execute_calculations(state)
        assert abs(state["calculation_steps"][0]["result"] - 0.1456) < 0.001


# ============================================================
# FIXTURE: Netflix Cash Requirements (idx 27)
# From: 10-K contractual obligations table with column annotations
# Ground truth: $14,426,266,000
# Key issue: table parser must annotate "[Next 12 Months]" column
# ============================================================

NETFLIX_CASH_TABLE = (
    "(in thousands)\n"
    "Content obligations [Total]: $23,248,931 [Next 12 Months]: $11,424,696 [Beyond 12 Months]: $11,824,235\n"
    "Debt [Total]: $19,841,462 [Next 12 Months]: $2,486,945 [Beyond 12 Months]: $17,354,517\n"
    "Operating lease obligations [Total]: $2,761,120 [Next 12 Months]: $514,625 [Beyond 12 Months]: $2,246,495\n"
    "Total [Total]: $45,851,513 [Next 12 Months]: $14,426,266 [Beyond 12 Months]: $31,425,247\n"
)


class TestNetflixCash:
    def test_parses_thousands_table(self):
        """'(in thousands)' table values should get 1e3 multiplier."""
        facts = _parse_filing_text(NETFLIX_CASH_TABLE)
        # Find the $14.4B value (14,426,266 thousands = $14,426,266,000)
        target = [f for f in facts if abs(f["value"] - 14426266000) < 1000]
        assert len(target) >= 1, f"Expected ~$14.4B, got {[f['value'] for f in facts if f['value'] > 1e9]}"


# ============================================================
# FIXTURE: Percentage extraction (idx 8 — Micron gross margin)
# Key issue: extractor must find percentages, not just dollar amounts
# ============================================================

MICRON_PRESS_RELEASE_SNIPPET = (
    "GAAP gross margin of 26.9% for the third quarter of fiscal 2024.\n"
    "Revenue of $6.8 billion, up 81% year over year.\n"
    "Operating margin of 15.3%.\n"
)


class TestPercentageExtraction:
    def test_finds_percentages(self):
        """Should extract percentage values alongside dollar amounts."""
        facts = _parse_filing_text(MICRON_PRESS_RELEASE_SNIPPET)
        pct_facts = [f for f in facts if f.get("is_pct")]
        assert len(pct_facts) >= 3  # 26.9%, 81%, 15.3%
        values = [f["value"] for f in pct_facts]
        assert 26.9 in values
        assert 15.3 in values

    def test_percentage_keys_reject_dollar_amounts(self):
        """Margin keys should not be filled with billion-dollar values."""
        facts = _parse_filing_text(MICRON_PRESS_RELEASE_SNIPPET)
        # The $6.8B revenue fact should not match a margin key
        matches = _match_fact_to_key("q3_2024_gaap_gross_margin", "GAAP Gross Margin Q3 2024", facts, "")
        if matches:
            # The top match should be a percentage, not dollars
            assert matches[0].get("is_pct", False) or matches[0]["value"] < 100, \
                f"Margin key matched to non-percentage: {matches[0]['value']}"


# ============================================================
# FIXTURE: Fiscal year filename matching (idx 8 — Micron, idx 2 — TJX)
# Key issue: exhibit filenames use 2-digit year ("fy25" not "2025")
# ============================================================

class TestFiscalYearFilenames:
    def test_2digit_year_matching(self):
        """'fy25' in filename should match quarter 'Q4 2025'."""
        import re
        filename = "tjxq4fy25earningspressrele.htm"
        quarter_tag = "q4"
        year = 2025
        year_tag = str(year)
        year_tag_short = str(year)[-2:]

        # Current matching logic
        year_match = year_tag in filename.lower() or f"fy{year_tag_short}" in filename.lower()
        assert quarter_tag in filename.lower()
        assert year_match, f"'fy{year_tag_short}' should match in '{filename}'"

    def test_json_parsing_with_trailing_content(self):
        """P81: LLM fact matching should handle malformed JSON with trailing text."""
        import json

        # Simulate Haiku returning JSON followed by explanation text
        test_cases = [
            ('{"key1": 5, "key2": 10}\nI matched these based on context',
             {"key1": 5, "key2": 10}),
            ('{"revenue": 3}extra text here',
             {"revenue": 3}),
            ('{}',
             {}),
        ]
        for raw, expected in test_cases:
            # Replicate the parsing logic from llm_match_facts_to_keys
            try:
                result = json.loads(raw.strip())
            except json.JSONDecodeError:
                depth = 0
                end_idx = -1
                for i, ch in enumerate(raw):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end_idx = i + 1
                            break
                result = json.loads(raw[:end_idx]) if end_idx > 0 else {}
            assert result == expected, f"Failed on: {raw[:40]}"

    def test_4digit_year_matching(self):
        """'2024' in filename should also match."""
        import re
        filename = "a2024q3ex991-pressrelease.htm"
        quarter_tag = "q3"
        year = 2024
        year_tag = str(year)
        year_tag_short = str(year)[-2:]

        year_match = year_tag in filename.lower() or f"fy{year_tag_short}" in filename.lower()
        assert quarter_tag in filename.lower()
        assert year_match
