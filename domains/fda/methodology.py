"""FDA regulatory methodology — HOW to correctly answer regulatory questions.

Parallel to financial_methodology.py. Defines correct procedures for each
question type so the planner generates valid research plans.
"""

FDA_METHODOLOGY = """\
CLEARANCE TIMELINE ANALYSIS:
- Clearance time = decision_date - date_received (in days)
- For median/average calculations: fetch ALL clearances matching the filter, compute per-record, then aggregate
- Use the openFDA count endpoint for total counts, detail endpoint for per-record date arithmetic
- Standard 510(k) review: typically 90-180 days. Special 510(k): typically 30-90 days.
- If the question asks "median clearance time for product code X," you need every clearance record in the date range — not a sample

PREDICATE DEVICE ANALYSIS:
- Predicate K-numbers are NOT in the openFDA API. They are in the 510(k) summary PDF.
- Use the predicate lookup tool to fetch the summary PDF and extract predicate K-numbers.
- Primary predicate: the main device the new device is compared to
- Additional predicates: secondary comparisons (often for specific features)
- Reference devices: cited for context but not the basis of substantial equivalence
- To build a predicate chain: look up the primary predicate's predicates, recursively. v1 = direct predicates only.

ADVERSE EVENT ANALYSIS:
- MAUDE event types: Death, Injury, Malfunction (plus rare blank/Other)
- For counts: use the count endpoint with event_type.exact — this gives a breakdown by type in one call
- For date-filtered counts: add date_received range to the search filter
- MAUDE date format is YYYYMMDD (no hyphens) in both search and response
- Narratives are in mdr_text array, under text_type_code "Description of Event or Problem"
- Brand names in MAUDE are often ALL CAPS and may include model suffixes (e.g., "HEARTMATE 3 LVAS IMPLANT KIT")
- Search brand names in uppercase for best results

CLASSIFICATION AND PATHWAY REASONING:
- Product code determines device class AND regulatory pathway
- Submission type IDs: 1=510(k), 2=PMA, 3=De Novo, 7=Exempt
- If a device description matches a Class III product code, it requires PMA — NOT 510(k)
- For novel devices not matching an existing product code: likely De Novo pathway
- Use the classification endpoint with device_name text search to find matching product codes

RECALL ANALYSIS:
- Recall classification (I/II/III) is NOT in the /device/recall endpoint
- Use /device/enforcement endpoint for classification field
- The recall endpoint has k_numbers field linking recalls to specific 510(k) clearances
- Root causes: Device Design, Software Design, Process Control, Nonconforming Material, etc.

MULTI-SOURCE REASONING:
- Product code is the primary join key across all endpoints
- Typical flow: classification (get product code + class) → 510(k) (clearance history) → MAUDE (adverse events) → recall (safety actions)
- When cross-referencing, always specify the same date range across all queries for consistency
- Report findings per source, then synthesize

DATA FORMAT CONVENTIONS:
- 510(k) dates: YYYY-MM-DD in API responses, YYYYMMDD in search queries
- MAUDE dates: YYYYMMDD in both responses and searches
- Recall dates: YYYY-MM-DD
- Always normalize to YYYY-MM-DD when reporting to the user
- K-numbers: always uppercase K followed by exactly 6 digits
- Product codes: always exactly 3 uppercase letters

TERMINOLOGY:
- "Clearance" = 510(k) pathway. "Approval" = PMA pathway. NEVER say "FDA approved" for a 510(k) device.
- "Predicate device" = legally marketed device used as the comparison basis for substantial equivalence
- "Adverse event" = any undesirable experience associated with use of a medical device
- "MDR" = Medical Device Report (the formal name for a MAUDE submission)"""
