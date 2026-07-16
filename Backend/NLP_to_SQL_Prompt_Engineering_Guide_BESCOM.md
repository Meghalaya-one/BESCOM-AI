# NLP-to-SQL Prompt Engineering Guide
## Database: BESCOM DCB (Demand-Collection-Balance) Audit — `bescom_dcb_audit`
### Built from: BESCOM.xlsx (verified, 20,000 rows, 132 columns)

---

## ⚠️ DATA QUALITY FACTS (Critical for Prompt Design)

Before building prompts, your middle layer must know these facts:

| Issue | Detail |
|---|---|
| **The geo hierarchy is NOT a clean drill-down tree** | Same `DIVISION` name appears under up to 5 different `CIRCLE`s (e.g. "TUMAKURU" appears under 5 circles); 45 of 67 subdivisions map to more than one division; each of the 4 `SECTION` codes maps to 19 different divisions. Treat Zone/Circle/Division/Subdivision/Section as **independent filterable columns**, not a nested tree — never assume filtering by Division narrows Circle, or vice versa |
| **DIVISION has a casing duplicate** | `'TUMAKURU'` (2,000 rows) and `'Tumakuru'` (603 rows) are almost certainly the same division split by inconsistent casing — always use `UPPER()` or `ILIKE` |
| **No time/period column exists** | Zone+Circle+Division+Subdivision+Section+Tariff+Voltage yields only 5,813 unique combinations across 20,000 rows — some combination of rows is duplicated per period, but no date/month/year field is present to group by. **Never answer "trend," "month-over-month," or "this year vs last year" questions** — the grain is unconfirmed |
| **Two columns are 100% empty** | Columns literally named `2` and `4` (blank headers in the source) have zero non-null values across all 20,000 rows — never query them |
| **Three "Active/Inactive/Total" installation triplets have no distinguishing label** | Beyond the confirmed `Active Installations`/`Inactive Installations` pair, three more unlabeled repeats of Active/Inactive/Total exist in the source with no header text explaining what each represents — do not silently pick one when a user asks about "active installations" |
| **Six Revenue/Interest/Tax/P&G Surcharge blocks repeat across DCB stages** | Columns 41–107 are six near-identical blocks (Opening Balance → Current Demand → Debit → Bill Cancellation → Net Demand → Collection → Closing). The stage each block belongs to is **inferred from column order and standard DCB structure, not stated explicitly** in most of them — a handful *do* have the stage spelled out in the header formula (e.g. `net_collection_final`, `net_demand_revenue_final`) and those should always be preferred over the inferred ones |
| **Several "Total" formulas reference sub-line codes not present as columns** | e.g. `Total Sum (37+38+39+40+40A+104)` references `40A`, which has no matching column in this extract — these totals may not fully reconcile if recomputed manually; use the stored total column, don't try to rebuild it |
| **`% of Recovery of Average Cost` has no stated formula and a very wide range** | Values run from 1.1% to 214.6% with no formula in the header (unlike every other `%` column, which states its formula) — flag this to the user as needing source verification if it drives a decision |
| **`CORPORATE`, `Debit`, and `Credit` columns are constants** | `CORPORATE` = `'BESCOM'`, `Debit` = `'DCB Debit Account'`, `Credit` = `'DCB Credit Account'` for all 20,000 rows — no filtering value, safe to exclude from SELECT lists by default |
| **`installations_billed` exceeds total installations in 31.6% of rows** | 6,329 of 20,000 rows have `Installations Billed` greater than `Total (10+11)` (active + inactive installations) — by up to 2,861 in one row. Billing more installations than exist in a row is logically inconsistent; check this against the source before `installations_billed` drives any "% billed" or coverage metric, since `pct_billed_installations` (billed/total × 100) will read above 100% wherever this occurs |
| **`installations_metered` exceeds total installations in 13.75% of rows** | 2,750 of 20,000 rows have `Metered Installations` greater than `Total (10+11)` — same category of inconsistency as above |
| **`Wheeled Energy Units` has negative values in ~1% of rows** | 208 rows (1.04%) have negative wheeled energy, down to -187,956 units. This could legitimately represent net export back to the grid, or could be a data error — the source doesn't say which, so don't assume either without checking |
| **No missing data otherwise** | Every other column is 100% populated (20,000/20,000) and there are zero exact-duplicate rows — this is unusually clean for a real-world extract; worth confirming with the source system whether this is live data or a generated/test set before wiring to production |

---

## 1. VERIFIED DATABASE SCHEMA

**Table name:** `bescom_dcb_audit`

Full column-by-column detail (name, category, DCB stage, confidence) lives in `schema_metadata.py`. Summary by group:

```
Group                Columns   Confidence          Notes
--------------------  --------  ------------------  --------------------------------------------
Geography              6       confirmed           corporate, zone, circle, division,
                                                     subdivision, section — NOT a clean tree, see above
Tariff/account          6       confirmed           tariff_category, voltage_class_kv,
                                                     is_demand_based_tariff, is_time_of_day_tariff, +2 constants
Installations          19       16 confirmed        active/inactive/total x4 groups; 3 groups unlabeled
                                  3 inferred
Consumption              8       confirmed           assessed/metered x taxed/exempt, totals, wheeled units
Demand (OB→NetDemand)   36       2 confirmed          6 repeating Revenue/Interest/Tax/P&G blocks by DCB stage
                                  34 inferred (stage)
Collection              27       6 confirmed          fixed/energy/FPPCA charges + net_collection_final
                                  21 inferred (stage)  are the trustworthy, explicitly-defined ones
Write-off/Closing        7       6 confirmed          closing_* columns have explicit reconciliation formulas
                                  1 inferred
Ratio/KPI               14       confirmed           all pre-computed; formulas stated except pct_recovery_of_avg_cost
GST/TCS                  9       confirmed           mirrors the OB→CB lifecycle for GST and TCS specifically
Other                    1       confirmed           balance_transfer_amount
Junk (drop)               2       confirmed           columns named '2' and '4', 100% empty
```

---

## 2. REAL REFERENCE VALUES

### Zones (record counts — evenly split)
```
BMAZ - South    5,000
BMAZ - North    5,000
BRAZ            5,000
CTAZ            5,000
```

### Circles (with record counts)
```
Bangalore West Circle     2,559
Bangalore North Circle    2,532
Davanagere Circle         2,509
Tumakuru Circle           2,491
Bangalore East Circle     2,468
Bangalore South Circle    2,441
Ramanagara Circle         1,689
Bangalore Rural Circle    1,682
Kolar Circle              1,629
```

### Divisions (top counts — 22 raw values, 21 after fixing casing)
```
N R MOHALLA                3,400
MALLESHWARAM                2,600
KR PURAM NORTH               2,000
TUMAKURU                     2,000   (also appears as: Tumakuru — 603)
Devanahalli                    860
Hoskote                         822
Sira                            656
Hosadurga                       656
Chitradurga                     647
Madhugiri                       626
Davanagere                      620
Tiptur                          606
Harihara                        586
Anekal                          465
Kolar Gold Fields (KGF)         427
Doddaballapura                  423
Channapatna                     418
Ramanagara                      412
Chikkaballapura                 395
Kanakapura                      394
Kolar                           384
```
**→ Always query DIVISION with `UPPER(division) = UPPER('user_input')`**

### Sections (only 4 codes, each spans many divisions — do not treat as a drill-down of division)
```
TMK1    6,200
NRM1    5,200
MLS1    5,000
KRP1    3,600
```

### Tariff categories (14 codes)
```
LT-1     2,659      HT-4     1,492      LT-2B    1,474
LT-3     1,454      HT-2B    1,440      LT-2A    1,436
HT-3     1,410      LT-6     1,409      HT-2A    1,408
HT-1     1,404      LT-7     1,396      LT-5     1,395
LT-4B      816      LT-4A      807
```

### Voltage class (KV)
```
0.230 (LT single-phase)   12,200
0.415 (LT three-phase)     6,000
11.000 (HT)                 1,800
```

### Demand-based tariff / Time-of-day flags
```
Demand Based Tariff:  No 11,000 / Yes 9,000
Time of Day:          No 15,800 / Yes 4,200
```

---

## 3. SYSTEM PROMPT — DROP THIS INTO YOUR PROMPT ASSEMBLER

```
You are a SQL query generator for the BESCOM DCB (Demand-Collection-Balance)
electricity utility audit database.

TABLE: bescom_dcb_audit
TOTAL RECORDS: 20,000

COLUMNS AND RULES (clean snake_case names — see schema_metadata.py for the full list):
- zone, circle, division, subdivision, section  → geography, but NOT a clean nested
  tree in this data — same division name can appear under multiple circles, and
  section does not map cleanly to a single division. Filter each independently.
- division                                      → ⚠ casing inconsistent
  ('TUMAKURU' vs 'Tumakuru') — ALWAYS use UPPER(division) = UPPER('value')
- tariff_category                               → 14 codes, e.g. LT-1, HT-3
- voltage_class_kv                              → 0.23, 0.415, or 11.0
- is_demand_based_tariff / is_time_of_day_tariff → 'Yes'/'No' text, not boolean
- installations_active / installations_inactive / installations_total
  and 3 more unlabeled groups (installations_active_grp2/3/4, etc.)
  → ⚠ three of these groups have NO stated purpose in the source. If the
    user's question could mean any of them, ask which one instead of guessing.
- ~70 demand/collection/closing columns          → belong to one of 6 DCB stages
  (OpeningBalance, CurrentDemand, Debit, BillCancellation, NetDemand, Collection,
  ClosingBalance). Stage is INFERRED from column order for most of them.
  Columns with "_final" in the name (net_demand_revenue_final,
  net_demand_tax_final, net_collection_final, closing_revenue, etc.) carry an
  EXPLICIT source formula — prefer these whenever they answer the question.
- 14 ratio/KPI columns (pct_*, *_per_unit, ratio_*) → pre-computed, formulas
  stated in the schema except pct_recovery_of_avg_cost (no stated formula,
  unusually wide range 1.1%–214.6% — flag it if used)
- 9 gst_tcs_* columns                            → mirror the demand/collection
  lifecycle specifically for GST and TCS
- write_off, balance_transfer_amount             → straightforward numeric columns

⚠ NEVER query the two columns with blank/empty headers in the source (100% empty)
⚠ Division casing is mixed — always use: UPPER(division) = UPPER('user_value')
⚠ There is no date/period column — never build a WHERE clause that filters by
  time, and never answer trend/month-over-month questions; tell the user the
  grain isn't confirmed instead
⚠ Do not recompute ratio/KPI columns from raw components — use the stored column

QUERY RULES:
1. Output ONLY the SQL query. No explanation, no markdown fences, unless the
   user's question requires a clarification or refusal (see rules 6-7) —
   in that case output plain text, not SQL.
2. Use snake_case column names exactly as given — no double-quoting needed,
   they're already SQL-safe.
3. For geography filters: UPPER(division) = UPPER('value') (division only —
   zone/circle/subdivision/section values are already consistently cased).
4. For tariff filters spanning a family: tariff_category LIKE 'LT-%' or 'HT-%'.
5. Default to LIMIT 100 for row-level SELECTs; omit LIMIT for COUNT/SUM/AVG
   aggregates.
6. If the question needs one of the three unlabeled installation groups, or
   hinges on which DCB stage an "inferred" column belongs to, ask a clarifying
   question instead of picking one silently.
7. If the question asks for a trend, month-over-month, or "this period vs
   last period" comparison, explain that no time dimension exists in this
   table rather than fabricating one.
```

---

## 4. FEW-SHOT EXAMPLES (Accurate to this database)

### 4.1 — Collection efficiency by circle
**User:** Which circle has the lowest collection efficiency with adjustment?
```sql
SELECT circle,
       AVG(pct_collection_efficiency_with_adj) AS avg_collection_efficiency
FROM bescom_dcb_audit
GROUP BY circle
ORDER BY avg_collection_efficiency ASC
LIMIT 1;
```

---

### 4.2 — Write-off total for a tariff family in a zone
**User:** What's the total write-off amount for LT tariffs in the BRAZ zone?
```sql
SELECT SUM(write_off) AS total_write_off
FROM bescom_dcb_audit
WHERE zone = 'BRAZ'
  AND tariff_category LIKE 'LT-%';
```

---

### 4.3 — Top N by net collection
**User:** Show me the top 5 subdivisions by net collection.
```sql
SELECT subdivision,
       SUM(net_collection_final) AS total_net_collection
FROM bescom_dcb_audit
GROUP BY subdivision
ORDER BY total_net_collection DESC
LIMIT 5;
```

---

### 4.4 — Division lookup (casing trap)
**User:** Show all rows for Tumakuru division.
```sql
SELECT *
FROM bescom_dcb_audit
WHERE UPPER(division) = UPPER('Tumakuru')
LIMIT 100;
```
This intentionally catches both `'TUMAKURU'` and `'Tumakuru'` — an exact-match query without `UPPER()` would silently miss 603 of the 2,603 combined rows.

---

### 4.5 — Count installations by tariff
**User:** How many active installations are there per tariff category?
```sql
SELECT tariff_category,
       SUM(installations_active) AS total_active_installations
FROM bescom_dcb_audit
GROUP BY tariff_category
ORDER BY total_active_installations DESC;
```
Uses `installations_active` (the confirmed, labeled column) rather than one of the three unlabeled Active/Inactive groups.

---

### 4.6 — Consumption per installation by voltage class
**User:** What's the average consumption per installation for HT connections, by circle?
```sql
SELECT circle,
       AVG(consumption_per_installation) AS avg_consumption_per_installation
FROM bescom_dcb_audit
WHERE tariff_category LIKE 'HT-%'
GROUP BY circle
ORDER BY avg_consumption_per_installation DESC;
```

---

### 4.7 — GST/TCS closing balance for a circle
**User:** What is the closing GST and TCS balance for Kolar Circle?
```sql
SELECT SUM(gst_tcs_closing_balance) AS total_gst_tcs_closing_balance
FROM bescom_dcb_audit
WHERE circle = 'Kolar Circle';
```

---

### 4.8 — Arrears ratio outliers
**User:** Which subdivisions have an arrears-to-demand ratio above 0.5?
```sql
SELECT subdivision, circle,
       AVG(ratio_arrears_to_demand) AS avg_arrears_ratio
FROM bescom_dcb_audit
GROUP BY subdivision, circle
HAVING AVG(ratio_arrears_to_demand) > 0.5
ORDER BY avg_arrears_ratio DESC;
```

---

### 4.9 — Time-of-day tariff revenue
**User:** How much net demand revenue comes from Time-of-Day tariff accounts?
```sql
SELECT SUM(net_demand_revenue_final) AS total_net_demand_revenue
FROM bescom_dcb_audit
WHERE is_time_of_day_tariff = 'Yes';
```

---

### 4.10 — Metered vs unmetered installations by zone
**User:** Compare metered and unmetered installation counts across zones.
```sql
SELECT zone,
       SUM(installations_metered) AS total_metered,
       SUM(installations_unmetered) AS total_unmetered
FROM bescom_dcb_audit
GROUP BY zone
ORDER BY zone;
```

---

### 4.11 — Voltage class breakdown
**User:** How many rows are there for each voltage class?
```sql
SELECT voltage_class_kv, COUNT(*) AS row_count
FROM bescom_dcb_audit
GROUP BY voltage_class_kv
ORDER BY voltage_class_kv;
```

---

### 4.12 — Average cost of supply vs recovery
**User:** What's the average recovery percentage against average cost of supply, by zone?
```sql
SELECT zone,
       AVG(pct_recovery_of_avg_cost) AS avg_recovery_pct
FROM bescom_dcb_audit
GROUP BY zone
ORDER BY avg_recovery_pct DESC;
```
Flag to the user: `pct_recovery_of_avg_cost` has no stated formula in the source and ranges unusually widely (1.1%–214.6%) — worth a source check before this number goes into a report.

---

### 4.13 — Ambiguous installation question (refusal / clarification pattern)
**User:** How many active installations does the Malleshwaram division have?
```
There are four different "Active/Inactive" installation column pairs in this
table: installations_active (confirmed, paired with installations_total), and
three further unlabeled repeats (installations_active_grp2/3/4) whose purpose
isn't stated in the source data. Which one did you mean, or would you like all
four broken out separately? I don't want to silently pick one and hand you a
number that's actually a different metric.
```

---

### 4.14 — Trend question (refusal pattern — no time dimension)
**User:** How has net collection trended month over month this year?
```
I can't answer that from this table as it stands — there's no period or
month column in the schema, and Zone/Circle/Division/Subdivision/Section/
Tariff combinations repeat far more times than they're unique (5,813 unique
combinations across 20,000 rows), which usually signals either a hidden time
dimension or duplicated rows. Could you confirm whether the source system has
a date/period field, or whether this extract represents a single period?
I don't want to fabricate a time filter that runs but returns a meaningless
answer.
```

---

### 4.15 — Demand-based tariff filter with aggregation
**User:** Total tax collected from demand-based tariff accounts in the Bangalore circles.
```sql
SELECT circle, SUM(collection_tax) AS total_tax_collected
FROM bescom_dcb_audit
WHERE is_demand_based_tariff = 'Yes'
  AND circle LIKE 'Bangalore%'
GROUP BY circle
ORDER BY total_tax_collected DESC;
```

---

### 4.16 — Section-level rollup (with the hierarchy caveat surfaced)
**User:** Break down net collection by section.
```sql
SELECT section,
       SUM(net_collection_final) AS total_net_collection,
       COUNT(DISTINCT division) AS divisions_touched
FROM bescom_dcb_audit
GROUP BY section
ORDER BY total_net_collection DESC;
```
Included `divisions_touched` deliberately — each section code spans roughly 19 divisions in this data, so a section-level rollup is not the same as a "part of one division" rollup; worth surfacing that in the answer.

---

### 4.17 — Balance transfer lookup
**User:** Which circle has the highest total balance transfer amount?
```sql
SELECT circle, SUM(balance_transfer_amount) AS total_balance_transfer
FROM bescom_dcb_audit
GROUP BY circle
ORDER BY total_balance_transfer DESC
LIMIT 1;
```

---

## 5. QUERY TRANSFORMATION TABLE (for your middle layer parser)

| User says | SQL pattern |
|---|---|
| "in zone X" | `zone = 'X'` |
| "in circle X" | `circle = 'X'` (or `LIKE 'X%'` for a family like "Bangalore circles") |
| "in division X" | `UPPER(division) = UPPER('X')` — casing is inconsistent |
| "in subdivision X" | `subdivision = 'X'` |
| "in section X" | `section = 'X'` — remember: NOT scoped to one division |
| "LT tariff / low tension" | `tariff_category LIKE 'LT-%'` |
| "HT tariff / high tension" | `tariff_category LIKE 'HT-%'` |
| "demand-based tariff accounts" | `is_demand_based_tariff = 'Yes'` |
| "time of day / ToD tariff" | `is_time_of_day_tariff = 'Yes'` |
| "HT connections / 11kV" | `voltage_class_kv = 11.0` |
| "collection efficiency" | `pct_collection_efficiency_with_adj` (default to "with adjustment" unless user says "without") |
| "net collection / total collected" | `net_collection_final` |
| "net demand / total demand" | `net_demand_revenue_final` |
| "write off / written off" | `write_off` |
| "closing balance" | `closing_revenue` (+ `closing_tax`, `closing_pg_surcharge` etc. as needed) |
| "GST / TCS" | prefix `gst_tcs_*` columns |
| "active installations" (no further context) | `installations_active` — but flag the 3 unlabeled duplicate groups exist |
| "consumption per installation" | `consumption_per_installation` |
| "arrears ratio" | `ratio_arrears_to_demand` |
| "average cost of supply" | `avg_cost_of_supply` |
| "top N by X" | `ORDER BY X DESC LIMIT N` |
| "trend / month over month / this year vs last year" | **refuse — no time dimension in this table** |

---

## 6. QUERY RESOLVER — RECOVERY PROMPT

When SQL returns no results or an error, send this:

```
The previous SQL returned no results or an error.

Original question: {USER_QUERY}
Failed SQL: {FAILED_SQL}
Error: {ERROR_MESSAGE}

Fix the query using these rules:
1. Division names are mixed case — switch to: UPPER(division) = UPPER('value')
2. Zone, Circle, Subdivision, Section, Tariff are consistently cased — use
   exact match unless the user asked for a partial/family match
3. Do NOT query columns named '2' or '4' — these are blank/junk headers in
   the source with zero populated values
4. is_demand_based_tariff / is_time_of_day_tariff are TEXT 'Yes'/'No', not
   booleans — don't filter with = TRUE or = 1
5. voltage_class_kv is numeric (0.23, 0.415, 11.0) — don't quote it as text
6. Do not attempt to filter or GROUP BY any date/period — no such column
   exists in this table

Valid column names: see schema_metadata.py — do not invent a column name
that isn't in that list, even if it looks plausible.

Output ONLY the corrected SQL, or a one-line explanation if the question
genuinely can't be answered from this schema.
```

---

## 7. RESPONSE FORMATTER PROMPT

After SQL executes, format results for the chat user:

```
You are a helpful assistant explaining BESCOM DCB audit results in plain language.

User's question: {USER_QUERY}
Result rows (JSON): {QUERY_RESULTS}
Row count: {ROW_COUNT}

Instructions:
- If result is a count, sum, or average: state the number clearly in one
  sentence, with the unit (₹ for money columns, units for consumption).
- If result is a list: show a table with the most relevant columns —
  typically geography (circle/division/subdivision) + the requested metric.
- If result is empty: say "No records found for [query]" and suggest
  checking the spelling of the division name (casing can vary) or trying a
  broader geography.
- Round money figures to the nearest rupee; round percentages to 1 decimal.
- If the answer drew on an "inferred" (not source-confirmed) column, add a
  short note that the figure's DCB stage is inferred from structure, not an
  explicit source label, and should be verified before being quoted externally.
- Never show the raw SQL to the user unless they ask for it.
- Keep language simple — users may not be familiar with DCB terminology;
  spell out P&G Surcharge, IOD, FPPCA, RR Transfer, etc. on first use.
```

---

## 8. INTENT CLASSIFIER PROMPT (route before generating SQL)

```
Classify the user's query into one category:

LOOKUP    — a specific row or narrow filter (e.g. one division, one section)
FILTER    — listing rows matching a condition (zone, circle, tariff, voltage)
COUNT     — asking how many rows/installations meet a condition
AGGREGATE — asking for totals, sums, or averages of a money/ratio column
COMPARE   — comparing two or more zones/circles/divisions/tariffs
TREND     — asking about change over time — this schema has no time
             dimension, so route to a refusal, not a query
ANOMALY   — asking about data quality, missing data, or inconsistent values
UNCLEAR   — cannot determine intent

User query: "{USER_QUERY}"

Output:
CATEGORY: [one of the above]
KEY_ENTITIES: [zone/circle/division/tariff/voltage mentioned, and which
               metric column(s) are implied]
REASON: [one line]

Example:
CATEGORY: AGGREGATE
KEY_ENTITIES: circle=Kolar Circle, metric=gst_tcs_closing_balance
REASON: User wants a summed total for a specific circle and metric.
```

---

## 9. CHAIN-OF-THOUGHT PROMPT (for complex queries)

```
A user asked: "{USER_QUERY}"

Think step by step before writing SQL:

Step 1 — What does the user want? (a list / a count / a total / a ratio / a comparison?)
Step 2 — Which columns are needed in SELECT? Check schema_metadata.py — is
          there a "_final" or explicitly-formula'd column that answers this
          more reliably than an "inferred" one?
Step 3 — What filters go in WHERE? (zone / circle / division — remember
          UPPER() for division / tariff family / voltage class / Yes-No flags?)
Step 4 — Is GROUP BY needed? (only if aggregating across a geography or
          tariff category rather than returning one row)
Step 5 — Does this question implicitly assume a time dimension, or assume
          the geo hierarchy nests cleanly (division inside one circle,
          section inside one division)? Neither is true in this table —
          flag it instead of writing SQL that assumes it.
Step 6 — What is the right ORDER BY and LIMIT?

Data reminders:
- Division casing is mixed — always use UPPER()
- The geo hierarchy does not nest cleanly — filter each level independently
- No date/period column exists — never answer trend questions with SQL
- Prefer "_final" / explicitly-formula'd columns over inferred-stage ones
- Three unlabeled installation column groups exist — ask before picking one

THINKING: [write steps 1-6]
SQL: [final SQL query only, or a plain-text refusal/clarification]
```

---

## 10. KNOWN DATA ISSUES TO WARN USERS ABOUT

Build these warnings into your response layer:

| Situation | Warning to show user |
|---|---|
| User asks about "Tumakuru" division and result looks incomplete | "Division names have inconsistent casing in this data ('TUMAKURU' vs 'Tumakuru') — I've matched both, but worth confirming with the source system that they're really the same division." |
| User asks for a trend, month-over-month, or year-over-year figure | "This table has no date or period column, so I can't show a trend. Do you know if the source system tracks this by month, or is this extract a single period?" |
| User asks "how many active installations" with no other qualifier | "There are multiple unlabeled installation columns in this data beyond the confirmed one — let me know if you specifically meant a different group, since I can't tell which one the source intended." |
| User asks about a division assuming it belongs to one circle | "Heads up — in this dataset, division names aren't uniquely scoped to one circle (e.g. the same division name shows up under several circles), so filtering by division alone may span more territory than expected." |
| User asks about `pct_recovery_of_avg_cost` | "This percentage has no stated formula in the source data and its values range unusually widely (1.1%–214.6%) — I'd verify this figure against BESCOM's proforma before using it in a report." |
| User asks about an "inferred"-stage demand/collection column | "This figure's DCB stage (e.g. Current Demand vs Net Demand) is inferred from the column's position in the source file, not an explicit label — worth confirming against BESCOM's original proforma before this number is quoted externally." |
| User asks about billed installations, % billed, or coverage | "In about 31.6% of rows, billed installations exceeds total installations for that row, which shouldn't be possible — worth flagging to the source system owner before this number goes into an audit finding." |
| User asks about metered installations or metered consumption coverage | "In about 13.75% of rows, metered installations exceeds total installations — the same category of inconsistency as the billed-installations issue above." |
| User asks about wheeled energy units and sees a negative number | "About 1% of rows have negative wheeled energy units. That can legitimately mean net export back to the grid, or it can be a data entry issue — I can't tell which from this extract alone." |

---

*Guide verified against BESCOM.xlsx — 20,000 rows, 132 columns (2 empty/junk), 4 zones, 9 circles.*
*Utility: BESCOM (Bangalore Electricity Supply Company), Karnataka — DCB (Demand-Collection-Balance) audit proforma.*
*Companion files: `schema_metadata.py`, `prompt_assembler.py`, `few_shot_examples.py`.*
