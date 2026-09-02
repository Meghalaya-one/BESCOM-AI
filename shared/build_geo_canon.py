"""
build_geo_canon.py — data-driven geography name canonicalization for the combined view
=======================================================================================
WHY: RAPDRP and "Non RAPDRP" name the SAME circle/division differently — different
casing, a ' Circle'/' Division' suffix on RAPDRP, spelling drift (Bangalore/Bengaluru,
Devanagere/Davanagere, Tumkur/Tumakuru) and even typos (BENGALURE). A hardcoded CASE
crosswalk kept missing new variants and producing half-empty pivot rows.

WHAT THIS DOES (run it whenever the source data changes):
  1. Reads every DISTINCT circle / division value from BOTH tables.
  2. Canonicalizes each name with a TOKEN-AWARE fuzzy rule:
       - strip LEVEL words (CIRCLE, DIVISION, SUBDIVISION, SUB, SECTION, ZONE, URBAN)
       - KEEP direction words (EAST/WEST/NORTH/SOUTH/RURAL/CENTRAL) as distinguishing
         (so 'Bengaluru East' and 'Bengaluru West' stay SEPARATE circles)
       - fuzzy-merge only the CORE city token (Levenshtein <= 2) so casing/spelling/typos
         collapse ('BENGALURE RURAL' == 'Bengaluru Rural')
  3. Writes two lookup tables: circle_canon(raw_circle -> circle_norm) and
     division_canon(raw_division -> division_norm).
  4. Recreates the bescom_combined view so it LEFT JOINs those tables, exposing
     circle_norm / division_norm columns. Any GROUP BY circle_norm then aligns the two
     datasets onto one row regardless of the raw spelling.

The result is data-driven: no name list is hardcoded here; re-running picks up any new
or misspelled value automatically. Print a report of every merge so drift is visible.

Usage:  python shared/build_geo_canon.py         (reads unified/.env for DATABASE_URL)
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))


def load_env():
    envp = os.path.join(REPO, "unified", ".env")
    if os.path.exists(envp):
        for line in open(envp):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))
    return (os.environ.get("DATABASE_URL") or os.environ.get("NEON_DATABASE_URL")
            or os.environ.get("DB_URL"))


LEVEL_WORDS = {"CIRCLE", "DIVISION", "SUBDIVISION", "SUB", "SECTION", "ZONE", "URBAN"}
DIR_WORDS = {"EAST", "WEST", "NORTH", "SOUTH", "RURAL", "CENTRAL"}
FUZZ = 2  # max Levenshtein distance to treat two core tokens as the same place


def _lev(a, b):
    m, n = len(a), len(b)
    d = list(range(n + 1))
    for i in range(1, m + 1):
        prev = d[0]
        d[0] = i
        for j in range(1, n + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return d[n]


def _split(name):
    """-> (core_string, sorted_direction_list). Strips level words, keeps dirs."""
    x = name.upper().strip()
    x = re.sub(r"[^A-Z0-9 ]", " ", x)
    x = x.replace("SUB DIVISION", "SUBDIVISION")
    toks = [t for t in x.split() if t and t not in LEVEL_WORDS]
    core = " ".join(t for t in toks if t not in DIR_WORDS)
    dirs = sorted(t for t in toks if t in DIR_WORDS)
    return core, dirs


def canonicalize(names):
    """names -> {raw_name: canonical_norm}. Fuzzy on core token, exact on direction."""
    core_reps = []          # canonical core strings seen so far
    mapping = {}
    for nm in sorted(set(n for n in names if n)):
        core, dirs = _split(nm)
        best, bestd = None, 99
        for rc in core_reps:
            d = _lev(core, rc)
            if d < bestd:
                bestd, best = d, rc
        if not (best is not None and bestd <= FUZZ):
            core_reps.append(core)
            best = core
        norm = (best + " " + " ".join(dirs)).strip()
        mapping[nm] = norm
    return mapping


def build(conn, level, table_out):
    cur = conn.cursor()
    cur.execute(f'SELECT DISTINCT {level} FROM "RAPDRP" '
                f'UNION SELECT DISTINCT {level} FROM "Non RAPDRP"')
    names = [r[0] for r in cur.fetchall() if r[0] is not None]
    mapping = canonicalize(names)

    cur.execute(f"DROP TABLE IF EXISTS {table_out}")
    cur.execute(f"CREATE TABLE {table_out} (raw text PRIMARY KEY, norm text NOT NULL)")
    cur.executemany(f"INSERT INTO {table_out}(raw, norm) VALUES (%s, %s)",
                    list(mapping.items()))

    # report
    groups = {}
    for raw, norm in mapping.items():
        groups.setdefault(norm, []).append(raw)
    print(f"\n=== {level}: {len(names)} raw -> {len(groups)} canonical ===")
    for norm, mem in sorted(groups.items()):
        if len(mem) > 1:
            print(f"  {norm:<22} <= {mem}")
    cur.close()
    return len(groups)


VIEW_SQL = r'''
DROP VIEW IF EXISTS bescom_combined;
CREATE VIEW bescom_combined AS
SELECT
    'RAPDRP'::text AS dataset,
    t.zone, t.circle, t.division, t.subdivision, t.section, t.tariff,
    COALESCE(cc.norm, UPPER(t.circle))     AS circle_norm,
    COALESCE(dc.norm, UPPER(t.division))   AS division_norm,
    t.voltage_class_kv::text               AS voltage_class_kv,
    t.active_installations, t.inactive_installations,
    t.total_installations                  AS total_installations,
    t.metered_installations, t.unmetered_installations, t.dc_mnr_installations,
    t.total_metered_unmetered              AS total_metered_unmetered,
    t.installations_billed, t.installations_unbilled,
    t.total_billed_unbilled                AS total_billed_unbilled,
    t.assessed_taxed_consumption, t.metered_taxed_consumption,
    t.total_consumption                    AS total_consumption,
    t.bill_cancellation_consumption,
    t.net_consumption                      AS net_consumption,
    t.ob_total_sum, t.net_demand_revenue, t.net_demand_tax,
    t.coll_revenue                         AS collection_revenue,
    t.net_collection, t.cb_total_sum, t.bc_total_sum, t.pc_total,
    t.write_off, t.net_iod,
    t.gst_tcs_net_demand, t.gst_tcs_collection, t.gst_tcs_cb
FROM "RAPDRP" t
LEFT JOIN circle_canon   cc ON cc.raw = t.circle
LEFT JOIN division_canon dc ON dc.raw = t.division

UNION ALL

SELECT
    'Non-RAPDRP'::text AS dataset,
    t.zone, t.circle, t.division, t.subdivision, t.section, t.tariff,
    COALESCE(cc.norm, UPPER(t.circle))     AS circle_norm,
    COALESCE(dc.norm, UPPER(t.division))   AS division_norm,
    t.voltage_class_kv::text               AS voltage_class_kv,
    t.active_installations, t.inactive_installations,
    t.total_installations_10_11            AS total_installations,
    t.metered_installations, t.unmetered_installations, t.dc_mnr_installations,
    t.total_metered_13_14                  AS total_metered_unmetered,
    t.installations_billed, t.installations_unbilled,
    t.total_billed_17_18                   AS total_billed_unbilled,
    t.assessed_taxed_consumption, t.metered_taxed_consumption,
    t.total_consumption_29_32              AS total_consumption,
    t.bill_cancellation_consumption,
    t.net_consumption_33_34                AS net_consumption,
    t.ob_total_sum, t.net_demand_revenue, t.net_demand_tax,
    t.col_revenue                          AS collection_revenue,
    t.net_collection, t.cb_total_sum, t.bc_total_sum, t.pc_total,
    t.write_off, t.net_iod,
    t.gst_tcs_net_demand, t.gst_tcs_collection, t.gst_tcs_cb
FROM "Non RAPDRP" t
LEFT JOIN circle_canon   cc ON cc.raw = t.circle
LEFT JOIN division_canon dc ON dc.raw = t.division;
'''


def main():
    dsn = load_env()
    if not dsn:
        print("No DATABASE_URL found in unified/.env", file=sys.stderr)
        sys.exit(1)
    import psycopg2
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    build(conn, "circle", "circle_canon")
    build(conn, "division", "division_canon")
    cur = conn.cursor()
    cur.execute(VIEW_SQL)
    cur.close()
    print("\nbescom_combined view recreated with circle_norm / division_norm.")
    conn.close()


if __name__ == "__main__":
    main()
