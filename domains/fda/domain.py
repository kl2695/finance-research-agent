"""FDADomain — stub implementation proving the Domain interface works.

Returns canned data for one test question. Purpose: validate that the
abstraction carries two domains without code changes to core.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from domains.base import Domain
from core.types import BenchmarkQuestion, ToolResult


# Canned 510(k) response for K213456
_CANNED_510K = {
    "k_number": "K213456",
    "applicant": "Medtronic Inc.",
    "device_name": "Cardiac Rhythm Management Lead",
    "product_code": "DRG",
    "clearance_date": "2024-03-15",
    "decision": "Substantially Equivalent (SE)",
    "predicate_device": "K201234",
}

_CANNED_RAW = f"""510(k) Clearance Record: K213456

Device: Cardiac Rhythm Management Lead
Applicant: Medtronic Inc.
Product Code: DRG
Date of Decision: March 15, 2024
Decision: Substantially Equivalent (SE)
Predicate Device: K201234

This device was found to be substantially equivalent to the predicate device
K201234 under the 510(k) premarket notification pathway. The clearance date
is March 15, 2024."""


class FDADomain(Domain):
    """Stub FDA domain — validates the interface with canned responses."""

    @property
    def name(self) -> str:
        return "fda"

    # ---- Prompts ----

    @property
    def planner_system(self) -> str:
        return """\
You are a regulatory research planner for FDA medical device data.

Your job is to:
1. Identify exactly what data points are needed to answer the question
2. Resolve any ambiguity (which device, which time period, which metric)
3. Choose the right data sources (510(k) database, MAUDE, ClinicalTrials.gov)
4. Define calculation steps if applicable

Respond with valid JSON only. No markdown, no explanation outside the JSON."""

    @property
    def planner_prompt_template(self) -> str:
        return """\
Create a research plan for this FDA regulatory question:

QUESTION: {question}

Today's date is {date}.

Return a JSON state dict with this structure:
{{
    "plan": "one-line description",
    "clarifications": {{
        "company": "applicant or device manufacturer",
        "period": "time period if relevant",
        "formula": "calculation formula or 'lookup only'",
        "source_strategy": "which FDA databases to search",
        "definitions": {{}}
    }},
    "data_needed": {{
        "descriptive_key": {{
            "value": null,
            "unit": "text or count or date",
            "source": null,
            "confidence": null,
            "attempts": [],
            "label": "human-readable description"
        }}
    }},
    "filings_needed": [
        // {{"type": "510k", "identifier": "K213456", "reason": "clearance record"}}
    ],
    "entities": {{}},
    "calculation_steps": [],
    "answer": {{
        "value": null,
        "formatted": null,
        "sources": [],
        "work_shown": null
    }}
}}"""

    @property
    def react_system(self) -> str:
        return """\
You are an FDA regulatory research agent. You have a research plan and tools to find data.

Your job: find all the data needed in the plan, then state your findings clearly.
Use the pre-fetched 510(k) data as your primary source.

IMPORTANT:
- Report exact values from the source data
- Include the K-number as a citation
- If data isn't available, say so"""

    @property
    def react_prompt_template(self) -> str:
        return """\
RESEARCH PLAN:
{plan}

RESEARCH DATE: {date}

Find all the data points listed in the plan above.
When you have all the data, provide a clear summary with exact values and sources."""

    @property
    def answer_system(self) -> str:
        return """\
You format FDA regulatory research results into precise, well-cited answers.
Respond with the answer text only — no JSON, no markdown fences."""

    @property
    def answer_prompt_template(self) -> str:
        return """\
Format the final answer for this FDA question.

QUESTION: {question}

COMPLETED RESEARCH STATE:
{state}

FORMATTING RULES:
- Lead with the specific answer
- Include the K-number or identifier as citation
- Specify exact dates in YYYY-MM-DD format

At the end, include sources:
{{
    "sources": [
        {{"url": "...", "name": "source description"}}
    ]
}}"""

    # ---- Tools ----

    @property
    def tool_dispatch(self) -> dict[str, Callable[[dict, str], list[ToolResult]]]:
        return {
            "510K": self._fetch_510k,
            "510k": self._fetch_510k,
        }

    def _fetch_510k(self, filing: dict, default_id: str) -> list[ToolResult]:
        """Stub: return canned 510(k) data."""
        identifier = filing.get("identifier") or default_id
        if identifier == "K213456":
            return [ToolResult(
                raw=_CANNED_RAW,
                tool_name="openfda_510k",
                input_data={"k_number": identifier},
            )]
        return [ToolResult(
            raw=f"No 510(k) record found for {identifier}",
            tool_name="openfda_510k",
            input_data={"k_number": identifier},
            success=False,
            error=f"Not found: {identifier}",
        )]

    @property
    def react_tools(self) -> list[dict]:
        return [{
            "name": "openfda_510k",
            "description": "Look up a 510(k) clearance record by K-number.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "k_number": {"type": "string", "description": "K-number (e.g., K213456)"},
                },
                "required": ["k_number"],
            },
        }]

    def execute_tool(self, name: str, input_data: dict) -> str:
        if name == "openfda_510k":
            k_number = input_data.get("k_number", "")
            if k_number == "K213456":
                return _CANNED_RAW
            return f"No 510(k) record found for {k_number}"
        return f"Unknown tool: {name}"

    # ---- Identifier ----

    def extract_identifier(self, text: str) -> Optional[str]:
        """Extract K-number from text."""
        import re
        match = re.search(r'K\d{6}', text)
        return match.group() if match else None

    # ---- Benchmark ----

    @property
    def benchmark_questions(self) -> list[BenchmarkQuestion]:
        return [BenchmarkQuestion(
            id="fda_stub_1",
            question="What is the clearance date for 510(k) K213456?",
            answer="March 15, 2024",
            rubric=[
                {"operator": "correctness", "criteria": "March 15, 2024"},
                {"operator": "correctness", "criteria": "K213456"},
            ],
            question_type="Regulatory Lookup",
            as_of_date="2025-02-01",
        )]

    @property
    def benchmark_date(self) -> str:
        return "2025-02-01"
