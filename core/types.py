"""Shared types used by core pipeline and domain implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Fact:
    """Parsed fact from a domain tool output. Used by extraction layers 3a/3b."""
    concept: str                                    # e.g., "Revenues", "clearance_date"
    value: float | str                              # precise number OR raw string for qualitative
    unit: Optional[str] = None                      # "USD", "days", "count", "percent", None
    period_start: Optional[str] = None              # ISO date string or None
    period_end: Optional[str] = None                # ISO date string or None
    source_ref: Optional[str] = None                # "10-K 2024 item 7" / "510(k) K213456"
    confidence: str = "high"                        # "high", "medium", "low"
    metadata: dict = field(default_factory=dict)    # domain-specific extras


@dataclass
class FilingRequest:
    """One entry in the planner's filings_needed list. Dispatched by prefetch."""
    type: str                                       # "xbrl" | "10-K" | "510k" | "maude" | ...
    identifier: Optional[str] = None                # override default identifier (e.g., per-company ticker)
    period: Optional[str] = None                    # "Q4 2024", "2024", "2020-2024"
    section: Optional[str] = None                   # "tax", "risk", "substantial_equivalence"
    concepts: Optional[list[str]] = None            # for XBRL-style lookups
    reason: Optional[str] = None                    # planner's rationale (debug only)
    extra: dict = field(default_factory=dict)       # domain-specific escape hatch


@dataclass
class ToolResult:
    """Uniform return type from every domain fetcher."""
    raw: str                                        # raw text/JSON output (injected into ReAct prompt)
    tool_name: str                                  # "sec_edgar_financials", "openfda_510k", etc.
    input_data: dict = field(default_factory=dict)  # original input params (for tool_log)
    success: bool = True
    error: Optional[str] = None


@dataclass
class BenchmarkQuestion:
    """A single benchmark question with rubric for evaluation."""
    id: str
    question: str
    answer: str                                     # ground truth answer text
    rubric: list                                    # rubric criteria (operator + criteria pairs)
    question_type: str = ""                         # "Numerical Reasoning", "Beat or Miss", etc.
    as_of_date: str = ""                            # ISO date — overrides planner's date context
    tags: list[str] = field(default_factory=list)   # optional tags
