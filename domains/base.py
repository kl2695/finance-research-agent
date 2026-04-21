"""Domain ABC — contract for a research domain.

Every domain (finance, FDA, etc.) implements this interface.
Core pipeline code depends ONLY on this interface, never on domain internals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from core.types import BenchmarkQuestion, FilingRequest, ToolResult


class Domain(ABC):
    """Contract for a research domain. Implementations: FinanceDomain, FDADomain."""

    # ---- Identity ----

    @property
    @abstractmethod
    def name(self) -> str:
        """Short name for this domain (e.g., 'finance', 'fda')."""
        ...

    # ---- Planner inputs (injected into core planner prompt template) ----

    @property
    @abstractmethod
    def planner_system(self) -> str:
        """Complete system prompt for the planner LLM call.
        Includes domain-specific concepts, methodology, and filing type references."""
        ...

    @property
    @abstractmethod
    def planner_prompt_template(self) -> str:
        """User prompt template for the planner. Must contain {question} and {date} slots.
        Includes domain-specific JSON schema examples, filings_needed documentation, etc."""
        ...

    @property
    @abstractmethod
    def react_system(self) -> str:
        """Complete system prompt for the ReAct agent.
        Includes domain-specific tool selection guidance, examples, and methodology."""
        ...

    @property
    @abstractmethod
    def react_prompt_template(self) -> str:
        """User prompt template for ReAct. Must contain {plan}, {date}, {question} slots."""
        ...

    @property
    @abstractmethod
    def answer_system(self) -> str:
        """System prompt for the answer formatter."""
        ...

    @property
    @abstractmethod
    def answer_prompt_template(self) -> str:
        """User prompt template for formatter. Must contain {question} and {state} slots."""
        ...

    # ---- Prefetch / tools ----

    @property
    @abstractmethod
    def tool_dispatch(self) -> dict[str, Callable[[dict, str], ToolResult]]:
        """Map from FilingRequest.type string -> fetcher callable.
        Fetcher signature: (filing_entry: dict, default_identifier: str) -> ToolResult.
        Core prefetch iterates filings_needed and dispatches by entry['type']."""
        ...

    @property
    @abstractmethod
    def react_tools(self) -> list[dict]:
        """Tool schemas (Anthropic tool-use format) available to ReAct for additional
        calls beyond prefetched data. Web search is added by core."""
        ...

    @abstractmethod
    def execute_tool(self, name: str, input_data: dict) -> str:
        """Execute a tool call from the ReAct loop.
        Returns raw string output. Server-side tools (web_search) never reach this."""
        ...

    # ---- Identifier handling ----

    @abstractmethod
    def extract_identifier(self, company_text: str) -> Optional[str]:
        """Extract the primary entity identifier from planner output.
        Finance: stock ticker. FDA: K-number or NCT ID."""
        ...

    # ---- Context sizing ----

    def context_size_tier(self, state: dict) -> int:
        """Return the max chars per prefetch source to inject into ReAct prompt.
        Default: 4000. Override for domain-specific logic (e.g., qualitative vs quantitative)."""
        return 4000

    # ---- Extraction support ----

    def classify_tools(self, tool_log: list[dict]) -> dict[str, list[dict]]:
        """Classify tool_log entries into categories for extraction routing.
        Returns dict with keys like 'structured', 'prose'.
        Default: all entries are 'prose'."""
        return {"structured": [], "prose": tool_log}

    @property
    def concept_map(self) -> dict[str, list[str]]:
        """Layer 3a/3b matching: keyword in data_needed key -> concept name candidates.
        Order = preference. Default: empty (no concept matching)."""
        return {}

    @property
    def keyword_map(self) -> dict[str, list[str]]:
        """Context keyword map: phrase found in filing text -> standardized key terms.
        Used by _extract_context_keywords. Default: empty."""
        return {}

    @property
    def extraction_hints(self) -> str:
        """Extra instructions appended to the LLM fact-matching prompt (Step 3c).
        Domain-specific guidance for matching values to keys. Default: empty."""
        return ""

    @property
    def sanity_check_config(self) -> dict:
        """Configuration for extraction sanity checks.
        Keys: 'pct_keywords' (list[str]), 'small_keywords' (list[str]),
              'pct_max' (float), 'small_max' (float).
        Default: reasonable generic defaults."""
        return {
            "pct_keywords": ["margin", "rate", "percent", "pct", "ratio", "growth"],
            "small_keywords": [],
            "pct_max": 1000,
            "small_max": 10000,
        }

    # ---- Pre/post extraction hooks (e.g., guidance key hiding) ----

    def pre_extraction_filter(self, state: dict) -> tuple[dict, Any]:
        """Called before structured extraction (Step 3b).
        Returns (modified_state, stash). Stash is passed back to post_extraction_restore.
        Default: no-op."""
        return state, None

    def post_extraction_restore(self, state: dict, stash: Any) -> dict:
        """Called after structured extraction to restore any hidden keys.
        Default: no-op."""
        return state

    # ---- Cross-validation (Step 3.5) ----

    def cross_validate(self, state: dict) -> dict:
        """Domain-specific sanity checks on extracted values.
        May clear bad values (set to None) so formatter falls back to narrative.
        Default: no-op."""
        return state

    # ---- Benchmark ----

    @property
    @abstractmethod
    def benchmark_questions(self) -> list[BenchmarkQuestion]:
        """Benchmark questions for eval harness. Return [] if no benchmark exists."""
        ...

    @property
    def benchmark_date(self) -> str:
        """Default as_of_date for benchmark questions that don't specify one.
        Default: empty string (use today's date)."""
        return ""
