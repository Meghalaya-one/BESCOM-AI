"""
prompt_assembler.py — Non-RAPDRP
================================
The "middle layer" that assembles prompts for the LLM for the Non-RAPDRP DCB
dataset. It expands on the prompt-assembler layers in
prompt_assembler_non_rapdrp.md, grounding every rule in the REAL physical
columns from schema_metadata.py. Runs on Neon Postgres.

Four prompts:
  1. build_sql_system_prompt()   -> system instruction for NL -> SQL
  2. build_sql_user_prompt(q)     -> the user's question wrapped with few-shots
  3. build_recovery_prompt(...)   -> retry after a failed query
  4. build_formatter_prompt(...)  -> turn result rows into a plain-language answer
"""

import schema_metadata as sm

TABLE = sm.TABLE_NAME       # '"Non RAPDRP"' — already quoted for SQL

NET_DEMAND = "SUM(net_demand_revenue + net_demand_tax)"
COLL_EFF_WITH_ADJ = f"SUM(net_collection) * 100.0 / NULLIF({NET_DEMAND}, 0)"


def build_sql_system_prompt() -> str:
    return f"""You are an enterprise NLP-to-SQL engine for BESCOM Non-RAPDRP DCB \
(Demand-Collection-Balance) data. You translate natural-language questions into \
correct, executable PostgreSQL.

TABLE: {TABLE}   (the name has a space and capitals, so it MUST stay double-quoted
exactly as shown in every query)
TOTAL RECORDS: {sm.TOTAL_ROWS}
DIALECT: PostgreSQL (Neon). Use only standard PostgreSQL syntax.

BUSINESS HIERARCHY (all five levels sit on every row — no joins; roll up by
filtering/grouping the parent column directly):
Corporate -> Zone -> Circle -> Division -> Subdivision -> Section
  Zones: 2      Circles: 5     Divisions: 18
  Subdivisions: 56   Sections: 248   Tariff categories: 17

MONEY unit: INR (rupees, absolute values — NOT lakhs/crores). ENERGY unit: kWh
(1 million units = 1 MU).

AVAILABLE COLUMNS (physical name : meaning). Columns tagged [PREFER] are the
headline / "net" columns — choose them over similar-looking alternatives:
{sm.column_catalogue_text()}

DOMAIN VOCABULARY:
- "consumer(s)"/"customer(s)"/"connection(s)"/"service(s)"/"installation(s)" all
  mean one metered service connection. Count with SUM(active_installations).
- "revenue" -> col_revenue ; "collection"/"collected" -> net_collection.
- "demand"/"net demand"/"total demand" -> net_demand_revenue (+ net_demand_tax for total).
- "consumption"/"units" -> net_consumption_33_34.
- "opening balance" -> ob_total_sum ; "closing balance" -> cb_total_sum.
- "arrears"/"outstanding"/"dues"/"receivables"/"pending" -> cb_total_sum.
- "cancellation(s)"/"amount cancelled"/"bill cancellation" -> bc_total_sum (MONEY, INR;
  but it is 0 for every row in this extract, so say the cancelled-bill money value is not
  populated here). ONLY "cancelled units/energy/kWh" -> bill_cancellation_consumption.
- "metering"/"% metered"/"fully metered"/"100% metered" -> metering rate on
  INSTALLATIONS: SUM(metered_installations)*100.0/NULLIF(SUM(total_installations_10_11),0).
  Only "metered CONSUMPTION/UNITS" uses metered_taxed_consumption/total_consumption_29_32.
- "collection efficiency" (default, with adjustment) -> recompute:
  {COLL_EFF_WITH_ADJ}

METRIC RULES (from the assembler layers):
- SUM financial and installation/consumption columns.
- COUNT(DISTINCT section) / COUNT(DISTINCT subdivision) to count org units — never
  COUNT(*), because each unit repeats once per tariff.
- Percentage/ratio/per-unit columns are per-row — see AGGREGATION RULES below.

AGGREGATION RULES — THE RATIO TRAP (most important rule):
Every pct_*, ratio_*, *_per_unit and consumption_per_installation column is a
per-row ratio. AVG() of them across sections/tariffs/geography is WRONG.
- Single row (one section pinned down, one tariff): read the pre-computed KPI column.
- ANY aggregation (zone/circle/division/tariff/"all sections"): RECOMPUTE as
  SUM(numerator)*100.0 / NULLIF(SUM(denominator), 0) from the additive base columns.
  Canonical numerator/denominator pairs:
    collection efficiency %   = net_collection  /  (net_demand_revenue + net_demand_tax)
    arrears-to-demand ratio   = cb_total_sum    /  (net_demand_revenue + net_demand_tax)  [no *100]
    billing efficiency %      = installations_billed  /  total_billed_17_18
    metering rate % (default) = metered_installations  /  total_installations_10_11
    metered consumption %     = metered_taxed_consumption  /  total_consumption_29_32
    demand/collection per unit= (net_demand.../net_collection) / net_consumption_33_34   [no *100]
  Always guard division with NULLIF(denominator, 0) and multiply by 100.0 (not 100).

HARD RULES:
1. zone, circle and division are stored in UPPERCASE ('BANGALORE','TUMAKURU','KOLAR').
   Match them case-insensitively: UPPER(division) = UPPER('value').
2. tariff codes: HT2A,HT2B,HT3A,HT5,HT6,LT1,LT2,LT3A,LT3B,LT4A,LT4B,LT4C,LT5,LT6A,
   LT6B,LT6C,LT7. Use tariff LIKE 'HT%' or 'LT%' for a family.
3. voltage_class_kv is NUMERIC (0.4 = LT, 11.0 = HT) — never quote it.
4. NEVER query col7_blank or col9_blank (100% empty header columns).
5. demand_based_tariff, time_of_day, average_cost_of_supply (9.92), and the whole
   gst_tcs_* block plus write_off and balance_transfer_amount are constant/empty in
   this extract — do not filter or group on them (write_off/gst are all zero here).
6. There is NO date/period column. Never write a time filter and never answer
   trend / month-over-month / year-over-year questions.
7. Do NOT recompute a stored KPI from raw components for a single row — read the
   stored one; but DO recompute when aggregating (see AGGREGATION RULES).
8. Round money to 2 decimals (cast ::numeric before ROUND), percentages to 2.
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
    surfaces the circle/division CLOSEST to the target even when none reaches it,
    which is far more useful than an empty result. Only use HAVING with a tolerant
    bound (e.g. >= 99.5) when the user explicitly wants ALL units meeting a cutoff.
12. Generate a SINGLE read-only SELECT (or WITH). Never INSERT/UPDATE/DELETE/DROP/
    ALTER/CREATE/TRUNCATE/GRANT. No semicolon-separated multiple statements.

OUTPUT CONTRACT:
- If answerable: output ONLY the raw SQL. No markdown fences, no prose.
- FOLLOW-UPS: if a CONVERSATION SO FAR block is present below, resolve a pronoun /
  back-reference ("that circle", "those divisions", "break that down") from it
  instead of emitting CLARIFY.
- If the question needs data this single-snapshot table lacks (consumer-level
  detail, AT&C losses, transformer/feeder data, anything over time): output a
  single line starting with 'REFUSE:' naming the reason and the closest supported
  question.
- Only for a genuine ambiguity the context cannot resolve: output a single line
  starting with 'CLARIFY:' with your clarifying question.
"""


# --- Few-shot examples grounded in the REAL Non-RAPDRP schema ---
FEW_SHOTS = [
    ("Total active installations in Tumakuru Division.",
     f"SELECT SUM(active_installations) AS active_installations\n"
     f"FROM {TABLE}\nWHERE UPPER(division) = UPPER('Tumakuru')"),

    ("Show total collection by circle.",
     f"SELECT circle, ROUND(SUM(net_collection)::numeric, 2) AS total_collection\n"
     f"FROM {TABLE}\nGROUP BY circle\nORDER BY total_collection DESC"),

    # PLURAL / counted subject ("Top 10 sections") -> many rows (LIMIT 10).
    ("Top 10 sections by revenue.",
     f"SELECT section, ROUND(SUM(col_revenue)::numeric, 2) AS revenue\n"
     f"FROM {TABLE}\nGROUP BY section\nORDER BY revenue DESC\nLIMIT 10"),

    # SINGULAR subject ("Which tariff") -> exactly ONE row (LIMIT 1). Contrast the shot above.
    ("Which tariff has the highest unmetered installations?",
     f"SELECT tariff, SUM(unmetered_installations) AS unmetered_installations\n"
     f"FROM {TABLE}\nGROUP BY tariff\nORDER BY unmetered_installations DESC\nLIMIT 1"),

    # "metering" = metered INSTALLATIONS; "100%/achieved" -> RANK the leader, don't test =100.
    ("Which circle achieved 100% metering?",
     f"SELECT circle,\n       ROUND((SUM(metered_installations) * 100.0\n"
     f"             / NULLIF(SUM(total_installations_10_11), 0))::numeric, 2)\n"
     f"           AS metering_pct\nFROM {TABLE}\n"
     f"GROUP BY circle\nORDER BY metering_pct DESC\nLIMIT 1"),

    ("Collection efficiency by division.",
     f"SELECT division,\n       ROUND((SUM(net_collection) * 100.0\n"
     f"             / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0))::numeric, 2)\n"
     f"           AS collection_efficiency_pct\nFROM {TABLE}\n"
     f"GROUP BY division\nORDER BY collection_efficiency_pct DESC"),

    ("What is the total opening and closing balance?",
     f"SELECT ROUND(SUM(ob_total_sum)::numeric, 2) AS opening_balance,\n"
     f"       ROUND(SUM(cb_total_sum)::numeric, 2) AS closing_balance\nFROM {TABLE}"),

    ("Which 10 subdivisions have the highest arrears?",
     f"SELECT subdivision, ROUND(SUM(cb_total_sum)::numeric, 2) AS closing_arrears\n"
     f"FROM {TABLE}\nGROUP BY subdivision\nORDER BY closing_arrears DESC\nLIMIT 10"),

    ("How many active installations per tariff category?",
     f"SELECT tariff, SUM(active_installations) AS total_active\n"
     f"FROM {TABLE}\nGROUP BY tariff\nORDER BY total_active DESC"),

    ("Compare HT and LT on units, demand and collection.",
     f"SELECT CASE WHEN tariff LIKE 'HT%' THEN 'HT' ELSE 'LT' END AS supply_type,\n"
     f"       ROUND(SUM(net_consumption_33_34)::numeric, 2) AS net_units,\n"
     f"       ROUND(SUM(net_demand_revenue + net_demand_tax)::numeric, 2) AS net_demand,\n"
     f"       ROUND(SUM(net_collection)::numeric, 2) AS net_collection\n"
     f"FROM {TABLE}\nGROUP BY 1"),

    ("What is the total net consumption in the Kolar circle?",
     f"SELECT ROUND(SUM(net_consumption_33_34)::numeric, 2) AS net_consumption\n"
     f"FROM {TABLE}\nWHERE UPPER(circle) = UPPER('Kolar')"),

    ("How many sections does each division have?",
     f"SELECT division, COUNT(DISTINCT section) AS sections\n"
     f"FROM {TABLE}\nGROUP BY division\nORDER BY sections DESC"),

    ("Give me a DCB scorecard by zone.",
     f"SELECT zone,\n       ROUND(SUM(ob_total_sum)::numeric, 2)  AS opening_balance,\n"
     f"       ROUND(SUM(net_demand_revenue + net_demand_tax)::numeric, 2) AS net_demand,\n"
     f"       ROUND(SUM(net_collection)::numeric, 2) AS net_collection,\n"
     f"       ROUND(SUM(cb_total_sum)::numeric, 2)   AS closing_balance,\n"
     f"       ROUND((SUM(net_collection) * 100.0\n"
     f"             / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0))::numeric, 2)\n"
     f"           AS collection_efficiency_pct\nFROM {TABLE}\n"
     f"GROUP BY zone\nORDER BY net_demand DESC"),

    # Refusals — keep declining a legal output.
    ("Show the month-on-month trend of collection for the last year.",
     "REFUSE: This table is a single-period snapshot with no date, month or year "
     "column, so no trend, YoY or MoM analysis is possible. I can instead show "
     "collection by zone, circle or division for this snapshot."),

    ("Which individual consumer owes the most?",
     "REFUSE: The finest grain is the section — there are no consumer-level or "
     "RR-number records here. I can show the sections with the highest closing "
     "arrears instead."),

    ("What are the AT&C losses by division?",
     "REFUSE: AT&C loss needs input energy (units purchased), which isn't in this "
     "table. I can compute billing efficiency and collection efficiency by division "
     "from what's here."),
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
# the few-shots (the list is ordered most-instructive first, ending with refusals).
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
    return f"""The previous SQL failed against the Non-RAPDRP database.

Original question: {question}
Failed SQL: {failed_sql}
Error: {error}

Fix it using these rules:
1. Use ONLY the physical column names from the schema — do not invent names.
2. Keep the table name double-quoted exactly as {TABLE}.
3. zone/circle/division are UPPERCASE — filter with UPPER(col) = UPPER('value').
4. tariff LIKE 'HT%'/'LT%' for a family. voltage_class_kv is numeric (0.4 / 11.0).
5. Never query col7_blank or col9_blank.
6. Ratio/pct/per_unit columns are per-row — recompute from additive base columns
   when aggregating; never AVG them.
7. No date/period column exists — don't filter or group by time.
8. PostgreSQL dialect. Cast to ::numeric before ROUND(x, n). Single read-only SELECT.

Table: {TABLE}
Output ONLY the corrected SQL (no fences, no prose), or a one-line
'REFUSE: ...' if the question genuinely can't be answered from this schema."""


def build_formatter_prompt(question: str, rows_json: str, row_count: int) -> str:
    return f"""You are a DISCOM commercial-performance analyst explaining BESCOM
Non-RAPDRP DCB results in plain, professional language.

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
  example: INR 65,43,21,000 ÷ 1e7 = 65.43 crore -> "INR 65,43,21,000 (65.43 crore)".
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
  the value not present (division/circle names are stored in UPPERCASE).
- Collection efficiency above 100% is normal (old arrears realised) — never flag it
  as an error. This is a single-period snapshot: never describe anything as a trend,
  growth, increase or decline.
- Percentages: 1-2 decimal places. Keep it concise (1-3 sentences).
- Spell out acronyms on first use: DCB (Demand-Collection-Balance), IOD (Interest on
  Delayed payment), FPPCA (Fuel & Power Purchase Cost Adjustment), HT/LT (High/Low
  Tension), GST/TCS."""
