"""Shim — re-exports prompt constants from FinanceDomain for backwards compatibility.

New code should use domain.planner_system, domain.react_system, etc. directly.
"""

from domains.finance.domain import FinanceDomain

_d = FinanceDomain()

PLANNER_SYSTEM = _d.planner_system
PLANNER_PROMPT = _d.planner_prompt_template
REACT_SYSTEM = _d.react_system
REACT_PROMPT = _d.react_prompt_template
ANSWER_SYSTEM = _d.answer_system
ANSWER_PROMPT = _d.answer_prompt_template
