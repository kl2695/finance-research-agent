"""Calculator — executes calculation steps as Python code.

No LLM calls. Deterministic arithmetic.
The LLM defines formulas in the plan; Python computes them.
"""

from __future__ import annotations

import logging
import math

log = logging.getLogger(__name__)


def execute_calculations(state: dict) -> dict:
    """Execute all calculation steps using data from the state dict.

    Returns the state with calculation results filled in.
    """
    data = _collect_values(state)
    steps = state.get("calculation_steps", [])

    for step in steps:
        if step.get("result") is not None:
            continue  # Already computed

        formula = step.get("formula", "")
        inputs = step.get("inputs", [])

        # Check all inputs are available
        missing = [i for i in inputs if i not in data]
        if missing:
            log.warning(f"Cannot compute {step['step']}: missing {missing}")
            continue

        try:
            result = _eval_formula(formula, data)
            step["result"] = result
            # Make the result available for subsequent steps
            data[step["step"]] = result
            log.info(f"Calculated {step['step']} = {result} ({formula})")
        except Exception as e:
            log.warning(f"Calculation error for {step['step']}: {e}")
            step["result"] = f"ERROR: {e}"

    # Fill in the answer if the last step completed
    if steps and steps[-1].get("result") is not None:
        last_result = steps[-1]["result"]
        if not isinstance(last_result, str) or not last_result.startswith("ERROR"):
            state["answer"]["value"] = last_result
            state["answer"]["work_shown"] = _build_work_shown(steps, data)

    return state


def _collect_values(state: dict) -> dict:
    """Collect all known values into a flat dict for formula evaluation."""
    values = {}

    # From data_needed
    for key, dp in state.get("data_needed", {}).items():
        if isinstance(dp, dict) and dp.get("value") is not None:
            num = _to_number(dp["value"])
            if num is not None:
                values[key] = num
            else:
                log.warning(f"Cannot convert {key}={dp['value']} to number — skipping")

    # From entity data (flatten as entity_metric_period)
    for entity, metrics in state.get("entities", {}).items():
        if not isinstance(metrics, dict):
            continue
        for metric, periods in metrics.items():
            if not isinstance(periods, dict):
                continue
            for period, val in periods.items():
                if val is None:
                    continue
                v = val.get("value") if isinstance(val, dict) else val
                if v is not None:
                    num = _to_number(v)
                    if num is not None:
                        flat_key = f"{entity}_{metric}_{period}"
                        values[flat_key] = num

    # From prior calculation step results
    for step in state.get("calculation_steps", []):
        if step.get("result") is not None:
            result = step["result"]
            if not isinstance(result, str) or not result.startswith("ERROR"):
                num = _to_number(result)
                if num is not None:
                    values[step["step"]] = num

    return values


def _to_number(val) -> float | None:
    """Convert a value to a float for calculation. Returns None if not convertible."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # Strip common formatting
        cleaned = val.replace(",", "").replace("$", "").replace("%", "").strip()
        if not cleaned:
            return None
        # Handle B/M/K suffixes
        multipliers = {"B": 1e9, "b": 1e9, "M": 1e6, "m": 1e6, "K": 1e3, "k": 1e3, "T": 1e12}
        for suffix, mult in multipliers.items():
            if cleaned.endswith(suffix):
                try:
                    return float(cleaned[:-1]) * mult
                except ValueError:
                    return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _eval_formula(formula: str, data: dict) -> float:
    """Safely evaluate a formula string with the given data.

    Only allows arithmetic operations and math functions.
    """
    # Build a safe namespace with only the data and math functions
    safe_ns = {
        "__builtins__": {},
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
        "pow": pow,
        "sqrt": math.sqrt,
        "log": math.log,
    }
    safe_ns.update(data)

    result = eval(formula, safe_ns)
    return round(float(result), 6)


def _build_work_shown(steps: list[dict], data: dict) -> str:
    """Build a human-readable calculation trace."""
    lines = []
    for step in steps:
        result = step.get("result")
        if result is None:
            continue
        formula = step["formula"]
        # Substitute known values into the formula for readability
        display = formula
        for key, val in data.items():
            if key in display:
                if isinstance(val, float) and val > 1e6:
                    formatted = f"{val:,.0f}"
                elif isinstance(val, float):
                    formatted = f"{val:.4f}"
                else:
                    formatted = str(val)
                display = display.replace(key, formatted)
        if isinstance(result, float):
            lines.append(f"{step['step']} = {display} = {result:.4f}")
        else:
            lines.append(f"{step['step']} = {display} = {result}")
    return "\n".join(lines)
