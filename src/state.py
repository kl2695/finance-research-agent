"""Structured state dict — the agent's working memory.

The state persists across tool loop turns and serves as:
1. Compressed context (agent reads this instead of raw tool output)
2. Todo list (null values = what to research next)
3. Output format (completed dict = the answer)
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


def create_empty_state(plan: str = "") -> dict:
    """Create a new empty state dict."""
    return {
        "plan": plan,
        "clarifications": {},
        "data_needed": {},
        "entities": {},
        "calculation_steps": [],
        "answer": {
            "value": None,
            "formatted": None,
            "sources": [],
            "work_shown": None,
        },
    }


def make_data_point(unit: str = "USD", label: str = "") -> dict:
    """Create an empty data point entry."""
    return {
        "value": None,
        "unit": unit,
        "source": None,
        "confidence": None,
        "attempts": [],
        "label": label,
    }


def get_missing_data(state: dict) -> list[str]:
    """Return keys of data_needed entries that are still null."""
    missing = []
    for key, dp in state.get("data_needed", {}).items():
        if isinstance(dp, dict) and dp.get("value") is None:
            missing.append(key)
    return missing


def get_missing_entities(state: dict) -> list[tuple[str, str, str]]:
    """Return (entity, metric, period) tuples for unfilled entity cells."""
    missing = []
    for entity, metrics in state.get("entities", {}).items():
        if not isinstance(metrics, dict):
            continue
        for metric, periods in metrics.items():
            if not isinstance(periods, dict):
                continue
            for period, val in periods.items():
                if val is None or (isinstance(val, dict) and val.get("value") is None):
                    missing.append((entity, metric, period))
    return missing


def get_unfilled_steps(state: dict) -> list[dict]:
    """Return calculation steps that haven't been computed yet."""
    return [s for s in state.get("calculation_steps", []) if s.get("result") is None]


def is_data_complete(state: dict) -> bool:
    """Check if all data_needed values are filled."""
    return len(get_missing_data(state)) == 0


def is_calculation_complete(state: dict) -> bool:
    """Check if all calculation steps have results."""
    return len(get_unfilled_steps(state)) == 0


def render_state_for_prompt(state: dict) -> str:
    """Render the state dict as a readable string for the LLM prompt.

    This is the compressed context the agent sees each turn.
    """
    lines = []

    lines.append(f"PLAN: {state.get('plan', '?')}")
    lines.append("")

    # Clarifications
    clarifications = state.get("clarifications", {})
    if clarifications:
        lines.append("CLARIFICATIONS:")
        for k, v in clarifications.items():
            if isinstance(v, dict):
                lines.append(f"  {k}:")
                for k2, v2 in v.items():
                    lines.append(f"    {k2}: {v2}")
            else:
                lines.append(f"  {k}: {v}")
        lines.append("")

    # Data needed
    data_needed = state.get("data_needed", {})
    if data_needed:
        lines.append("DATA NEEDED:")
        for key, dp in data_needed.items():
            if not isinstance(dp, dict):
                continue
            val = dp.get("value")
            src = dp.get("source")
            conf = dp.get("confidence")
            attempts = dp.get("attempts", [])

            if val is not None:
                icon = "FOUND"
                detail = f"{val} {dp.get('unit', '')}".strip()
                if src:
                    detail += f" (source: {src}, confidence: {conf})"
            else:
                icon = "MISSING"
                detail = f"({len(attempts)} attempt(s))"
                if attempts:
                    last = attempts[-1]
                    detail += f" — last tried: {last.get('query', '?')[:50]}"

            lines.append(f"  [{icon}] {key}: {detail}")
        lines.append("")

    # Entities (cross-company comparison)
    entities = state.get("entities", {})
    if entities:
        lines.append("ENTITY DATA:")
        for entity, metrics in entities.items():
            if not isinstance(metrics, dict):
                continue
            lines.append(f"  {entity}:")
            for metric, periods in metrics.items():
                if not isinstance(periods, dict):
                    continue
                cells = []
                for period, val in sorted(periods.items()):
                    if val is None:
                        cells.append(f"{period}: ???")
                    elif isinstance(val, dict):
                        v = val.get("value")
                        cells.append(f"{period}: {v}" if v is not None else f"{period}: ???")
                    else:
                        cells.append(f"{period}: {val}")
                lines.append(f"    {metric}: {', '.join(cells)}")
        lines.append("")

    # Calculation steps
    steps = state.get("calculation_steps", [])
    if steps:
        lines.append("CALCULATION STEPS:")
        for s in steps:
            result = s.get("result")
            if result is not None:
                lines.append(f"  [DONE] {s['step']} = {result}  ({s['formula']})")
            else:
                lines.append(f"  [TODO] {s['step']} = {s['formula']}")
        lines.append("")

    # Answer
    answer = state.get("answer", {})
    if answer.get("value") is not None:
        lines.append(f"ANSWER: {answer.get('formatted', answer['value'])}")
    else:
        lines.append("ANSWER: Not yet determined")

    return "\n".join(lines)


def validate_state_update(current: dict, update: dict) -> dict:
    """Validate and merge an agent's state update into the current state.

    Returns the merged state. Raises ValueError if the update is malformed.
    """
    merged = deepcopy(current)

    # Merge data_needed updates
    for key, dp in update.get("data_needed", {}).items():
        if key in merged.get("data_needed", {}):
            if isinstance(dp, dict):
                merged["data_needed"][key].update(dp)
            else:
                merged["data_needed"][key]["value"] = dp

    # Merge entity updates
    for entity, metrics in update.get("entities", {}).items():
        if entity not in merged.get("entities", {}):
            merged.setdefault("entities", {})[entity] = {}
        for metric, periods in (metrics if isinstance(metrics, dict) else {}).items():
            if metric not in merged["entities"][entity]:
                merged["entities"][entity][metric] = {}
            if isinstance(periods, dict):
                merged["entities"][entity][metric].update(periods)

    # Merge calculation results
    for updated_step in update.get("calculation_steps", []):
        step_name = updated_step.get("step")
        for s in merged.get("calculation_steps", []):
            if s["step"] == step_name and updated_step.get("result") is not None:
                s["result"] = updated_step["result"]

    # Merge answer
    if update.get("answer"):
        merged["answer"].update(update["answer"])

    # Merge clarifications
    if update.get("clarifications"):
        merged.setdefault("clarifications", {}).update(update["clarifications"])

    return merged
