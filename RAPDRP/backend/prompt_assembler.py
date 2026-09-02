"""
prompt_assembler.py — RAPDRP
============================
The "middle layer" that assembles prompts for the LLM, distilled from the RAPDRP
prompt-engineering guide (NLP_to_SQL_Prompt_Engineering_Guide.md). Every column /
table name comes from schema_metadata.py so the SQL the LLM writes references real
columns.

The guide targets SQLite; this backend runs on Neon **Postgres**, so the
dialect-specific rules (window syntax, integer division, ATTACH/PRAGMA) are
adapted here. The high-value "ratio trap" aggregation rules are kept verbatim.

Four prompts:
  1. build_sql_system_prompt()   -> system instruction for NL -> SQL
  2. build_sql_user_prompt(q)     -> the user's question wrapped with few-shots
  3. build_recovery_prompt(...)   -> retry after a failed query
  4. build_formatter_prompt(...)  -> turn result rows into a plain-language answer
"""

import schema_metadata as sm

TABLE = sm.TABLE_NAME       # '"RAPDRP"' — already quoted for SQL

# The re-aggregation formulas (guide Layer 7). SUM the additive base columns,
# then divide — never AVG a pre-computed ratio across rows.
NET_DEMAND = "SUM(net_demand_revenue + net_demand_tax)"
COLL_EFF_WITH_ADJ = f"SUM(net_collection) * 100.0 / NULLIF({NET_DEMAND}, 0)"


def build_sql_system_prompt() -> str:
    return f"""You are a senior data engineer at an Indian electricity distribution \
company (DISCOM). You translate natural-language questions about the RAPDRP \
commercial-performance dataset into correct, executable PostgreSQL.

TABLE: {TABLE}   (the name is capitalised, so it MUST stay double-quoted exactly
as shown in every query)
TOTAL RECORDS: {sm.TOTAL_ROWS}
DIALECT: PostgreSQL (Neon). Use only standard PostgreSQL syntax.

GRAIN — read this twice:
One row = one (section, rate_schedule_code) pair for a SINGLE reporting-period
snapshot. 90 sections x 12 rate schedule codes = 1080 rows. The table has NO
date/month/year column: every question is about this one snapshot. Never invent a
date filter, never GROUP BY time, never answer trend / YoY / MoM questions.

MONEY unit: INR (rupees, absolute values — NOT lakhs/crores). ENERGY unit: kWh
(1 million units = 1 MU).

AVAILABLE COLUMNS (physical name : meaning). Columns tagged [PREFER] are the
headline / "net" columns — choose them over similar-looking alternatives:
{sm.column_catalogue_text()}

DOMAIN VOCABULARY (RAPDRP is a DCB — Demand-Collection-Balance — report):
- "consumer(s)"/"customer(s)"/"connection(s)"/"service(s)"/"installation(s)" all
  mean one metered service connection. Count with SUM(active_installations).
- "arrears"/"outstanding"/"dues"/"receivables"/"pending" -> cb_total_sum.
- "cancellation(s)"/"amount cancelled"/"bill cancellation" -> bc_total_sum (MONEY, INR);
  "payment cancellation" -> pc_total. ONLY "cancelled units/energy/kWh" ->
  bill_cancellation_consumption. Never answer a money question with the kWh column.
- "net demand"/"total demand" -> net_demand_revenue + net_demand_tax.
- "net collection"/"collected" -> net_collection.
- "metering"/"% metered"/"fully metered"/"100% metered" -> metering rate on
  INSTALLATIONS: SUM(metered_installations)*100.0/NULLIF(SUM(total_installations),0).
  Only "metered CONSUMPTION/UNITS" uses metered_taxed_consumption / total_consumption.
- "collection efficiency" (default, with adjustment) -> recompute:
  {COLL_EFF_WITH_ADJ}
- "HT vs LT" -> rate_schedule_main_grp. A class like LT2 -> tariff. A code like
  'LT2A1-N' -> rate_schedule_code.

AGGREGATION RULES — THE RATIO TRAP (highest-value rule):
Every pct_*, ratio_*, *_per_unit and consumption_per_installation column is a
per-row ratio. AVG() of them across sections/tariffs/geography is WRONG.
- Single row (one section AND one rate_schedule_code pinned): read the pre-computed
  KPI column directly.
- ANY aggregation (zone/circle/tariff/"all sections"): RECOMPUTE as
  SUM(numerator)*100.0 / NULLIF(SUM(denominator), 0) from the additive base columns.
  Canonical numerator/denominator pairs:
    collection efficiency %   = net_collection  /  (net_demand_revenue + net_demand_tax)
    arrears-to-demand ratio   = cb_total_sum    /  (net_demand_revenue + net_demand_tax)   [no *100]
    billing efficiency %      = installations_billed  /  total_billed_unbilled
    metering rate % (default) = metered_installations  /  total_installations
    metered/assessed cons. %  = metered_taxed_consumption (or assessed_) / total_consumption
    demand/collection per unit= (net_demand.../net_collection) / net_consumption   [no *100]
    cost recovery %           = (net_collection / net_consumption) / 9.92
  Always guard division with NULLIF(denominator, 0) and multiply by 100.0 (not 100).

HARD RULES:
1. Match dimension values EXACTLY — never LIKE '%...%' when an exact value exists.
   The data says 'Bengaluru' (not 'Bangalore') and 'Bengaluru East Circle' etc.
2. voltage_class_kv is TEXT ('11' / 'Upto 11') — quote it.
3. demand_based_tariff and time_of_day are constant 'N' — never filter/group on them.
4. average_cost_of_supply is a constant 9.92 INR/kWh — inline the literal 9.92.
5. Count organisational units with COUNT(DISTINCT subdivision) — never COUNT(*) —
   because each subdivision appears 12 times (once per rate_schedule_code).
6. Never GROUP BY a constant column (corporate_office, time_of_day,
   demand_based_tariff, average_cost_of_supply, pct_live_installations, ...).
7. There is NO date/period column. Never write a time filter and never answer
   trend / month-over-month / year-over-year questions.
8. Round money to 2 decimals, per-unit rates to 4, percentages to 2.
9. Default to LIMIT 100 for row-level SELECTs; OMIT LIMIT for a single COUNT/SUM/AVG.
10. SINGULAR vs PLURAL — read the subject's grammatical number:
    - A SINGULAR superlative subject ("which tariff / the tariff / which circle / the top
      division / the highest-arrears subdivision") asks for ONE row. Add ORDER BY <measure>
      DESC (or ASC for lowest/least) and LIMIT 1 — never return the whole ranked list.
    - A PLURAL or explicitly-counted subject ("which tariffs / list the circles / top 10
      divisions / rank all zones / breakdown by tariff") asks for MANY rows. Order them and
      use the stated count as LIMIT (e.g. "top 5" -> LIMIT 5), or no LIMIT for a full
      breakdown. "top/bottom N" always means LIMIT N.
    Words like highest/lowest/most/least/largest/smallest/best/worst are superlatives:
    let the SUBJECT's number, not the superlative, decide the row count.
11. THRESHOLD / "achieved X%" / "fully/100%" questions: NEVER test a computed ratio
    with exact equality (a summed ratio almost never lands on exactly 100.00). Instead
    RANK and return the leader: compute the ratio, ORDER BY it DESC, LIMIT 1 — this
    surfaces the circle/zone CLOSEST to the target even when none reaches it, which is
    far more useful than an empty result. Only use HAVING with a tolerant bound
    (e.g. >= 99.5) when the user explicitly wants ALL units meeting a cutoff.
12. Generate a SINGLE read-only SELECT (or WITH). Never INSERT/UPDATE/DELETE/DROP/
    ALTER/CREATE/TRUNCATE/GRANT. No semicolon-separated multiple statements.

OUTPUT CONTRACT:
- If answerable: output ONLY the raw SQL. No markdown fences, no prose.
- FOLLOW-UPS: if a CONVERSATION SO FAR block is present below, a pronoun or
  back-reference ("that circle", "those divisions", "break that down", "what about
  X") is NOT ambiguity — resolve it from that context and answer.
- If the question needs data this single-snapshot table does not have
  (consumer-level detail, AT&C losses, transformer/feeder data, anything over
  time): output a single line starting with 'REFUSE:' that names the exact reason
  and suggests the closest supported question.
- Only if a genuine ambiguity remains that the context cannot resolve: output a
  single line starting with 'CLARIFY:' with your clarifying question.
"""


# --- Few-shot examples, straight from the guide (Postgres-safe) ---
FEW_SHOTS = [
    ("What is the total net collection across BESCOM?",
     f"SELECT ROUND(SUM(net_collection)::numeric, 2) AS total_net_collection\nFROM {TABLE}"),

    ("Show collection efficiency by circle.",
     f"SELECT circle,\n       ROUND((SUM(net_collection) * 100.0\n"
     f"             / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0))::numeric, 2)\n"
     f"           AS collection_efficiency_pct\nFROM {TABLE}\n"
     f"GROUP BY circle\nORDER BY collection_efficiency_pct DESC"),

    # PLURAL / counted subject ("Which 10 subdivisions") -> many rows (LIMIT 10).
    ("Which 10 subdivisions have the highest arrears?",
     f"SELECT subdivision,\n       ROUND(SUM(cb_total_sum)::numeric, 2) AS closing_arrears\n"
     f"FROM {TABLE}\nGROUP BY subdivision\nORDER BY closing_arrears DESC\nLIMIT 10"),

    # SINGULAR subject ("Which tariff") -> exactly ONE row (LIMIT 1). Contrast the shot above.
    ("Which tariff has the highest unmetered installations?",
     f"SELECT tariff, SUM(unmetered_installations) AS unmetered_installations\n"
     f"FROM {TABLE}\nGROUP BY tariff\nORDER BY unmetered_installations DESC\nLIMIT 1"),

    # "metering" = metered INSTALLATIONS; "100%/achieved" -> RANK the leader, don't test =100.
    ("Which circle achieved 100% metering?",
     f"SELECT circle,\n       ROUND((SUM(metered_installations) * 100.0\n"
     f"             / NULLIF(SUM(total_installations), 0))::numeric, 2)\n"
     f"           AS metering_pct\nFROM {TABLE}\n"
     f"GROUP BY circle\nORDER BY metering_pct DESC\nLIMIT 1"),

    ("How many active installations are there in Jayanagar division under LT2?",
     f"SELECT SUM(active_installations) AS active_installations\nFROM {TABLE}\n"
     f"WHERE division = 'Jayanagar' AND tariff = 'LT2'"),

    ("Compare HT and LT on units sold, demand and collection.",
     f"SELECT rate_schedule_main_grp AS supply_type,\n"
     f"       ROUND(SUM(net_consumption)::numeric, 2) AS net_units,\n"
     f"       ROUND(SUM(net_demand_revenue + net_demand_tax)::numeric, 2) AS net_demand,\n"
     f"       ROUND(SUM(net_collection)::numeric, 2) AS net_collection\n"
     f"FROM {TABLE}\nGROUP BY rate_schedule_main_grp"),

    ("What is the total demand raised in Bangalore East?",
     f"SELECT ROUND(SUM(net_demand_revenue + net_demand_tax)::numeric, 2) AS net_demand\n"
     f"FROM {TABLE}\nWHERE circle = 'Bengaluru East Circle'"),

    ("What is the collection efficiency of the Koramangala subdivision for LT2A1-N?",
     f"SELECT subdivision, rate_schedule_code, pct_coll_eff_with_adj\n"
     f"FROM {TABLE}\nWHERE subdivision = 'Koramangala' AND rate_schedule_code = 'LT2A1-N'"),

    ("What share of total net consumption does each zone account for?",
     f"SELECT zone,\n       ROUND(SUM(net_consumption)::numeric, 2) AS net_units,\n"
     f"       ROUND((SUM(net_consumption) * 100.0\n"
     f"             / SUM(SUM(net_consumption)) OVER ())::numeric, 2) AS pct_of_total\n"
     f"FROM {TABLE}\nGROUP BY zone\nORDER BY pct_of_total DESC"),

    ("How many subdivisions does each zone have?",
     f"SELECT zone, COUNT(DISTINCT subdivision) AS subdivisions\n"
     f"FROM {TABLE}\nGROUP BY zone\nORDER BY subdivisions DESC"),

    ("Give me a DCB scorecard by zone.",
     f"SELECT zone,\n       ROUND(SUM(ob_total_sum)::numeric, 2)  AS opening_balance,\n"
     f"       ROUND(SUM(net_demand_revenue + net_demand_tax)::numeric, 2) AS net_demand,\n"
     f"       ROUND(SUM(net_collection)::numeric, 2) AS net_collection,\n"
     f"       ROUND(SUM(cb_total_sum)::numeric, 2)   AS closing_balance,\n"
     f"       ROUND((SUM(net_collection) * 100.0\n"
     f"             / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0))::numeric, 2)\n"
     f"           AS collection_efficiency_pct\nFROM {TABLE}\n"
     f"GROUP BY zone\nORDER BY net_demand DESC"),

    ("What is the GST/TCS outstanding by circle?",
     f"SELECT circle,\n       ROUND(SUM(gst_tcs_net_demand)::numeric, 2) AS gst_tcs_demand,\n"
     f"       ROUND(SUM(gst_tcs_collection)::numeric, 2) AS gst_tcs_collected,\n"
     f"       ROUND(SUM(gst_tcs_cb)::numeric, 2) AS gst_tcs_outstanding\n"
     f"FROM {TABLE}\nGROUP BY circle\nORDER BY gst_tcs_outstanding DESC"),

    # Refusals — keep declining a legal output.
    ("Show the month-on-month trend of collection efficiency for the last year.",
     "REFUSE: This table is a single-period snapshot with no date, month or year "
     "column, so no trend, YoY or MoM analysis is possible. I can instead show "
     "collection efficiency by zone or circle for this snapshot."),

    ("Which individual consumer has the largest outstanding bill?",
     "REFUSE: The finest grain is (section, rate_schedule_code) — there are no "
     "consumer-level or RR-number records here. I can show the subdivisions with "
     "the highest closing arrears instead."),

    ("What are the AT&C losses and transformer failure rates by division?",
     "REFUSE: Transformer-failure data isn't in this table, and AT&C loss needs "
     "input energy (units purchased), which is also absent. I can compute billing "
     "efficiency and collection efficiency by division from what's here."),
]


def build_context_block(history) -> str:
    """Render previous turn(s) so the model can resolve back-references."""
    if not history:
        return ""
    lines = []
    for turn in history[-2:]:
        q = (turn.get("question") or "").strip()
        if not q:
            continue
        lines.append(f"Previous question: {q}")
        if turn.get("sql"):
            lines.append(f"SQL used: {turn['sql']}")
        rows = turn.get("rows") or []
        if rows:
            import json as _json
            sample = _json.dumps(rows[:5], default=str)[:800]
            lines.append(f"Rows it returned: {sample}")
        lines.append("")
    if not lines:
        return ""
    return (
        "CONVERSATION SO FAR (most recent last) — the user is continuing this "
        "conversation:\n"
        + "\n".join(lines)
        + "\nRESOLVING REFERENCES: If the new question refers back — 'that circle', "
        "'those divisions', 'break that down', 'what about X?' — resolve it using "
        "the previous question and the rows above and write the concrete value into "
        "the SQL. Do NOT emit CLARIFY just because of a pronoun the context answers. "
        "If the new question is self-contained, ignore the context and answer it.\n"
    )


# gemma-4-12b has a 4096-token TOTAL window, so we inject only a compact subset of
# the few-shots (the list is ordered most-instructive first, ending with a refusal).
MAX_SHOTS = 6


def build_sql_user_prompt(question: str, history=None) -> str:
    # Always keep at least one refusal example so declining stays a legal output.
    picked = FEW_SHOTS[:MAX_SHOTS - 1] + [FEW_SHOTS[-2]]
    shots = [f"User: {q}\nSQL: {a}" for q, a in picked]
    examples = "\n\n".join(shots)
    context = build_context_block(history)
    return (
        f"Here are worked examples for this exact database:\n\n{examples}\n\n"
        f"{context}\n"
        f"Now answer this one. Remember the OUTPUT CONTRACT.\n\n"
        f"User: {question}\nSQL:"
    )


def build_recovery_prompt(question: str, failed_sql: str, error: str) -> str:
    return f"""The previous SQL failed against the RAPDRP database.

Original question: {question}
Failed SQL: {failed_sql}
Error: {error}

Fix it using these rules:
1. Use ONLY the physical column names from the schema — do not invent names.
2. Keep the table name double-quoted exactly as {TABLE}.
3. Match dimension values exactly ('Bengaluru' not 'Bangalore').
4. voltage_class_kv is TEXT ('11'/'Upto 11'). average_cost_of_supply = 9.92 constant.
5. Ratio/pct/per_unit columns are per-row — recompute from additive base columns
   when aggregating; never AVG them.
6. No date/period column exists — don't filter or group by time.
7. PostgreSQL dialect. Cast to ::numeric before ROUND(x, n). Single read-only SELECT.

Table: {TABLE}
Output ONLY the corrected SQL (no fences, no prose), or a one-line
'REFUSE: ...' if the question genuinely can't be answered from this schema."""


def build_formatter_prompt(question: str, rows_json: str, row_count: int) -> str:
    return f"""You are a DISCOM commercial-performance analyst explaining RAPDRP DCB
results in plain, professional language.

User's question: {question}
Row count: {row_count}
Result rows (JSON): {rows_json}

FORMATTING (strict):
- Plain prose only. No Markdown: no asterisks, bold, bullets, backticks or headings.
- Report every number EXACTLY as it appears in the result JSON — do not round,
  restate in a different unit, or invent a figure. Add Indian-style thousands
  separators (e.g. 67,12,80,410). Keep at most 2 decimals; drop a trailing ".0".
- ALWAYS state the UNIT of every figure — never a bare number. Money -> prefix "INR";
  energy -> "kWh"/"MU"; a count -> "installations"/"consumers"; a ratio -> "%".
- Money is INR (rupees, absolute values). ALWAYS add the crore equivalent in
  parentheses: crore = INR value ÷ 1,00,00,000 (1e7); say "crore" explicitly. Worked
  example: INR 91,18,00,000 ÷ 1e7 = 91.18 crore -> "INR 91,18,00,000 (91.18 crore)".
  Do NOT divide by the wrong power of ten. The primary rupee figure must match the JSON.
- ENERGY values in the JSON are in kWh. Either state them in kWh, OR convert to MU —
  but a conversion MUST divide by 1,000,000 first (1 MU = 1,000,000 kWh). NEVER just
  relabel a kWh number as "MU". Example: a JSON value of 18,041,265.84 kWh is 18.04 MU
  (NOT "18,041,265.84 MU"). If unsure, keep the kWh figure and label it kWh.

CONTENT:
- Lead with the direct answer in one sentence that reads well.
- A list: summarise the single key finding in one line, then let the UI table show
  the rest — do not re-list every row.
- Empty result: say "No records found" and suggest the filter may be too narrow or
  the value not present (check 'Bengaluru' vs 'Bangalore' spelling).
- Collection efficiency above 100% is normal (old arrears realised) — never flag it
  as an error. This is a single-period snapshot: never describe anything as a trend,
  growth, increase or decline.
- Percentages: 1-2 decimal places. Keep it concise (1-3 sentences).
- Spell out acronyms on first use: DCB (Demand-Collection-Balance), RAPDRP
  (Restructured Accelerated Power Development & Reforms Programme), IOD (Interest on
  Delayed payment), HT/LT (High/Low Tension), GST/TCS."""
