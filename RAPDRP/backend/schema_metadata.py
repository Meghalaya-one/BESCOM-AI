"""
schema_metadata.py — RAPDRP
===========================
Single source of truth for the RAPDRP commercial-performance table AS IT
ACTUALLY EXISTS in the Neon Postgres database.

The physical table is named "RAPDRP" (capitalised, so it MUST be double-quoted
in every SQL statement). It has 1,080 rows and 118 clean snake_case columns.

Every prompt sent to the LLM is built from THIS file so the SQL it writes only
references columns that truly exist. The prompt-engineering guide
(NLP_to_SQL_Prompt_Engineering_Guide.md) was written for SQLite; this backend
runs on Postgres, so the SQLite-specific rules are translated in
prompt_assembler.py.
"""

# ---------------------------------------------------------------------------
# Physical location. The table name has capitals -> always double-quote it.
# ---------------------------------------------------------------------------
TABLE_NAME = '"RAPDRP"'          # used verbatim inside SQL (already quoted)
TABLE_LABEL = "RAPDRP"           # used in prose / the validator (unquoted)
TOTAL_ROWS = 1080

# No blank/junk header columns in RAPDRP (unlike the old BESCOM extract).
JUNK_COLUMNS = []

# Columns that are constant across all 1,080 rows — carry zero information, so
# never GROUP BY them. (From the guide's data-quirks section.)
CONSTANT_COLUMNS = [
    "corporate_office", "demand_based_tariff", "time_of_day",
    "unmetered_installations", "dc_mnr_installations", "wheeled_energy_units",
    "ob_inactive", "demand_inactive", "collection_inactive",
    "assessed_tax_exempted_consumption", "metered_tax_exempted_consumption",
    "cur_interest_tax", "dr_adj_misc", "dr_adj_tax", "bc_misc",
    "bc_interest_rev_misc", "bc_interest_tax", "coll_interest_tax",
    "cr_adj_misc", "cr_adj_tax", "suspense_to_rr_transfer", "from_rr_transfer",
    "to_rr_transfer", "pc_interest_revenue", "pc_interest_tax", "pc_tax",
    "cb_interest_tax", "average_cost_of_supply", "pct_live_installations",
    "pct_dc_mnr_installations", "gst_tcs_debit_adjustment",
    "gst_tcs_bill_cancellation", "gst_tcs_credit_adjustment",
    "gst_tcs_payment_cancellation",
]

# ---------------------------------------------------------------------------
# Curated column catalogue. Only the columns a user realistically asks about
# get a friendly description. `preferred` marks the headline/"net" columns to
# choose over similar-looking alternatives.
# ---------------------------------------------------------------------------
COLUMNS = [
    # --- Geography (all five levels sit on every row; no joins) ---
    {"name": "zone",        "meaning": "Zone — level 1. 4 values: 'BMAZ-North','BMAZ-South','BRAZ','CTAZ'."},
    {"name": "circle",      "meaning": "Circle — level 2. 9 values e.g. 'Bengaluru East Circle','Kolar Circle'. NOTE the data says 'Bengaluru' not 'Bangalore'."},
    {"name": "division",    "meaning": "Division — level 3. 32 values e.g. 'Jayanagar','Whitefield'. Casing is consistent — exact match."},
    {"name": "subdivision", "meaning": "Sub-division — level 4, the reporting unit. 90 values. Prefer this in answers."},
    {"name": "section",     "meaning": "Section — level 5. In this extract section is 100% identical to subdivision; prefer subdivision."},

    # --- Tariff / rate attributes ---
    {"name": "rate_schedule_main_grp", "meaning": "Top tariff group: 'HT' (high tension) or 'LT' (low tension). Use this for 'HT vs LT'."},
    {"name": "tariff",                 "meaning": "Tariff class: 'HT','LT1','LT2','LT3','LT4','LT5'. A specific class like LT2 means this column."},
    {"name": "rate_schedule_code",     "meaning": "Finest tariff dimension, 12 codes e.g. 'HT1','LT2A1-N'."},
    {"name": "debit_rate",             "meaning": "Debit rate for the rate_schedule_code (23.10-23.27). Not additive."},
    {"name": "credit_rate",            "meaning": "Credit rate for the rate_schedule_code (61.10-61.28). Not additive."},
    {"name": "voltage_class_kv",       "meaning": "Voltage class, TEXT: '11' (HT1,HT2B1,HT2B2,HT2C1) or 'Upto 11' (all others)."},

    # --- Installations (additive counts) ---
    {"name": "active_installations",    "meaning": "Live / active service connections. 'consumers'/'customers'/'connections' -> this."},
    {"name": "inactive_installations",  "meaning": "Inactive (disconnected/dormant) connections."},
    {"name": "total_installations",     "meaning": "= active_installations + inactive_installations."},
    {"name": "metered_installations",   "meaning": "Connections with a working meter."},
    {"name": "dc_mnr_installations",    "meaning": "DC/MNR = Disconnected or Meter-Not-Reading connections. Maps 'DC/MNR','DC MNR','DC-MNR','DCMNR' (0 across all rows here)."},
    {"name": "installations_billed",    "meaning": "Connections billed this period."},
    {"name": "installations_unbilled",  "meaning": "Connections NOT billed this period."},
    {"name": "total_billed_unbilled",   "meaning": "= installations_billed + installations_unbilled."},

    # --- Consumption (units, kWh) ---
    {"name": "assessed_taxed_consumption", "meaning": "Assessed (estimated, unmetered) taxable consumption, kWh."},
    {"name": "metered_taxed_consumption",  "meaning": "Metered taxable consumption, kWh."},
    {"name": "total_consumption",          "meaning": "Sum of the four consumption components, kWh."},
    {"name": "bill_cancellation_consumption", "meaning": "Units reversed by bill cancellations, kWh."},
    {"name": "net_consumption",            "meaning": "= total_consumption - bill_cancellation_consumption. THE headline energy figure; use for 'units sold' and per-unit KPIs.", "preferred": True},

    # --- Opening balance / arrears carried in (INR) ---
    {"name": "ob_active",     "meaning": "Opening arrears on active consumers, INR (= ob_total = ob_revenue here)."},
    {"name": "ob_total",      "meaning": "Total opening balance / arrears carried in, INR."},
    {"name": "ob_total_sum",  "meaning": "Opening balance grand total across all components, INR."},

    # --- Demand raised (INR) ---
    {"name": "demand_total",        "meaning": "Total demand raised, INR (= demand_active = net_demand_revenue here)."},
    {"name": "net_demand_revenue",  "meaning": "NET DEMAND — revenue portion, INR. Denominator of collection efficiency.", "preferred": True},
    {"name": "net_demand_tax",      "meaning": "NET DEMAND — tax portion, INR. Add to net_demand_revenue for total net demand.", "preferred": True},
    {"name": "cur_revenue",         "meaning": "Current-period energy revenue demand, INR."},
    {"name": "cur_total_sum",       "meaning": "Current demand total, INR."},

    # --- Collection (INR) ---
    {"name": "collection_total", "meaning": "Total amount collected, INR (= collection_active = coll_revenue here)."},
    {"name": "coll_revenue",     "meaning": "Collection — energy revenue component, INR."},
    {"name": "coll_total_sum",   "meaning": "Gross collection BEFORE adjustments, INR. Numerator of collection efficiency 'without adjustment'."},
    {"name": "net_collection",   "meaning": "NET COLLECTION after credit adjustments, IOD and payment cancellations. THE headline collection figure and numerator of efficiency 'with adjustment'.", "preferred": True},

    # --- Adjustments / leakage (INR) ---
    {"name": "dr_adj_total",   "meaning": "Debit adjustment total, INR."},
    {"name": "bc_total_sum",   "meaning": "Bill cancellation total, INR."},
    {"name": "cr_adj_total",   "meaning": "Credit adjustment total, INR."},
    {"name": "net_iod",        "meaning": "Net Interest on Delayed payment (IOD) charged, INR."},
    {"name": "net_reversal_iod","meaning": "IOD reversed, INR."},
    {"name": "pc_total",       "meaning": "Payment cancellation total (bounced / reversed payments), INR."},
    {"name": "write_off",      "meaning": "Amount written off, INR."},

    # --- Closing balance / arrears outstanding (INR) ---
    {"name": "cb_revenue",   "meaning": "Closing balance — revenue component, INR."},
    {"name": "cb_total_sum", "meaning": "CLOSING BALANCE grand total = arrears outstanding at period end. Use for 'arrears','outstanding','dues','receivables','pending'.", "preferred": True},

    # --- Pre-computed KPIs (per-row ratios — do NOT AVG across rows; recompute) ---
    {"name": "pct_billed_installations", "meaning": "% of installations billed. Recompute when aggregating.", "alias": "pct_billed"},
    {"name": "pct_metered_consumption",  "meaning": "% of consumption metered. Recompute when aggregating."},
    {"name": "pct_assessed_consumption", "meaning": "% of consumption assessed (red flag). Recompute when aggregating."},
    {"name": "pct_coll_eff_with_adj",    "meaning": "Collection efficiency WITH adjustments %. Default 'collection efficiency' to this. Recompute when aggregating.", "preferred": True},
    {"name": "pct_coll_eff_without_adj", "meaning": "Collection efficiency WITHOUT adjustments %. Use only if user says 'without adjustment'."},
    {"name": "ratio_arrears_to_demand",  "meaning": "Arrears-to-demand ratio (closing balance / net demand). Higher = worse. Recompute when aggregating."},
    {"name": "demand_per_unit",          "meaning": "Demand raised per unit of energy, INR/kWh. Recompute when aggregating."},
    {"name": "coll_per_unit_with_adj",   "meaning": "Collection per unit WITH adjustments, INR/kWh. Recompute when aggregating."},
    {"name": "coll_per_unit_without_adj","meaning": "Collection per unit WITHOUT adjustments, INR/kWh."},
    {"name": "consumption_per_installation", "meaning": "Average units consumed per installation, kWh. Recompute when aggregating."},
    {"name": "pct_recovery_of_avg_cost", "meaning": "% of average cost of supply recovered. Recompute when aggregating."},

    # --- GST / TCS parallel DCB block (INR) ---
    {"name": "gst_tcs_ob",         "meaning": "GST/TCS opening balance, INR."},
    {"name": "gst_tcs_demand",     "meaning": "GST/TCS demand raised, INR."},
    {"name": "gst_tcs_net_demand", "meaning": "GST/TCS net demand, INR."},
    {"name": "gst_tcs_collection", "meaning": "GST/TCS collected, INR."},
    {"name": "gst_tcs_cb",         "meaning": "GST/TCS closing balance (outstanding), INR."},
]

# All physical column names that exist (for the read-only validator).
ALL_PHYSICAL_COLUMNS = [
    "id", "corporate_office", "zone", "circle", "division", "subdivision",
    "section", "rate_schedule_main_grp", "tariff", "rate_schedule_code",
    "debit_rate", "credit_rate", "demand_based_tariff", "time_of_day",
    "voltage_class_kv", "active_installations", "inactive_installations",
    "total_installations", "metered_installations", "unmetered_installations",
    "total_metered_unmetered", "dc_mnr_installations", "installations_billed",
    "installations_unbilled", "total_billed_unbilled", "ob_active",
    "ob_inactive", "ob_total", "demand_active", "demand_inactive",
    "demand_total", "collection_active", "collection_inactive",
    "collection_total", "assessed_taxed_consumption",
    "assessed_tax_exempted_consumption", "metered_taxed_consumption",
    "metered_tax_exempted_consumption", "total_consumption",
    "bill_cancellation_consumption", "net_consumption", "wheeled_energy_units",
    "ob_revenue", "ob_interest_rev_misc", "ob_interest_tax", "ob_tax",
    "ob_total_sum", "misc_revenue", "misc_demand", "misc_interest_rev_arrears",
    "misc_total_sum", "cur_revenue", "cur_misc_demand", "cur_interest_rev_arrears",
    "cur_interest_tax", "cur_tax", "cur_total_sum", "dr_adj_revenue",
    "dr_adj_misc", "dr_adj_tax", "dr_adj_total", "bc_revenue", "bc_misc",
    "bc_interest_rev_misc", "bc_interest_tax", "bc_tax", "bc_total_sum",
    "net_demand_revenue", "net_demand_tax", "coll_revenue",
    "coll_interest_rev_misc", "coll_interest_tax", "coll_tax", "coll_total_sum",
    "cr_adj_revenue", "cr_adj_misc", "cr_adj_tax", "cr_adj_total", "net_iod",
    "net_reversal_iod", "suspense_to_rr_transfer", "from_rr_transfer",
    "to_rr_transfer", "pc_revenue", "pc_interest_revenue", "pc_interest_tax",
    "pc_tax", "pc_total", "net_collection", "write_off", "cb_revenue",
    "cb_interest_rev_misc", "cb_interest_tax", "cb_tax", "cb_total_sum",
    "average_cost_of_supply", "pct_live_installations",
    "pct_billed_installations", "pct_dc_mnr_installations",
    "pct_metered_consumption", "pct_assessed_consumption",
    "pct_coll_eff_with_adj", "pct_coll_eff_without_adj",
    "ratio_arrears_to_demand", "demand_per_unit", "coll_per_unit_with_adj",
    "coll_per_unit_without_adj", "consumption_per_installation",
    "pct_recovery_of_avg_cost", "gst_tcs_ob", "gst_tcs_demand",
    "gst_tcs_debit_adjustment", "gst_tcs_bill_cancellation",
    "gst_tcs_net_demand", "gst_tcs_collection", "gst_tcs_credit_adjustment",
    "gst_tcs_payment_cancellation", "gst_tcs_cb",
]


def column_catalogue_text() -> str:
    """Render the curated columns as a compact list for the system prompt."""
    lines = []
    for c in COLUMNS:
        star = " [PREFER]" if c.get("preferred") else ""
        lines.append(f"- {c['name']}{star}: {c['meaning']}")
    return "\n".join(lines)


def follow_up_topics_text() -> str:
    """Short list of what CAN be asked, to ground the follow-up suggester."""
    lines = []
    for c in COLUMNS:
        first = (c.get("meaning") or "").split(".")[0].strip()
        lines.append(f"- {c['name']}: {first}")
    return "\n".join(lines)
