"""Research Agent — domain-agnostic orchestrator.

Pipeline: Plan → Prefetch → ReAct tool loop → Extract → Calculate → Format answer.
All domain-specific logic is injected via the Domain interface.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.llm import call_claude, call_with_tools, parse_json_response, MODEL_HAIKU
from core.calculator import execute_calculations
from core.extractor import extract_from_tool_log
from domains.base import Domain

log = logging.getLogger(__name__)

# Action log — accumulates all agent actions for post-hoc analysis
_action_log: list[dict] = []

# Anthropic server-side web search — available to all domains
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}


def _log_action(action: str, details: dict | None = None):
    """Record an action to the action log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "details": details or {},
    }
    _action_log.append(entry)
    log.info(f"[{action}] {json.dumps(details or {}, default=str)[:200]}")


def run(question: str, domain: Domain, as_of_date: str | None = None) -> dict:
    """Run the full agent pipeline on a question.

    Args:
        question: The research question to answer.
        domain: Domain implementation providing all domain-specific logic.
        as_of_date: Optional date override (YYYY-MM-DD). When set, the planner
                    uses this as "today's date" — useful for benchmark evaluation.
    """
    start = time.time()

    # Reset action log for this run
    global _action_log
    _action_log = []

    # --- Step 1: Plan ---
    log.info(f"Planning: {question[:80]}...")
    state = _plan(question, domain, as_of_date=as_of_date)
    _log_action("plan_created", {
        "plan": state.get("plan", ""),
        "data_needed": list(state.get("data_needed", {}).keys()),
        "calc_steps": len(state.get("calculation_steps", [])),
        "source_strategy": state.get("clarifications", {}).get("source_strategy", ""),
        "company": state.get("clarifications", {}).get("company", ""),
        "period": state.get("clarifications", {}).get("period", ""),
    })
    log.info(f"Plan: {state.get('plan', '?')}")

    # --- Step 2a: Pre-fetch data programmatically ---
    log.info("Pre-fetching data...")
    prefetch_results, prefetch_log = _prefetch_data(state, domain)
    if prefetch_results:
        _log_action("prefetch_data", {
            "results_count": len(prefetch_results),
            "tools_called": len(prefetch_log),
        })

    # --- Step 2b: ReAct tool loop (with pre-fetched data injected) ---
    log.info("Researching...")

    prefetch_char_limit = domain.context_size_tier(state)

    prefetch_context = ""
    if prefetch_results:
        prefetch_context = "\n\nPRE-FETCHED DATA (use these as primary source):\n"
        for label, data in prefetch_results.items():
            prefetch_context += f"\n--- {label} ---\n{data[:prefetch_char_limit]}\n"

    research_date = as_of_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    final_text, tool_log = call_with_tools(
        system=domain.react_system,
        user_message=domain.react_prompt_template.format(
            question=question,
            plan=json.dumps(state, indent=2),
            date=research_date,
        ) + prefetch_context,
        tools=[WEB_SEARCH_TOOL] + domain.react_tools,
        tool_executor=domain.execute_tool,
        # Complex questions (many data points or many filings) need more turns.
        max_turns=15 if len(state.get("filings_needed", [])) > 4 else 10,
    )
    tool_log = prefetch_log + tool_log

    # Log tool usage summary
    tool_counts: dict[str, int] = {}
    for t in tool_log:
        name = t.get("tool", "?")
        tool_counts[name] = tool_counts.get(name, 0) + 1
    _log_action("research_complete", {"tool_counts": tool_counts, "total": len(tool_log)})
    log.info(f"Research done — {len(tool_log)} tool calls: {tool_counts}")

    # --- Step 3: Extract data from tool results + Calculate ---
    state["_tool_log"] = tool_log

    # Use domain to classify which tools produce structured vs prose data
    classified = domain.classify_tools(tool_log)
    structured_log = classified.get("structured", [])
    prose_log = classified.get("prose", [])

    # Step 3a: Structured extraction (e.g., XBRL concept matching)
    if structured_log:
        state = extract_from_tool_log(structured_log, state, domain)

    # Step 3b: Prose text extraction with domain pre/post hooks
    if prose_log:
        state, stash = domain.pre_extraction_filter(state)
        state = extract_from_tool_log(prose_log, state, domain)
        state = domain.post_extraction_restore(state, stash)

    # Step 3c: LLM fact matching for anything still unfilled
    still_unfilled = [k for k, dp in state.get("data_needed", {}).items()
                      if isinstance(dp, dict) and dp.get("value") is None]
    if still_unfilled and prose_log:
        from core.extractor import _parse_filing_text, llm_match_facts_to_keys

        all_facts = []
        for source_idx, entry in enumerate(prose_log):
            output = entry.get("output", "")
            tool_input = entry.get("input", {})
            source_label = tool_input.get("quarter", "") or tool_input.get("period", "") or ""
            source_type = tool_input.get("type", entry.get("tool", ""))
            source_desc = f"{source_type} {source_label}".strip()

            new_facts = _parse_filing_text(output)
            for f in new_facts:
                f["source_idx"] = source_idx
                f["source_doc"] = source_desc
            all_facts.extend(new_facts)

        if all_facts:
            state = llm_match_facts_to_keys(all_facts, state, domain)

    # Step 3d: LLM raw extraction for everything still missing
    missing = [k for k, dp in state.get("data_needed", {}).items()
               if isinstance(dp, dict) and dp.get("value") is None]

    if missing:
        _log_action("llm_extraction_needed", {"missing": missing, "prose_sources": len(prose_log)})
        _llm_extract_remaining(final_text, state, missing, prefetch_results=prefetch_results)

        still_missing = [k for k, dp in state.get("data_needed", {}).items()
                         if isinstance(dp, dict) and dp.get("value") is None]
        if still_missing:
            _log_action("extraction_incomplete", {"still_missing": still_missing})
        else:
            _log_action("all_data_extracted", {
                "values": {k: dp.get("value") for k, dp in state.get("data_needed", {}).items() if isinstance(dp, dict)},
                "method": "hybrid (structured + LLM)"
            })
    elif not missing:
        _log_action("all_data_extracted_from_structured", {
            "values": {k: dp.get("value") for k, dp in state.get("data_needed", {}).items() if isinstance(dp, dict)}
        })

    # --- Step 3.5: Cross-validation ---
    state = domain.cross_validate(state)

    calc_steps = state.get("calculation_steps", [])
    if calc_steps:
        state = execute_calculations(state)
        _log_action("calculations_complete", {
            "steps": [{
                "step": s["step"],
                "formula": s["formula"],
                "result": s.get("result"),
            } for s in calc_steps],
        })

    # --- Step 4: Format answer ---
    log.info("Formatting answer...")
    answer = _format_answer(question, final_text, state, domain)

    elapsed = time.time() - start
    log.info(f"Done in {elapsed:.1f}s — {len(tool_log)} tool calls")

    _log_action("run_complete", {"elapsed": elapsed, "tool_calls": len(tool_log)})

    return {
        "question": question,
        "answer": answer,
        "state": state,
        "research_text": final_text,
        "tool_log": tool_log,
        "action_log": list(_action_log),
        "elapsed": elapsed,
    }


# --- Plan caching ---

_PLAN_CACHE_PATH = Path("results/planner_cache.json")


def _load_plan_cache() -> dict:
    if _PLAN_CACHE_PATH.exists():
        try:
            with open(_PLAN_CACHE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_plan_cache(cache: dict):
    _PLAN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PLAN_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, default=str)


def _plan(question: str, domain: Domain, as_of_date: str | None = None,
          use_cache: bool = True) -> dict:
    """Step 1: Create the research plan.

    Cache key includes domain name to avoid collisions between domains.
    """
    today = as_of_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Cache key includes domain name (spec §12.7)
    cache_key = hashlib.md5(f"{question}|{today}|{domain.name}".encode()).hexdigest()

    if use_cache:
        cache = _load_plan_cache()
        if cache_key in cache:
            log.info("  Using cached plan")
            state = cache[cache_key]
            for dp in state.get("data_needed", {}).values():
                if isinstance(dp, dict):
                    dp["value"] = None
                    dp["source"] = None
                    dp["confidence"] = None
            for step in state.get("calculation_steps", []):
                step["result"] = None
            state["answer"] = {"value": None, "formatted": None, "sources": [], "work_shown": None}
            return state

    response = call_claude(
        system=domain.planner_system,
        messages=[{"role": "user", "content": domain.planner_prompt_template.format(
            question=question,
            date=today,
        )}],
        max_tokens=4096,
    )

    try:
        state = parse_json_response(response.content[0].text)
    except json.JSONDecodeError:
        state = {"plan": question, "clarifications": {}, "data_needed": {},
                 "entities": {}, "calculation_steps": [],
                 "answer": {"value": None, "formatted": None, "sources": [], "work_shown": None}}

    state.setdefault("answer", {"value": None, "formatted": None, "sources": [], "work_shown": None})
    state.setdefault("data_needed", {})
    state.setdefault("calculation_steps", [])
    state.setdefault("filings_needed", [])

    if use_cache:
        cache = _load_plan_cache()
        cache[cache_key] = state
        _save_plan_cache(cache)

    return state


# --- Prefetch ---

def _prefetch_data(state: dict, domain: Domain) -> tuple[dict[str, str], list[dict]]:
    """Pre-fetch data using the domain's tool dispatch.

    Iterates filings_needed, dispatches each by type via domain.tool_dispatch.
    """
    results: dict[str, str] = {}
    tool_log: list[dict] = []
    clarifications = state.get("clarifications", {})
    company = clarifications.get("company", "")

    identifier = domain.extract_identifier(company)
    if not identifier:
        log.info("  No identifier found in clarifications — skipping prefetch")
        return results, tool_log

    filings_needed = state.get("filings_needed", [])

    # Tag qualitative flag for domain fetchers that need it
    is_qualitative = (
        not state.get("calculation_steps") and
        "lookup" in clarifications.get("formula", "").lower()
    )

    dispatch = domain.tool_dispatch

    for filing in filings_needed:
        if not isinstance(filing, dict):
            continue
        ftype = filing.get("type", "").upper()
        reason = filing.get("reason", "")

        # Pass qualitative flag to domain fetchers
        filing["_is_qualitative"] = is_qualitative

        fetcher = dispatch.get(ftype) or dispatch.get(ftype.lower())
        if not fetcher:
            log.info(f"  No fetcher for type '{ftype}' — skipping")
            continue

        try:
            tool_results = fetcher(filing, identifier)
            if not isinstance(tool_results, list):
                tool_results = [tool_results]
            for tr in tool_results:
                if tr.success and tr.raw:
                    period = filing.get("period", "")
                    section = filing.get("section", "")
                    # "identifier" is the standard key; "ticker" is a finance-domain alias
                    filing_id = filing.get("identifier") or filing.get("ticker") or identifier
                    label = f"{ftype} {filing_id} {period} {section} ({reason})".strip()
                    results[label] = tr.raw
                    tool_log.append({
                        "tool": tr.tool_name,
                        "input": tr.input_data,
                        "output": tr.raw,
                    })
                    log.info(f"  Prefetched {ftype} {period} {section} for {filing_id} ({reason})")
        except Exception as e:
            log.warning(f"  Prefetch {ftype} failed: {e}")

    return results, tool_log


# --- LLM Extraction Fallback ---

def _llm_extract_remaining(text: str, state: dict, missing_keys: list[str],
                           prefetch_results: dict[str, str] | None = None):
    """LLM fallback: extract values the structured parser couldn't find.

    Uses raw source text (not agent summary) for precision.
    """
    try:
        descriptions = {}
        clarifications = state.get("clarifications", {})
        period_context = clarifications.get("period", "")
        source_strategy = clarifications.get("source_strategy", "")
        for key in missing_keys:
            dp = state.get("data_needed", {}).get(key, {})
            label = dp.get("label", key) if isinstance(dp, dict) else key
            unit = dp.get("unit", "") if isinstance(dp, dict) else ""
            descriptions[key] = f"{label} ({unit})" if unit else label

        has_guidance_keys = any("guid" in k.lower() for k in missing_keys)
        has_actual_keys = any("guid" not in k.lower() for k in missing_keys)

        raw_sources = ""
        for entry in state.get("_tool_log", []):
            tool = entry.get("tool", "")
            output = entry.get("output", "")
            if len(output) < 50:
                continue

            is_guidance_source = any(kw in output.lower() for kw in
                                    ["outlook", "guidance", "expects", "anticipates"])

            if has_guidance_keys and is_guidance_source:
                outlook_match = re.search(
                    r'(?i)(fourth quarter|q4)\s*\d{4}\s*outlook', output)
                if not outlook_match:
                    outlook_match = re.search(
                        r'(?i)\boutlook\b.{0,20}(gross booking|adjusted ebitda|revenue)', output)
                if outlook_match:
                    start = outlook_match.start()
                    section = output[start:start + 1500]
                    raw_sources += f"\n---GUIDANCE SOURCE---\n{section}\n"
                else:
                    raw_sources += f"\n---{tool} (contains guidance)---\n{output[:2000]}\n"

            if has_actual_keys and not is_guidance_source:
                raw_sources += f"\n---ACTUALS SOURCE---\n{output[:2000]}\n"

        if prefetch_results:
            for label, content in prefetch_results.items():
                if content and len(content) > 50 and label not in raw_sources:
                    raw_sources += f"\n---PREFETCHED: {label}---\n{content[:3000]}\n"

        if not raw_sources:
            raw_sources = text[:4000]
            for entry in state.get("_tool_log", []):
                output = entry.get("output", "")
                if output and len(output) > 10:
                    raw_sources += f"\n{output[:1500]}\n"

        response = call_claude(
            system=(
                "Extract specific numeric values from source documents. "
                "Respond with a JSON object ONLY — no explanation, no prose. "
                "CRITICAL: Do NOT round numbers."
            ),
            messages=[
                {"role": "user", "content": f"""\
Find these specific values from the source documents below.

PERIOD: {period_context}
SOURCE STRATEGY: {source_strategy}

DATA FIELDS NEEDED:
{json.dumps(descriptions, indent=2)}

IMPORTANT DISTINCTIONS:
- Keys containing "guided" or "guidance" → extract from OUTLOOK/GUIDANCE section, NOT actuals
- Keys containing "actual" or without "guided" → extract from RESULTS section, NOT guidance
- If guidance is a RANGE, extract the low and high separately
- Use EXACT numbers. "$4,278.9 million" → 4278.9, NOT 4300
- Do NOT mix annual and quarterly figures — match the period specified
- Return values in the unit specified (e.g., if unit is "USD millions", return 4278.9 not 4278900000)

SOURCE DOCUMENTS:
{raw_sources[:8000]}

Return ONLY a JSON object: {{"key_name": numeric_value, ...}}
Use {{}} if no values found."""},
                {"role": "assistant", "content": "{"},
            ],
            max_tokens=512,
        )
        raw_text = "{" + response.content[0].text
        log.info(f"  LLM extraction raw response: {raw_text[:300]}")

        values: dict = {}
        try:
            values = json.loads(raw_text.strip())
        except (json.JSONDecodeError, ValueError):
            try:
                values = parse_json_response(raw_text)
            except (json.JSONDecodeError, ValueError):
                json_match = re.search(r'\{[^{}]*\}', raw_text)
                if json_match:
                    try:
                        values = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        pass
                if not values:
                    log.warning(f"  LLM extraction returned non-JSON: {raw_text[:200]}")

        for key, val in values.items():
            if val is not None and key in state.get("data_needed", {}):
                dp = state["data_needed"][key]
                if isinstance(dp, dict):
                    dp["value"] = val
                    dp["source"] = "LLM extraction from source documents"
                    dp["confidence"] = "medium"
                    log.info(f"  LLM extracted {key} = {val}")
    except Exception as e:
        log.warning(f"LLM extraction fallback failed: {e}")


# --- Answer Formatting ---

def _format_answer(question: str, research_text: str, state: dict, domain: Domain) -> str:
    """Step 4: Format the final answer."""
    calc_info = ""
    calc_steps = state.get("calculation_steps", [])
    completed = [s for s in calc_steps if s.get("result") is not None]
    if completed:
        calc_info = "\n\nCALCULATION RESULTS:\n"
        for s in completed:
            calc_info += f"  {s['step']} = {s['formula']} = {s['result']}\n"
        answer_val = state.get("answer", {}).get("value")
        if answer_val:
            calc_info += f"\n  FINAL: {answer_val}"
        work = state.get("answer", {}).get("work_shown", "")
        if work:
            calc_info += f"\n\nWORK:\n{work}"

    response = call_claude(
        system=domain.answer_system,
        messages=[{"role": "user", "content": domain.answer_prompt_template.format(
            question=question,
            state=f"RESEARCH FINDINGS:\n{research_text[:6000]}{calc_info}",
        )}],
        max_tokens=1024,
        model=MODEL_HAIKU,
    )

    return response.content[0].text
