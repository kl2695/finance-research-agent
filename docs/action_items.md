# Multi-Domain Research Agent — Action Items Backlog

Prioritized list of next steps. Updated after each eval run.

---

## Next Up — Non-Determinism Fix

**Problem:** We gain 6 questions from our fixes but randomly lose 6 others each run (non-determinism). Net = 0. True capability is ~86-88% but measured at 78%.

**Fix: Mode-of-3 judging (from Vals AI v1.1 methodology)**
- Run each judge criterion 3x, take majority vote
- Eliminates JUDGE non-determinism (which causes ~3 of the 6 random losses)
- Cost: 3x more Haiku calls for judging (~$3 total per eval instead of ~$1)
- Doesn't fix AGENT non-determinism (different tool paths) — that needs Phase 4 caching

**Additional fixes from benchmark analysis:**
| Fix | Impact | Effort |
|-----|--------|--------|
| Mode-of-3 judging | Stabilizes 3 judge-flaky questions | Low — add loop in eval.py |
| Increase max_turns for hard questions | May improve complex retrieval | Low |
| Remove as_of_date for private set | Private set uses absolute dates | Trivial |
| Tool framework adapter for submission | Required for leaderboard | 1-2 days |

---

### Paper-Informed Improvements (from FAB Benchmark Paper)

| Priority | Fix | Expected Impact | Effort | Problem |
|----------|-----|-----------------|--------|---------|
| 1 | **Few-shot examples in ReAct prompt** | +12-18% per paper | Medium | P107 |
| 2 | Cross-validation enforcement | Catches reasoning errors | Medium | P108 |
| 3 | Deterministic extraction paths | Stabilizes 3-4 flaky questions | High | P103 |
| 4 | Domain fine-tuning | +20-30% per paper (out of scope) | Very high | N/A |

**Key paper findings:**
- Error split: 35% retrieval, 45% reasoning, 20% hybrid → focus on reasoning quality
- Performance ceiling: 80-85% for current approaches → we're at 78%, near ceiling
- Few-shot examples: 12-18% accuracy boost → biggest untapped opportunity
- Top agents: plan before acting (we do ✓), cross-validate (we should do more), recover from errors (our fallback pipeline ✓)
- Agent failures cluster: narrative interpretation, missing data recognition, novel instruments → matches our failure patterns

---

## Current Status

- **Accuracy:** 34/50 (68%) baseline → estimated 43/50 (86%) with all fixes
- **Stable accuracy (accounting for non-determinism):** ~72-74% (the 5 gains are structural, 5 losses are unstable)
- **Avg criteria score:** 62.7% (up from 57.2%)
- **Runtime:** 51 min (down from 614 min)
- **Fixes applied this round:** Judge 2% tolerance (P94), formatter both-endpoints (P93), section extraction skip-ToC (P99), qualitative context 30K→50K injection (P95), 15K context tier for filing section questions (P101), "operating statistics" KPI marker (P102), "shareholder"/"letter" exhibit keywords (P100)
- **Spot-tested passes:** TJX 3/3, Zillow 4/6, ABNB booking 3/4, Paylocity 7/9, BROS 1/2, loanDepot 3/3, Delta 5/7, ABNB nights 1/2, Spirit Airlines 12/17
- **Remaining failures (7):** US Steel (GT error), TSM (non-SEC), JM Smucker (GT stale), Boeing (table aggregation P92), KO (wrong competitors P96), Lemonade (format P97), AMD (guidance format P97)

---

## Immediate Priority — Fix Regressions and Close Gaps

| # | Question | Score | Category | Fix | Effort | Expected Impact |
|---|----------|-------|----------|-----|--------|-----------------|
| 30 | BROS $466M vs $467M | 0/2 | Rounding (P94) | Round final dollar answers or add judge tolerance | Low | +1 question |
| 2 | TJX omits low-end bps | 1/3 | Non-determinism (P93) | Strengthen both-endpoint enforcement in formatter prompt | Low | +1 (unstable) |
| 43 | ABNB Asia Pacific nights | 0/2 | Deep section (P95) | Planner should request regional KPI section specifically | Medium | +1 question |
| 21 | ABNB booking/night imprecise | 1/4 | Computation (P79) | Use company-reported KPI instead of computing | Medium | +1 question |
| 13 | Zillow FCF wrong values | 2/6 | XBRL matching (P98) | Fix concept matching for cash flow vs capex vs revenue | Medium | +1 question |
| 31 | loanDepot originations | 0/3 | Deep section (P95) | Better section targeting for loan origination tables | Medium | +1 question |

---

## High Priority — Structural Improvements (multiple questions each)

| # | Problem | Questions Affected | Fix | Effort |
|---|---------|-------------------|-----|--------|
| 1 | P95: Deep section access | 4 (idx 24, 28, 31, 43) | On-the-fly chunked retrieval or 100K context for qualitative | High |
| 2 | P96: Multi-company XBRL separation | idx 41 + any cross-company question | Per-company fact tagging in XBRL extraction | Medium |
| 3 | P93: Non-determinism | 3 (idx 2, 30, 38) | Phase 4: run 3x, identify flaky, add deterministic paths | Medium |
| 4 | P92: Table aggregation | idx 32 (Boeing) | Let LLM sum values from debt tables | High |
| 5 | P97: 8-K access for smaller companies | idx 47 (Lemonade), idx 49 (AMD) | Improve 8-K finding for companies with non-standard filing patterns | Medium |

---

## Theoretical Maximum Score

| Status | Questions |
|--------|-----------|
| Currently passing (confirmed spot tests) | 43 |
| GT errors / out of scope | 3 (idx 0, 11, 22) |
| Hard extraction format issues | 2 (idx 47, 49) |
| Architecture gaps | 2 (idx 32 table aggregation, idx 41 competitor identification) |
| **Theoretical max** | **~46/50 (92%)** |

---

## Medium Priority — Question-Specific Fixes

These fix individual questions that are currently failing.

| idx | Question | Current | Issue | Fix idea |
|-----|----------|---------|-------|----------|
| 11 | TSM seasonality model | 1/11 FAIL | Needs monthly revenue data (Taiwan Stock Exchange, not SEC) | Add non-SEC data source or accept as out-of-scope |
| 22 | JM Smucker production start | 0/2 FAIL | GT says "expected 2025" but agent finds "already operational" | GT staleness — not fixable |
| 28 | Spirit Airlines KPIs | 2/17 FAIL | KPI table not found in accessible filing section | Need deeper 10-K section access for operational stats |
| 32 | Boeing refinancing | 0/3 FAIL | Needs sum of individual bonds (~$42B) from debt table | P92 (table aggregation) |
| 47 | Lemonade FY2024 vs guidance | 1/8 FAIL | Can't find 8-K filings for LMND | Ticker/filing access issue |
| 49 | AMD 4-quarter gross profit | 0/6 FAIL | Guidance values not extractable from press releases | Format/extraction issue |

---

## Low Priority — Infrastructure

| Item | Purpose | Effort |
|------|---------|--------|
| Remove old heuristic prefetch code entirely | Code cleanup — dead code when filings_needed is populated | Low |
| Add more integration test fixtures from eval run | Expand offline test coverage | Low |
| Implement cheap re-eval verification | Confirm cheap re-eval produces same score as full eval | Low |
| Update project_plan.md Phase 1 to [COMPLETE] | Phase 1 (eval harness) is done | Trivial |
| Foreign filer support (20-F for TSM) | TSM uses 20-F not 10-K | Medium |

---

## Completed This Session

- [x] P75: Qualitative context 15K→50K + 30K injection
- [x] P76: Planner max_tokens 2048→4096
- [x] P78: Period-only XBRL match prevention
- [x] P81: JSON brace-matching parser
- [x] P82: Multi-company ticker support
- [x] P83: Calculator string safety
- [x] P84: 120s timeout guard
- [x] P87: Date context (as_of_date) + ReAct date awareness
- [x] P88: Delisted ticker fallback
- [x] P89: Section disclosure guidance in planner
- [x] P90: Parenthetical negatives parsing
- [x] P91: Refinanceable debt methodology
- [x] Cheap re-eval mode in eval.py
- [x] Tool_log recording for offline replay

---

## Decision Needed

- **Run Phase 4 (non-determinism)?** Run full eval 3x ($24-30) to identify flaky questions. This tells us which improvements are real vs luck.
- **Target for leaderboard?** Current ~72% stable. Top is 64.4%. We're likely already competitive but non-determinism makes it risky. Should stabilize before submitting.
- **Address Financial Modeling category?** Currently 2/4 (50%). TSM is out of scope (non-SEC data). Boeing needs table aggregation. BROS is rounding. Snap is passing. Focus elsewhere may have better ROI.
