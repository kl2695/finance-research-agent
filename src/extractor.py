"""Shim — re-exports from core.extractor with FinanceDomain defaults.

Tests and old code that call these functions without domain parameter
get finance-specific concept maps and keyword maps automatically.
"""

from __future__ import annotations

from core.extractor import (  # noqa: F401
    _parse_concept_output as _parse_xbrl_output,
    _parse_filing_text,
    _parse_kv_output as _parse_fmp_output,
    llm_match_facts_to_keys as _core_llm_match,
    _match_fact_to_key as _core_match,
    extract_from_tool_log as _core_extract,
    _extract_context_keywords as _core_extract_kw,
)
import core.extractor as _ce
from domains.finance.domain import FinanceDomain

_fd = FinanceDomain()

# Set the active keyword map in core.extractor so _extract_context_keywords works
_ce._active_keyword_map = _fd.keyword_map


def extract_from_tool_log(tool_log: list[dict], state: dict, domain=None) -> dict:
    """Backwards-compatible wrapper that injects FinanceDomain when no domain given."""
    if domain is None:
        domain = _fd
    return _core_extract(tool_log, state, domain)


def _match_fact_to_key(key, label, facts, period, concept_map=None, keyword_map=None):
    """Backwards-compatible wrapper that injects finance concept_map."""
    if concept_map is None:
        concept_map = _fd.concept_map
    if keyword_map is None:
        keyword_map = _fd.keyword_map
    return _core_match(key, label, facts, period, concept_map, keyword_map)


def _extract_context_keywords(context: str) -> list[str]:
    """Backwards-compatible wrapper — uses finance keyword map."""
    return _core_extract_kw(context)


def llm_match_facts_to_keys(facts, state, domain=None):
    """Backwards-compatible wrapper."""
    if domain is None:
        domain = _fd
    return _core_llm_match(facts, state, domain)
