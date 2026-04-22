"""FDADomain — FDA regulatory QA domain implementation.

Answers precise questions about medical devices using openFDA APIs
and AccessData PDF scraping for predicate device information.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from domains.base import Domain
from domains.fda.concepts import FDA_CONCEPTS
from domains.fda.methodology import FDA_METHODOLOGY
from domains.fda.identifier import extract_identifier as _extract_id
from domains.fda.tools import (
    search_510k, get_510k_predicates, search_maude,
    search_recalls, lookup_classification,
)
from core.types import BenchmarkQuestion, ToolResult

log = logging.getLogger(__name__)


class FDADomain(Domain):
    """FDA regulatory QA domain.

    Answers questions about 510(k) clearances, adverse events, recalls,
    device classification, and predicate devices using openFDA APIs.
    """

    @property
    def name(self) -> str:
        return "fda"

    # ---- Prompts ----

    @property
    def planner_system(self) -> str:
        return f"""\
You are an FDA regulatory research planner. Given a question about medical devices, \
you create a structured research plan.

Your job is to:
1. Identify exactly what data points are needed to answer the question
2. Resolve any ambiguity (which device, which time period, which metric)
3. Choose the CORRECT METHODOLOGY for this type of question
4. Define the calculation steps (if any) following the methodology
5. Choose the right structure: simple data lookup, multi-step calculation, or cross-device comparison

{FDA_CONCEPTS}

{FDA_METHODOLOGY}

Respond with valid JSON only. No markdown, no explanation outside the JSON."""

    @property
    def planner_prompt_template(self) -> str:
        return """\
Create a research plan for this FDA regulatory question:

QUESTION: {question}

Today's date is {date}. Use this to determine "most recent" periods.

Return a JSON state dict with this structure:
{{
    "plan": "one-line description of what we're solving",
    "clarifications": {{
        "company": "device manufacturer, applicant, or K-number if known",
        "period": "exact period(s) needed, resolved from the question",
        "formula": "the calculation formula if applicable, or 'lookup only'",
        "source_strategy": "which databases to search — e.g., 'openFDA 510(k) for clearance records, MAUDE for adverse events'",
        "definitions": {{}}
    }},
    "data_needed": {{
        "descriptive_key": {{
            "value": null,
            "unit": "days, count, date, text, etc.",
            "source": null,
            "confidence": null,
            "attempts": [],
            "label": "human-readable description"
        }}
    }},
    "filings_needed": [
        // List of specific FDA data to fetch. The orchestrator fetches these BEFORE you start researching.
        // TYPES:
        //   "510k"           — clearance record by K-number or product code search
        //   "predicates"     — predicate device chain for a K-number (fetches 510(k) summary PDF)
        //   "maude"          — adverse event reports (by brand, product code, date range)
        //   "maude_count"    — adverse event COUNT only (faster, for aggregate questions)
        //   "recall"         — recall search by product code or firm
        //   "classification" — product code / device class lookup
        //
        // EXAMPLES:
        //   {{"type": "510k", "identifier": "K213456", "reason": "clearance record for subject device"}}
        //   {{"type": "510k", "concepts": ["product_code:DRG"], "period": "2022-2024", "reason": "all DRG clearances in date range"}}
        //   {{"type": "predicates", "identifier": "K213456", "reason": "predicate device chain"}}
        //   {{"type": "maude_count", "concepts": ["brand_name:HeartMate 3", "event_type:Death"], "period": "2023-01-01 to 2024-06-30", "reason": "death event count"}}
        //   {{"type": "maude", "concepts": ["brand_name:HeartMate 3"], "period": "2023-2024", "reason": "adverse event details with narratives"}}
        //   {{"type": "recall", "concepts": ["product_code:LWS"], "period": "2022-2024", "reason": "recalls for product code"}}
        //   {{"type": "classification", "concepts": ["product_code:DRG"], "reason": "device class and regulatory pathway"}}
        //   {{"type": "classification", "concepts": ["device_name:ablation catheter"], "reason": "find matching product codes"}}
        //
        // IMPORTANT:
        //   - For predicate lookups, ALWAYS include a "510k" entry for the subject device AND a "predicates" entry
        //   - For clearance timeline questions, use "510k" with a date range — you need individual records for date arithmetic
        //   - For adverse event counts, prefer "maude_count" (faster) over "maude" (detailed)
        //   - For multi-source questions, include entries for EACH data source needed
        //   - Product codes are 3 uppercase letters (e.g., DRG, LWS, DSQ)
        //   - K-numbers are K followed by 6 digits (e.g., K213456)
        //   - Brand names in MAUDE are often ALL CAPS
    ],
    "entities": {{}},
    "calculation_steps": [
        // only if calculation needed
        // {{"step": "name", "formula": "python expression using data_needed keys", "inputs": ["key1", "key2"], "result": null}}
    ],
    "answer": {{
        "value": null,
        "formatted": null,
        "sources": [],
        "work_shown": null
    }}
}}

IMPORTANT:
- Use descriptive keys for data_needed (e.g., "clearance_date_k213456" not "x1")
- For clearance time calculations, you need both date_received and decision_date
- For median/average calculations over multiple records, plan to fetch ALL matching records
- K-numbers are case-insensitive but conventionally uppercase
- "Clearance" is for 510(k); "Approval" is for PMA — use correct terminology"""

    @property
    def react_system(self) -> str:
        return f"""\
You are an FDA regulatory research agent. You have a research plan and tools to find data.

Your job: find all the data needed in the plan, then state your findings clearly.
Think step by step. Use tools to find specific data. When done, summarize what you found.

TOOL SELECTION:
- 510(k) clearance records → openfda_510k (by K-number, product code, applicant, or date range)
- Predicate devices → openfda_predicates (fetches the 510(k) summary PDF)
- Adverse events → openfda_maude (detail reports with narratives) or openfda_maude with count_only=true
- Recalls → openfda_recall (by product code, firm, or date range)
- Device classification → openfda_classification (by product code or device name text search)
- ONLY use web_search when openFDA doesn't have the data

SOURCE HIERARCHY: openFDA API > AccessData website > web article
Web articles may have ERRORS. Always verify against the primary database.

IMPORTANT:
- State exact numbers with their sources — DO NOT ROUND
- K-numbers, dates, counts must be EXACT
- Preserve ALL significant digits
- If a tool returns data, extract the specific value you need
- If you can't find something after 2 tries with different queries, say so
- Do NOT hallucinate numbers — only report what tools actually returned

TERMINOLOGY:
- "Clearance" = 510(k) pathway (NOT "approval")
- "Approval" = PMA pathway only
- Use official product code descriptions from the classification database

BE DECISIVE — do NOT hedge when the data clearly answers the question:
- openFDA may use different labels than the question. Report the data with its database label.
- If the data answers the question under a slightly different name, REPORT IT.
- CHECK THE PRE-FETCHED DATA FIRST. It was selected specifically for this question.

CROSS-VALIDATION — before reporting your final numbers:
- Check internal consistency. If you computed clearance time from dates AND found a stated timeline, they should match.
- If two sources disagree, investigate before reporting.

{FDA_CONCEPTS}

EXAMPLES OF SUCCESSFUL RESEARCH PATTERNS:

Example 1 — Clearance timeline computation:
Question: "Median clearance time for 510(k)s in product code DRG from 2022-2024?"
Research flow:
- Fetched all DRG clearances in date range: 23 records
- For each: computed decision_date - date_received
- Sorted times: [45, 52, 67, ..., 234] days
- Median = 12th value = 142 days
- Cross-validated: typical 510(k) review is 90-180 days — 142 is reasonable
- REPORTED: "142 days (n=23 submissions)"

Example 2 — Multi-source cross-reference:
Question: "For product code LWS, list clearances since 2022 and any recalls or adverse event clusters."
Research flow:
- Searched 510(k) by product_code=LWS, date 2022-2024: 14 clearances
- Searched recalls by product_code=LWS: 2 Class II recalls
- Searched MAUDE by product_code=LWS, count only: 89 events (67 malfunction, 15 injury, 7 death)
- Cross-referenced: one recall (Z-1234-2023) linked to K-number K221567
- REPORTED all findings with cross-references

Example 3 — Predicate device lookup:
Question: "List all predicate devices cited in 510(k) K213456 with their clearance dates."
Research flow:
- Fetched K213456 clearance record: Xenco Medical Multilevel CerviKit, cleared 2021-12-21
- Fetched predicates from summary PDF: K160702 (primary), K080646 (additional), K160313, K191074 (references)
- For each predicate K-number: fetched clearance record to get clearance date
- REPORTED: "Primary predicate: K160702 (Astura Medical ZION, cleared 2019-02-08). Additional: K080646 (Biomet C-TekV, cleared 2008-11-14)."

Find all the data points listed in the plan above.
Follow the source_strategy in the clarifications — it tells you WHICH databases to search.
When you have all the data, provide a clear summary with exact numbers and sources."""

    @property
    def react_prompt_template(self) -> str:
        return """\
RESEARCH PLAN:
{plan}

RESEARCH DATE: {date}
You are researching as of this date. Only include data available by this date.

Find all the data points listed in the plan above.
Follow the source_strategy in the clarifications — it tells you WHICH databases to search.
When you have all the data, provide a clear summary with exact numbers and sources."""

    @property
    def answer_system(self) -> str:
        return """\
You format FDA regulatory research results into precise, well-cited answers.
Respond with the answer text only — no JSON, no markdown fences.

IMPORTANT: Use correct regulatory terminology.
- "Clearance" for 510(k) devices, "approval" for PMA devices.
- K-numbers must be exact (K + 6 digits).
- Dates in YYYY-MM-DD format.
- Counts must be exact integers."""

    @property
    def answer_prompt_template(self) -> str:
        return """\
Format the final answer for this FDA regulatory question.

QUESTION: {question}

COMPLETED RESEARCH STATE:
{state}

FORMATTING RULES:
- Lead with the specific answer (number, date, K-number, or conclusion)
- Show calculation work if applicable (e.g., clearance time = decision_date - date_received)
- Use exact values — K-numbers, dates (YYYY-MM-DD), counts
- Specify the exact time period and data source
- Include step-by-step reasoning for computed values

At the end, include sources:
{{
    "sources": [
        {{"url": "https://api.fda.gov/device/...", "name": "source description"}}
    ]
}}"""

    # ---- Tools ----

    @property
    def tool_dispatch(self) -> dict[str, Callable]:
        return {
            "510K": self._dispatch_510k,
            "510k": self._dispatch_510k,
            "PREDICATES": self._dispatch_predicates,
            "predicates": self._dispatch_predicates,
            "MAUDE": self._dispatch_maude,
            "maude": self._dispatch_maude,
            "MAUDE_COUNT": self._dispatch_maude_count,
            "maude_count": self._dispatch_maude_count,
            "RECALL": self._dispatch_recall,
            "recall": self._dispatch_recall,
            "CLASSIFICATION": self._dispatch_classification,
            "classification": self._dispatch_classification,
        }

    def _parse_period(self, period: str) -> tuple[str | None, str | None]:
        """Parse period string into (date_from, date_to)."""
        import re
        if not period:
            return None, None
        # "2022-2024" or "2022 to 2024"
        m = re.match(r'(\d{4})\s*[-–to]+\s*(\d{4})', period)
        if m:
            return f"{m.group(1)}-01-01", f"{m.group(2)}-12-31"
        # "2023-01-01 to 2024-06-30"
        m = re.match(r'(\d{4}-\d{2}-\d{2})\s*to\s*(\d{4}-\d{2}-\d{2})', period)
        if m:
            return m.group(1), m.group(2)
        # Single year "2024"
        m = re.match(r'^(\d{4})$', period.strip())
        if m:
            return f"{m.group(1)}-01-01", f"{m.group(1)}-12-31"
        return None, None

    def _parse_concepts(self, concepts: list[str] | None) -> dict[str, str]:
        """Parse concepts list like ["brand_name:HeartMate 3", "event_type:Death"] into dict."""
        result = {}
        for c in (concepts or []):
            if ":" in c:
                key, val = c.split(":", 1)
                result[key.strip()] = val.strip()
        return result

    def _dispatch_510k(self, filing: dict, default_id: str) -> list[ToolResult]:
        identifier = filing.get("identifier") or filing.get("ticker") or default_id
        concepts = self._parse_concepts(filing.get("concepts"))
        date_from, date_to = self._parse_period(filing.get("period", ""))

        output = search_510k(
            k_number=identifier if identifier and identifier.upper().startswith("K") else None,
            product_code=concepts.get("product_code") or (identifier if identifier and len(identifier) == 3 else None),
            applicant=concepts.get("applicant"),
            date_from=date_from,
            date_to=date_to,
            limit=int(concepts.get("limit", "20")),
        )
        return [ToolResult(raw=output, tool_name="openfda_510k",
                           input_data=filing)]

    def _dispatch_predicates(self, filing: dict, default_id: str) -> list[ToolResult]:
        identifier = filing.get("identifier") or default_id
        output = get_510k_predicates(identifier)
        return [ToolResult(raw=output, tool_name="openfda_predicates",
                           input_data={"k_number": identifier})]

    def _dispatch_maude(self, filing: dict, default_id: str) -> list[ToolResult]:
        concepts = self._parse_concepts(filing.get("concepts"))
        date_from, date_to = self._parse_period(filing.get("period", ""))

        output = search_maude(
            brand_name=concepts.get("brand_name"),
            product_code=concepts.get("product_code"),
            event_type=concepts.get("event_type"),
            date_from=date_from,
            date_to=date_to,
            count_only=False,
            limit=int(concepts.get("limit", "10")),
        )
        return [ToolResult(raw=output, tool_name="openfda_maude",
                           input_data=filing)]

    def _dispatch_maude_count(self, filing: dict, default_id: str) -> list[ToolResult]:
        concepts = self._parse_concepts(filing.get("concepts"))
        date_from, date_to = self._parse_period(filing.get("period", ""))

        output = search_maude(
            brand_name=concepts.get("brand_name"),
            product_code=concepts.get("product_code"),
            event_type=concepts.get("event_type"),
            date_from=date_from,
            date_to=date_to,
            count_only=True,
        )
        return [ToolResult(raw=output, tool_name="openfda_maude",
                           input_data=filing)]

    def _dispatch_recall(self, filing: dict, default_id: str) -> list[ToolResult]:
        concepts = self._parse_concepts(filing.get("concepts"))
        date_from, date_to = self._parse_period(filing.get("period", ""))

        output = search_recalls(
            product_code=concepts.get("product_code"),
            recalling_firm=concepts.get("recalling_firm"),
            date_from=date_from,
            date_to=date_to,
            limit=int(concepts.get("limit", "10")),
        )
        return [ToolResult(raw=output, tool_name="openfda_recall",
                           input_data=filing)]

    def _dispatch_classification(self, filing: dict, default_id: str) -> list[ToolResult]:
        concepts = self._parse_concepts(filing.get("concepts"))
        identifier = filing.get("identifier") or default_id

        output = lookup_classification(
            product_code=concepts.get("product_code") or (identifier if identifier and len(identifier) == 3 else None),
            device_name=concepts.get("device_name"),
            limit=int(concepts.get("limit", "5")),
        )
        return [ToolResult(raw=output, tool_name="openfda_classification",
                           input_data=filing)]

    @property
    def react_tools(self) -> list[dict]:
        return [
            {
                "name": "openfda_510k",
                "description": "Search FDA 510(k) clearance database. Find devices by K-number, product code, applicant, or date range. Returns clearance records with dates, decisions, and device info.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "k_number": {"type": "string", "description": "Specific K-number (e.g., K213456)"},
                        "product_code": {"type": "string", "description": "FDA product code (e.g., DRG)"},
                        "applicant": {"type": "string", "description": "Company name"},
                        "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                        "date_to": {"type": "string", "description": "End date YYYY-MM-DD"},
                        "limit": {"type": "integer", "description": "Max results (default 10, max 100)"},
                    },
                },
            },
            {
                "name": "openfda_predicates",
                "description": "Get predicate devices for a specific 510(k) submission. Fetches the 510(k) summary PDF and extracts cited predicate K-numbers with context.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "k_number": {"type": "string", "description": "K-number to look up predicates for (e.g., K213456)"},
                    },
                    "required": ["k_number"],
                },
            },
            {
                "name": "openfda_maude",
                "description": "Search MAUDE adverse event database. Find reports by device brand, product code, event type (Death/Injury/Malfunction), or date range. Set count_only=true for aggregate counts.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "brand_name": {"type": "string", "description": "Device brand name (use UPPERCASE for best results)"},
                        "product_code": {"type": "string", "description": "FDA product code"},
                        "event_type": {"type": "string", "enum": ["Death", "Injury", "Malfunction"], "description": "Filter by event type"},
                        "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                        "date_to": {"type": "string", "description": "End date YYYY-MM-DD"},
                        "count_only": {"type": "boolean", "description": "If true, return only counts by event type (faster)"},
                        "limit": {"type": "integer", "description": "Max detailed results (default 10, max 20)"},
                    },
                },
            },
            {
                "name": "openfda_recall",
                "description": "Search FDA device recall database. Find recalls by product code, firm, or date range. Includes reason, root cause, and related K-numbers.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "product_code": {"type": "string", "description": "FDA product code"},
                        "recalling_firm": {"type": "string", "description": "Company name"},
                        "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                        "date_to": {"type": "string", "description": "End date YYYY-MM-DD"},
                        "limit": {"type": "integer", "description": "Max results (default 10)"},
                    },
                },
            },
            {
                "name": "openfda_classification",
                "description": "Look up FDA device classification. Find product code, device class (I/II/III), and regulatory pathway by product code or device name text search.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "product_code": {"type": "string", "description": "Exact 3-letter product code (e.g., DRG)"},
                        "device_name": {"type": "string", "description": "Device name text search (e.g., 'ablation catheter')"},
                    },
                },
            },
        ]

    def execute_tool(self, name: str, input_data: dict) -> str:
        if name == "openfda_510k":
            return search_510k(
                k_number=input_data.get("k_number"),
                product_code=input_data.get("product_code"),
                applicant=input_data.get("applicant"),
                date_from=input_data.get("date_from"),
                date_to=input_data.get("date_to"),
                limit=input_data.get("limit", 10),
            )
        elif name == "openfda_predicates":
            return get_510k_predicates(input_data.get("k_number", ""))
        elif name == "openfda_maude":
            return search_maude(
                brand_name=input_data.get("brand_name"),
                product_code=input_data.get("product_code"),
                event_type=input_data.get("event_type"),
                date_from=input_data.get("date_from"),
                date_to=input_data.get("date_to"),
                count_only=input_data.get("count_only", False),
                limit=input_data.get("limit", 10),
            )
        elif name == "openfda_recall":
            return search_recalls(
                product_code=input_data.get("product_code"),
                recalling_firm=input_data.get("recalling_firm"),
                date_from=input_data.get("date_from"),
                date_to=input_data.get("date_to"),
                limit=input_data.get("limit", 10),
            )
        elif name == "openfda_classification":
            return lookup_classification(
                product_code=input_data.get("product_code"),
                device_name=input_data.get("device_name"),
            )
        return f"Unknown FDA tool: {name}"

    # ---- Identifier ----

    def extract_identifier(self, company_text: str) -> Optional[str]:
        return _extract_id(company_text)

    # ---- Context sizing ----

    def context_size_tier(self, state: dict) -> int:
        # Predicate lookups and multi-source questions need more context (PDF text)
        has_predicates = any(
            f.get("type", "").lower() == "predicates"
            for f in state.get("filings_needed", [])
        )
        is_qualitative = (
            not state.get("calculation_steps") and
            "lookup" in state.get("clarifications", {}).get("formula", "").lower()
        )
        if has_predicates or is_qualitative:
            return 30000
        return 8000

    # ---- Extraction ----

    def classify_tools(self, tool_log: list[dict]) -> dict[str, list[dict]]:
        structured = []
        prose = []
        for entry in tool_log:
            tool = entry.get("tool", "")
            if tool in ("openfda_510k", "openfda_classification", "openfda_recall"):
                structured.append(entry)
            elif tool in ("openfda_maude", "openfda_predicates"):
                prose.append(entry)
        return {"structured": structured, "prose": prose}

    @property
    def concept_map(self) -> dict[str, list[str]]:
        return {
            "clearance_date": ["decision_date"],
            "received_date": ["date_received"],
            "applicant": ["applicant"],
            "device_name": ["device_name"],
            "product_code": ["product_code"],
            "device_class": ["device_class"],
            "clearance_time": ["clearance_days"],
            "event_count": ["total", "count"],
            "recall_reason": ["reason_for_recall"],
            "predicate": ["predicate_device", "predicate_k_number"],
            "k_number": ["k_number"],
            "regulation": ["regulation_number"],
        }

    @property
    def keyword_map(self) -> dict[str, list[str]]:
        return {
            "clearance date": ["clearance_date", "decision_date"],
            "date received": ["received_date", "date_received"],
            "clearance time": ["clearance_time", "clearance_days"],
            "product code": ["product_code"],
            "device class": ["device_class"],
            "adverse event": ["event_count", "event_type"],
            "death": ["death_count", "event_count"],
            "injury": ["injury_count", "event_count"],
            "malfunction": ["malfunction_count", "event_count"],
            "recall": ["recall_count", "recall_reason"],
            "predicate": ["predicate", "predicate_k_number"],
            "applicant": ["applicant"],
            "manufacturer": ["manufacturer", "applicant"],
        }

    @property
    def extraction_hints(self) -> str:
        return """\
- K-numbers (K followed by 6 digits) are exact identifiers — match them precisely
- Dates may appear as YYYY-MM-DD or YYYYMMDD — both are valid
- For clearance time, compute from decision_date - date_received in the 510(k) record
- MAUDE event counts come from the count endpoint breakdown by event_type"""

    @property
    def sanity_check_config(self) -> dict:
        return {
            "pct_keywords": ["rate", "percent", "pct", "ratio"],
            "small_keywords": ["days", "clearance_time", "median", "count"],
            "pct_max": 100,
            "small_max": 100000,
        }

    # ---- Cross-validation ----

    def cross_validate(self, state: dict) -> dict:
        data_needed = state.get("data_needed", {})
        filled = {k: dp for k, dp in data_needed.items()
                  if isinstance(dp, dict) and dp.get("value") is not None}

        for k, dp in filled.items():
            val = dp["value"]
            key_lower = k.lower()

            # Clearance time should be 1-2000 days
            if "clearance_time" in key_lower or "clearance_days" in key_lower:
                if isinstance(val, (int, float)) and (val < 1 or val > 2000):
                    log.warning(f"  Cross-validation: {k}={val} days is outside reasonable range, clearing")
                    dp["value"] = None
                    dp["source"] = None

            # Device class must be 1, 2, or 3
            if "device_class" in key_lower:
                if str(val) not in ("1", "2", "3"):
                    log.warning(f"  Cross-validation: {k}={val} is not a valid device class, clearing")
                    dp["value"] = None
                    dp["source"] = None

        return state

    # ---- Benchmark ----

    @property
    def benchmark_questions(self) -> list[BenchmarkQuestion]:
        """Load 510kQA benchmark from local file, or return empty list if not yet built."""
        import json
        from pathlib import Path

        benchmark_path = Path(__file__).parent / "benchmark" / "questions.jsonl"
        if not benchmark_path.exists():
            return []

        questions = []
        with open(benchmark_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                questions.append(BenchmarkQuestion(
                    id=data["id"],
                    question=data["question"],
                    answer=data.get("answer", ""),
                    rubric=data.get("rubric", []),
                    question_type=data.get("question_type", ""),
                    as_of_date=data.get("as_of_date", "2026-04-21"),
                ))
        return questions

    @property
    def benchmark_date(self) -> str:
        return "2026-04-21"
