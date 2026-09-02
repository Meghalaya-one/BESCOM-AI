-- create_combined_view.sql
-- =========================
-- Builds the "bescom_combined" view that stacks the RAPDRP and "Non RAPDRP"
-- tables over their SHARED, canonical column set. Used by the unified backend
-- for questions that mention NEITHER dataset (the "combine both" case): because
-- the view UNIONs both tables' rows, a SUM/COUNT/GROUP BY over it naturally adds
-- the two datasets together.
--
-- A "dataset" tag column ('RAPDRP' | 'Non-RAPDRP') is added so a user CAN still
-- break the combined figure down by source if they ask.
--
-- Columns whose physical names differ between the two tables are aliased to a
-- single canonical name here (e.g. RAPDRP.net_consumption and
-- Non RAPDRP.net_consumption_33_34 both become net_consumption).
-- voltage_class_kv differs in TYPE (text vs numeric) so it is cast to text.
--
-- GEOGRAPHY NAME NORMALIZATION (added 2026-07-25):
-- The two sources name the SAME circle differently — RAPDRP splits Bengaluru into
-- 5 circles and suffixes ' CIRCLE' (e.g. 'Kolar Circle'), while Non-RAPDRP uses one
-- bare uppercase 'BANGALORE'/'KOLAR' and spells a few differently
-- (Davanagere/Devanagere, Ramanagar/Ramanagara, Tumkur/Tumakuru). So a raw
-- GROUP BY circle produced ~14 half-empty rows (each real circle appearing twice).
-- To make "RAPDRP and Non-RAPDRP <metric> circle wise" ALWAYS align onto one row
-- regardless of the exact SQL the LLM writes, a canonical `circle_norm` column is
-- computed here in the DATA layer via a CASE crosswalk (5 canonical circles).
-- `division_norm` is just UPPER(division) — division names already match across
-- sources (only casing differs); Bengaluru-area divisions are RAPDRP-only and
-- MAGADI is Non-RAPDRP-only, so those rows are legitimately one-sided.
-- Subdivisions share NO names across sources, so there is no subdivision_norm.
--
-- To refresh: this DROPs then CREATEs (column list changed — CREATE OR REPLACE
-- cannot insert columns mid-list).

DROP VIEW IF EXISTS bescom_combined;

CREATE VIEW bescom_combined AS
-- ---- RAPDRP ----
SELECT
    'RAPDRP'::text                         AS dataset,
    zone, circle, division, subdivision, section, tariff,
    -- canonical geography (see header): merges the two sources' differing names
    CASE
        -- Bengaluru: Non-RAPDRP only has 'BANGALORE RURAL', which is the SAME circle as
        -- RAPDRP's 'Bengaluru Rural Circle'. The other 4 RAPDRP Bengaluru circles
        -- (East/North/South/West) have no Non-RAPDRP counterpart, so they stay distinct.
        WHEN UPPER(circle) LIKE '%BANGALORE RURAL%' OR UPPER(circle) LIKE '%BENGALURU RURAL%' THEN 'BENGALURU RURAL'
        WHEN UPPER(circle) LIKE 'BENGALURU EAST%'   THEN 'BENGALURU EAST'
        WHEN UPPER(circle) LIKE 'BENGALURU NORTH%'  THEN 'BENGALURU NORTH'
        WHEN UPPER(circle) LIKE 'BENGALURU SOUTH%'  THEN 'BENGALURU SOUTH'
        WHEN UPPER(circle) LIKE 'BENGALURU WEST%'   THEN 'BENGALURU WEST'
        WHEN UPPER(circle) LIKE 'KOLAR%'                                    THEN 'KOLAR'
        WHEN UPPER(circle) LIKE 'DAVANAGERE%' OR UPPER(circle) LIKE 'DEVANAGERE%' THEN 'DAVANAGERE'
        WHEN UPPER(circle) LIKE 'RAMANAGAR%'                               THEN 'RAMANAGARA'
        WHEN UPPER(circle) LIKE 'TUMKUR%' OR UPPER(circle) LIKE 'TUMAKURU%' THEN 'TUMAKURU'
        ELSE UPPER(circle)
    END                                    AS circle_norm,
    UPPER(division)                        AS division_norm,
    voltage_class_kv::text                 AS voltage_class_kv,
    -- installations
    active_installations, inactive_installations,
    total_installations                    AS total_installations,
    metered_installations, unmetered_installations,
    dc_mnr_installations,
    total_metered_unmetered                AS total_metered_unmetered,
    installations_billed, installations_unbilled,
    total_billed_unbilled                  AS total_billed_unbilled,
    -- consumption
    assessed_taxed_consumption, metered_taxed_consumption,
    total_consumption                      AS total_consumption,
    bill_cancellation_consumption,
    net_consumption                        AS net_consumption,
    -- opening balance
    ob_total_sum,
    -- demand
    net_demand_revenue, net_demand_tax,
    -- collection
    coll_revenue                           AS collection_revenue,
    net_collection,
    -- closing balance / arrears
    cb_total_sum,
    -- adjustments / leakage (INR). NOTE: Non-RAPDRP bc_total_sum is all 0 in this
    -- extract, so combined bill-cancellation MONEY is effectively RAPDRP-only.
    bc_total_sum, pc_total,
    write_off, net_iod,
    -- gst/tcs
    gst_tcs_net_demand, gst_tcs_collection, gst_tcs_cb
FROM "RAPDRP"

UNION ALL

-- ---- Non-RAPDRP ----
SELECT
    'Non-RAPDRP'::text                     AS dataset,
    zone, circle, division, subdivision, section, tariff,
    CASE
        WHEN UPPER(circle) LIKE '%BANGALORE RURAL%' OR UPPER(circle) LIKE '%BENGALURU RURAL%' THEN 'BENGALURU RURAL'
        WHEN UPPER(circle) LIKE 'BENGALURU EAST%'   THEN 'BENGALURU EAST'
        WHEN UPPER(circle) LIKE 'BENGALURU NORTH%'  THEN 'BENGALURU NORTH'
        WHEN UPPER(circle) LIKE 'BENGALURU SOUTH%'  THEN 'BENGALURU SOUTH'
        WHEN UPPER(circle) LIKE 'BENGALURU WEST%'   THEN 'BENGALURU WEST'
        WHEN UPPER(circle) LIKE 'KOLAR%'                                    THEN 'KOLAR'
        WHEN UPPER(circle) LIKE 'DAVANAGERE%' OR UPPER(circle) LIKE 'DEVANAGERE%' THEN 'DAVANAGERE'
        WHEN UPPER(circle) LIKE 'RAMANAGAR%'                               THEN 'RAMANAGARA'
        WHEN UPPER(circle) LIKE 'TUMKUR%' OR UPPER(circle) LIKE 'TUMAKURU%' THEN 'TUMAKURU'
        ELSE UPPER(circle)
    END                                    AS circle_norm,
    UPPER(division)                        AS division_norm,
    voltage_class_kv::text                 AS voltage_class_kv,
    active_installations, inactive_installations,
    total_installations_10_11              AS total_installations,
    metered_installations, unmetered_installations,
    dc_mnr_installations,
    total_metered_13_14                    AS total_metered_unmetered,
    installations_billed, installations_unbilled,
    total_billed_17_18                     AS total_billed_unbilled,
    assessed_taxed_consumption, metered_taxed_consumption,
    total_consumption_29_32                AS total_consumption,
    bill_cancellation_consumption,
    net_consumption_33_34                  AS net_consumption,
    ob_total_sum,
    net_demand_revenue, net_demand_tax,
    col_revenue                            AS collection_revenue,
    net_collection,
    cb_total_sum,
    bc_total_sum, pc_total,
    write_off, net_iod,
    gst_tcs_net_demand, gst_tcs_collection, gst_tcs_cb
FROM "Non RAPDRP";
