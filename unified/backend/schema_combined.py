"""
schema_combined.py — COMBINED (RAPDRP + Non-RAPDRP)
===================================================
Describes the `bescom_combined` VIEW, which UNIONs the "RAPDRP" and "Non RAPDRP"
tables over their shared, canonical column set (see shared/create_combined_view.sql).

Used when a question mentions NEITHER dataset: because the view stacks both
tables' rows, a SUM/COUNT/GROUP BY over it naturally adds the two datasets
together. A `dataset` tag column lets a user break the combined figure back down
by source ('RAPDRP' / 'Non-RAPDRP') if they ask.

Same interface as the per-dataset schema_metadata modules (TABLE_NAME,
TABLE_LABEL, JUNK_COLUMNS, column_catalogue_text(), follow_up_topics_text()).
"""

# The view name is a plain lowercase identifier — no quoting needed.
TABLE_NAME = "bescom_combined"
TABLE_LABEL = "bescom_combined"
TOTAL_ROWS = 4871          # 1080 RAPDRP + 3791 Non-RAPDRP

JUNK_COLUMNS = []

COLUMNS = [
    {"name": "dataset",     "meaning": "Source dataset tag: 'RAPDRP' or 'Non-RAPDRP'. GROUP BY this to split a combined total by source."},

    # --- Geography (present in both; NOTE casing differs by source: RAPDRP uses
    #     'Bengaluru East Circle', Non-RAPDRP uses UPPERCASE 'BANGALORE') ---
    {"name": "zone",        "meaning": "Zone. Casing differs by source — match with UPPER(zone)=UPPER('value') to be safe."},
    {"name": "circle",      "meaning": "Circle (RAW name, spelling differs by source). Use UPPER(circle)=UPPER('value') to filter ONE circle."},
    {"name": "circle_norm", "meaning": "Canonical circle name unified across both sources (typos/suffixes/casing already merged). GROUP BY this to compare/align the two datasets circle-wise so each circle is ONE row."},
    {"name": "division",    "meaning": "Division (RAW name). Use UPPER(division)=UPPER('value') to filter ONE division."},
    {"name": "division_norm", "meaning": "Canonical division name unified across both sources. GROUP BY this to compare/align the two datasets division-wise."},
    {"name": "subdivision", "meaning": "Sub-division."},
    {"name": "section",     "meaning": "Section (finest reporting unit)."},
    {"name": "tariff",      "meaning": "Tariff class. Code sets differ by source; use tariff LIKE 'HT%'/'LT%' for a family."},
    {"name": "voltage_class_kv", "meaning": "Voltage class as TEXT (values differ by source)."},

    # --- Installations (additive) ---
    {"name": "active_installations",   "meaning": "Active service connections. 'consumers'/'customers'/'connections' -> this.", "preferred": True},
    {"name": "inactive_installations", "meaning": "Inactive connections."},
    {"name": "total_installations",    "meaning": "Total installations (active + inactive)."},
    {"name": "metered_installations",  "meaning": "Metered connections."},
    {"name": "unmetered_installations","meaning": "Unmetered connections."},
    {"name": "dc_mnr_installations",   "meaning": "DC/MNR installations = Disconnected or Meter-Not-Reading connections. Maps 'DC/MNR','DC MNR','DC-MNR','DCMNR','disconnected/meter not reading' (0 in RAPDRP rows; ~2.35 lakh in Non-RAPDRP)."},
    {"name": "total_metered_unmetered","meaning": "Total metered + unmetered."},
    {"name": "installations_billed",   "meaning": "Connections billed this period."},
    {"name": "installations_unbilled", "meaning": "Connections not billed."},
    {"name": "total_billed_unbilled",  "meaning": "Total billed + unbilled."},

    # --- Consumption (kWh) ---
    {"name": "assessed_taxed_consumption", "meaning": "Assessed (estimated) taxable consumption, kWh."},
    {"name": "metered_taxed_consumption",  "meaning": "Metered taxable consumption, kWh."},
    {"name": "total_consumption",          "meaning": "Total consumption, kWh."},
    {"name": "bill_cancellation_consumption", "meaning": "ENERGY (kWh) reversed by bill cancellations. This is UNITS, not money — use only if the user says cancelled units/energy/consumption."},
    {"name": "net_consumption",            "meaning": "Net consumption (headline energy figure), kWh.", "preferred": True},

    # --- Money (INR) ---
    {"name": "ob_total_sum",       "meaning": "Opening balance grand total (arrears carried in), INR."},
    {"name": "net_demand_revenue", "meaning": "NET DEMAND revenue portion, INR. Denominator of collection efficiency.", "preferred": True},
    {"name": "net_demand_tax",     "meaning": "NET DEMAND tax portion, INR. Add to net_demand_revenue for total net demand."},
    {"name": "collection_revenue", "meaning": "Collection — energy revenue component, INR."},
    {"name": "net_collection",     "meaning": "NET COLLECTION (headline collection figure), INR. Numerator of efficiency 'with adjustment'.", "preferred": True},
    {"name": "cb_total_sum",       "meaning": "CLOSING BALANCE grand total = arrears outstanding. Use for 'arrears','outstanding','dues','pending'.", "preferred": True},
    {"name": "bc_total_sum",       "meaning": "BILL CANCELLATION total, INR (money value of cancelled bills). Use for 'cancellations','amount/value cancelled','bill cancellation' (note: 0 across all Non-RAPDRP rows, so combined is effectively RAPDRP-only)."},
    {"name": "pc_total",           "meaning": "PAYMENT CANCELLATION total, INR (bounced / reversed payments). Use for 'payment cancellations','reversed payments'."},
    {"name": "write_off",          "meaning": "Amount written off, INR (note: 0 across all Non-RAPDRP rows)."},
    {"name": "net_iod",            "meaning": "Net Interest on Delayed payment (IOD), INR."},

    # --- GST/TCS (INR; note: 0 across all Non-RAPDRP rows) ---
    {"name": "gst_tcs_net_demand", "meaning": "GST/TCS net demand, INR."},
    {"name": "gst_tcs_collection", "meaning": "GST/TCS collected, INR."},
    {"name": "gst_tcs_cb",         "meaning": "GST/TCS closing balance (outstanding), INR."},
]

# Physical columns that exist in the view (for the read-only validator).
ALL_PHYSICAL_COLUMNS = [c["name"] for c in COLUMNS]


def column_catalogue_text() -> str:
    lines = []
    for c in COLUMNS:
        star = " [PREFER]" if c.get("preferred") else ""
        lines.append(f"- {c['name']}{star}: {c['meaning']}")
    return "\n".join(lines)


def follow_up_topics_text() -> str:
    lines = []
    for c in COLUMNS:
        first = (c.get("meaning") or "").split(".")[0].strip()
        lines.append(f"- {c['name']}: {first}")
    return "\n".join(lines)
