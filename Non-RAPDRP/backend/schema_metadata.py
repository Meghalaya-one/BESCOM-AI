"""
schema_metadata.py — Non-RAPDRP
===============================
Single source of truth for the Non-RAPDRP DCB table AS IT ACTUALLY EXISTS in the
Neon Postgres database.

The physical table is named "Non RAPDRP" (capitalised, with a SPACE, so it MUST
be double-quoted in every SQL statement). It has 3,791 rows and 133 clean
snake_case columns.

Every prompt sent to the LLM is built from THIS file so the SQL it writes only
references columns that truly exist. Data-quirk facts below were verified live
against the database.
"""

# ---------------------------------------------------------------------------
# Physical location. Name has a space + capitals -> always double-quote it.
# ---------------------------------------------------------------------------
TABLE_NAME = '"Non RAPDRP"'       # used verbatim inside SQL (already quoted)
TABLE_LABEL = "Non RAPDRP"        # used in prose / the validator (unquoted)
TOTAL_ROWS = 3791

# Blank header columns (100% NULL in every row) — never query them.
JUNK_COLUMNS = ["col7_blank", "col9_blank"]

# Columns that are constant across all 3,791 rows — carry zero information, so
# never GROUP BY or filter on them. Verified live (min == max).
CONSTANT_COLUMNS = [
    "corporate", "demand_based_tariff", "time_of_day",
    "ob_active", "ob_inactive", "ob_total_20_21",
    "sl_kw_inactive", "sl_kw_total_23_24", "sl_hp_active", "sl_hp_inactive",
    "sl_hp_total_26_27", "assessed_tax_exempted_consumption",
    "metered_tax_exempted_consumption", "wheeled_energy_units",
    "texd_revenue", "texd_misc_demand", "texd_interest_rev_misc_arrears",
    "texd_pg_surcharge", "texd_total_sum", "dbadj_misc", "dbadj_pg_surcharge",
    "bc_misc", "bc_interest_rev_misc", "bc_interest_tax", "bc_tax",
    "bc_pg_surcharge", "bc_total_sum", "cradj_misc", "cradj_pg_surcharge",
    "net_reversal_iod", "suspense_to_rr_transfer", "from_rr_transfer",
    "to_rr_transfer", "pc_revenue", "pc_interest_rev", "pc_interest_tax",
    "pc_tax", "pc_pg_surcharge", "pc_total", "write_off",
    "average_cost_of_supply",
    # Entire GST/TCS block is 0 in this extract.
    "gst_tcs_ob", "gst_tcs_demand", "gst_tcs_debit_adjustment",
    "gst_tcs_bill_cancellation", "gst_tcs_net_demand", "gst_tcs_collection",
    "gst_tcs_credit_adjustment", "gst_tcs_payment_cancellation", "gst_tcs_cb",
    "balance_transfer_amount",
]

# ---------------------------------------------------------------------------
# Curated column catalogue. `preferred` marks headline/"net" columns.
# ---------------------------------------------------------------------------
COLUMNS = [
    # --- Geography (all levels sit on every row; no joins) ---
    {"name": "zone",        "meaning": "Zone — level 1. 2 values: 'BRAZ','CTAZ'."},
    {"name": "circle",      "meaning": "Circle — level 2. 5 values: 'BANGALORE','DEVANAGERE','KOLAR','RAMANAGARA','TUMAKURU' (ALL UPPERCASE in the data)."},
    {"name": "division",    "meaning": "Division — level 3. 18 values e.g. 'TUMAKURU','KOLAR' (ALL UPPERCASE). Casing consistent — exact match."},
    {"name": "subdivision", "meaning": "Sub-division — level 4. 56 values."},
    {"name": "section",     "meaning": "Section — level 5, the finest reporting unit. 248 values. Use COUNT(DISTINCT section) to count sections."},

    # --- Tariff / rate attributes ---
    {"name": "tariff",           "meaning": "Tariff class. 17 codes: HT2A,HT2B,HT3A,HT5,HT6,LT1..LT7. Use tariff LIKE 'HT%' or 'LT%' for a family."},
    {"name": "voltage_class_kv", "meaning": "Voltage class in kV, NUMERIC: 0.4 (LT) or 11.0 (HT). Do not quote it."},

    # --- Installations (additive counts) ---
    {"name": "active_installations",         "meaning": "Live / active service connections. 'consumers'/'customers'/'connections' -> this."},
    {"name": "inactive_installations",       "meaning": "Inactive (disconnected/dormant) connections."},
    {"name": "total_installations_10_11",    "meaning": "= active + inactive installations (source columns 10+11)."},
    {"name": "metered_installations",        "meaning": "Connections with a working meter."},
    {"name": "unmetered_installations",      "meaning": "Connections without a meter."},
    {"name": "dc_mnr_installations",         "meaning": "DC/MNR installations = Disconnected or Meter-Not-Reading connections. Maps 'DC/MNR','DC MNR','DC-MNR','DCMNR','disconnected/meter not reading'."},
    {"name": "total_metered_13_14",          "meaning": "= metered + unmetered installations (columns 13+14)."},
    {"name": "installations_billed",         "meaning": "Connections billed this period."},
    {"name": "installations_unbilled",       "meaning": "Connections NOT billed this period."},
    {"name": "total_billed_17_18",           "meaning": "= billed + unbilled installations (columns 17+18)."},

    # --- Street-light KW blocks (mostly zero except sl_kw_active) ---
    {"name": "sl_kw_active", "meaning": "Street-light KW active connections."},

    # --- Consumption (units, kWh) ---
    {"name": "assessed_taxed_consumption", "meaning": "Assessed (estimated, unmetered) taxable consumption, kWh."},
    {"name": "metered_taxed_consumption",  "meaning": "Metered taxable consumption, kWh."},
    {"name": "total_consumption_29_32",    "meaning": "Total consumption (columns 29+30+31+32), kWh."},
    {"name": "bill_cancellation_consumption", "meaning": "Units reversed by bill cancellations, kWh."},
    {"name": "net_consumption_33_34",      "meaning": "= total consumption - bill cancellation (columns 33-34), kWh. THE headline energy figure; use for 'consumption'/'units sold'.", "preferred": True},

    # --- Opening balance / arrears carried in (INR) ---
    {"name": "ob_revenue",   "meaning": "Opening balance — energy revenue component, INR."},
    {"name": "ob_total_sum", "meaning": "Opening balance grand total = arrears carried in, INR. Use for 'opening balance'.", "preferred": True},

    # --- Demand raised (INR) ---
    {"name": "tcd_revenue",        "meaning": "Total current demand — revenue component, INR."},
    {"name": "tcd_total_sum",      "meaning": "Total current demand grand total, INR."},
    {"name": "net_demand_revenue", "meaning": "NET DEMAND — revenue portion, INR. Use for 'net demand'/'total demand'. Denominator of collection efficiency.", "preferred": True},
    {"name": "net_demand_tax",     "meaning": "NET DEMAND — tax portion, INR. Add to net_demand_revenue for total net demand."},
    {"name": "fixed_charges",      "meaning": "Fixed charges component of demand, INR."},
    {"name": "energy_charges",     "meaning": "Energy charges component of demand, INR."},
    {"name": "fppca_charges",      "meaning": "FPPCA (Fuel & Power Purchase Cost Adjustment) charges, INR."},

    # --- Collection (INR) ---
    {"name": "col_revenue",   "meaning": "Collection — energy revenue component, INR. Use for 'revenue'.", "preferred": True},
    {"name": "col_total_sum", "meaning": "Gross collection total BEFORE adjustments, INR. Numerator of efficiency 'without adjustment'."},
    {"name": "net_collection","meaning": "NET COLLECTION after adjustments/IOD. THE headline collection figure; use for 'collection'/'collected'. Numerator of efficiency 'with adjustment'.", "preferred": True},

    # --- Adjustments / leakage (INR) ---
    {"name": "dbadj_total", "meaning": "Debit adjustment total, INR."},
    {"name": "cradj_total", "meaning": "Credit adjustment total, INR."},
    {"name": "bc_total_sum","meaning": "BILL CANCELLATION total, INR (money value of cancelled bills). Use for 'cancellations'/'amount cancelled'. NOTE: 0 for every row in this extract."},
    {"name": "net_iod",     "meaning": "Net Interest on Delayed payment (IOD) charged, INR."},

    # --- Closing balance / arrears outstanding (INR) ---
    {"name": "cb_revenue",   "meaning": "Closing balance — revenue component, INR."},
    {"name": "cb_total_sum", "meaning": "CLOSING BALANCE grand total = arrears outstanding at period end. Use for 'arrears','outstanding','dues','receivables','pending','closing balance'.", "preferred": True},

    # --- Pre-computed KPIs (per-row ratios — do NOT AVG across rows; recompute) ---
    {"name": "pct_billed_installations",   "meaning": "% of installations billed. Recompute when aggregating."},
    {"name": "pct_metered_consumption",    "meaning": "% of consumption metered. Recompute when aggregating."},
    {"name": "pct_assessed_consumption",   "meaning": "% of consumption assessed (red flag). Recompute when aggregating."},
    {"name": "pct_collection_eff_with_adj","meaning": "Collection efficiency WITH adjustments %. Default 'collection efficiency' to this. Recompute when aggregating.", "preferred": True},
    {"name": "pct_collection_eff_without_adj", "meaning": "Collection efficiency WITHOUT adjustments %. Use only if user says 'without adjustment'."},
    {"name": "ratio_arrears_demand",       "meaning": "Arrears-to-demand ratio (closing balance / net demand). Higher = worse. Recompute when aggregating."},
    {"name": "demand_per_unit",            "meaning": "Demand raised per unit of energy, INR/kWh. Recompute when aggregating."},
    {"name": "collection_per_unit_with_adj",   "meaning": "Collection per unit WITH adjustments, INR/kWh. Recompute when aggregating."},
    {"name": "collection_per_unit_without_adj","meaning": "Collection per unit WITHOUT adjustments, INR/kWh."},
    {"name": "consumption_per_installation",   "meaning": "Average units consumed per installation, kWh. Recompute when aggregating."},
    {"name": "pct_recovery_avg_cost",      "meaning": "% of average cost of supply recovered. Recompute when aggregating."},
]

# All physical column names that exist (for the read-only validator).
ALL_PHYSICAL_COLUMNS = [
    "id", "corporate", "zone", "circle", "division", "subdivision", "section",
    "col7_blank", "tariff", "col9_blank", "debit", "credit",
    "demand_based_tariff", "time_of_day", "voltage_class_kv",
    "active_installations", "inactive_installations", "total_installations_10_11",
    "metered_installations", "unmetered_installations", "total_metered_13_14",
    "dc_mnr_installations", "installations_billed", "installations_unbilled",
    "total_billed_17_18", "ob_active", "ob_inactive", "ob_total_20_21",
    "sl_kw_active", "sl_kw_inactive", "sl_kw_total_23_24", "sl_hp_active",
    "sl_hp_inactive", "sl_hp_total_26_27", "assessed_taxed_consumption",
    "assessed_tax_exempted_consumption", "metered_taxed_consumption",
    "metered_tax_exempted_consumption", "total_consumption_29_32",
    "bill_cancellation_consumption", "net_consumption_33_34",
    "wheeled_energy_units", "ob_revenue", "ob_interest_rev_misc",
    "ob_interest_tax", "ob_tax", "ob_pg_surcharge", "ob_total_sum",
    "texd_revenue", "texd_misc_demand", "texd_interest_rev_misc_arrears",
    "texd_pg_surcharge", "texd_total_sum", "tcd_revenue", "tcd_misc_demand",
    "tcd_interest_rev_misc_arrears", "tcd_interest_tax", "tcd_tax",
    "tcd_pg_surcharge", "tcd_total_sum", "dbadj_revenue", "dbadj_misc",
    "dbadj_tax", "dbadj_pg_surcharge", "dbadj_total", "bc_revenue", "bc_misc",
    "bc_interest_rev_misc", "bc_interest_tax", "bc_tax", "bc_pg_surcharge",
    "bc_total_sum", "net_demand_revenue", "fixed_charges", "energy_charges",
    "fppca_charges", "net_demand_tax", "net_demand_pg_surcharge", "col_revenue",
    "col_interest_rev_misc", "col_interest_tax", "col_tax", "col_pg_surcharge",
    "col_total_sum", "cradj_revenue", "cradj_misc", "cradj_tax",
    "cradj_pg_surcharge", "cradj_total", "net_iod", "net_reversal_iod",
    "suspense_to_rr_transfer", "from_rr_transfer", "to_rr_transfer",
    "pc_revenue", "pc_interest_rev", "pc_interest_tax", "pc_tax",
    "pc_pg_surcharge", "pc_total", "net_collection", "net_collection_pg_surcharge",
    "write_off", "cb_revenue", "cb_interest_rev_misc", "cb_interest_tax",
    "cb_tax", "cb_pg_surcharge", "cb_total_sum", "average_cost_of_supply",
    "pct_live_installations", "pct_billed_installations",
    "pct_dc_mnr_installations", "pct_metered_consumption",
    "pct_assessed_consumption", "pct_collection_eff_with_adj",
    "pct_collection_eff_without_adj", "ratio_arrears_demand", "demand_per_unit",
    "collection_per_unit_with_adj", "collection_per_unit_without_adj",
    "consumption_per_installation", "pct_recovery_avg_cost", "gst_tcs_ob",
    "gst_tcs_demand", "gst_tcs_debit_adjustment", "gst_tcs_bill_cancellation",
    "gst_tcs_net_demand", "gst_tcs_collection", "gst_tcs_credit_adjustment",
    "gst_tcs_payment_cancellation", "gst_tcs_cb", "balance_transfer_amount",
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
