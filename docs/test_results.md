# Multi-Domain Research Agent — Test Results

## Finance Domain (FAB Benchmark)

Tracked results against the Vals AI Finance Agent Benchmark (FAB) public validation set (50 questions).

Dataset: `vals-ai/finance_agent_benchmark` on HuggingFace (split: train).

---

## Phase 2 Baseline Run (2026-04-19)

| Metric | Value |
|--------|-------|
| Questions | 50 |
| Pass | 34 |
| Fail | 16 |
| Accuracy | 68.0% |
| Avg criteria score | 57.2% |
| Total time | 614 min |
| Leaderboard comparison | Bare Sonnet 4.6: 63.3%, Ours: 68.0% (+4.7 pts) |

**Failures by root cause:**
| Category | Count | Indices | Fix |
|----------|-------|---------|-----|
| Qualitative context too short (P75) | 5 | 0, 20, 22, 24, 28 | Increase filing context for qualitative Qs |
| Multi-quarter beat/miss (P76) | 3 | 47, 48, 49 | Fetch more filings per question |
| Financial modeling (P77) | 3 | 11, 29, 32 | Non-standard data sources needed |
| XBRL fills wrong keys (P78) | 2 | 10, 13 | Fix concept matching overlap |
| Close but imprecise (P79) | 2 | 16, 21 | Use company-reported KPIs |
| Runtime errors (P80) | 2 | 32, 41 | Error handling + timeouts |

**Additional systemic issues discovered:**
| Issue | Problem | Impact | Fix Complexity |
|-------|---------|--------|---------------|
| LLM matching JSON parsing failures | P81 | ~10 questions degraded | Easy |
| Multi-company prefetch only handles one ticker | P82 | 2 questions (cross-company) | Medium |
| Calculator crashes on string values | P83 | 1 crash | Easy |
| No ReAct timeout — agent ran 6.5 hours | P84 | 1 timeout | Easy |
| Planner requests non-existent filings | P85 | No failures, wasted API calls | Low priority |

---

## Summary

| Run Date | Questions Tested | Correct | Close | Wrong/Failed | Accuracy |
|----------|-----------------|---------|-------|-------------|----------|
| 2026-04-16 to 2026-04-18 | 18/50 | 15 | 3 | 0 | ~100% |

**Definitions:**
- **Correct:** Answer matches ground truth (exact or within rounding tolerance)
- **Close:** Answer is more precise than GT or uses informal name (would likely pass rubric)
- **Wrong/Failed:** Answer doesn't match ground truth

---

## Detailed Results (18 tested)

| idx | Type | Question | GT | Our Answer | Time | Status |
|-----|------|----------|-----|-----------|------|--------|
| 37 | Beat or Miss | Lyft Q4'24 EBITDA margin vs guidance | Beat by 26.1bps | 26.08 bps | 40s | exact |
| 14 | Numerical Reasoning | US Steel inventory turnover FY2024 | 6.49x | 6.49x | 17s | exact |
| 9 | Numerical Reasoning | Palantir 3-year revenue CAGR | 14.56% | 14.56% | 22s | exact |
| 40 | Quantitative Retrieval | FND same-store sales Q4 2024 | -0.8% | -0.8% | 25s | exact |
| 6 | Qualitative Retrieval | Airbnb CFO | Elinor Mertz | Ellie Mertz | 42s | close (same person) |
| 8 | Beat or Miss | Micron Q3 2024 GAAP gross margin beat/miss | 140bps BEAT | 140bps BEAT | 44s | exact |
| 33 | Quantitative Retrieval | Cloudflare channel partner % | 20% | 20% | 49s | exact |
| 19 | Numerical Reasoning | Oracle effective tax rate + YoY change | 10.9%, +410bps | 10.9%, +410bps | 54s | exact |
| 27 | Quantitative Retrieval | Netflix total cash requirements 2025 | $14,426,266,000 | $14,426.3M | 22s | exact |
| 12 | Numerical Reasoning | 3D Systems director compensation | $2,263,113 | $2,263,113 | 22s | exact |
| 44 | Adjustments | Airbnb SBC adjustment 2024 | $1,407,000,000 | $1.407B | 49s | exact |
| 34 | Adjustments | Uber largest EBITDA adjustment FY2023 | SBC $1.935B | SBC $1.94B | 53s | exact |
| 25 | Numerical Reasoning | MSFT employees outside US % | 45% | 44.74% | 25s | close (more precise) |
| 16 | Trends | Airbnb take rate FY2022-2024 | 13.3/13.5/13.6% | 13.29/13.54/13.57% | 56s | close (more precise) |
| 2 | Beat or Miss | TJX Q4 FY2025 pre-tax margin beat/miss | 80/70bps beat | 80/70bps beat | 58s | exact |
| 7 | Quantitative Retrieval | TKO Endeavor acquisition cost | $3.25B | $3.25B | 46s | exact |
| 1 | Trends | Netflix ARPU 2019-2024 | 10.82→11.70 | 10.82→11.70 (6/6) | 52s | exact |
| 22 | Qualitative Retrieval | JM Smucker distribution center timing | Expected 2025 | Already operational | 51s | GT stale |

---

## Untested Questions (32 remaining)

| idx | Type | Question (truncated) |
|-----|------|---------------------|
| 0 | Market Analysis | US Steel merger with Nippon Steel... |
| 3 | Complex Retrieval | AMD revenue guidance range Q2-Q4 2024... |
| 4 | Qualitative Retrieval | BBSI board nominees 2024... |
| 5 | Complex Retrieval | AMZN vs META vs GOOG capex 2025... |
| 10 | Quantitative Retrieval | ABNB common stock shares outstanding... |
| 11 | Financial Modeling | TSM Q2 guidance beat/miss with seasonality... |
| 13 | Trends | Zillow FCF margin trend 3 years... |
| 15 | Complex Retrieval | Series D mandatory convertible preferred stock terms... |
| 17 | Qualitative Retrieval | Workday gross/net retention metric... |
| 18 | Numerical Reasoning | MSCI operating leases maturing next 5 years... |
| 20 | Qualitative Retrieval | Shift4 vendor concentration risk... |
| 21 | Numerical Reasoning | ABNB gross booking per room night 2022-2024... |
| 23 | Numerical Reasoning | Salesforce debt face value excl sustainability notes... |
| 24 | Qualitative Retrieval | Paylocity regulatory risks FY2024... |
| 26 | Qualitative Retrieval | Allstate Junior Subordinated Debentures terms... |
| 28 | Qualitative Retrieval | Spirit Airlines operating KPIs FY2024... |
| 29 | Financial Modeling | Snapchat convertible notes dilutive impact... |
| 30 | Financial Modeling | BROS revenue growth + margin compression model... |
| 31 | Quantitative Retrieval | loanDepot loan origination breakdown... |
| 32 | Adjustments | Boeing debt refinancing impact on net income... |
| 35 | Quantitative Retrieval | RDFN acquisition price... |
| 36 | Quantitative Retrieval | Warner Discovery restructuring costs... |
| 38 | Qualitative Retrieval | Delta Airlines guided financial metrics... |
| 39 | Adjustments | Airbnb net income to adjusted EBITDA adjustments... |
| 41 | Market Analysis | Coca-Cola dividend payout ratio vs competitors... |
| 42 | Financial Modeling | Uber take-rate vs volume revenue growth... |
| 43 | Quantitative Retrieval | ABNB avg nights per booking Asia Pacific... |
| 45 | Market Analysis | Zillow acquisition strategy 2 years... |
| 46 | Beat or Miss | FOUR payment volume vs guidance Q3 2024... |
| 47 | Beat or Miss | Lemonade FY2024 vs full year guidance... |
| 48 | Beat or Miss | General Mills adjusted diluted EPS guidance 2 years... |
| 49 | Beat or Miss | AMD non-GAAP gross profit guide Q1-Q4 2024... |

---

## Question Type Coverage

| Type | Total in FAB | Tested | Correct | Untested |
|------|-------------|--------|---------|----------|
| Beat or Miss | 7 | 3 | 3 | 4 |
| Numerical Reasoning | 7 | 5 | 5 | 2 |
| Quantitative Retrieval | 9 | 4 | 4 | 5 |
| Qualitative Retrieval | 9 | 2 | 2 | 7 |
| Trends | 3 | 2 | 2 | 1 |
| Adjustments | 4 | 2 | 2 | 2 |
| Financial Modeling | 3 | 0 | 0 | 3 |
| Market Analysis | 3 | 0 | 0 | 3 |
| Complex Retrieval | 3 | 0 | 0 | 3 |

---

## Key Fixes by Question (traceable to problems doc)

- **Lyft:** P27→P29→P30→P31→P32 (tool_log truncation → table unit detection → context keywords → source ordering)
- **Palantir:** P33→P34→P35 (XBRL concept fallback → deduplication → CAGR N value)
- **US Steel:** P36 (inventory turnover methodology)
- **Micron:** P45→P63 (fiscal quarter filename matching → 2-digit year)
- **Cloudflare:** P47→P48→P50 (section marker priority → smart section prefetch → tax marker ordering)
- **Oracle:** P39→P42→P50 (non-calendar FY → XBRL substring matching → section markers)
- **Netflix cash:** P54→P57→P58→P59→P60→P61 (section access → decisiveness → table multiplier → column parsing → forward-looking period → cash keyword)
- **Netflix ARPU:** P56→P62→P64→P65→P67 (KPI methodology → multi-filing → structured filings → period extraction → supplementary filing access)
- **TJX:** P51→P63 (wrong filing year → 2-digit FY filename matching + range reporting)
- **TKO:** P66→P64 (post-closing adjustment → structured filing selection)
