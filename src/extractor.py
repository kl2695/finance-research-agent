"""Extract data values from raw tool results into the state dict.

Two extraction modes:
1. XBRL: Parses structured XBRL output directly (concept names are exact, no LLM needed).
2. Filing text: Regex PARSES all dollar amounts and percentages, then LLM MATCHES
   them to data_needed keys. This scales to any metric without hardcoded keyword maps.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)


def extract_from_tool_log(tool_log: list[dict], state: dict) -> dict:
    """Extract data values from tool results into the state's data_needed.

    Parses XBRL responses, FMP responses, and web search results.
    Matches extracted values to the plan's data_needed keys using
    the clarifications and key labels.
    """
    # Collect all numeric facts from tool results.
    # Tag each fact with a source_idx so position tiebreakers only compare
    # within the same document (not across Q4 vs Q3 press releases).
    facts = []
    for source_idx, entry in enumerate(tool_log):
        tool = entry.get("tool", "")
        output = entry.get("output", "")

        new_facts = []
        if tool == "sec_edgar_financials":
            new_facts = _parse_xbrl_output(output)
        elif tool in ("sec_edgar_filing_text", "sec_edgar_earnings"):
            new_facts = _parse_filing_text(output)
        elif tool == "fmp_financials":
            new_facts = _parse_fmp_output(output)

        for f in new_facts:
            f["source_idx"] = source_idx
        facts.extend(new_facts)

    if not facts:
        return state

    # Match facts to data_needed keys
    data_needed = state.get("data_needed", {})
    clarifications = state.get("clarifications", {})
    period = clarifications.get("period", "")

    for key, dp in data_needed.items():
        if not isinstance(dp, dict) or dp.get("value") is not None:
            continue  # Already filled

        label = dp.get("label", key)
        matches = _match_fact_to_key(key, label, facts, period)

        if matches:
            best = matches[0]  # Take highest confidence match
            value = best["value"]

            # Unit sanity check: don't fill keys with clearly wrong magnitudes
            unit = dp.get("unit", "")
            unit_lower = unit.lower() if unit else ""
            key_lower_check = key.lower()

            # Percentage/ratio keys: value should be < 1000
            is_pct_key = any(w in key_lower_check for w in ("margin", "rate", "percent", "pct", "ratio", "growth"))
            is_pct_unit = "%" in unit_lower or "percent" in unit_lower or "bps" in unit_lower
            if (is_pct_key or is_pct_unit) and abs(value) > 1000:
                log.info(f"  Skipping {key}: value {value} too large for a percentage/ratio key")
                continue

            # Small-number keys (per-unit metrics, counts, days): value should be < 10,000
            is_small_key = any(w in key_lower_check for w in (
                "nights", "per_night", "per_booking", "per_room", "per_user", "per_member",
                "days_", "turnover", "times", "multiple", "utilization",
            ))
            if is_small_key and abs(value) > 10000:
                log.info(f"  Skipping {key}: value {value} too large for a per-unit/small metric key")
                continue

            dp["value"] = value
            dp["source"] = best.get("source", "SEC EDGAR")
            dp["confidence"] = best.get("confidence", "high")
            log.info(f"  Extracted {key} = {best['value']} from {best.get('source', '?')}")

    return state


def _parse_xbrl_output(output: str) -> list[dict]:
    """Parse SEC EDGAR XBRL output into structured facts.

    Example input:
    "InventoryNet (USD): 2168000000 (period: ? to 2024-12-31, filed: 2025-01-31)"
    """
    facts = []
    # Match pattern: ConceptName (unit): value (period: start to end, filed: date)
    pattern = r'(\w+)\s*\((\w+)\):\s*\$?([\d,.]+)\s*\(period:\s*(\S*)\s*to\s*(\S+),\s*filed:\s*(\S+)\)'
    for match in re.finditer(pattern, output):
        concept, unit, value_str, start, end, filed = match.groups()
        try:
            value = float(value_str.replace(",", ""))
        except ValueError:
            continue
        facts.append({
            "concept": concept,
            "unit": unit,
            "value": value,
            "period_start": start if start != "?" else None,
            "period_end": end,
            "filed": filed,
            "source": f"SEC EDGAR XBRL ({concept}, period ending {end})",
            "confidence": "high",
        })
    return facts


def _parse_filing_text(output: str) -> list[dict]:
    """Parse filing text for dollar amounts with context.

    Looks for patterns like "$4,278.9" or "$14,060 million".
    Captures surrounding context for matching to data_needed keys.

    Detects table-level unit declarations like "(in millions)" and applies
    the multiplier to subsequent bare dollar amounts that lack their own unit.
    """
    facts = []

    # Detect table-level unit declarations: "(in millions)", "(in billions)", etc.
    # These apply to bare dollar amounts (no inline unit) that follow them.
    table_unit_regions = []  # list of (start_pos, multiplier)
    for m in re.finditer(r'\(in\s+(millions|billions|thousands)', output, re.IGNORECASE):
        unit_word = m.group(1).lower()
        mult = {"millions": 1e6, "billions": 1e9, "thousands": 1e3}[unit_word]
        table_unit_regions.append((m.start(), mult))

    def _get_table_multiplier(pos: int) -> float | None:
        """Return the table-level multiplier active at this position, if any."""
        best = None
        for region_start, mult in table_unit_regions:
            if pos > region_start:
                best = mult
        return best

    # Match dollar amounts with optional millions/billions, preserving decimals
    # Also detect parenthetical negatives: ($11,817) = negative $11,817
    pattern = r'(\()??\$\s*([\d,.]+)\s*(million|billion|M|B|thousand|K)?(\))?'
    for match in re.finditer(pattern, output, re.IGNORECASE):
        open_paren, value_str, unit, close_paren = match.groups()
        try:
            value = float(value_str.replace(",", ""))
        except ValueError:
            continue
        # Parentheses in accounting = negative value
        if open_paren and close_paren:
            value = -value

        # Apply multiplier — inline unit takes precedence over table-level
        # Round after multiplying to avoid floating point artifacts (66.6*1e6 = 66599999.99)
        from_table = False
        if unit:
            unit_lower = unit.lower()
            if unit_lower in ("billion", "b"):
                value = round(value * 1e9, 2)
            elif unit_lower in ("million", "m"):
                value = round(value * 1e6, 2)
            elif unit_lower in ("thousand", "k"):
                value = round(value * 1e3, 2)
        else:
            # No inline unit — check for table-level unit declaration
            table_mult = _get_table_multiplier(match.start())
            if table_mult:
                # Apply multiplier if the result is a plausible financial value (< $1 trillion)
                multiplied = value * table_mult
                if multiplied < 1e12:
                    value = round(multiplied, 2)
                    from_table = True

        # Get surrounding context — wider window for better matching
        ctx_start = max(0, match.start() - 80)
        ctx_end = min(len(output), match.end() + 30)
        context = output[ctx_start:ctx_end].strip()

        # Extract keywords from BEFORE the value only — in tables, row labels
        # precede their values, so "after" keywords are from adjacent rows
        before_context = output[ctx_start:match.start()]
        context_keywords = _extract_context_keywords(before_context)

        facts.append({
            "value": value,
            "position": match.start(),
            "context": context,
            "context_keywords": context_keywords,
            "from_table": from_table,
            "is_pct": False,
            "source": "SEC filing text",
            "confidence": "high",
        })

    # Second pass: extract percentages (e.g., "27.9%", "gross margin of 27.9%")
    pct_pattern = r'([\d,.]+)\s*%'
    for match in re.finditer(pct_pattern, output):
        value_str = match.group(1)
        try:
            value = float(value_str.replace(",", ""))
        except ValueError:
            continue

        # Skip obviously non-financial percentages (>100% is rare but possible,
        # e.g., "up 150% year over year" — we still capture these)
        if value > 500:
            continue

        ctx_start = max(0, match.start() - 80)
        ctx_end = min(len(output), match.end() + 30)
        context = output[ctx_start:ctx_end].strip()

        before_context = output[ctx_start:match.start()]
        context_keywords = _extract_context_keywords(before_context)

        facts.append({
            "value": value,
            "position": match.start(),
            "context": context,
            "context_keywords": context_keywords,
            "from_table": False,
            "is_pct": True,
            "source": "SEC filing text",
            "confidence": "high",
        })

    return facts


# Mapping of common financial terms in press releases to data_needed key patterns
_CONTEXT_KEYWORD_MAP = {
    "gross booking": ["gross_bookings", "bookings"],
    "adjusted ebitda": ["adjusted_ebitda", "ebitda"],
    "revenue": ["revenue"],
    "net income": ["net_income"],
    "net loss": ["net_income", "net_loss"],
    "gross profit": ["gross_profit"],
    "gross margin": ["gross_margin", "margin"],
    "operating income": ["operating_income"],
    "operating margin": ["operating_margin", "margin"],
    "ebitda margin": ["ebitda_margin", "margin"],
    "net margin": ["net_margin", "margin"],
    "free cash flow": ["free_cash_flow", "fcf"],
    "total assets": ["total_assets", "assets"],
    "total debt": ["total_debt", "debt"],
    "shares outstanding": ["shares_outstanding", "shares"],
    "diluted eps": ["diluted_eps", "eps"],
    "effective tax": ["effective_tax", "tax_rate"],
    "tax rate": ["tax_rate", "effective_tax"],
    "same-store": ["same_store", "comparable"],
    "comparable store": ["same_store", "comparable"],
    "take rate": ["take_rate"],
    "cash requirement": ["cash_requirement", "cash", "material_cash"],
    "contractual obligation": ["obligation", "cash_requirement", "cash"],
    "guidance": ["guided", "guidance"],
}


def _extract_context_keywords(context: str) -> list[str]:
    """Extract financial keywords from surrounding context."""
    ctx_lower = context.lower()
    keywords = []
    for phrase, keys in _CONTEXT_KEYWORD_MAP.items():
        if phrase in ctx_lower:
            keywords.extend(keys)
    return keywords


def _parse_fmp_output(output: str) -> list[dict]:
    """Parse FMP financial data output."""
    facts = []
    # FMP returns key: value pairs
    for line in output.split("\n"):
        match = re.match(r'(\w[\w\s]+):\s*\$?([\d,.]+)', line)
        if match:
            concept, value_str = match.groups()
            try:
                value = float(value_str.replace(",", ""))
            except ValueError:
                continue
            facts.append({
                "concept": concept.strip(),
                "value": value,
                "source": "FMP",
                "confidence": "medium",
            })
    return facts


def _match_fact_to_key(key: str, label: str, facts: list[dict], period: str) -> list[dict]:
    """Match extracted facts to a data_needed key using fuzzy matching.

    Strategy: score each fact by how well it matches the key name and period.
    """
    # Build search terms from the key name
    # e.g., "cogs_fy2024" -> ["cogs", "costofgoods", "2024"]
    # e.g., "inventory_ending_fy2024" -> ["inventory", "inventorynet", "2024", "12-31"]
    # e.g., "inventory_beginning_fy2024" -> ["inventory", "inventorynet", "2023", "12-31"]
    key_lower = key.lower()
    label_lower = label.lower() if label else ""

    concept_map = {
        "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
        "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
        "inventory": ["InventoryNet"],
        "net_income": ["NetIncomeLoss"],
        "operating_income": ["OperatingIncomeLoss"],
        "total_assets": ["Assets"],
        "total_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
        "cash_equivalent": ["CashAndCashEquivalentsAtCarryingValue"],
        "depreciation": ["DepreciationAndAmortization"],
        "interest_expense": ["InterestExpense"],
        "shares_outstanding": ["CommonStockSharesOutstanding"],
        "gross_profit": ["GrossProfit"],
        "ebitda": ["OperatingIncomeLoss"],  # EBITDA needs D&A added
        "equity": ["StockholdersEquity"],
        "income_tax": ["IncomeTaxExpenseBenefit"],
        "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                    "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"],
    }

    # Determine which XBRL concepts match this key
    # Skip XBRL matching for keys with qualifiers that indicate a SUBSET of a standard concept
    # (e.g., "refinanceable_debt" needs the footnote total, not XBRL LongTermDebt which is broader)
    subset_qualifiers = ["refinanc", "excluding", "ex_", "adjusted", "non_gaap", "nongaap"]
    is_subset_key = any(q in key_lower for q in subset_qualifiers)

    target_concepts = []
    if not is_subset_key:
        for keyword, concepts in concept_map.items():
            if keyword in key_lower or keyword in label_lower:
                target_concepts.extend(concepts)

    # Determine target period
    target_year = None
    year_match = re.search(r'(20\d{2})', key_lower)
    if year_match:
        target_year = year_match.group(1)

    # For "beginning" inventory, we want the prior year's ending value
    is_beginning = "begin" in key_lower or "start" in key_lower
    if is_beginning and target_year:
        target_year = str(int(target_year) - 1)

    # Score and rank facts
    scored = []
    for fact in facts:
        score = 0
        concept = fact.get("concept", "")
        period_end = fact.get("period_end", "")
        context_keywords = fact.get("context_keywords", [])

        # XBRL concept match
        if concept in target_concepts:
            score += 10
        elif any(tc.lower() in concept.lower() for tc in target_concepts):
            score += 5
        elif any(kw in concept.lower() for kw in key_lower.split("_") if len(kw) > 3):
            score += 2

        # Context keyword match (for press release data without XBRL concepts)
        if context_keywords and not concept:
            key_parts = [p for p in key_lower.split("_") if len(p) > 2]
            # Score best keyword match — longer matches are more specific
            best_kw_score = 0
            for kw in context_keywords:
                if kw in key_lower:
                    # Compound match: score by specificity (keyword length)
                    # "gross_margin" (12) > "margin" (6) > "gross" (5)
                    best_kw_score = max(best_kw_score, 6 + len(kw))
                elif any(part in kw for part in key_parts):
                    # Partial match: single key part appears in keyword
                    best_kw_score = max(best_kw_score, 4)
            score += best_kw_score

        # Period match — ONLY if there's already a concept or keyword match (P78 fix).
        # Without this gate, ANY fact from the right year scores 7+ even if the concept
        # is completely unrelated (e.g., LongTermDebt matching "interest_expense" key).
        has_concept_or_keyword_match = score > 0
        if target_year and period_end:
            if period_end.startswith(target_year):
                if has_concept_or_keyword_match:
                    score += 5
                    if "12-31" in period_end or "12-30" in period_end:
                        score += 2
            else:
                # Wrong year — skip this fact entirely for this key
                continue

        # Type matching: percentage keys should prefer percentage facts
        is_pct_key = any(w in key_lower for w in ("margin", "rate", "percent", "pct", "ratio", "growth"))
        is_pct_fact = fact.get("is_pct", False)
        if is_pct_key and is_pct_fact:
            score += 5  # Percentage key matched to percentage value
        elif is_pct_key and not is_pct_fact and not concept:
            score -= 5  # Percentage key matched to dollar value — penalize
        elif not is_pct_key and is_pct_fact:
            score -= 3  # Dollar key matched to percentage value — penalize

        # Table values are more precise than prose — boost them
        if fact.get("from_table"):
            score += 4

        # Context period match — boost/penalize based on quarterly vs annual context
        context = fact.get("context", "").lower()
        is_quarterly_key = any(q in key_lower for q in ("q1", "q2", "q3", "q4", "quarter"))
        is_annual_key = any(a in key_lower for a in ("fy", "annual", "full_year"))

        if is_quarterly_key:
            # Boost if context mentions the right quarter or "quarter"
            if any(q in context for q in ("fourth quarter", "q4", "three months")):
                score += 3
            # Penalize full-year figures
            if any(a in context for a in ("full-year", "full year", "year ended", "twelve months")):
                score -= 5

        if is_annual_key:
            if any(a in context for a in ("full-year", "full year", "year ended", "annual")):
                score += 3
            if any(q in context for q in ("fourth quarter", "third quarter", "three months")):
                score -= 3

        # Only include if there's some match
        if score > 0:
            scored.append({**fact, "_score": score})

    # Sort by score descending, then by precision (more significant digits wins ties)
    def _sig_figs(fact):
        """Count significant figures — higher = more precise.
        4300000000 (from "$4.3 billion") → 2 sig figs
        4278900000 (from "$4,278.9 million") → 5 sig figs
        """
        val = fact["value"]
        if val == 0:
            return 0
        v = round(val)
        s = str(v).rstrip("0").replace("-", "").lstrip("0")
        return len(s)

    # Sort: highest score → earliest source document → earliest position in document
    # Source order: Q4 press release is prefetched before Q3, so earlier source = more relevant
    # Position: in financial tables, the most recent quarter is the first column
    scored.sort(key=lambda x: (-x["_score"], x.get("source_idx", 0), x.get("position", 0)))
    return scored


def llm_match_facts_to_keys(facts: list[dict], state: dict) -> dict:
    """Use LLM to match parsed facts to data_needed keys.

    This is the scalable alternative to _match_fact_to_key's hardcoded keyword maps.
    The regex parser finds ALL values; the LLM assigns them to the right keys
    based on semantic understanding of context.

    Only used for filing text facts (not XBRL, which has exact concept matching).
    """
    from src.llm import call_claude, MODEL_HAIKU

    data_needed = state.get("data_needed", {})
    unfilled = {k: dp for k, dp in data_needed.items()
                if isinstance(dp, dict) and dp.get("value") is None}

    if not unfilled or not facts:
        return state

    # Deduplicate facts and filter imprecise values:
    # 1. Remove exact duplicates (same value + same context from duplicate fetches)
    # 2. When a TABLE value and a prose approximation exist for similar amounts,
    #    remove the less precise prose value (e.g., $4.3B rounded when $4,278.9M exists)
    seen_values = set()
    deduped_facts = []
    for f in facts:
        key = (f["value"], f.get("source_doc", ""), f.get("context", "")[:30])
        if key not in seen_values:
            seen_values.add(key)
            deduped_facts.append(f)

    # Remove imprecise prose values when a more precise TABLE value exists
    table_values = {f["value"] for f in deduped_facts if f.get("from_table")}
    filtered_facts = []
    for f in deduped_facts:
        if not f.get("from_table") and not f.get("is_pct"):
            # Check if there's a table value within 5% of this prose value
            val = f["value"]
            has_precise_equiv = any(
                abs(tv - val) / max(val, 1) < 0.05 and tv != val
                for tv in table_values
            )
            if has_precise_equiv:
                continue  # Skip imprecise prose value
        filtered_facts.append(f)
    facts = filtered_facts

    # Include facts from ALL source documents proportionally (max 80 total)
    # This ensures every prefetched document is represented
    source_groups = {}
    for i, f in enumerate(facts):
        src = f.get("source_idx", 0)
        source_groups.setdefault(src, []).append((i, f))

    max_facts = 80
    per_source = max(10, max_facts // max(len(source_groups), 1))
    selected_indices = []
    for src_idx in sorted(source_groups.keys()):
        group = source_groups[src_idx]
        selected_indices.extend([idx for idx, _ in group[:per_source]])
    selected_indices = sorted(selected_indices)[:max_facts]

    # Build a compact representation
    # Include value, type, table flag, source document, and context
    fact_summaries = []
    for i in selected_indices:
        f = facts[i]
        val = f["value"]
        ctx = f.get("context", "")[:80]
        is_pct = f.get("is_pct", False)
        from_table = f.get("from_table", False)
        source_doc = f.get("source_doc", "")
        val_type = "%" if is_pct else "$"
        table_flag = " [TABLE]" if from_table else ""
        src_flag = f" [from: {source_doc}]" if source_doc else ""
        fact_summaries.append(f"[{i}] {val_type}{val:,.2f}{table_flag}{src_flag} — {ctx}")

    # Build key descriptions
    key_descriptions = {}
    for k, dp in unfilled.items():
        label = dp.get("label", k)
        unit = dp.get("unit", "")
        key_descriptions[k] = f"{label} ({unit})" if unit else label

    prompt = f"""Match numeric values to data fields. Each value has surrounding context from an SEC filing.

DATA FIELDS NEEDED:
{json.dumps(key_descriptions, indent=2)}

AVAILABLE VALUES (index, type, value, context):
{chr(10).join(fact_summaries)}

For each data field, identify which value index (if any) is the correct match.
Rules:
- The metric name in context must match the field, AND the period must match.
- When multiple values match the same concept, prefer [TABLE] values (more precise).
  Example: "$4,278.9M [TABLE]" is better than "$4.3 billion" for the same metric.
- For percentage fields (margin, rate, growth), choose percentage values, not dollar amounts.
- Use the [from: ...] source tag to match guidance vs actuals:
  - "guided" or "guidance" fields → match to values from the PRIOR quarter's press release
  - "actual" fields → match to values from the CURRENT quarter's press release
- If no value matches a field, omit it.

Return JSON: {{"field_key": index_number}}"""

    try:
        response = call_claude(
            system="Match financial values to data fields based on context. Return JSON only.",
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "{"},
            ],
            max_tokens=300,
            model=MODEL_HAIKU,
        )
        raw = "{" + response.content[0].text

        # Robust JSON parsing — Haiku sometimes adds trailing text after the JSON object
        matches = {}
        try:
            matches = json.loads(raw.strip())
        except json.JSONDecodeError:
            # Find the matching closing brace for the opening {
            depth = 0
            end_idx = -1
            for i, ch in enumerate(raw):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break
            if end_idx > 0:
                try:
                    matches = json.loads(raw[:end_idx])
                except json.JSONDecodeError:
                    pass
            if not matches:
                log.warning(f"  LLM fact matching: could not parse JSON from response")

        for key, idx in matches.items():
            if key in unfilled and isinstance(idx, int) and 0 <= idx < len(facts):
                fact = facts[idx]
                dp = data_needed[key]
                dp["value"] = fact["value"]
                dp["source"] = fact.get("source", "SEC filing text (LLM-matched)")
                dp["confidence"] = "high"
                log.info(f"  LLM-matched {key} = {fact['value']} (fact[{idx}])")

    except (ValueError, KeyError) as e:
        log.warning(f"  LLM fact matching failed: {e}")

    return state
