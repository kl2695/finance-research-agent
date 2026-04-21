# FDA Regulatory QA Agent — Tech Spec

## Context

Product spec defines an FDA regulatory QA agent for regulatory affairs professionals, answering precise questions across 5 categories (predicate lookup, clearance timeline, classification reasoning, adverse event synthesis, multi-source reasoning) using openFDA APIs + AccessData PDF scraping. This tech spec defines the architecture, file-level changes, API integration details, and implementation sequence.

The agent plugs into the existing multi-domain core pipeline via the Domain ABC. No core changes needed.

## Data Source Architecture

### API Layer: openFDA (structured JSON)

| Endpoint | URL | Key Fields | Use |
|----------|-----|------------|-----|
| 510(k) | `/device/510k.json` | k_number, applicant, device_name, product_code, decision_date, clearance_type | Clearance records, timelines, metadata |
| MAUDE | `/device/event.json` | event_type, date_received, device.brand_name, device.device_report_product_code, mdr_text[].text | Adverse events, narratives, counts |
| Recalls | `/device/recall.json` | product_res_number, product_code, reason_for_recall, event_date_initiated, k_numbers | Recall history, cross-ref to 510(k)s |
| Classification | `/device/classification.json` | product_code, device_name, device_class, submission_type_id, regulation_number | Product code lookup, pathway determination |
| Enforcement | `/device/enforcement.json` | classification (I/II/III), product_description, recalling_firm | Recall class (not available in recall endpoint) |

**Query syntax:** `?search=field:value+AND+field2:[date1+TO+date2]&limit=N&skip=N`
**Count queries:** `?search=...&count=field.exact` returns aggregates without fetching records
**Rate limit:** 240 req/min with free API key, 40 req/min without. Register for key.
**Pagination ceiling:** skip max 25,000. For large result sets, partition by date range.
**Date format inconsistency:** 510(k) uses `YYYY-MM-DD`, MAUDE uses `YYYYMMDD`. Normalize in parser.

### Scraping Layer: AccessData (PDFs for predicate info)

The openFDA 510(k) endpoint has NO predicate device field and NO summary text. Predicate K-numbers live in the 510(k) Summary PDF.

**Fetch flow:**
1. `GET https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={K_NUMBER}` — server-rendered HTML, no JS needed
2. Extract PDF URL from HTML: `HREF="...cdrh_docs/pdf{YY}/{K_NUMBER}.pdf"`
3. Download PDF, extract text with `pdfplumber` (better table extraction than PyPDF2)
4. Regex `K\d{6}` to find all K-numbers, filter out subject device
5. LLM (Haiku) to classify: primary predicate vs additional predicate vs reference device from surrounding text

**Rate limiting:** Conservative 1 req/sec for AccessData (government server, no documented limits).
**Coverage:** Modern 510(k)s (2010+) consistently have Summary PDFs. Older ones may have "Statement" only (less structured).

## File Structure

```
domains/fda/
├── __init__.py           # exports FDADomain
├── domain.py             # FDADomain(Domain) — wires everything together
├── tools.py              # openFDA API client + AccessData scraper
├── concepts.py           # Regulatory concepts reference for planner prompt
├── methodology.py        # Regulatory methodology (clearance pathways, event taxonomy)
├── parser.py             # JSON response parser + PDF text extractor
├── identifier.py         # K-number / PMA number / product code extraction
└── benchmark/
    ├── questions.jsonl    # 510kQA benchmark (30 questions)
    └── ground_truth.py    # Canonical openFDA queries for each question
```

## Tool Implementations

### `tools.py` — 6 functions

**1. `search_510k(query_params: dict) -> str`**
- Calls `/device/510k.json`
- Params: k_number, product_code, applicant, date_range, limit
- Returns formatted text: one record per line with key fields
- Handles pagination for multi-result queries

**2. `get_510k_predicates(k_number: str) -> str`**
- Fetches AccessData detail page → extracts PDF URL → downloads PDF → extracts text
- Regex `K\d{6}` to find all K-numbers, filters out subject device
- Haiku call to classify predicate types from context
- Returns: "Primary predicate: K160702 (Astura Medical...). Additional: K080646 (Biomet...)"
- Falls back to "No summary PDF available" for older submissions

**3. `search_maude(query_params: dict) -> str`**
- Calls `/device/event.json`
- Params: brand_name, product_code, event_type, date_range, limit
- For count queries: uses `count=` endpoint for aggregates
- Returns formatted text with event summaries + narrative excerpts
- Truncates narratives to first 200 chars per event (full text available on follow-up)

**4. `search_recalls(query_params: dict) -> str`**
- Calls `/device/recall.json` + `/device/enforcement.json` (for recall class)
- Params: product_code, recalling_firm, date_range, limit
- Cross-references enforcement endpoint to get recall class (I/II/III)
- Returns: recall number, class, product, reason, date, firm

**5. `lookup_product_code(query: str) -> str`**
- Calls `/device/classification.json`
- Params: product_code (exact) OR device_name (text search)
- Returns: product code, device name, device class, submission type, regulation number
- For text search: returns top 5 matches ranked by relevance

**6. `_openfda_request(endpoint: str, params: dict) -> dict`**
- Shared HTTP client for all openFDA calls
- Adds API key if available (from .env `OPENFDA_API_KEY`)
- Rate limiting: 0.25s between requests (4 req/sec, well under 240/min limit)
- Error handling: returns readable error message on 404/400/429
- Timeout: 30s per request
- Uses `httpx` (same as SEC EDGAR client)

### Tool Schemas for ReAct

5 tools available to the ReAct agent:

```python
OPENFDA_510K_TOOL = {
    "name": "openfda_510k",
    "description": "Search FDA 510(k) clearance database. Find devices by K-number, product code, applicant, or date range. Returns clearance records with dates, decisions, and device info.",
    "input_schema": {
        "type": "object",
        "properties": {
            "k_number": {"type": "string", "description": "Specific K-number (e.g., K213456)"},
            "product_code": {"type": "string", "description": "FDA product code (e.g., DRG)"},
            "applicant": {"type": "string", "description": "Company name"},
            "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
            "date_to": {"type": "string", "description": "End date YYYY-MM-DD"},
            "limit": {"type": "integer", "description": "Max results (default 10, max 100)"},
        },
    },
}

OPENFDA_PREDICATES_TOOL = {
    "name": "openfda_predicates",
    "description": "Get predicate devices for a specific 510(k) submission. Fetches the 510(k) summary PDF and extracts cited predicate K-numbers.",
    "input_schema": {
        "type": "object",
        "properties": {
            "k_number": {"type": "string", "description": "K-number to look up predicates for"},
        },
        "required": ["k_number"],
    },
}

OPENFDA_MAUDE_TOOL = {
    "name": "openfda_maude",
    "description": "Search MAUDE adverse event database. Find reports by device brand, product code, event type (Death/Injury/Malfunction), or date range. Can return counts or detailed reports.",
    "input_schema": {
        "type": "object",
        "properties": {
            "brand_name": {"type": "string"},
            "product_code": {"type": "string"},
            "event_type": {"type": "string", "enum": ["Death", "Injury", "Malfunction"]},
            "date_from": {"type": "string"},
            "date_to": {"type": "string"},
            "count_only": {"type": "boolean", "description": "If true, return only the count of matching events"},
            "limit": {"type": "integer"},
        },
    },
}

OPENFDA_RECALL_TOOL = {
    "name": "openfda_recall",
    "description": "Search FDA device recall database. Find recalls by product code, firm, or date range. Includes recall class, reason, and corrective action.",
    "input_schema": { ... }  # similar pattern
}

OPENFDA_CLASSIFICATION_TOOL = {
    "name": "openfda_classification",
    "description": "Look up FDA device classification. Find product code, device class (I/II/III), and regulatory pathway (510(k)/PMA/De Novo/Exempt) by product code or device name.",
    "input_schema": { ... }  # similar pattern
}
```

## Extraction Architecture

openFDA returns structured JSON, not messy HTML like SEC filings. The extraction layer is simpler:

**Layer 1: JSON field extraction (exact)**
- Parse openFDA JSON response → extract named fields directly
- Map response fields to data_needed keys (e.g., `decision_date` → `clearance_date_k213456`)
- This is analogous to XBRL exact matching but easier — field names are consistent

**Layer 2: LLM fallback for ambiguous/cross-document queries**
- When the answer requires reasoning across multiple API responses
- When the question asks about something not directly a named field (e.g., "median clearance time" requires computation)
- Sonnet reads the assembled tool outputs and extracts the answer

The intermediate layers (regex text parsing, Haiku fact matching) are less needed because the source data is structured JSON, not HTML tables. The core extractor's `_parse_filing_text()` still runs on PDF text and MAUDE narratives for dollar amounts / dates / counts found in prose.

**Classification for `classify_tools()`:**
- Structured: `openfda_510k`, `openfda_classification`, `openfda_recall` (JSON with named fields)
- Prose: `openfda_maude` (has mdr_text narratives), `openfda_predicates` (PDF text)

## Prompt Design

### Planner System Prompt

Includes:
- `fda_concepts.py`: Regulatory pathway definitions (510(k), PMA, De Novo, Exempt), device class meanings (I/II/III), product code taxonomy, substantial equivalence definition, MAUDE event types
- `fda_methodology.py`: How to compute clearance timelines (decision_date - date_received), how to count adverse events (use count endpoint for totals, detail endpoint for samples), how to trace predicate chains, how to cross-reference clearances with recalls

### Planner Prompt Template

`filings_needed` types for FDA:

```
// TYPES:
//   "510k"           — clearance record lookup by K-number or product code
//   "predicates"     — predicate device chain for a specific K-number (fetches PDF)
//   "maude"          — adverse event search (by brand, product code, date range)
//   "maude_count"    — adverse event COUNT only (aggregate, no detail records)
//   "recall"         — recall search by product code or firm
//   "classification" — product code / device class lookup
//
// EXAMPLES:
//   {"type": "510k", "identifier": "K213456", "reason": "clearance record for subject device"}
//   {"type": "510k", "concepts": ["product_code:DRG"], "period": "2022-2024", "reason": "all DRG clearances in date range"}
//   {"type": "predicates", "identifier": "K213456", "reason": "predicate device chain"}
//   {"type": "maude_count", "concepts": ["brand_name:HeartMate 3", "event_type:Death"], "period": "2023-01-01 to 2024-06-30", "reason": "death event count"}
//   {"type": "recall", "concepts": ["product_code:LWS"], "period": "2022-2024", "reason": "recalls for product code LWS"}
//   {"type": "classification", "concepts": ["product_code:DRG"], "reason": "device class and pathway"}
```

### ReAct Few-Shot Examples (3 patterns)

**Example 1 — Clearance timeline (maps to TJX beat/miss pattern):**
Compute median clearance time for product code DRG. Fetch all clearances in date range → compute (decision_date - date_received) for each → compute median. Cross-validate against known typical timelines.

**Example 2 — Multi-source reasoning (maps to KO peer comparison):**
For product code LWS, list clearances since 2022 + any recalls + adverse event clusters. Three separate tool calls → merge results → report synthesis.

**Example 3 — Predicate lookup (maps to Shift4 deep-section extraction):**
Find predicates for K213456. Calls predicate tool → gets PDF text → identifies predicate K-numbers → looks up each predicate's clearance date via 510(k) tool.

## Identifier Handling

Primary identifiers in FDA domain:
- **K-number:** `K` + 6 digits (e.g., K213456). Regex: `K\d{6}`
- **PMA number:** `P` + 6 digits (e.g., P160054). Regex: `P\d{6}` (v2)
- **Product code:** 3 uppercase letters (e.g., DRG). Regex: `[A-Z]{3}`

`extract_identifier()` tries K-number first, then product code. Exclusion list: common 3-letter words that aren't product codes (THE, AND, FOR, etc.).

## Concept Map (for extraction matching)

```python
{
    "clearance_date": ["decision_date"],
    "received_date": ["date_received"],
    "applicant": ["applicant"],
    "device_name": ["device_name"],
    "product_code": ["product_code"],
    "device_class": ["device_class"],
    "event_count": ["total", "count"],
    "recall_reason": ["reason_for_recall"],
    "predicate": ["predicate_device", "predicate_k_number"],
}
```

## Cross-Validation

FDA-specific sanity checks:
- Clearance time (decision_date - date_received) should be 30-1000 days. Outside this range → likely wrong dates
- Device class must be 1, 2, or 3. Anything else → extraction error
- Product codes are exactly 3 uppercase letters. Invalid format → extraction error
- Event counts should be non-negative integers

## Benchmark: 510kQA

### Ground Truth Strategy

Each question has a `ground_truth.py` entry with the canonical openFDA query:

```python
{
    "id": "510kqa_003",
    "query": "GET /device/510k.json?search=product_code:DRG+AND+decision_date:[20220101+TO+20241231]&limit=0",
    "extract": "meta.results.total",  # or a computation over results
    "captured_at": "2026-05-01",
}
```

This makes ground truth reproducible and verifiable by re-running the query.

### Question Distribution (30 questions)

| Category | Count | Data Sources Used |
|----------|-------|-------------------|
| Predicate lookup | 6 | 510(k) API + AccessData PDF |
| Clearance timeline | 6 | 510(k) API (date arithmetic) |
| Classification reasoning | 5 | Classification API |
| Adverse event synthesis | 6 | MAUDE API (counts + narratives) |
| Multi-source reasoning | 7 | 510(k) + MAUDE + Recall APIs |

### 5 Showcase Questions (curated subset)

Pick one from each category that demonstrates the most impressive capability. Polish the output with clear citations and exact numbers.

## Implementation Sequence

| Step | Days | Output | Verification |
|------|------|--------|-------------|
| 1. openFDA API client (`tools.py`) | 2 | All 5 endpoints working, rate limiting, error handling | Unit tests with recorded API responses |
| 2. AccessData PDF scraper | 1 | `get_510k_predicates()` working for 5 test K-numbers | Unit tests with saved PDFs |
| 3. Response parser (`parser.py`) | 1 | JSON → text formatting, date normalization, narrative truncation | Unit tests |
| 4. FDADomain wiring (`domain.py`) | 1 | All Domain ABC methods implemented, prompts written | `FDADomain()` instantiates, smoke test on 1 question per type |
| 5. Methodology + concepts docs | 1 | `concepts.py`, `methodology.py` with regulatory reference content | Included in planner prompt, verified byte-count |
| 6. 510kQA question design | 1 | 30 questions in `questions.jsonl` (draft) | Category balance check |
| 7. Ground truth research | 2-3 | Canonical queries + verified answers for all 30 questions | Each answer reproducible via API query |
| 8. Eval runs + iteration | 2 | Benchmark scores vs Sonnet/Opus baselines, showcase questions polished | Scores reported per category |
| **Total** | **11-14** | | |

## Dependencies

- `httpx` — already in requirements (used by SEC EDGAR client)
- `pdfplumber` — **new dependency** for PDF text extraction (pip install pdfplumber)
- `OPENFDA_API_KEY` — optional, add to `.env`. Without it: 40 req/min (sufficient for dev, tight for eval)

## Open Questions Resolved

1. **API key:** Register for free key upfront. 40 req/min without key is too tight for eval runs (30 questions × multiple API calls each).
2. **510(k) summary text:** Not available via API. Get from AccessData PDFs. Coverage is good for 2010+.
3. **MAUDE narratives:** Reliably populated for manufacturer reports. Rich clinical detail. Use for adverse event synthesis questions.
4. **Predicate chain depth:** v1 = direct predicates only (from subject device's summary PDF). Recursive chain (predicates of predicates) is v2.
5. **Ground truth stability:** Freeze `captured_at` date per question. Document in benchmark metadata. Re-verify before any public release.
