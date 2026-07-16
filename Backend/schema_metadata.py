"""
schema_metadata.py
==================
Single source of truth for the BESCOM DCB audit table AS IT ACTUALLY EXISTS
in the Neon Postgres database.

IMPORTANT: The NLP-to-SQL guide (NLP_to_SQL_Prompt_Engineering_Guide_BESCOM.md)
was written against idealized snake_case names (e.g. `bescom_dcb_audit`,
`tariff_category`, `net_collection_final`). The live database uses DIFFERENT
physical names (table `bescom_database`, column `tariff`, etc.). Every prompt
we send to Gemini is built from THIS file so the generated SQL references
columns that truly exist.

The data-quality rules from the guide still apply — they are just mapped onto
the real column names here.
"""

# ---------------------------------------------------------------------------
# Physical location
# ---------------------------------------------------------------------------
TABLE_NAME = "bescom_database"
TOTAL_ROWS = 20000

# Columns that must NEVER be queried (blank/junk headers in the source, 100% empty)
JUNK_COLUMNS = ["c_2", "c_4"]

# Columns that are constants for all 20,000 rows — safe to exclude from SELECT
CONSTANT_COLUMNS = {
    "corporate": "BESCOM",
    "debit": "DCB Debit Account",
    "credit": "DCB Credit Account",
}

# ---------------------------------------------------------------------------
# Curated column catalogue.
# Only the columns a user is realistically going to ask about get a friendly
# description. The ~70 repeating DCB stage columns are summarized as a group in
# the prompt rather than listed one-by-one (that would blow the context and add
# noise). The `preferred` flag marks the explicitly-formula'd "_final"-equivalent
# columns the guide says to prefer over inferred-stage ones.
# ---------------------------------------------------------------------------
COLUMNS = [
    # --- Geography (filter each level INDEPENDENTLY — not a clean tree) ---
    {"name": "zone",        "meaning": "Operational zone. 4 values: 'BMAZ - South', 'BMAZ - North', 'BRAZ', 'CTAZ'."},
    {"name": "circle",      "meaning": "Circle. 9 values e.g. 'Bangalore West Circle', 'Kolar Circle'. Use LIKE 'Bangalore%' for the Bangalore family."},
    {"name": "division",    "meaning": "Division. CASING IS INCONSISTENT ('TUMAKURU' vs 'Tumakuru'). ALWAYS filter with UPPER(division)=UPPER('value')."},
    {"name": "subdivision", "meaning": "Subdivision. Consistently cased — exact match is fine."},
    {"name": "section",     "meaning": "Section code. Only 4 values (TMK1, NRM1, MLS1, KRP1); each spans ~19 divisions — NOT a drill-down of division."},

    # --- Tariff / account attributes ---
    {"name": "tariff",              "meaning": "Tariff category. 14 codes e.g. 'LT-1','HT-3'. Use tariff LIKE 'LT-%' or 'HT-%' for a family."},
    {"name": "voltage_class_in_kv", "meaning": "Voltage class in kV, NUMERIC: 0.23 (LT single-phase), 0.415 (LT three-phase), 11.0 (HT). Do not quote as text."},
    {"name": "demand_based_tariff", "meaning": "Demand-based tariff flag. TEXT 'Yes'/'No' (not boolean)."},
    {"name": "time_of_day",         "meaning": "Time-of-Day (ToD) tariff flag. TEXT 'Yes'/'No' (not boolean)."},

    # --- Installations (confirmed, labeled) ---
    {"name": "active_installations",   "meaning": "Active installations (confirmed, labeled). Paired with total_10_11."},
    {"name": "inactive_installations", "meaning": "Inactive installations (confirmed, labeled)."},
    {"name": "total_10_11",            "meaning": "Total installations = active + inactive (source columns 10+11)."},
    {"name": "metered_installations",  "meaning": "Metered installations. NOTE: exceeds total in ~13.75% of rows — flag if used for coverage %."},
    {"name": "unmetered_installations","meaning": "Unmetered installations."},
    {"name": "total_13_14",            "meaning": "Total metered + unmetered installations (columns 13+14)."},
    {"name": "dc_mnr_installations",   "meaning": "DC/MNR (disconnected / meter-not-reading) installations."},
    {"name": "installations_billed",  "meaning": "Installations billed. NOTE: exceeds total in ~31.6% of rows — % billed can read >100%. Flag if used."},
    {"name": "installations_unbilled","meaning": "Installations not billed."},
    {"name": "total_17_18",            "meaning": "Total billed + unbilled installations (columns 17+18)."},

    # --- The 3 UNLABELED active/inactive groups (ask before choosing) ---
    {"name": "active",       "meaning": "UNLABELED active-installations group #1 — purpose not stated in source. Ask the user before using."},
    {"name": "inactive",     "meaning": "UNLABELED inactive group #1 — purpose not stated in source."},
    {"name": "active_1",     "meaning": "UNLABELED active-installations group #2 — purpose not stated in source. Ask before using."},
    {"name": "inactive_1",   "meaning": "UNLABELED inactive group #2 — purpose not stated in source."},
    {"name": "active_2",     "meaning": "UNLABELED active-installations group #3 — purpose not stated in source. Ask before using."},
    {"name": "inactive_2",   "meaning": "UNLABELED inactive group #3 — purpose not stated in source."},

    # --- Consumption ---
    {"name": "assessed_taxed_consumption",         "meaning": "Assessed (unmetered) taxed consumption, units."},
    {"name": "assessed_tax_exempted_consumption",  "meaning": "Assessed tax-exempted consumption, units."},
    {"name": "metered_taxed_consumption",          "meaning": "Metered taxed consumption, units."},
    {"name": "metered_tax_exempted_consumption",   "meaning": "Metered tax-exempted consumption, units."},
    {"name": "total_consumption_29_30_31_32",      "meaning": "Total consumption (columns 29+30+31+32), units."},
    {"name": "net_consumption_33_34",              "meaning": "Net consumption after bill-cancellation (columns 33-34), units. Use for 'consumption'.", "preferred": True},
    {"name": "wheeled_energy_units",               "meaning": "Wheeled energy units. ~1% of rows are NEGATIVE (net export to grid OR data error — flag)."},

    # --- The trustworthy, explicitly-formula'd demand/collection/closing cols ---
    {"name": "net_demand_revenue_current_demand_debits_bill_cancellations_42_", "meaning": "NET DEMAND (revenue) — explicit formula. Use this for 'net demand'/'total demand'.", "preferred": True, "alias": "net_demand_revenue"},
    {"name": "net_demand_tax_current_demand_debits_bill_cancellations_49_50_5", "meaning": "NET DEMAND (tax component) — explicit formula.", "preferred": True, "alias": "net_demand_tax"},
    {"name": "net_collection_collection_credits_suspense_to_rr_transfer_payme", "meaning": "NET COLLECTION — explicit formula. Use this for 'net collection'/'total collected'.", "preferred": True, "alias": "net_collection"},
    {"name": "fixed_charges",   "meaning": "Fixed charges component of collection (explicitly labeled)."},
    {"name": "energy_charges",  "meaning": "Energy charges component of collection (explicitly labeled)."},
    {"name": "fppca_charges",   "meaning": "FPPCA (Fuel & Power Purchase Cost Adjustment) charges (explicitly labeled)."},

    # --- Write-off / closing / transfers ---
    {"name": "write_off",               "meaning": "Amount written off (₹)."},
    {"name": "net_iod",                 "meaning": "Net IOD (Interest on Deposit)."},
    {"name": "net_reversal_iod",        "meaning": "Net reversal of IOD (Interest on Deposit)."},
    {"name": "suspense_to_rr_transfer", "meaning": "Suspense-to-RR (Revenue Register) transfer amount."},
    {"name": "from_rr_transfer",        "meaning": "Amount transferred FROM RR."},
    {"name": "to_rr_transfer",          "meaning": "Amount transferred TO RR."},
    {"name": "balance_transfer_amount", "meaning": "Balance transfer amount (₹)."},

    # --- Ratio / KPI (pre-computed — do NOT recompute from components) ---
    {"name": "average_cost_of_supply",                                   "meaning": "Average cost of supply (₹ per unit)."},
    {"name": "of_live_installations_10_12_100",                          "meaning": "% of live installations = active/total ×100.", "alias": "pct_live_installations"},
    {"name": "of_billed_installations_17_10_100",                        "meaning": "% of billed installations = billed/total ×100. Can exceed 100% (data issue).", "alias": "pct_billed_installations"},
    {"name": "of_dc_mnr_installations_16_10_100",                        "meaning": "% of DC/MNR installations.", "alias": "pct_dc_mnr_installations"},
    {"name": "of_metered_consumption_31_32_33_100",                      "meaning": "% of metered consumption.", "alias": "pct_metered_consumption"},
    {"name": "of_assessed_consumption_29_30_33_100",                     "meaning": "% of assessed consumption.", "alias": "pct_assessed_consumption"},
    {"name": "of_collection_efficiency_with_adjustment_68_72_45_51_55_100","meaning": "Collection efficiency % WITH adjustment. Default 'collection efficiency' to this one.", "preferred": True, "alias": "pct_collection_efficiency_with_adj"},
    {"name": "of_collection_efficiency_without_adjustment_68_45_51_100", "meaning": "Collection efficiency % WITHOUT adjustment. Use only if user says 'without adjustment'.", "alias": "pct_collection_efficiency_without_adj"},
    {"name": "ratio_of_arrears_w_r_t_demand_89_45_51_55",               "meaning": "Ratio of arrears w.r.t. demand.", "alias": "ratio_arrears_to_demand"},
    {"name": "demand_per_unit_45_51_55_33",                             "meaning": "Demand per unit (₹/unit).", "alias": "demand_per_unit"},
    {"name": "collection_per_unit_with_adjustment_68_72_33",           "meaning": "Collection per unit WITH adjustment (₹/unit).", "alias": "collection_per_unit_with_adj"},
    {"name": "collection_per_unit_without_adjustment_68_33",           "meaning": "Collection per unit WITHOUT adjustment (₹/unit).", "alias": "collection_per_unit_without_adj"},
    {"name": "consumption_per_installation_33_10",                    "meaning": "Consumption per installation (units).", "alias": "consumption_per_installation"},
    {"name": "of_recovery_of_average_cost",                          "meaning": "% recovery of average cost. NO stated formula, range 1.1%-214.6% — FLAG as needing source verification if used.", "alias": "pct_recovery_of_avg_cost"},

    # --- GST / TCS lifecycle ---
    {"name": "gst_and_tcs_ob",                    "meaning": "GST & TCS opening balance."},
    {"name": "gst_and_tcs_demand",                "meaning": "GST & TCS current demand."},
    {"name": "gst_and_tcs_debit_adjustment",      "meaning": "GST & TCS debit adjustment."},
    {"name": "gst_and_tcs_bill_cancellation",     "meaning": "GST & TCS bill cancellation."},
    {"name": "gst_and_tcs_net_demand_105_106_107","meaning": "GST & TCS net demand (105+106+107)."},
    {"name": "gst_and_tcs_collection",            "meaning": "GST & TCS collection."},
    {"name": "gst_and_tcs_credit_adjustment",     "meaning": "GST & TCS credit adjustment."},
    {"name": "gst_and_tcs_payment_cancellation",  "meaning": "GST & TCS payment cancellation."},
    {"name": "gst_and_tcs_cb",                    "meaning": "GST & TCS closing balance."},
]

# All physical column names that exist in the table (for the read-only validator)
ALL_PHYSICAL_COLUMNS = [
    "corporate", "zone", "circle", "division", "subdivision", "section", "c_2",
    "tariff", "c_4", "debit", "credit", "demand_based_tariff", "time_of_day",
    "voltage_class_in_kv", "active_installations", "inactive_installations",
    "total_10_11", "metered_installations", "unmetered_installations",
    "total_13_14", "dc_mnr_installations", "installations_billed",
    "installations_unbilled", "total_17_18", "active", "inactive", "total_20_21",
    "active_1", "inactive_1", "total_23_24", "active_2", "inactive_2",
    "total_26_27", "assessed_taxed_consumption", "assessed_tax_exempted_consumption",
    "metered_taxed_consumption", "metered_tax_exempted_consumption",
    "total_consumption_29_30_31_32", "bill_cancellation_consumption",
    "net_consumption_33_34", "wheeled_energy_units", "revenue",
    "interest_on_revenue_and_miscellaneous", "interest_on_tax", "tax",
    "p_g_surcharge", "total_sum_37_38_39_40_40a_104", "revenue_1",
    "miscellaneous_demand", "interest_on_revenue_and_miscellaneous_arrears",
    "p_g_surcharge_1", "total_sum_42_43_44_44a", "revenue_2",
    "miscellaneous_demand_1", "interest_on_revenue_and_miscellaneous_arrears_1",
    "interest_on_tax_1", "tax_1", "p_g_surcharge_2",
    "total_sum_46_47_48_49_50_50a_105", "revenue_adjustment",
    "miscellaneous_adjustment", "tax_adjustment", "p_g_surcharge_3",
    "total_adjustment_52_53_54_54a_106", "revenue_3", "miscellaneous",
    "interest_on_revenue_and_miscellaneous_1", "interest_on_tax_2", "tax_2",
    "p_g_surcharge_4", "total_sum_56_57_58_59_60_60a_107",
    "net_demand_revenue_current_demand_debits_bill_cancellations_42_",
    "fixed_charges", "energy_charges", "fppca_charges",
    "net_demand_tax_current_demand_debits_bill_cancellations_49_50_5",
    "p_g_surcharge_44a_50a_54a_60a", "revenue_4",
    "interest_on_revenue_and_miscellaneous_2", "interest_on_tax_3", "tax_3",
    "p_g_surcharge_5", "total_sum_64_65_66_67_67a_109", "revenue_adjustment_1",
    "miscellaneous_adjustment_1", "tax_adjustment_1", "p_g_surcharge_6",
    "total_adjustment_69_70_71_71a_110", "net_iod", "net_reversal_iod",
    "suspense_to_rr_transfer", "from_rr_transfer", "to_rr_transfer", "revenue_5",
    "interest_on_revenue", "interest_on_tax_4", "tax_4", "p_g_surcharge_7",
    "total_78_79_80_81_81a_111",
    "net_collection_collection_credits_suspense_to_rr_transfer_payme",
    "p_g_surcharge_67a_71a_81a", "write_off",
    "revenue_37_42_43_46_47_52_53_56_57_64_69_70_rev_77_rev_75_rev_7",
    "interest_on_revenue_and_miscellaneous_38_48_44_58_65_79_intonre",
    "interest_on_tax_39_49_59_66_80_intontax_75_intontax_77_intontax",
    "tax_40_50_54_71_67_81_60_tax_75_tax_77_tax_76",
    "p_g_surcharge_40a_44a_50a_54a_60a_67a_85a_81a_75_p_g_77_p_g_76_",
    "total_sum_85_86_87_88_88a_112", "average_cost_of_supply",
    "of_live_installations_10_12_100", "of_billed_installations_17_10_100",
    "of_dc_mnr_installations_16_10_100", "of_metered_consumption_31_32_33_100",
    "of_assessed_consumption_29_30_33_100",
    "of_collection_efficiency_with_adjustment_68_72_45_51_55_100",
    "of_collection_efficiency_without_adjustment_68_45_51_100",
    "ratio_of_arrears_w_r_t_demand_89_45_51_55", "demand_per_unit_45_51_55_33",
    "collection_per_unit_with_adjustment_68_72_33",
    "collection_per_unit_without_adjustment_68_33",
    "consumption_per_installation_33_10", "of_recovery_of_average_cost",
    "gst_and_tcs_ob", "gst_and_tcs_demand", "gst_and_tcs_debit_adjustment",
    "gst_and_tcs_bill_cancellation", "gst_and_tcs_net_demand_105_106_107",
    "gst_and_tcs_collection", "gst_and_tcs_credit_adjustment",
    "gst_and_tcs_payment_cancellation", "gst_and_tcs_cb", "balance_transfer_amount",
    "bill_cancellation_consumption",
]


def column_catalogue_text() -> str:
    """Render the curated columns as a compact list for the system prompt."""
    lines = []
    for c in COLUMNS:
        star = " [PREFER]" if c.get("meaning") and c.get("preferred") else ""
        lines.append(f"- {c['name']}{star}: {c['meaning']}")
    return "\n".join(lines)


def follow_up_topics_text() -> str:
    """A short, human-readable list of what CAN be asked, for grounding the
    follow-up suggester. Column name + the first sentence of its meaning so the
    model only proposes questions the data can actually answer."""
    lines = []
    for c in COLUMNS:
        first = (c.get("meaning") or "").split(".")[0].strip()
        lines.append(f"- {c['name']}: {first}")
    return "\n".join(lines)
