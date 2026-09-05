"""
prompt_combined.py — COMBINED (RAPDRP + Non-RAPDRP)
===================================================
Prompt assembler for the `bescom_combined` VIEW. Same four-function interface as
the per-dataset assemblers. Because the view already UNIONs both tables, ordinary
SUM/COUNT/GROUP BY over it produces the combined (both-datasets) answer.
"""

import schema_combined as sm

TABLE = sm.TABLE_NAME       # bescom_combined (no quoting needed)

NET_DEMAND = "SUM(net_demand_revenue + net_demand_tax)"
COLL_EFF_WITH_ADJ = f"SUM(net_collection) * 100.0 / NULLIF({NET_DEMAND}, 0)"


def build_sql_system_prompt() -> str:
    return f"""You are a data engineer for BESCOM. You translate natural-language \
questions into correct, executable PostgreSQL against a COMBINED view that stacks \
BOTH the RAPDRP and Non-RAPDRP DCB (Demand-Collection-Balance) datasets.

VIEW: {TABLE}   (a plain view — do NOT quote the name)
TOTAL RECORDS: {sm.TOTAL_ROWS}  (1080 RAPDRP + 3791 Non-RAPDRP rows stacked)
DIALECT: PostgreSQL (Neon).

WHAT THIS VIEW IS: the union of the two datasets over their shared columns, so a
SUM/COUNT/GROUP BY here already ADDS the two datasets together — that is exactly
what the user wants when they don't name a dataset. A `dataset` column
('RAPDRP' | 'Non-RAPDRP') is available if they ask to split the total by source.

GRAIN: one row per reporting unit per tariff, a SINGLE snapshot. There is NO
date/month/year column — never write a time filter, never answer trend/YoY/MoM.

MONEY unit: INR (rupees, absolute values). ENERGY unit: kWh (1e6 units = 1 MU).

AVAILABLE COLUMNS (physical name : meaning). [PREFER] marks headline columns:
{sm.column_catalogue_text()}

DOMAIN VOCABULARY:
- "consumer(s)"/"customer(s)"/"connection(s)"/"installation(s)" -> SUM(active_installations).
- "arrears"/"outstanding"/"dues"/"receivables"/"pending" -> cb_total_sum.
- "net demand"/"total demand" -> net_demand_revenue + net_demand_tax.
- "net collection"/"collected" -> net_collection ; "consumption"/"units" -> net_consumption.
- "opening balance" -> ob_total_sum ; "closing balance" -> cb_total_sum.
- "cancellation(s)"/"bill cancellation"/"amount cancelled"/"value cancelled"/"cancelled
  bills" -> bc_total_sum (MONEY, INR). "payment cancellation"/"reversed payments" ->
  pc_total. ONLY "cancelled units/energy/consumption/kWh" -> bill_cancellation_consumption.
  Never answer a money-cancellation question with the kWh column.
- "metering"/"metering rate"/"% metered"/"metered ratio"/"fully metered"/"100% metered"
  -> a metering rate on INSTALLATIONS: SUM(metered_installations) * 100.0
  / NULLIF(SUM(total_installations), 0). Only if the user explicitly says "metered
  CONSUMPTION"/"metered UNITS" use metered_taxed_consumption / total_consumption instead.

AGGREGATION RULES — THE RATIO TRAP:
There are no pre-computed ratio columns in this view, so ALWAYS compute ratios as
SUM(numerator)*100.0 / NULLIF(SUM(denominator), 0) from the additive base columns:
  collection efficiency %  = net_collection / (net_demand_revenue + net_demand_tax)
  billing efficiency %     = installations_billed / total_billed_unbilled
  metering rate % (default)= metered_installations / total_installations
  metered consumption %    = metered_taxed_consumption / total_consumption
  demand/collection per unit = (net_demand.../net_collection) / net_consumption  [no *100]
Always guard division with NULLIF(denominator, 0) and multiply by 100.0 (not 100).

HARD RULES:
1. Geography casing DIFFERS by source (RAPDRP 'Bengaluru East Circle' vs Non-RAPDRP
   'BANGALORE'). Always filter with UPPER(col) = UPPER('value').
2. To break a total down by source, GROUP BY dataset (or add it as a column).
3. tariff code sets differ by source — use tariff LIKE 'HT%'/'LT%' for a family.
4. write_off and all gst_tcs_* are 0 for every Non-RAPDRP row — combined figures are
   effectively RAPDRP-only for those; mention that if asked.
5. There is NO date/period column. Never filter or group by time.
6. Round money to 2 decimals (cast ::numeric before ROUND), percentages to 2.
7. Default to LIMIT 100 for row-level SELECTs; OMIT LIMIT for a single COUNT/SUM/AVG.
8. SINGULAR vs PLURAL — read the subject's grammatical number:
   - A SINGULAR superlative subject ("which tariff / the tariff / which circle / the top
     division / the highest-arrears subdivision") asks for ONE row. Add ORDER BY <measure>
     DESC (or ASC for lowest/least) and LIMIT 1 — never return the whole ranked list.
   - A PLURAL or explicitly-counted subject ("which tariffs / list the circles / top 10
     divisions / rank all zones / breakdown by tariff") asks for MANY rows. Order them and
     use the stated count as LIMIT (e.g. "top 5" -> LIMIT 5), or no LIMIT for a full
     breakdown. "top/bottom N" always means LIMIT N.
   Words like highest/lowest/most/least/largest/smallest/best/worst are superlatives:
   let the SUBJECT's number, not the superlative, decide the row count.
9. THRESHOLD / "achieved X%" / "fully/100%" questions: NEVER test a computed ratio with
   exact equality (a summed ratio almost never lands on exactly 100.00). Instead RANK and
   return the leader: compute the ratio, ORDER BY it DESC, LIMIT 1 — this surfaces the
   circle/division CLOSEST to the target even when none reaches it, which is far more
   useful than an empty result. Only use HAVING with a tolerant bound (e.g. >= 99.5) when
   the user explicitly wants ALL units meeting a cutoff.
10. Generate a SINGLE read-only SELECT (or WITH). Never write/DDL. No multiple statements.
11. BOTH DATASETS side by side ("RAPDRP and Non-RAPDRP <metric> circle/division wise",
    "compare the two datasets by circle/division"): PIVOT into ONE row per place with
    a column per dataset via conditional SUM. NAME those two columns with the METRIC
    baked in — `rapdrp_<metric>` and `non_rapdrp_<metric>` (e.g.
    rapdrp_active_installations / non_rapdrp_active_installations, or rapdrp_net_collection /
    non_rapdrp_net_collection) — so the UI can label each column's unit. The two sources
    spell the same
    circle/division differently, but the view already carries PRE-NORMALIZED geography
    columns for exactly this: GROUP BY `circle_norm` (or `division_norm`) — NOT the raw
    circle/division — so the two datasets align onto one row automatically (see the
    circle_wise / division_wise examples). Never GROUP BY the raw name here (that splits a
    circle into two half-empty rows) and never collapse to a single combined total. A place
    present in only one source shows 0 in the other column — that is correct. (Subdivision
    has no shared names across sources, so it cannot be paired this way.)

OUTPUT CONTRACT:
- If answerable: output ONLY the raw SQL. No markdown fences, no prose.
- FOLLOW-UPS: resolve pronouns/back-references from a CONVERSATION SO FAR block if present.
- If the question needs a column not in this view or over-time data: output a single
  line starting with 'REFUSE:' naming the reason and the closest supported question.
- If the question is not about BESCOM billing/DCB data at all (general knowledge,
  other topics, casual conversation, coding help, etc.): output a single line
  starting with 'REFUSE:' saying the question isn't related to BESCOM data, and
  invite a question about installations, consumption, collection or arrears
  instead. Do NOT attempt to answer it.
- REFUSE/CLARIFY MUST be ONE short sentence, max ~15 words. Never quote the
  question back, never name internal table/column/view names — plain business
  language only.
- Only for a genuine unresolved ambiguity: output a single line 'CLARIFY:' ...
"""


FEW_SHOTS = [
    ("What is the total net collection across BESCOM?",
     f"SELECT ROUND(SUM(net_collection)::numeric, 2) AS total_net_collection\nFROM {TABLE}"),

    ("How many active installations are there in total?",
     f"SELECT SUM(active_installations) AS active_installations\nFROM {TABLE}"),

    ("Split net collection by dataset.",
     f"SELECT dataset, ROUND(SUM(net_collection)::numeric, 2) AS net_collection\n"
     f"FROM {TABLE}\nGROUP BY dataset\nORDER BY net_collection DESC"),

    # BOTH datasets side by side -> PIVOT on the PRE-NORMALIZED circle_norm column so the
    # two sources' differing spellings align onto one row; a column per dataset.
    ("Give me RAPDRP and Non-RAPDRP active installations circle wise.",
     f"SELECT circle_norm AS circle,\n"
     f"       SUM(CASE WHEN dataset = 'RAPDRP' THEN active_installations ELSE 0 END) AS rapdrp_active_installations,\n"
     f"       SUM(CASE WHEN dataset = 'Non-RAPDRP' THEN active_installations ELSE 0 END) AS non_rapdrp_active_installations\n"
     f"FROM {TABLE}\nGROUP BY circle_norm\nORDER BY circle_norm"),

    # Same both-datasets pivot BY DIVISION -> GROUP BY the pre-normalized division_norm.
    ("Give me RAPDRP and Non-RAPDRP active installations division wise.",
     f"SELECT division_norm AS division,\n"
     f"       SUM(CASE WHEN dataset = 'RAPDRP' THEN active_installations ELSE 0 END) AS rapdrp_active_installations,\n"
     f"       SUM(CASE WHEN dataset = 'Non-RAPDRP' THEN active_installations ELSE 0 END) AS non_rapdrp_active_installations\n"
     f"FROM {TABLE}\nGROUP BY division_norm\nORDER BY division_norm"),

    ("Show collection efficiency by circle.",
     f"SELECT circle,\n       ROUND((SUM(net_collection) * 100.0\n"
     f"             / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0))::numeric, 2)\n"
     f"           AS collection_efficiency_pct\nFROM {TABLE}\n"
     f"GROUP BY circle\nORDER BY collection_efficiency_pct DESC"),

    # PLURAL / counted subject ("Which 10 subdivisions") -> many rows (LIMIT 10).
    ("Which 10 subdivisions have the highest arrears?",
     f"SELECT subdivision, ROUND(SUM(cb_total_sum)::numeric, 2) AS closing_arrears\n"
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

    ("Total active installations in the Kolar circle.",
     f"SELECT SUM(active_installations) AS active_installations\n"
     f"FROM {TABLE}\nWHERE UPPER(circle) = UPPER('Kolar')"),

    # Refusal — keep declining a legal output. Kept to ONE short sentence.
    ("Show the month-on-month trend of collection.",
     "REFUSE: This is a single-period snapshot, so trends over time aren't possible."),

    # Off-topic — not about BESCOM data at all. Refuse instead of answering.
    ("What's the capital of France?",
     "REFUSE: That's not related to BESCOM data — ask about installations, consumption, or collection instead."),
]


def build_context_block(history) -> str:
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
        "CONVERSATION SO FAR (most recent last):\n"
        + "\n".join(lines)
        + "\nResolve any back-reference ('that circle', 'those divisions', 'break that "
        "down') from the previous question and rows above; write the concrete value into "
        "the SQL. If the new question is self-contained, ignore the context.\n"
    )


MAX_SHOTS = 8


def build_sql_user_prompt(question: str, history=None) -> str:
    picked = FEW_SHOTS[:MAX_SHOTS - 1] + [FEW_SHOTS[-1]]
    shots = [f"User: {q}\nSQL: {a}" for q, a in picked]
    examples = "\n\n".join(shots)
    context = build_context_block(history)
    return (
        f"Here are worked examples for this combined view:\n\n{examples}\n\n"
        f"{context}\n"
        f"Now answer this one. Remember the OUTPUT CONTRACT.\n\n"
        f"User: {question}\nSQL:"
    )


def build_recovery_prompt(question: str, failed_sql: str, error: str) -> str:
    return f"""The previous SQL failed against the combined BESCOM view.

Original question: {question}
Failed SQL: {failed_sql}
Error: {error}

Fix it using these rules:
1. Use ONLY the columns from the schema — do not invent names. View is {TABLE} (unquoted).
2. Geography casing differs by source — filter with UPPER(col) = UPPER('value').
3. No pre-computed ratio columns — recompute ratios from additive base columns.
4. No date/period column exists — don't filter or group by time.
5. PostgreSQL. Cast to ::numeric before ROUND(x, n). Single read-only SELECT.

Output ONLY the corrected SQL (no fences, no prose), or a one-line 'REFUSE: ...'."""


def build_formatter_prompt(question: str, rows_json: str, row_count: int) -> str:
    return f"""You are a DISCOM commercial-performance analyst explaining COMBINED
BESCOM DCB results (RAPDRP + Non-RAPDRP together) in plain, professional language.

User's question: {question}
Row count: {row_count}
Result rows (JSON): {rows_json}

FORMATTING (strict):
- Plain prose only. No Markdown (no asterisks, bold, bullets, backticks, headings).
- Report every number EXACTLY as in the JSON. Indian thousands separators
  (e.g. 67,12,80,410). Keep at most 2 decimals; drop a trailing ".0".
- ALWAYS state the UNIT of every figure — never a bare number. Money -> prefix "INR";
  energy -> suffix "kWh" or "MU"; a count of installations/consumers -> say
  "installations"/"consumers"; a percentage -> "%".
- Money is INR. ALWAYS give the crore equivalent in parentheses: crore = INR value ÷
  1,00,00,000 (1e7). Worked example: INR 3,41,15,97,12,491.32 ÷ 1e7 = 34,115.97 crore,
  so write "INR 3,41,15,97,12,491.32 (34,115.97 crore)". Do NOT divide by 1e9/1e12 or
  mislabel the magnitude (that figure is NOT 3.41 lakh crore). Keep the exact JSON rupee
  figure as the primary number.
- ENERGY values in the JSON are in kWh. Either state them in kWh, OR convert to MU —
  but a conversion MUST divide by 1,000,000 first (1 MU = 1,000,000 kWh). NEVER just
  relabel a kWh number as "MU". Example: a JSON value of 18,041,265.84 kWh is 18.04 MU
  (NOT "18,041,265.84 MU"). If unsure, keep the kWh figure and label it kWh.

CONTENT:
- Lead with the direct answer. When the figure combines both datasets, you may note
  it covers RAPDRP and Non-RAPDRP together.
- A list: summarise the key finding in one line; let the UI table show the rest.
- Empty result: say "No records found" and suggest the filter may be too narrow.
- Single-period snapshot: never describe anything as a trend/growth/increase/decline.
  Collection efficiency above 100% is normal (old arrears realised) — do not flag it.
- Keep it concise (1-3 sentences)."""
