"""Finance Research Agent — main orchestrator.

Simple pipeline: Plan → ReAct tool loop → Calculate → Format answer.
The agent reasons in natural language. The orchestrator tracks state.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from src.llm import call_claude, call_with_tools, parse_json_response, MODEL_HAIKU
from src.calculator import execute_calculations
from src.extractor import extract_from_tool_log
from src.prompts import PLANNER_SYSTEM, PLANNER_PROMPT, REACT_SYSTEM, REACT_PROMPT, ANSWER_SYSTEM, ANSWER_PROMPT
from src.tools.registry import ALL_TOOLS, execute_tool

log = logging.getLogger(__name__)

# Action log — accumulates all agent actions for post-hoc analysis
_action_log: list[dict] = []


def _log_action(action: str, details: dict | None = None):
    """Record an action to the action log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "details": details or {},
    }
    _action_log.append(entry)
    log.info(f"[{action}] {json.dumps(details or {}, default=str)[:200]}")


def run(question: str, as_of_date: str | None = None) -> dict:
    """Run the full agent pipeline on a question.

    Args:
        question: The financial research question to answer.
        as_of_date: Optional date override (YYYY-MM-DD). When set, the planner
                    uses this as "today's date" — useful for benchmark evaluation
                    where the ground truth assumes a specific point in time.
    """
    start = time.time()

    # Reset action log for this run
    global _action_log
    _action_log = []

    # --- Step 1: Plan ---
    log.info(f"Planning: {question[:80]}...")
    state = _plan(question, as_of_date=as_of_date)
    _log_action("plan_created", {
        "plan": state.get("plan", ""),
        "data_needed": list(state.get("data_needed", {}).keys()),
        "calc_steps": len(state.get("calculation_steps", [])),
        "source_strategy": state.get("clarifications", {}).get("source_strategy", ""),
        "company": state.get("clarifications", {}).get("company", ""),
        "period": state.get("clarifications", {}).get("period", ""),
    })
    log.info(f"Plan: {state.get('plan', '?')}")

    # --- Step 2a: Pre-fetch SEC data programmatically ---
    log.info("Pre-fetching SEC data...")
    prefetch_results, prefetch_log = _prefetch_sec_data(state)
    if prefetch_results:
        _log_action("prefetch_sec_data", {
            "results_count": len(prefetch_results),
            "tools_called": len(prefetch_log),
        })

    # --- Step 2b: ReAct tool loop (with pre-fetched data injected) ---
    log.info("Researching...")

    # Qualitative questions need more filing context (P75)
    # Detect: no calculation steps + formula is "lookup only"
    is_qualitative = (
        not state.get("calculation_steps") and
        "lookup" in state.get("clarifications", {}).get("formula", "").lower()
    )
    # Context injection size depends on question type:
    # - Qualitative (text answer): 50K per source (need full sections)
    # - Quantitative with filing sections: 15K per source (need tables from sections)
    # - Simple XBRL calculation: 4K per source (just need confirmation)
    has_filing_sections = any(
        f.get("type", "").upper() in ("10-K", "10-Q") and f.get("section")
        for f in state.get("filings_needed", [])
    )
    if is_qualitative:
        prefetch_char_limit = 50000
    elif has_filing_sections:
        prefetch_char_limit = 15000
    else:
        prefetch_char_limit = 4000

    prefetch_context = ""
    if prefetch_results:
        prefetch_context = "\n\nPRE-FETCHED SEC FILING DATA (use these as primary source):\n"
        for label, data in prefetch_results.items():
            prefetch_context += f"\n--- {label} ---\n{data[:prefetch_char_limit]}\n"

    research_date = as_of_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    final_text, tool_log = call_with_tools(
        system=REACT_SYSTEM,
        user_message=REACT_PROMPT.format(
            question=question,
            plan=json.dumps(state, indent=2),
            date=research_date,
        ) + prefetch_context,
        tools=ALL_TOOLS,
        tool_executor=execute_tool,
        max_turns=10,
    )
    tool_log = prefetch_log + tool_log

    # Log tool usage summary
    tool_counts = {}
    for t in tool_log:
        name = t.get("tool", "?")
        tool_counts[name] = tool_counts.get(name, 0) + 1
    _log_action("research_complete", {"tool_counts": tool_counts, "total": len(tool_log)})
    log.info(f"Research done — {len(tool_log)} tool calls: {tool_counts}")

    # --- Step 3: Extract data from tool results + Calculate ---
    # Store tool log in state for the LLM extraction fallback
    state["_tool_log"] = tool_log

    # HYBRID EXTRACTION:
    # 1. XBRL: exact concept matching (reliable, no LLM needed)
    # 2. Structured text matching for known financial terms (precise, handles tables)
    # 3. LLM fact matching for novel metrics the keyword map doesn't cover (scalable)
    # 4. LLM raw extraction fallback for anything still missing

    # Step 3a: Structured extraction for XBRL (concept names are exact)
    xbrl_log = [t for t in tool_log if t.get("tool") == "sec_edgar_financials"]
    if xbrl_log:
        state = extract_from_tool_log(xbrl_log, state)

    # Step 3b: Structured text extraction for known metrics (handles tables, position, type)
    prose_log = [t for t in tool_log if t.get("tool") in ("sec_edgar_earnings", "sec_edgar_filing_text")]
    if prose_log:
        # Temporarily hide guidance keys — structured extractor can't distinguish actuals vs guidance
        guidance_keys = {}
        for k, dp in list(state.get("data_needed", {}).items()):
            if "guid" in k.lower() and isinstance(dp, dict) and dp.get("value") is None:
                guidance_keys[k] = state["data_needed"].pop(k)

        state = extract_from_tool_log(prose_log, state)

        # Restore guidance keys
        state["data_needed"].update(guidance_keys)

    # Step 3c: LLM fact matching for anything still unfilled (scales to novel metrics)
    still_unfilled = [k for k, dp in state.get("data_needed", {}).items()
                      if isinstance(dp, dict) and dp.get("value") is None]
    if still_unfilled and prose_log:
        from src.extractor import _parse_filing_text, llm_match_facts_to_keys

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
            state = llm_match_facts_to_keys(all_facts, state)

    # Step 3c: LLM raw extraction for everything still missing
    missing = [k for k, dp in state.get("data_needed", {}).items()
               if isinstance(dp, dict) and dp.get("value") is None]

    if missing:
        _log_action("llm_extraction_needed", {"missing": missing, "prose_sources": len(prose_log)})
        # Feed raw press release text to LLM extraction — not the agent's summary
        # Also pass prefetched section results directly (they may not be in tool_log)
        _llm_extract_remaining(final_text, state, missing, prefetch_results=prefetch_results)

        still_missing = [k for k, dp in state.get("data_needed", {}).items()
                         if isinstance(dp, dict) and dp.get("value") is None]
        if still_missing:
            _log_action("extraction_incomplete", {"still_missing": still_missing})
        else:
            _log_action("all_data_extracted", {
                "values": {k: dp.get("value") for k, dp in state.get("data_needed", {}).items() if isinstance(dp, dict)},
                "method": "hybrid (XBRL structured + LLM for press release)"
            })
    elif not missing:
        _log_action("all_data_extracted_from_xbrl", {
            "values": {k: dp.get("value") for k, dp in state.get("data_needed", {}).items() if isinstance(dp, dict)}
        })

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
    answer = _format_answer(question, final_text, state)

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


def _plan(question: str, as_of_date: str | None = None) -> dict:
    """Step 1: Create the research plan."""
    today = as_of_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    response = call_claude(
        system=PLANNER_SYSTEM,
        messages=[{"role": "user", "content": PLANNER_PROMPT.format(
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
    return state


def _prefetch_sec_data(state: dict) -> tuple[dict[str, str], list[dict]]:
    """Pre-fetch SEC filing data based on the plan's filings_needed and data_needed.

    Primary path: uses the planner's structured filings_needed list.
    Fallback: keyword-based heuristics for XBRL concepts and section detection.

    Returns (results_dict, tool_log) where results_dict maps labels to raw tool output.
    """
    results = {}
    tool_log = []
    clarifications = state.get("clarifications", {})
    company = clarifications.get("company", "")

    # Extract ticker from company string
    from src.ticker import extract_ticker
    ticker = extract_ticker(company)

    if not ticker:
        log.info("  No ticker found in clarifications — skipping prefetch")
        return results, tool_log

    # --- PRIMARY PATH: Structured filings_needed from planner ---
    filings_needed = state.get("filings_needed", [])

    # Qualitative questions need more filing text (P75)
    is_qualitative = (
        not state.get("calculation_steps") and
        "lookup" in clarifications.get("formula", "").lower()
    )

    if filings_needed:
        from src.tools.sec_edgar import get_filing_text, get_earnings_press_release, get_company_facts
        for filing in filings_needed:
            if not isinstance(filing, dict):
                continue
            ftype = filing.get("type", "").upper()
            period = filing.get("period", "")
            section = filing.get("section")
            reason = filing.get("reason", "")
            # Multi-company support (P82): each filing entry can specify its own ticker
            filing_ticker = filing.get("ticker") or ticker
            if filing_ticker and filing_ticker != ticker:
                # Resolve the per-entry ticker to ensure it works
                from src.ticker import extract_ticker as _et
                filing_ticker = _et(filing_ticker) or filing_ticker
            try:
                if ftype == "XBRL":
                    # XBRL concept fetch — planner specifies exact concept names
                    concepts = filing.get("concepts", [])
                    for concept in concepts:
                        output = get_company_facts(filing_ticker, concept)
                        if "No XBRL data found" not in output and len(output) > 50:
                            results[f"XBRL {concept} ({filing_ticker})"] = output
                            tool_log.append({
                                "tool": "sec_edgar_financials",
                                "input": {"ticker": filing_ticker, "metric": concept},
                                "output": output,
                            })
                            log.info(f"  Prefetched XBRL {concept} for {filing_ticker} ({reason})")
                            break  # Found data, don't try alternatives
                        else:
                            log.info(f"  XBRL {concept} not found for {filing_ticker}, trying next")
                    continue
                elif ftype == "8-K" and period:
                    # Earnings press release
                    output = get_earnings_press_release(filing_ticker, period)
                    label = f"Earnings {filing_ticker} {period} ({reason})"
                elif ftype in ("10-K", "10-Q"):
                    # Qualitative questions get more filing text (P75)
                    filing_max_chars = 50000 if is_qualitative else 15000
                    output = get_filing_text(filing_ticker, ftype, section=section, period=period,
                                            max_chars=filing_max_chars)
                    label = f"{ftype} {filing_ticker} {period} {section or ''} ({reason})"
                else:
                    continue

                if "No " not in output[:50] and len(output) > 100:
                    results[label] = output
                    tool_name = "sec_edgar_earnings" if ftype == "8-K" else "sec_edgar_filing_text"
                    tool_log.append({
                        "tool": tool_name,
                        "input": {"ticker": ticker, "type": ftype, "period": period, "section": section},
                        "output": output,
                    })
                    log.info(f"  Prefetched {ftype} {period} {section or ''} for {ticker} ({reason})")
                else:
                    log.info(f"  Filing not found: {ftype} {period} {section or ''} for {ticker}")
            except Exception as e:
                log.warning(f"  Prefetch {ftype} {period} failed: {e}")

    # --- FALLBACK: Keyword-based heuristics for XBRL and sections ---
    # Only runs if filings_needed was empty (planner didn't specify structured requests).
    # When filings_needed is populated, it already handles XBRL + filings — no need for heuristics.
    if filings_needed:
        return results, tool_log

    # Determine which XBRL metrics to fetch based on data_needed keys.
    # Map keywords to a list of XBRL concepts to try (primary + alternatives).
    # Companies use different concept names — e.g., "Revenues" vs
    # "RevenueFromContractWithCustomerExcludingAssessedTax".
    xbrl_concepts = []  # list of lists: [[primary, alt1, alt2], ...]
    concept_map = {
        "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue"],
        "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
        "inventory": ["InventoryNet"],
        "net_income": ["NetIncomeLoss"],
        "operating_income": ["OperatingIncomeLoss"],
        "total_assets": ["Assets"],
        "debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
        "cash_equivalent": ["CashAndCashEquivalentsAtCarryingValue"],
        "depreciation": ["DepreciationAndAmortization"],
        "interest": ["InterestExpense"],
        "shares": ["CommonStockSharesOutstanding"],
        "gross_profit": ["GrossProfit"],
        "equity": ["StockholdersEquity"],
        "receivable": ["AccountsReceivableNetCurrent"],
        "income_tax": ["IncomeTaxExpenseBenefit"],
        "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                    "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"],
    }

    seen_keywords = set()
    for key in state.get("data_needed", {}).keys():
        key_lower = key.lower()
        for keyword, concepts in concept_map.items():
            if keyword in key_lower and keyword not in seen_keywords:
                seen_keywords.add(keyword)
                xbrl_concepts.append(concepts)

    # Fetch XBRL data for each concept group — try alternatives if primary returns nothing
    from src.tools.sec_edgar import get_company_facts
    for concept_group in xbrl_concepts:
        for concept in concept_group:
            try:
                output = get_company_facts(ticker, concept)
                if "No XBRL data found" in output or "not found" in output.lower():
                    log.info(f"  {concept} not found for {ticker}, trying next alternative")
                    continue  # Try next alternative
                results[f"XBRL {concept}"] = output
                tool_log.append({
                    "tool": "sec_edgar_financials",
                    "input": {"ticker": ticker, "metric": concept},
                    "output": output,
                })
                log.info(f"  Prefetched {concept} for {ticker}")
                break  # Found data, don't try alternatives
            except Exception as e:
                log.warning(f"  Prefetch failed for {concept}: {e}")

    # Also fetch the most relevant filing text if source_strategy mentions specific filing types
    source_strategy = clarifications.get("source_strategy", "")
    period = clarifications.get("period", "")

    if "8-k" in source_strategy.lower() or "press release" in source_strategy.lower() or "earnings" in source_strategy.lower():
        # Use the earnings press release tool — finds the right exhibit, not just the 8-K cover
        period = clarifications.get("period", "")
        quarter_match = re.search(r'Q\d\s*\d{4}', period) or re.search(r'Q\d\s*\d{4}', source_strategy)
        if quarter_match:
            quarter = quarter_match.group()
            try:
                from src.tools.sec_edgar import get_earnings_press_release
                output = get_earnings_press_release(ticker, quarter)
                results[f"Earnings Press Release {quarter}"] = output
                tool_log.append({
                    "tool": "sec_edgar_earnings",
                    "input": {"ticker": ticker, "quarter": quarter},
                    "output": output,
                })
                log.info(f"  Prefetched earnings press release for {ticker} {quarter}")
            except Exception as e:
                log.warning(f"  Prefetch earnings failed: {e}")

        # Also try the prior quarter for guidance
        if quarter_match:
            q = quarter_match.group()
            q_num = int(q[1])
            q_year = int(q[3:].strip())
            prior_q = q_num - 1 if q_num > 1 else 4
            prior_year = q_year if q_num > 1 else q_year - 1
            prior_quarter = f"Q{prior_q} {prior_year}"
            try:
                from src.tools.sec_edgar import get_earnings_press_release
                output = get_earnings_press_release(ticker, prior_quarter)
                results[f"Prior Quarter Earnings {prior_quarter} (for guidance)"] = output
                tool_log.append({
                    "tool": "sec_edgar_earnings",
                    "input": {"ticker": ticker, "quarter": prior_quarter},
                    "output": output,
                })
                log.info(f"  Prefetched prior quarter earnings for {ticker} {prior_quarter} (guidance)")
            except Exception as e:
                log.warning(f"  Prefetch prior quarter failed: {e}")

    if "10-q" in source_strategy.lower():
        try:
            from src.tools.sec_edgar import get_filing_text
            # Try to extract quarter from period
            q_match = re.search(r'Q(\d)', period)
            period_param = f"Q{q_match.group(1)} {re.search(r'20d{2}', period).group()}" if q_match else None
            output = get_filing_text(ticker, "10-Q", period=period_param)
            results["10-Q Filing"] = output
            tool_log.append({
                "tool": "sec_edgar_filing_text",
                "input": {"ticker": ticker, "filing_type": "10-Q", "period": period_param},
                "output": output,
            })
            log.info(f"  Prefetched 10-Q for {ticker}")
        except Exception as e:
            log.warning(f"  Prefetch 10-Q failed: {e}")

    # Smart section prefetch: if the question or data_needed keys mention specific topics,
    # fetch the relevant 10-K section (these are deep in the filing, past the 15K default limit)
    question_lower = state.get("plan", "").lower() + " " + " ".join(state.get("data_needed", {}).keys()).lower()
    section_keywords = {
        "revenue": ["channel", "disaggregat", "revenue by", "sales channel", "customer type"],
        "tax": ["tax rate", "effective tax", "tax provision", "income_tax", "pretax"],
        "compensation": ["director compensation", "executive compensation", "compensation paid"],
        "leases": ["operating lease", "lease obligation", "right-of-use", "lease maturity"],
        "employees": ["employee", "headcount", "human capital", "workforce"],
        "shares": ["shares outstanding", "share repurchase", "stock repurchase", "buyback"],
        "cash_obligations": ["cash requirement", "cash commitment", "contractual obligation", "material cash"],
        "kpi": ["average revenue per", "average monthly revenue", "arpu", "revenue per member",
                "revenue per user", "same-store", "comparable store", "active rider",
                "gross booking", "take rate"],
        "reconciliation": ["ebitda adjust", "ebitda reconcil", "non-gaap reconcil", "reconciliation of gaap",
                           "stock-based compensation", "adjusted ebitda"],
        "officers": ["cfo", "ceo", "chief financial officer", "chief executive officer",
                     "officer", "director", "board member", "nominated"],
    }
    from src.tools.sec_edgar import get_filing_text
    for section, keywords in section_keywords.items():
        if any(kw in question_lower for kw in keywords):
            try:
                # Determine filing year: prefer source_strategy's explicit 10-K year,
                # then the LATEST year mentioned in period (not the first)
                source_strat = clarifications.get("source_strategy", "")
                tenk_match = re.search(r'(20\d{2})\s*10-K', source_strat)
                period_years = re.findall(r'(20\d{2})', clarifications.get("period", ""))
                fy_period = (tenk_match.group(1) if tenk_match
                             else max(period_years) if period_years
                             else None)
                output = get_filing_text(ticker, "10-K", section=section, period=fy_period)
                if "No " not in output[:50]:
                    results[f"10-K {section} section"] = output
                    tool_log.append({
                        "tool": "sec_edgar_filing_text",
                        "input": {"ticker": ticker, "filing_type": "10-K", "section": section, "period": fy_period},
                        "output": output,
                    })
                    log.info(f"  Prefetched 10-K {section} section for {ticker}")

                # Multi-period: if data_needed spans 4+ years, fetch an older 10-K too
                # (each 10-K shows ~3 years, so 6-year trend needs 2 filings)
                all_years = sorted(set(
                    int(m.group()) for k in state.get("data_needed", {})
                    for m in [re.search(r'(20\d{2})', k)] if m
                ))
                if len(all_years) >= 4 and fy_period:
                    older_year = str(min(all_years) + 1)  # e.g., 2020 data → FY2021 10-K has it
                    if older_year != fy_period:
                        older_output = get_filing_text(ticker, "10-K", section=section, period=older_year)
                        if "No " not in older_output[:50]:
                            results[f"10-K {section} section (older FY{older_year})"] = older_output
                            tool_log.append({
                                "tool": "sec_edgar_filing_text",
                                "input": {"ticker": ticker, "filing_type": "10-K", "section": section, "period": older_year},
                                "output": older_output,
                            })
                            log.info(f"  Prefetched older 10-K {section} section (FY{older_year}) for {ticker}")
            except Exception as e:
                log.warning(f"  Prefetch 10-K {section} section failed: {e}")

    return results, tool_log


def _llm_extract_remaining(text: str, state: dict, missing_keys: list[str],
                           prefetch_results: dict[str, str] | None = None):
    """LLM fallback: extract values the structured parser couldn't find.

    Uses raw press release text (not agent summary) for precision.
    Understands semantic context (actuals vs guidance).
    """
    try:
        # Build descriptions for each missing key
        descriptions = {}
        clarifications = state.get("clarifications", {})
        period_context = clarifications.get("period", "")
        source_strategy = clarifications.get("source_strategy", "")
        for key in missing_keys:
            dp = state.get("data_needed", {}).get(key, {})
            label = dp.get("label", key) if isinstance(dp, dict) else key
            unit = dp.get("unit", "") if isinstance(dp, dict) else ""
            descriptions[key] = f"{label} ({unit})" if unit else label

        # Collect raw press release / filing text from tool log (NOT agent summary)
        # Be smart about what we include: for guidance keys, prioritize the guidance source
        has_guidance_keys = any("guid" in k.lower() for k in missing_keys)
        has_actual_keys = any("guid" not in k.lower() for k in missing_keys)

        raw_sources = ""
        for entry in state.get("_tool_log", []):
            tool = entry.get("tool", "")
            output = entry.get("output", "")
            if tool not in ("sec_edgar_earnings", "sec_edgar_filing_text") or len(output) < 50:
                continue

            # For guidance keys, look for outlook/guidance sections specifically
            is_guidance_source = any(kw in output.lower() for kw in ["outlook", "guidance", "expects", "anticipates"])

            if has_guidance_keys and is_guidance_source:
                # Extract the specific quarterly outlook section, not the document headline
                import re as _re
                # Try specific quarter outlook first (e.g., "Fourth Quarter 2024 Outlook")
                outlook_match = _re.search(r'(?i)(fourth quarter|q4)\s*\d{4}\s*outlook', output)
                if not outlook_match:
                    # Fall back to any "Outlook" section that's NOT a headline
                    outlook_match = _re.search(r'(?i)\boutlook\b.{0,20}(gross booking|adjusted ebitda|revenue)', output)
                if outlook_match:
                    start = outlook_match.start()
                    section = output[start:start + 1500]
                    raw_sources += f"\n---GUIDANCE SOURCE---\n{section}\n"
                else:
                    raw_sources += f"\n---{tool} (contains guidance)---\n{output[:2000]}\n"

            if has_actual_keys and not is_guidance_source:
                raw_sources += f"\n---ACTUALS SOURCE---\n{output[:2000]}\n"

        # Always include prefetched section results — these are targeted for this question
        if prefetch_results:
            for label, content in prefetch_results.items():
                if content and len(content) > 50 and label not in raw_sources:
                    raw_sources += f"\n---PREFETCHED: {label}---\n{content[:3000]}\n"

        # If still no sources, fall back to all tool outputs + agent text
        if not raw_sources:
            raw_sources = text[:4000]
            for entry in state.get("_tool_log", []):
                output = entry.get("output", "")
                if output and len(output) > 10:
                    raw_sources += f"\n{output[:1500]}\n"

        response = call_claude(
            system=(
                "Extract specific numeric values from SEC filings and press releases. "
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
                # Prefill forces Claude to start with JSON
                {"role": "assistant", "content": "{"},
            ],
            max_tokens=512,
        )
        # Prepend the "{" prefill to reconstruct the full JSON
        raw_text = "{" + response.content[0].text
        log.info(f"  LLM extraction raw response: {raw_text[:300]}")

        # Robust JSON extraction
        values = {}
        try:
            values = json.loads(raw_text.strip())
        except (json.JSONDecodeError, ValueError):
            # Try stripping markdown fences
            try:
                values = parse_json_response(raw_text)
            except (json.JSONDecodeError, ValueError):
                # Try to find a JSON object embedded in prose
                import re as _re
                json_match = _re.search(r'\{[^{}]*\}', raw_text)
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
                    dp["source"] = "LLM extraction from press release"
                    dp["confidence"] = "medium"
                    log.info(f"  LLM extracted {key} = {val}")
    except Exception as e:
        log.warning(f"LLM extraction fallback failed: {e}")


def _format_answer(question: str, research_text: str, state: dict) -> str:
    """Step 4: Format the final answer."""
    # Include calculation results if available
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
        system=ANSWER_SYSTEM,
        messages=[{"role": "user", "content": ANSWER_PROMPT.format(
            question=question,
            state=f"RESEARCH FINDINGS:\n{research_text[:6000]}{calc_info}",
        )}],
        max_tokens=1024,
        model=MODEL_HAIKU,
    )

    return response.content[0].text
