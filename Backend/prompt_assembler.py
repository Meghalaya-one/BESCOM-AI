"""
prompt_assembler.py
===================
The "middle layer" that assembles prompts for Gemini. It mirrors the guide
(NLP_to_SQL_Prompt_Engineering_Guide_BESCOM.md) but every column/table name
comes from schema_metadata.py so the SQL Gemini writes references real columns.

Four prompts:
  1. build_sql_system_prompt()   -> system instruction for NL -> SQL
  2. build_sql_user_prompt(q)     -> the user's question wrapped with few-shots
  3. build_recovery_prompt(...)   -> retry after a failed query
  4. build_formatter_prompt(...)  -> turn result rows into a plain-language answer
"""

import schema_metadata as sm

TABLE = sm.TABLE_NAME

# --- The trustworthy explicit column names (real, long, physical) ---
NET_COLLECTION = "net_collection_collection_credits_suspense_to_rr_transfer_payme"
NET_DEMAND_REVENUE = "net_demand_revenue_current_demand_debits_bill_cancellations_42_"
COLL_EFF_WITH_ADJ = "of_collection_efficiency_with_adjustment_68_72_45_51_55_100"
ARREARS_RATIO = "ratio_of_arrears_w_r_t_demand_89_45_51_55"
CONSUMPTION_PER_INST = "consumption_per_installation_33_10"
NET_CONSUMPTION = "net_consumption_33_34"
PCT_RECOVERY = "of_recovery_of_average_cost"
GST_TCS_CB = "gst_and_tcs_cb"


def build_sql_system_prompt() -> str:
    return f"""You are a PostgreSQL query generator for the BESCOM DCB \
(Demand-Collection-Balance) electricity utility audit database.

TABLE: {TABLE}
TOTAL RECORDS: {sm.TOTAL_ROWS}
DIALECT: PostgreSQL (Neon). Use only standard PostgreSQL syntax.

You must use the EXACT physical column names below. They are ugly/long because
they come straight from the source spreadsheet — do NOT invent cleaner names,
they will not exist. Wrap any column in double quotes only if it starts with a
digit or contains characters needing quoting; the names below are all safe
unquoted.

AVAILABLE COLUMNS (physical name : meaning). Columns tagged [PREFER] carry an
explicit source formula and should be chosen over similar-looking alternatives:
{sm.column_catalogue_text()}

DOMAIN VOCABULARY (BESCOM is an electricity distribution utility — the unit of
business is the electrical INSTALLATION, i.e. one metered service connection to
a premises. There is NO separate "consumer" / "customer" / "beneficiary" /
"account" / "household" / "connection" entity or column — every such word maps
to installations. Translate the user's everyday word to the installation column):
- "consumer(s)" / "customer(s)" / "beneficiary(ies)" / "account(s)" /
  "household(s)" / "connection(s)" / "service(s)"  -> installations.
  - Count / "how many consumers"                 -> SUM(active_installations)
    (active = live connections; use total_10_11 only if they explicitly say
    "including inactive/disconnected" or "total installations").
  - "active consumers"                            -> SUM(active_installations)
  - "inactive / disconnected consumers"           -> SUM(inactive_installations)
  - "metered / unmetered consumers"               -> metered_installations /
                                                     unmetered_installations
  - "billed / unbilled consumers"                 -> installations_billed /
                                                     installations_unbilled
  - "consumption per consumer/customer"           -> consumption_per_installation_33_10
- Never invent a consumers/customers table or column — it does not exist. Always
  express these questions against the installation columns above.

KEY MAPPINGS (use the physical name on the right):
- "net collection" / "total collected"     -> {NET_COLLECTION}
- "net demand" / "total demand"            -> {NET_DEMAND_REVENUE}
- "collection efficiency" (default)        -> {COLL_EFF_WITH_ADJ}
- "arrears ratio"                          -> {ARREARS_RATIO}
- "consumption" / "net consumption"        -> {NET_CONSUMPTION}
- "consumption per installation"           -> {CONSUMPTION_PER_INST}
- "GST / TCS closing balance"              -> {GST_TCS_CB}
- "active installations" (no qualifier)    -> active_installations (the labeled one)
- "voltage 11kV / HT connection"           -> voltage_class_in_kv = 11.0

HARD RULES:
1. Division casing is inconsistent ('TUMAKURU' vs 'Tumakuru'). ALWAYS filter
   division with UPPER(division) = UPPER('value').
2. zone, circle, subdivision, section, tariff are consistently cased — exact
   match, unless the user asks for a family: circle LIKE 'Bangalore%',
   tariff LIKE 'LT-%' / 'HT-%'.
3. demand_based_tariff and time_of_day are TEXT 'Yes'/'No' — never = TRUE / = 1.
4. voltage_class_in_kv is NUMERIC (0.23, 0.415, 11.0) — never quote it.
5. NEVER query columns c_2 or c_4 (blank junk headers, 100% empty).
6. corporate/debit/credit are constants — do not filter on them.
7. There is NO date/period column. Never write a time filter and never answer
   trend / month-over-month / year-over-year questions.
8. Do NOT recompute a ratio/KPI column from raw components — use the stored one.
9. Default to LIMIT 100 for row-level SELECTs. OMIT LIMIT for COUNT/SUM/AVG.
10. Generate a SINGLE read-only SELECT statement. Never INSERT/UPDATE/DELETE/
    DROP/ALTER/CREATE/GRANT. No semicolon-separated multiple statements.

OUTPUT CONTRACT:
- If the question is answerable: output ONLY the raw SQL. No markdown fences,
  no prose, no explanation.
- FOLLOW-UPS: the user is in a conversation. If a CONVERSATION SO FAR block is
  present below, a pronoun or back-reference ("that circle", "those divisions",
  "break that down", "what about X") is NOT ambiguity — resolve it from that
  context and answer. Only fall back to CLARIFY if the context genuinely does
  not contain the referent.
- If the question needs one of the UNLABELED installation groups (active,
  active_1, active_2 and their inactive partners), or hinges on which DCB stage
  an ambiguous column belongs to: output a single line starting with
  'CLARIFY:' followed by your clarifying question.
- If the question asks for a trend / change over time: output a single line
  starting with 'REFUSE:' explaining there is no time dimension in this table.
"""


# --- Few-shot examples, rewritten against REAL column names ---
FEW_SHOTS = [
    ("Which circle has the lowest collection efficiency with adjustment?",
     f"SELECT circle, AVG({COLL_EFF_WITH_ADJ}) AS avg_collection_efficiency\n"
     f"FROM {TABLE}\nGROUP BY circle\nORDER BY avg_collection_efficiency ASC\nLIMIT 1"),

    ("What's the total write-off amount for LT tariffs in the BRAZ zone?",
     f"SELECT SUM(write_off) AS total_write_off\nFROM {TABLE}\n"
     f"WHERE zone = 'BRAZ' AND tariff LIKE 'LT-%'"),

    ("Show me the top 5 subdivisions by net collection.",
     f"SELECT subdivision, SUM({NET_COLLECTION}) AS total_net_collection\n"
     f"FROM {TABLE}\nGROUP BY subdivision\nORDER BY total_net_collection DESC\nLIMIT 5"),

    ("Show all rows for Tumakuru division.",
     f"SELECT *\nFROM {TABLE}\nWHERE UPPER(division) = UPPER('Tumakuru')\nLIMIT 100"),

    ("How many active installations are there per tariff category?",
     f"SELECT tariff, SUM(active_installations) AS total_active_installations\n"
     f"FROM {TABLE}\nGROUP BY tariff\nORDER BY total_active_installations DESC"),

    # "consumers" is BESCOM-speak for installations — count active installations.
    ("How many consumers does each zone have?",
     f"SELECT zone, SUM(active_installations) AS total_consumers\n"
     f"FROM {TABLE}\nGROUP BY zone\nORDER BY total_consumers DESC"),

    ("Which circle has the most customers?",
     f"SELECT circle, SUM(active_installations) AS total_customers\n"
     f"FROM {TABLE}\nGROUP BY circle\nORDER BY total_customers DESC\nLIMIT 1"),

    ("How much net demand revenue comes from Time-of-Day tariff accounts?",
     f"SELECT SUM({NET_DEMAND_REVENUE}) AS total_net_demand_revenue\n"
     f"FROM {TABLE}\nWHERE time_of_day = 'Yes'"),

    ("What is the closing GST and TCS balance for Kolar Circle?",
     f"SELECT SUM({GST_TCS_CB}) AS total_gst_tcs_closing_balance\n"
     f"FROM {TABLE}\nWHERE circle = 'Kolar Circle'"),

    ("Compare metered and unmetered installation counts across zones.",
     f"SELECT zone, SUM(metered_installations) AS total_metered, "
     f"SUM(unmetered_installations) AS total_unmetered\n"
     f"FROM {TABLE}\nGROUP BY zone\nORDER BY zone"),

    ("How has net collection trended month over month this year?",
     "REFUSE: This table has no date or period column, so a month-over-month "
     "trend can't be produced. Zone/Circle/Division/Subdivision/Section/Tariff "
     "combinations repeat far more than they're unique (5,813 unique combos "
     "across 20,000 rows), which points to either a hidden time dimension or "
     "duplicated rows. Can you confirm whether the source has a date/period field?"),

    ("How many active installations does the Malleshwaram division have?",
     "CLARIFY: There are four different Active/Inactive installation column sets "
     "in this table: active_installations (the confirmed, labeled one) plus three "
     "unlabeled repeats (active, active_1, active_2) whose purpose isn't stated in "
     "the source. Which one did you mean, or shall I break out all four?"),
]


def build_context_block(history) -> str:
    """Render the previous turn(s) so the model can resolve back-references.

    A follow-up like "which division within that circle?" is meaningless on its
    own — "that circle" only has a referent in the turn before it. We show the
    prior question, the SQL that answered it, and a few of the rows it returned
    (the rows are what actually name the circle), so the model can substitute the
    real value into the new query.
    """
    if not history:
        return ""
    lines = []
    for turn in history[-2:]:  # two turns is enough for "that"/"those"; keeps latency down
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
        + "\nRESOLVING REFERENCES: If the new question refers back to the previous "
        "turn — 'that circle', 'those divisions', 'in it', 'there', 'break that "
        "down', 'what about X?' — resolve the reference using the previous question "
        "and the rows above, and write the concrete value into the SQL (e.g. if the "
        "previous answer's top row was circle 'Bangalore South Circle', then 'that "
        "circle' means circle = 'Bangalore South Circle'). Do NOT emit CLARIFY just "
        "because the question contains a pronoun that the context above already "
        "answers.\n"
        "If the new question is self-contained (names its own geography/tariff, or "
        "changes the subject), IGNORE the context above and answer it on its own.\n"
    )


def build_sql_user_prompt(question: str, history=None) -> str:
    shots = []
    for q, a in FEW_SHOTS:
        shots.append(f"User: {q}\nSQL: {a}")
    examples = "\n\n".join(shots)
    context = build_context_block(history)
    return (
        f"Here are worked examples for this exact database:\n\n{examples}\n\n"
        f"{context}\n"
        f"Now answer this one. Remember the OUTPUT CONTRACT.\n\n"
        f"User: {question}\nSQL:"
    )


def build_recovery_prompt(question: str, failed_sql: str, error: str) -> str:
    return f"""The previous SQL failed against the BESCOM database.

Original question: {question}
Failed SQL: {failed_sql}
Error: {error}

Fix it using these rules:
1. Use ONLY the physical column names from the schema — do not invent names.
2. Division names are mixed case: UPPER(division) = UPPER('value').
3. zone/circle/subdivision/section/tariff are consistently cased — exact match
   (or LIKE 'X%' for a family).
4. Never query c_2 or c_4 (junk/empty columns).
5. demand_based_tariff / time_of_day are TEXT 'Yes'/'No' — not booleans.
6. voltage_class_in_kv is numeric (0.23, 0.415, 11.0) — don't quote it.
7. No date/period column exists — don't filter or group by time.
8. Single read-only SELECT only.

Table: {TABLE}
Output ONLY the corrected SQL (no fences, no prose), or a one-line
'REFUSE: ...' if the question genuinely can't be answered from this schema."""


def build_formatter_prompt(question: str, rows_json: str, row_count: int) -> str:
    return f"""You are a helpful assistant explaining BESCOM DCB audit results in
plain, professional language for a utility staff user who may not know DCB jargon.

User's question: {question}
Row count: {row_count}
Result rows (JSON): {rows_json}

FORMATTING (strict):
- Write plain prose only. Do NOT use Markdown: no asterisks (**), no bold, no
  bullet characters, no backticks, no headings. Emphasise with word choice, not
  symbols.
- Report every number EXACTLY as it appears in the result JSON — do not round,
  truncate, restate in a different unit, or invent a different figure. Add
  thousands separators in the Indian style (e.g. 67,12,80,410). If a value has
  decimals, keep at most 2; drop a trailing ".0".
- Money columns are rupees; you MAY additionally note the crore equivalent in
  parentheses (e.g. "67,12,80,410 (about 67.13 Cr)"), but the primary figure
  must match the JSON. Consumption is in units.

CONTENT:
- Single count/sum/average: state the figure clearly and confidently in one
  sentence that reads well (e.g. "The total net consumption in Bangalore West
  Circle is 67,12,80,410 units.").
- A list: summarise the single key finding in one line, then let the UI table
  show the rest — do not re-list every row.
- Empty result: say "No records found" and suggest checking the division name
  spelling (casing varies) or trying a broader geography.
- Percentages: 1 decimal place.
- If the answer used of_recovery_of_average_cost, add: "Note: '% recovery of
  average cost' has no stated formula in the source and ranges very widely
  (1.1% to 214.6%) — verify against BESCOM's proforma before quoting it."
- If the answer used installations_billed / % billed, add a note that billed
  installations exceed total in about 31.6% of rows.
- Keep it concise (1-3 sentences). Do NOT show SQL unless asked.
- Spell out acronyms on first use: DCB (Demand-Collection-Balance),
  FPPCA (Fuel & Power Purchase Cost Adjustment), IOD (Interest on Deposit),
  RR (Revenue Register),
  P&G Surcharge, GST, TCS."""
