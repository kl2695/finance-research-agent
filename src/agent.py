"""Shim — delegates to core.agent with FinanceDomain for backwards compatibility.

All callers that use `from src.agent import run` get finance behavior automatically.
New callers should use `from core.agent import run` with an explicit domain.
"""

from __future__ import annotations

from core.agent import run as _core_run
from domains.finance.domain import FinanceDomain

_finance_domain = FinanceDomain()


def run(question: str, as_of_date: str | None = None) -> dict:
    """Run the finance research agent pipeline.

    Backwards-compatible wrapper that passes FinanceDomain to core.agent.run().
    """
    return _core_run(question, domain=_finance_domain, as_of_date=as_of_date)
