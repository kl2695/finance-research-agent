"""Extract data values from raw tool results into the state dict.

Domain-agnostic extraction framework. Domain-specific parsers
are provided via the Domain interface. Currency/percentage parsing stays in core.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from domains.base import Domain

log = logging.getLogger(__name__)


def extract_from_tool_log(tool_log: list[dict], state: dict,
                          domain: "Domain | None" = None) -> dict:
    """Extract data values from tool results into the state's data_needed.

    Parses tool outputs using domain-provided parsers, then matches extracted
    facts to data_needed keys using concept maps and keyword scoring.
    """
    facts = []
    for source_idx, entry in enumerate(tool_log):
        tool = entry.get("tool", "")
        output = entry.get("output", "")

        new_facts = []
        if domain:
            # Use domain to classify and parse
            classified = domain.classify_tools([entry])
            if classified.get("structured"):
                new_facts = _parse_structured_output(output, tool, domain)
            else:
                new_facts = _parse_filing_text(output)
        else:
            # No domain — parse all outputs as prose text
            new_facts = _parse_filing_text(output)

        for f in new_facts:
            f["source_idx"] = source_idx
        facts.extend(new_facts)

    if not facts:
        return state

    # Get domain-specific maps or use empty defaults
    concept_map = domain.concept_map if domain else {}
    keyword_map = domain.keyword_map if domain else {}
    sanity_config = domain.sanity_check_config if domain else {
        "pct_keywords": ["margin", "rate", "percent", "pct", "ratio", "growth"],
        "small_keywords": [],
        "pct_max": 1000,
        "small_max": 10000,
    }

    data_needed = state.get("data_needed", {})
    clarifications = state.get("clarifications", {})
    period = clarifications.get("period", "")

    for key, dp in data_needed.items():
        if not isinstance(dp, dict) or dp.get("value") is not None:
            continue

        label = dp.get("label", key)
        matches = _match_fact_to_key(key, label, facts, period, concept_map, keyword_map)

        if matches:
            best = matches[0]
            value = best["value"]

            # Unit sanity checks using domain config
            unit = dp.get("unit", "")
            unit_lower = unit.lower() if unit else ""
            key_lower_check = key.lower()

            pct_kws = sanity_config.get("pct_keywords", [])
            is_pct_key = any(w in key_lower_check for w in pct_kws)
            is_pct_unit = "%" in unit_lower or "percent" in unit_lower or "bps" in unit_lower
            pct_max = sanity_config.get("pct_max", 1000)
            if (is_pct_key or is_pct_unit) and abs(value) > pct_max:
                log.info(f"  Skipping {key}: value {value} too large for a percentage/ratio key")
                continue

            small_kws = sanity_config.get("small_keywords", [])
            is_small_key = any(w in key_lower_check for w in small_kws)
            small_max = sanity_config.get("small_max", 10000)
            if is_small_key and abs(value) > small_max:
                log.info(f"  Skipping {key}: value {value} too large for a per-unit/small metric key")
                continue

            dp["value"] = value
            dp["source"] = best.get("source", "extracted")
            dp["confidence"] = best.get("confidence", "high")
            log.info(f"  Extracted {key} = {best['value']} from {best.get('source', '?')}")

    return state


def _parse_structured_output(output: str, tool_name: str,
                             domain: "Domain") -> list[dict]:
    """Parse structured tool output. Delegates to domain-registered parsers.

    The domain's classify_tools() already identified this as structured data.
    Try domain-registered structured parsers by tool name convention.
    """
    # Domain-registered structured parsers check by tool_name
    # Structured output typically has concept-based format or JSON
    # Try concept-based parsing (works for any structured data with named fields)
    concept_facts = _parse_concept_output(output)
    if concept_facts:
        return concept_facts
    # Fall back to prose parsing
    return _parse_filing_text(output)


def _parse_concept_output(output: str) -> list[dict]:
    """Parse structured concept output into facts.

    Matches pattern: ConceptName (Unit): Value (period: start to end, filed: date)
    This is a generic structured data format used by data APIs.
    """
    facts = []
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
            "source": f"structured data ({concept}, period ending {end})",
            "confidence": "high",
        })
    return facts


def _parse_filing_text(output: str) -> list[dict]:
    """Parse text for dollar amounts and percentages with context.

    Detects table-level unit declarations like "(in millions)" and applies
    the multiplier to subsequent bare dollar amounts.
    """
    facts = []

    # Detect table-level unit declarations
    table_unit_regions = []
    for m in re.finditer(r'\(in\s+(millions|billions|thousands)', output, re.IGNORECASE):
        unit_word = m.group(1).lower()
        mult = {"millions": 1e6, "billions": 1e9, "thousands": 1e3}[unit_word]
        table_unit_regions.append((m.start(), mult))

    def _get_table_multiplier(pos: int) -> float | None:
        best = None
        for region_start, mult in table_unit_regions:
            if pos > region_start:
                best = mult
        return best

    # Dollar amounts with optional millions/billions
    pattern = r'(\()??\$\s*([\d,.]+)\s*(million|billion|M|B|thousand|K)?(\))?'
    for match in re.finditer(pattern, output, re.IGNORECASE):
        open_paren, value_str, unit, close_paren = match.groups()
        try:
            value = float(value_str.replace(",", ""))
        except ValueError:
            continue
        if open_paren and close_paren:
            value = -value

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
            table_mult = _get_table_multiplier(match.start())
            if table_mult:
                multiplied = value * table_mult
                if multiplied < 1e12:
                    value = round(multiplied, 2)
                    from_table = True

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
            "from_table": from_table,
            "is_pct": False,
            "source": "filing text",
            "confidence": "high",
        })

    # Percentages
    pct_pattern = r'([\d,.]+)\s*%'
    for match in re.finditer(pct_pattern, output):
        value_str = match.group(1)
        try:
            value = float(value_str.replace(",", ""))
        except ValueError:
            continue

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
            "source": "filing text",
            "confidence": "high",
        })

    return facts


# Default keyword map — used when no domain is provided
_DEFAULT_KEYWORD_MAP: dict[str, list[str]] = {}

# Active keyword map — set by domain
_active_keyword_map: dict[str, list[str]] = {}


def _extract_context_keywords(context: str) -> list[str]:
    """Extract keywords from surrounding context."""
    ctx_lower = context.lower()
    keywords = []
    kw_map = _active_keyword_map or _DEFAULT_KEYWORD_MAP
    for phrase, keys in kw_map.items():
        if phrase in ctx_lower:
            keywords.extend(keys)
    return keywords


def _parse_kv_output(output: str) -> list[dict]:
    """Parse key:value line output into facts."""
    facts = []
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
                "source": "key-value data",
                "confidence": "medium",
            })
    return facts


def _match_fact_to_key(key: str, label: str, facts: list[dict], period: str,
                       concept_map: dict[str, list[str]] | None = None,
                       keyword_map: dict[str, list[str]] | None = None) -> list[dict]:
    """Match extracted facts to a data_needed key using fuzzy matching."""
    key_lower = key.lower()
    label_lower = label.lower() if label else ""

    # Use provided concept map or empty
    cmap = concept_map or {}

    # Determine which concepts match this key
    subset_qualifiers = ["refinanc", "excluding", "ex_", "adjusted", "non_gaap", "nongaap"]
    is_subset_key = any(q in key_lower for q in subset_qualifiers)

    target_concepts = []
    if not is_subset_key:
        for keyword, concepts in cmap.items():
            if keyword in key_lower or keyword in label_lower:
                target_concepts.extend(concepts)

    # Target period
    target_year = None
    year_match = re.search(r'(20\d{2})', key_lower)
    if year_match:
        target_year = year_match.group(1)

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

        # Concept match
        if concept in target_concepts:
            score += 10
        elif any(tc.lower() in concept.lower() for tc in target_concepts):
            score += 5
        elif any(kw in concept.lower() for kw in key_lower.split("_") if len(kw) > 3):
            score += 2

        # Context keyword match
        if context_keywords and not concept:
            key_parts = [p for p in key_lower.split("_") if len(p) > 2]
            best_kw_score = 0
            for kw in context_keywords:
                if kw in key_lower:
                    best_kw_score = max(best_kw_score, 6 + len(kw))
                elif any(part in kw for part in key_parts):
                    best_kw_score = max(best_kw_score, 4)
            score += best_kw_score

        # Period match — gated behind concept/keyword match (P78 fix)
        has_concept_or_keyword_match = score > 0
        if target_year and period_end:
            if period_end.startswith(target_year):
                if has_concept_or_keyword_match:
                    score += 5
                    if "12-31" in period_end or "12-30" in period_end:
                        score += 2
            else:
                continue

        # Type matching
        is_pct_key = any(w in key_lower for w in ("margin", "rate", "percent", "pct", "ratio", "growth"))
        is_pct_fact = fact.get("is_pct", False)
        if is_pct_key and is_pct_fact:
            score += 5
        elif is_pct_key and not is_pct_fact and not concept:
            score -= 5
        elif not is_pct_key and is_pct_fact:
            score -= 3

        # Table bonus
        if fact.get("from_table"):
            score += 4

        # Context period match
        context = fact.get("context", "").lower()
        is_quarterly_key = any(q in key_lower for q in ("q1", "q2", "q3", "q4", "quarter"))
        is_annual_key = any(a in key_lower for a in ("fy", "annual", "full_year"))

        if is_quarterly_key:
            if any(q in context for q in ("fourth quarter", "q4", "three months")):
                score += 3
            if any(a in context for a in ("full-year", "full year", "year ended", "twelve months")):
                score -= 5

        if is_annual_key:
            if any(a in context for a in ("full-year", "full year", "year ended", "annual")):
                score += 3
            if any(q in context for q in ("fourth quarter", "third quarter", "three months")):
                score -= 3

        if score > 0:
            scored.append({**fact, "_score": score})

    # Sort: highest score → earliest source → earliest position
    scored.sort(key=lambda x: (-x["_score"], x.get("source_idx", 0), x.get("position", 0)))
    return scored


def llm_match_facts_to_keys(facts: list[dict], state: dict,
                            domain: "Domain | None" = None) -> dict:
    """Use LLM to match parsed facts to data_needed keys.

    The regex parser finds ALL values; the LLM assigns them to the right keys
    based on semantic understanding of context.
    """
    from core.llm import call_claude, MODEL_HAIKU

    data_needed = state.get("data_needed", {})
    unfilled = {k: dp for k, dp in data_needed.items()
                if isinstance(dp, dict) and dp.get("value") is None}

    if not unfilled or not facts:
        return state

    # Deduplicate facts
    seen_values: set[tuple] = set()
    deduped_facts = []
    for f in facts:
        fkey = (f["value"], f.get("source_doc", ""), f.get("context", "")[:30])
        if fkey not in seen_values:
            seen_values.add(fkey)
            deduped_facts.append(f)

    # Remove imprecise prose values when precise TABLE value exists
    table_values = {f["value"] for f in deduped_facts if f.get("from_table")}
    filtered_facts = []
    for f in deduped_facts:
        if not f.get("from_table") and not f.get("is_pct"):
            val = f["value"]
            has_precise_equiv = any(
                abs(tv - val) / max(val, 1) < 0.05 and tv != val
                for tv in table_values
            )
            if has_precise_equiv:
                continue
        filtered_facts.append(f)
    facts = filtered_facts

    # Proportional source sampling (max 80 total)
    source_groups: dict[int, list[tuple[int, dict]]] = {}
    for i, f in enumerate(facts):
        src = f.get("source_idx", 0)
        source_groups.setdefault(src, []).append((i, f))

    max_facts = 80
    per_source = max(10, max_facts // max(len(source_groups), 1))
    selected_indices: list[int] = []
    for src_idx in sorted(source_groups.keys()):
        group = source_groups[src_idx]
        selected_indices.extend([idx for idx, _ in group[:per_source]])
    selected_indices = sorted(selected_indices)[:max_facts]

    # Build fact summaries
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

    # Get domain-specific extraction hints
    hints = domain.extraction_hints if domain else ""
    hint_block = f"\n{hints}" if hints else ""

    prompt = f"""Match numeric values to data fields. Each value has surrounding context from a source document.

DATA FIELDS NEEDED:
{json.dumps(key_descriptions, indent=2)}

AVAILABLE VALUES (index, type, value, context):
{chr(10).join(fact_summaries)}

For each data field, identify which value index (if any) is the correct match.
Rules:
- The metric name in context must match the field, AND the period must match.
- When multiple values match the same concept, prefer [TABLE] values (more precise).
  Example: "$4,278.9M [TABLE]" is better than "$4.3 billion" for the same metric.
- For percentage fields (margin, rate, growth), choose percentage values, not dollar amounts.{hint_block}
- If no value matches a field, omit it.

Return JSON: {{"field_key": index_number}}"""

    try:
        response = call_claude(
            system="Match values to data fields based on context. Return JSON only.",
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "{"},
            ],
            max_tokens=300,
            model=MODEL_HAIKU,
        )
        raw = "{" + response.content[0].text

        matches: dict = {}
        try:
            matches = json.loads(raw.strip())
        except json.JSONDecodeError:
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
                log.warning("  LLM fact matching: could not parse JSON from response")

        for key, idx in matches.items():
            if key in unfilled and isinstance(idx, int) and 0 <= idx < len(facts):
                fact = facts[idx]
                dp = data_needed[key]
                dp["value"] = fact["value"]
                dp["source"] = fact.get("source", "filing text (LLM-matched)")
                dp["confidence"] = "high"
                log.info(f"  LLM-matched {key} = {fact['value']} (fact[{idx}])")

    except (ValueError, KeyError) as e:
        log.warning(f"  LLM fact matching failed: {e}")

    return state
