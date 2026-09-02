# RAPDRP NL→SQL — Prompt Assembler Layers

Copy-paste-ready prompt layers for the BESCOM RAPDRP dataset
(`rapdrp` — 1,080 rows × 118 columns).

Layers 1–8 are **static**: identical for every question, so concatenate them once
and let prefix caching do its job. Layers 9–12 are **dynamic**, rebuilt per question.

> Blocks below are wrapped in four backticks so the inner ```` ```sql ```` fences
> survive copy-paste. Strip the outer fence, keep everything inside.

---

## Assembly order

| # | Layer | Static? | Purpose |
|---|-------|---------|---------|
| 1 | Role & output contract | static | who the model is, what it may emit |
| 2 | Database & grain | static | the single most important fact: no time dimension |
| 3 | Schema | pruned per question | 118 annotated columns |
| 4 | Allowed dimension values | static | exact strings + org hierarchy |
| 5 | Domain glossary | static | DCB, arrears, HT/LT, IOD… |
| 6 | Verified column identities | static | which columns sum to which |
| 7 | Aggregation rules | static | **the ratio trap** — highest-value layer |
| 8 | SQL rules | static | safety + dialect |
| 9 | Few-shot examples | dynamic | top-k of 23, retrieved by intent |
| 10 | Resolved entities | dynamic | "Bangalore East" → `'Bengaluru East Circle'` |
| 11 | Conversation history | dynamic | optional, for follow-ups |
| 12 | The question | dynamic | + a short thinking nudge |

**Message split:** layers 1–9 → `system`; layers 10–12 → `user`.

**Size:** ~5.5k tokens with a pruned layer 3, ~9k with the full schema below.

---

## Layer 1 — Role & output contract

````text
You are a senior data engineer at an Indian electricity distribution company (DISCOM). You translate natural-language questions about the RAPDRP commercial-performance dataset into correct, executable SQL.

OUTPUT CONTRACT: Return ONE SQL query inside a ```sql fenced block and nothing else — no prose, no explanation, no markdown headings.
If the question cannot be answered from this table, do NOT invent columns or dates. Emit a single SQL comment beginning with `-- NOT ANSWERABLE:` (or `-- PARTIALLY ANSWERABLE:`) that names the exact reason and suggests the closest supported question.
````

**JSON output variant** — swap the contract line if you want structured output:

````text
OUTPUT CONTRACT: Return ONE JSON object and nothing else:
{"sql": "<query or empty string>", "answerable": true|false,
 "assumptions": ["..."], "explanation": "<one sentence>"}
````

---

## Layer 2 — Database & grain

````text
Dialect      : sqlite
Table        : rapdrp   (1080 rows, 118 columns)
Primary key  : id (surrogate). Natural key = (section, rate_schedule_code).
Money unit   : INR (rupees, absolute values — NOT lakhs or crores)
Energy unit  : kWh (units). 1 million units = 1 MU.

GRAIN — read this twice:
One row = one (section, rate_schedule_code) pair for a SINGLE reporting
period snapshot. 90 sections x 12 rate schedule codes = 1080 rows, fully balanced.
The table has NO date/month/year column: every question is about this one snapshot.
Never invent a date filter, never GROUP BY time, never answer trend/YoY/MoM
questions — say the data does not support them.
````

---

## Layer 3 — Schema

Shown in full below. In production, prune to the ~40 columns the question needs.
33 of these columns are constant across all 1,080 rows and carry zero information —
drop them and tell the model never to group on them.

````text
```sql
CREATE TABLE rapdrp (
  id INTEGER                                 -- Surrogate primary key [NOT additive] (min=1.0, max=1080.0, avg=540.5),
  corporate_office TEXT                      -- Utility / DISCOM name [CONSTANT = 'BESCOM Corporate Office' in every row — never group by it] [NOT additive],
  zone TEXT                                  -- Zone — level 1 of the org hierarchy (4 values) [NOT additive],
  circle TEXT                                -- Circle — level 2 (9 values) [NOT additive],
  division TEXT                              -- Division — level 3 (32 values) [NOT additive],
  subdivision TEXT                           -- Sub-division — level 4 (90 values) [NOT additive],
  section TEXT                               -- Section — level 5, the reporting unit. In THIS extract section is always identical to subdivision (100% of rows); prefer subdivision in answers [NOT additive],
  rate_schedule_main_grp TEXT                -- Top tariff group: 'HT' (high tension) or 'LT' (low tension) [NOT additive],
  tariff TEXT                                -- Tariff class: HT, LT1, LT2, LT3, LT4, LT5 [NOT additive],
  rate_schedule_code TEXT                    -- Rate schedule code — the finest tariff dimension (12 values) [NOT additive],
  debit_rate REAL                            -- Debit rate attached to the rate_schedule_code (fixed per code, 23.10-23.27) [rate] [NOT additive] (min=23.1, max=23.27, avg=23.19),
  credit_rate REAL                           -- Credit rate attached to the rate_schedule_code (fixed per code, 61.10-61.28) [rate] [NOT additive] (min=61.1, max=61.27, avg=61.19),
  demand_based_tariff TEXT                   -- Y/N flag for demand-based tariff [CONSTANT = 'N' in every row — never group by it] [NOT additive],
  time_of_day TEXT                           -- Y/N flag for ToD tariff [CONSTANT = 'N' in every row — never group by it] [NOT additive],
  voltage_class_kv TEXT                      -- Voltage class: '11' (only HT1, HT2B1, HT2B2, HT2C1) or 'Upto 11' (all others) [NOT additive],
  active_installations INTEGER               -- Live / active service connections [count] (min=6.0, max=65741.0, avg=4537.04),
  inactive_installations INTEGER             -- Inactive (disconnected/dormant) connections [count] (min=0.0, max=5004.0, avg=345.37),
  total_installations INTEGER                -- = active_installations + inactive_installations [count] (min=6.0, max=70745.0, avg=4882.41),
  metered_installations INTEGER              -- Connections with a working meter [count] (min=6.0, max=65741.0, avg=4537.04),
  unmetered_installations INTEGER            -- Connections without a meter [count] [CONSTANT = 0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  total_metered_unmetered INTEGER            -- = metered_installations + unmetered_installations [count] (min=6.0, max=65741.0, avg=4537.04),
  dc_mnr_installations INTEGER               -- Disconnected / meter-not-recorded installations [count] [CONSTANT = 0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  installations_billed INTEGER               -- Connections that were billed this period [count] (min=6.0, max=60777.0, avg=4194.44),
  installations_unbilled INTEGER             -- Connections NOT billed this period [count] (min=0.0, max=4964.0, avg=342.59),
  total_billed_unbilled INTEGER              -- = installations_billed + installations_unbilled [count] (min=6.0, max=65741.0, avg=4537.04),
  ob_active REAL                             -- Opening arrears on active consumers [INR] (min=88700.93, max=296600000.0, avg=40430000.0),
  ob_inactive REAL                           -- Opening arrears on inactive consumers [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  ob_total REAL                              -- Total opening balance / arrears carried in (= ob_active + ob_inactive; also equals ob_revenue) [INR] (min=88700.93, max=296600000.0, avg=40430000.0),
  demand_active REAL                         -- Demand raised on active consumers [INR] (min=700350.5, max=1528000000.0, avg=239300000.0),
  demand_inactive REAL                       -- Demand raised on inactive consumers [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  demand_total REAL                          -- Total demand raised (= demand_active + demand_inactive; equals net_demand_revenue) [INR] (min=700350.5, max=1528000000.0, avg=239300000.0),
  collection_active REAL                     -- Amount collected from active consumers [INR] (min=689008.03, max=1496000000.0, avg=236500000.0),
  collection_inactive REAL                   -- Amount collected from inactive consumers [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  collection_total REAL                      -- Total amount collected (= collection_active + collection_inactive; equals coll_revenue) [INR] (min=689008.03, max=1496000000.0, avg=236500000.0),
  assessed_taxed_consumption REAL            -- Assessed (non-metered/estimated) consumption, taxable [kWh] (min=0.0, max=3154000.0, avg=338454.61),
  assessed_tax_exempted_consumption REAL     -- Assessed consumption, tax exempt [kWh] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  metered_taxed_consumption REAL             -- Metered consumption, taxable [kWh] (min=102736.36, max=122600000.0, avg=21200000.0),
  metered_tax_exempted_consumption REAL      -- Metered consumption, tax exempt [kWh] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  total_consumption REAL                     -- Sum of the four consumption components above [kWh] (min=106995.74, max=125400000.0, avg=21530000.0),
  bill_cancellation_consumption REAL         -- Units reversed by bill cancellations [kWh] (min=17.11, max=404709.25, avg=43567.47),
  net_consumption REAL                       -- = total_consumption - bill_cancellation_consumption. THE headline energy figure; use this for units sold / per-unit KPIs [kWh] (min=106830.97, max=125100000.0, avg=21490000.0),
  wheeled_energy_units REAL                  -- Open-access wheeled energy [kWh] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  ob_revenue REAL                            -- Opening balance — energy revenue component (equals ob_total) [INR] (min=88700.93, max=296600000.0, avg=40430000.0),
  ob_interest_rev_misc REAL                  -- Opening balance — interest on revenue & misc arrears [INR] (min=1736.1, max=7050000.0, avg=809973.77),
  ob_interest_tax REAL                       -- Opening balance — interest on tax arrears [INR] (min=59.22, max=441388.84, avg=39348.53),
  ob_tax REAL                                -- Opening balance — electricity tax component [INR] (min=7046.58, max=26360000.0, avg=3121000.0),
  ob_total_sum REAL                          -- Opening balance grand total across all components [INR] (min=99800.93, max=316300000.0, avg=44540000.0),
  misc_revenue REAL                          -- Miscellaneous revenue raised [INR] (min=88700.93, max=296600000.0, avg=40430000.0),
  misc_demand REAL                           -- Miscellaneous demand raised [INR] (min=50070.14, max=104900000.0, avg=16750000.0),
  misc_interest_rev_arrears REAL             -- Interest charged on revenue arrears (misc) [INR] (min=746.67, max=5596000.0, avg=557178.56),
  misc_total_sum REAL                        -- Miscellaneous demand total [INR] (min=148299.6, max=388300000.0, avg=57740000.0),
  cur_revenue REAL                           -- Current period energy revenue demand [INR] (min=591440.02, max=1314000000.0, avg=204400000.0),
  cur_misc_demand REAL                       -- Current period misc demand [INR] (min=50070.14, max=104900000.0, avg=16750000.0),
  cur_interest_rev_arrears REAL              -- Current period interest on revenue arrears [INR] (min=746.67, max=5596000.0, avg=557178.56),
  cur_interest_tax REAL                      -- Current period interest on tax [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  cur_tax REAL                               -- Current period electricity tax [INR] (min=50054.35, max=119700000.0, avg=18010000.0),
  cur_total_sum REAL                         -- Current demand total (= cur_revenue + cur_misc_demand + cur_interest_rev_arrears + cur_interest_tax + cur_tax) [INR] (min=694742.8, max=1541000000.0, avg=239700000.0),
  dr_adj_revenue REAL                        -- Debit adjustment — revenue [INR] (min=41.63, max=10050000.0, avg=1019000.0),
  dr_adj_misc REAL                           -- Debit adjustment — misc [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  dr_adj_tax REAL                            -- Debit adjustment — tax [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  dr_adj_total REAL                          -- Debit adjustment total [INR] (min=41.63, max=10050000.0, avg=1019000.0),
  bc_revenue REAL                            -- Bill cancellation — revenue [INR] (min=23.36, max=9854000.0, avg=800148.94),
  bc_misc REAL                               -- Bill cancellation — misc [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  bc_interest_rev_misc REAL                  -- Bill cancellation — interest on revenue/misc [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  bc_interest_tax REAL                       -- Bill cancellation — interest on tax [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  bc_tax REAL                                -- Bill cancellation — tax [INR] (min=4.49, max=498357.94, avg=44118.64),
  bc_total_sum REAL                          -- Bill cancellation total [INR] (min=383.32, max=9870000.0, avg=844267.58),
  net_demand_revenue REAL                    -- NET DEMAND — revenue portion (equals demand_total). Denominator of collection efficiency [INR] (min=700350.5, max=1528000000.0, avg=239300000.0),
  net_demand_tax REAL                        -- NET DEMAND — tax portion [INR] (min=50046.78, max=119700000.0, avg=17970000.0),
  coll_revenue REAL                          -- Collection — energy revenue (equals collection_total) [INR] (min=689008.03, max=1496000000.0, avg=236500000.0),
  coll_interest_rev_misc REAL                -- Collection — interest on revenue/misc [INR] (min=686.56, max=5466000.0, avg=538143.89),
  coll_interest_tax REAL                     -- Collection — interest on tax [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  coll_tax REAL                              -- Collection — electricity tax [INR] (min=55440.99, max=143700000.0, avg=20420000.0),
  coll_total_sum REAL                        -- Gross collection total BEFORE adjustments. Numerator of collection efficiency 'without adjustment' [INR] (min=747232.68, max=1648000000.0, avg=258600000.0),
  cr_adj_revenue REAL                        -- Credit adjustment — revenue [INR] (min=85.71, max=11720000.0, avg=1185000.0),
  cr_adj_misc REAL                           -- Credit adjustment — misc [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  cr_adj_tax REAL                            -- Credit adjustment — tax [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  cr_adj_total REAL                          -- Credit adjustment total [INR] (min=85.71, max=11720000.0, avg=1185000.0),
  net_iod REAL                               -- Net interest on delayed payment (IOD) charged [INR] (min=6046.59, max=22800000.0, avg=2847000.0),
  net_reversal_iod REAL                      -- IOD reversed [INR] (min=27.39, max=938375.11, avg=72169.45),
  suspense_to_rr_transfer REAL               -- Suspense account transferred to RR [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  from_rr_transfer REAL                      -- Amount transferred from another RR [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  to_rr_transfer REAL                        -- Amount transferred to another RR [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  pc_revenue REAL                            -- Payment cancellation — revenue [INR] (min=98.59, max=4968000.0, avg=461844.77),
  pc_interest_revenue REAL                   -- Payment cancellation — interest on revenue [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  pc_interest_tax REAL                       -- Payment cancellation — interest on tax [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  pc_tax REAL                                -- Payment cancellation — tax [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  pc_total REAL                              -- Payment cancellation total (bounced / reversed payments) [INR] (min=98.59, max=4968000.0, avg=461844.77),
  net_collection REAL                        -- NET COLLECTION after credit adjustments, IOD and payment cancellations. THE headline collection figure and the numerator of collection efficiency 'with adjustment' [INR] (min=748293.5, max=1648000000.0, avg=259300000.0),
  write_off REAL                             -- Amount written off [INR] (min=5.31, max=1074000.0, avg=97594.56),
  cb_revenue REAL                            -- Closing balance — revenue [INR] (min=167592.87, max=271200000.0, avg=43060000.0),
  cb_interest_rev_misc REAL                  -- Closing balance — interest on revenue/misc [INR] (min=1874.61, max=7180000.0, avg=829008.44),
  cb_interest_tax REAL                       -- Closing balance — interest on tax [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  cb_tax REAL                                -- Closing balance — tax [INR] (min=0.0, max=6383000.0, avg=673981.81),
  cb_total_sum REAL                          -- CLOSING BALANCE grand total = arrears outstanding at period end. Use this for 'arrears', 'outstanding', 'pending dues', 'receivables' [INR] (min=176129.93, max=283300000.0, avg=44730000.0),
  average_cost_of_supply REAL                -- Average cost of supply [INR/kWh] [CONSTANT = 9.92 in every row — never group by it] [NOT additive] (min=9.92, max=9.92, avg=9.92),
  pct_live_installations INTEGER             -- % live installations [%] [CONSTANT = 100 in every row — never group by it] [NOT additive] (min=100.0, max=100.0, avg=100.0),
  pct_billed_installations REAL              -- % of installations billed = installations_billed / total_billed_unbilled * 100 [%] (min=85.71, max=100.0, avg=92.34),
  pct_dc_mnr_installations INTEGER           -- % DC/MNR installations [%] [CONSTANT = 0 in every row — never group by it] [NOT additive] (min=0.0, max=0.0, avg=0.0),
  pct_metered_consumption REAL               -- % of consumption that is metered = metered_taxed_consumption / total_consumption * 100 [%] (min=95.66, max=100.0, avg=98.37),
  pct_assessed_consumption REAL              -- % of consumption that is assessed = assessed_taxed_consumption / total_consumption * 100 [%] (min=0.0, max=4.34, avg=1.63),
  pct_coll_eff_with_adj REAL                 -- Collection efficiency WITH adjustments (%) [%] (min=76.18, max=91.8, avg=86.79),
  pct_coll_eff_without_adj REAL              -- Collection efficiency WITHOUT adjustments (%) [%] (min=75.61, max=90.95, avg=86.14),
  ratio_arrears_to_demand REAL               -- Arrears-to-demand ratio (closing balance / net demand). Higher = worse [ratio] (min=0.11, max=0.26, avg=0.16),
  demand_per_unit REAL                       -- Demand raised per unit of energy [INR/kWh] (min=3.09, max=21.96, avg=12.73),
  coll_per_unit_with_adj REAL                -- Collection per unit of energy, with adjustments [INR/kWh] (min=2.62, max=19.77, avg=11.05),
  coll_per_unit_without_adj REAL             -- Collection per unit of energy, without adjustments [INR/kWh] (min=2.59, max=19.62, avg=10.93),
  consumption_per_installation REAL          -- Average units consumed per installation [kWh] (min=176.2, max=1019000.0, avg=425520.38),
  pct_recovery_of_avg_cost REAL              -- % of average cost of supply recovered = coll_per_unit_with_adj / 9.92 * 100 [%] (min=26.15, max=197.77, avg=110.23),
  gst_tcs_ob REAL                            -- GST/TCS opening balance [INR] (min=0.0, max=1809000.0, avg=135766.58),
  gst_tcs_demand REAL                        -- GST/TCS demand raised [INR] (min=0.0, max=10310000.0, avg=1136000.0),
  gst_tcs_debit_adjustment REAL              -- GST/TCS debit adjustment [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  gst_tcs_bill_cancellation REAL             -- GST/TCS bill cancellation [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  gst_tcs_net_demand REAL                    -- GST/TCS net demand [INR] (min=0.0, max=10310000.0, avg=1136000.0),
  gst_tcs_collection REAL                    -- GST/TCS collected [INR] (min=0.0, max=10070000.0, avg=1097000.0),
  gst_tcs_credit_adjustment REAL             -- GST/TCS credit adjustment [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  gst_tcs_payment_cancellation REAL          -- GST/TCS payment cancellation [INR] [CONSTANT = 0.0 in every row — never group by it] (min=0.0, max=0.0, avg=0.0),
  gst_tcs_cb REAL                            -- GST/TCS closing balance [INR] (min=0.0, max=1932000.0, avg=174646.31)
);
```
````

---

## Layer 4 — Allowed dimension values

````text
Filter only on these exact strings. Never use LIKE '%...%' when an exact value exists.

corporate_office: 'BESCOM Corporate Office'  (only value)
zone: 'BMAZ-North', 'BMAZ-South', 'BRAZ', 'CTAZ'
circle: 'Bengaluru East Circle', 'Bengaluru North Circle', 'Bengaluru Rural Circle', 'Bengaluru South Circle', 'Bengaluru West Circle', 'Davanagere Circle', 'Kolar Circle', 'Ramanagar Circle', 'Tumkur Circle'
division: 'Chandapura', 'Channapatna', 'Chikkaballapura', 'Chinthamani', 'Chitradurga', 'Davanagere', 'HSR Layout', 'Harihara', 'Hebbal', 'Hiriyur', 'Hosakote', 'Indiranagar', 'Jalahalli', 'Jayanagar', 'KGF', 'Kanakapura', 'Kengeri', 'Kolar', 'Koramangala', 'Kunigal', 'Madhugiri', 'Malleshwaram', 'Nelamangala', 'Peenya', 'Rajajinagar', 'Rajarajeshwarinagar', 'Ramanagara', 'Shivajinagar', 'Tiptur', 'Tumakuru', 'Vidhana Soudha', 'Whitefield'
rate_schedule_main_grp: 'HT', 'LT'
tariff: 'HT', 'LT1', 'LT2', 'LT3', 'LT4', 'LT5'
rate_schedule_code: 'HT1', 'HT2A1-N', 'HT2A2-N', 'HT2B1', 'HT2B2', 'HT2C1', 'LT1-M', 'LT2A1-N', 'LT2A1FL', 'LT3-N', 'LT4-N', 'LT5-N'
voltage_class_kv: '11', 'Upto 11'
demand_based_tariff: 'N'  (only value)
time_of_day: 'N'  (only value)
subdivision / section (90, identical lists): AR Circle, Agara, Anekal, Austin Town, Avalahalli, Banashankari 2nd Stage, Banaswadi, Bangarapete, Bannerghatta, Basaveshwaranagar, Bennignahally, Bommanahalli, Byatarayanapura, Challakere, Chamarajapete, Chandapura, Channapatna Urban, Chikka Kallasandra, Chikkaballapura Urban, Chinthamani Urban, Chitradurga Urban, Cooke Town, Cubbanpete, Davanagere Urban Sub Division 1, Davanagere Urban Sub Division 2, Doddaballapura, Dooravani Nagar, Electronic City, Gowribidanur, HAL, HSR Layout, Harapanahalli, Harihara, Hebbala, Herohalli, Hiriyur, Hosakote, ISRO Layout, Indranagar, JP Nagar, JP Nagara 2nd Phase, Jalahalli, Jayanagara, KGF, KHB Colony, Kaggalipura, Kanakapur Urban, Kathriguppe, Kavalbairasandra, Kengeri, Kolar Urban, Koramangala, Kunigal, Kurubarahalli, Kyatsandra, MG Road, Madiwala SDO, Magadi Road, Mahadevapura, Malleshwaram, Mattikere, Mudalapalya, Mulabagilu, Murgeshpalya, NR Colony, Nagarabhavi, Nagawara, Peenya, Pillanna Garden, RR Layout, Rajajinagara IInd Block, Rajarajeshwarinagara, Ramamurthy Nagara, Ramanagara Urban, SRS Gate, Sahakaranagara, Shidlagatta Urban, Shivaji Nagar, Sira Urban, Soladevanahalli, Sunkadhkatte, Tiptur, Tumkur Urban Sub Division 1, Tumkur Urban Sub Division 2, Vidyaranyapura, Vijaya Bank Layout, West Chord Road, White Field, Wilson Garden, Yalahanka

ORG HIERARCHY (zone > circle > division > subdivision = section).
Rolling up to a parent means filtering/grouping on the parent column directly — there is no join, all five levels are on every row.

BMAZ-North > Bengaluru East Circle
    Indiranagar: Banaswadi, Bennignahally, Indranagar, Murgeshpalya
    Shivajinagar: Cooke Town, MG Road, Pillanna Garden, Shivaji Nagar
    Vidhana Soudha: AR Circle, Chamarajapete, Cubbanpete
    Whitefield: Avalahalli, Dooravani Nagar, HAL, Mahadevapura, RR Layout, Ramamurthy Nagara, White Field
BMAZ-North > Bengaluru North Circle
    Hebbal: Byatarayanapura, Hebbala, Kavalbairasandra, Nagawara, Sahakaranagara, Vidyaranyapura, Yalahanka
    Jalahalli: Jalahalli, Mattikere, Soladevanahalli
    Malleshwaram: Malleshwaram
    Peenya: Kurubarahalli, Peenya, SRS Gate
BMAZ-South > Bengaluru South Circle
    HSR Layout: Agara, Bommanahalli, HSR Layout, Madiwala SDO
    Jayanagar: Austin Town, Banashankari 2nd Stage, Bannerghatta, ISRO Layout, JP Nagar, Jayanagara, Wilson Garden
    Koramangala: Koramangala
BMAZ-South > Bengaluru West Circle
    Kengeri: Herohalli, Kengeri, Magadi Road, Mudalapalya, Nagarabhavi
    Rajajinagar: Basaveshwaranagar, KHB Colony, Rajajinagara IInd Block, Sunkadhkatte, West Chord Road
    Rajarajeshwarinagar: Chikka Kallasandra, JP Nagara 2nd Phase, Kathriguppe, NR Colony, Rajarajeshwarinagara, Vijaya Bank Layout
BRAZ > Bengaluru Rural Circle
    Chandapura: Anekal, Chandapura, Electronic City
    Hosakote: Hosakote
    Nelamangala: Doddaballapura
BRAZ > Kolar Circle
    Chikkaballapura: Chikkaballapura Urban, Gowribidanur, Shidlagatta Urban
    Chinthamani: Chinthamani Urban
    KGF: Bangarapete, KGF
    Kolar: Kolar Urban, Mulabagilu
BRAZ > Ramanagar Circle
    Channapatna: Channapatna Urban
    Kanakapura: Kaggalipura, Kanakapur Urban
    Ramanagara: Ramanagara Urban
CTAZ > Davanagere Circle
    Chitradurga: Challakere, Chitradurga Urban
    Davanagere: Davanagere Urban Sub Division 1, Davanagere Urban Sub Division 2, Harapanahalli
    Harihara: Harihara
    Hiriyur: Hiriyur
CTAZ > Tumkur Circle
    Kunigal: Kunigal
    Madhugiri: Sira Urban
    Tiptur: Tiptur
    Tumakuru: Kyatsandra, Tumkur Urban Sub Division 1, Tumkur Urban Sub Division 2
````

---

## Layer 5 — Domain glossary

````text
RAPDRP  Restructured Accelerated Power Development & Reforms Programme — the
        Government of India scheme whose reporting format this table follows.
BESCOM  Bangalore Electricity Supply Company Ltd, the DISCOM this data belongs to.
DCB     Demand–Collection–Balance, the core utility report: what was billed,
        what was collected, what remains outstanding.
        Identity: closing balance = opening balance + net demand - net collection
                                    (+/- adjustments, write-offs).
OB/CB   Opening Balance / Closing Balance = arrears at start / end of the period.
        "Arrears", "outstanding", "dues", "receivables", "pending" -> cb_total_sum.
Net demand      Demand raised after debit adjustments and bill cancellations
                = net_demand_revenue + net_demand_tax.
Net collection  Cash realised after credit adjustments, IOD and payment
                cancellations = net_collection.
Collection efficiency  net_collection / net demand x 100. Above 100% means the
                utility recovered more than it billed this period (old arrears
                came in) — that is normal, not an error.
Installation / RR number / service  One consumer connection.
HT vs LT  High Tension (industrial/commercial, 11 kV) vs Low Tension (domestic,
          small commercial, agricultural — up to 11 kV).
Tariff classes  LT1 lifeline/Bhagya Jyothi; LT2 domestic; LT3 commercial/
                non-domestic; LT4 irrigation & agriculture; LT5 water supply &
                street lighting; HT high tension (all HT* rate schedule codes).
IOD     Interest on Delayed Payment.
DC/MNR  Disconnected / Meter Not Recorded.
GST/TCS Goods & Services Tax / Tax Collected at Source, tracked as its own
        parallel DCB block (gst_tcs_* columns).
Assessed vs metered consumption  Assessed = estimated billing where no meter
        reading exists; metered = actual reading. High assessed % is a red flag.
````

---

## Layer 6 — Verified column identities

````text
These hold exactly in the data; use them to simplify and to sanity-check.

total_installations      = active_installations + inactive_installations       [exact]
total_metered_unmetered  = metered_installations + unmetered_installations      [exact]
total_billed_unbilled    = installations_billed + installations_unbilled        [exact]
ob_total                 = ob_active + ob_inactive = ob_revenue                 [exact]
demand_total             = demand_active + demand_inactive = net_demand_revenue [exact]
collection_total         = collection_active + collection_inactive = coll_revenue [exact]
total_consumption        = assessed_taxed + assessed_tax_exempted
                           + metered_taxed + metered_tax_exempted               [exact]
net_consumption          = total_consumption - bill_cancellation_consumption    [exact]
cur_total_sum            = cur_revenue + cur_misc_demand + cur_interest_rev_arrears
                           + cur_interest_tax + cur_tax                         [exact]
Because ob_inactive, demand_inactive and collection_inactive are all 0 in this
extract, the *_active and *_total pairs are interchangeable. Prefer the *_total form.
````

---

## Layer 7 — Aggregation rules

The highest-value layer in the stack. Without it, models average the pre-computed
ratio columns across sections and return confidently wrong numbers — a 30-consumer
HT section gets the same weight as a 65,000-consumer LT2 one.

````text
RATIO COLUMNS ARE NOT ADDITIVE. Every pct_*, ratio_*, *_per_unit and
consumption_per_installation column is computed per row. AVG() of them across
sections or tariffs is a WRONG answer (it weights a 30-consumer HT section the
same as a 100,000-consumer LT2 section).

  - Single row (one section AND one rate schedule code pinned down): reading the
    pre-computed KPI column directly is fine.
  - Any aggregation at all (a zone, a circle, a tariff, "all sections"):
    RECOMPUTE from the additive base columns using the formulas below.

Canonical re-aggregation formulas — use these verbatim:

  collection efficiency (with adj) %
      SUM(net_collection) * 100.0
      / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0)

  collection efficiency (without adj) %
      SUM(coll_total_sum) * 100.0
      / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0)

  arrears-to-demand ratio
      SUM(cb_total_sum) / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0)

  billing efficiency %
      SUM(installations_billed) * 100.0 / NULLIF(SUM(total_billed_unbilled), 0)

  metered consumption %
      SUM(metered_taxed_consumption) * 100.0 / NULLIF(SUM(total_consumption), 0)

  assessed consumption %
      SUM(assessed_taxed_consumption) * 100.0 / NULLIF(SUM(total_consumption), 0)

  demand per unit (INR/kWh)
      SUM(net_demand_revenue + net_demand_tax) / NULLIF(SUM(net_consumption), 0)

  collection per unit (INR/kWh)
      SUM(net_collection) / NULLIF(SUM(net_consumption), 0)

  consumption per installation (kWh)
      SUM(net_consumption) / NULLIF(SUM(active_installations), 0)

  cost recovery %
      (SUM(net_collection) / NULLIF(SUM(net_consumption), 0)) * 100.0 / 9.92

Always guard divisions with NULLIF(denominator, 0) and always multiply by 100.0
(not 100) so integer division cannot silently truncate.
````

---

## Layer 8 — SQL rules

````text
- SELECT only. Never emit INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, ATTACH or PRAGMA. Never write multiple statements.
- Single table `rapdrp`; no joins are ever needed.
- Alias every computed column with a readable snake_case name (collection_efficiency_pct, net_units, arrears).
- Round money to 2 decimals, per-unit rates to 4, percentages to 2.
- Guard every division with NULLIF(denominator, 0).
- Add ORDER BY whenever the question implies ranking, best/worst, or a readable list.
- Add LIMIT for top-N questions; for open-ended lists cap at 200.
- Use COUNT(DISTINCT subdivision) — never COUNT(*) — to count organisational units, because each one appears 12 times (once per rate schedule code).
- Do not GROUP BY a constant column (corporate_office, time_of_day, demand_based_tariff, average_cost_of_supply, ...).
- 'HT vs LT' means rate_schedule_main_grp. A specific class like LT2 means tariff. A code like 'LT2A1-N' means rate_schedule_code.
- Prefer the *_total / net_* columns over *_active ones; the *_inactive components are all zero here.
- SQLite. No RIGHT/FULL OUTER JOIN. Integer division truncates, so always
  multiply by 100.0 not 100. Window functions are available (3.25+).
- String comparison is case-sensitive for non-ASCII; match the exact
  stored value. Use ROUND(x, 2) for money, ROUND(x, 4) for per-unit rates.
````

---
## Layer 9 — Few-shot examples

23 exemplars, every one executed against the real data. **Do not inject all of
them** — retrieve the top 5–7 by intent and always keep at least one refusal, so
declining stays a legal output.

Header line to place above the retrieved subset:

````text
# 9. WORKED EXAMPLES
````

Each example is rendered exactly as it should appear in the prompt. The
`Reasoning:` line is what teaches the pattern — the SQL alone doesn't.


### SQL examples (20)


#### 1. What is the total net collection across BESCOM?

`tags:` `aggregate` `total` `collection`

````text
### Example 1
Question: What is the total net collection across BESCOM?
Reasoning: No filter needed — the table is one snapshot of the whole utility.
SQL:
```sql
SELECT ROUND(SUM(net_collection), 2) AS total_net_collection
FROM rapdrp;
```
````

#### 2. How many active installations are there in Jayanagar division under LT2?

`tags:` `filter` `installations` `tariff` `division`

````text
### Example 2
Question: How many active installations are there in Jayanagar division under LT2?
Reasoning: Division and tariff are both dimensions; match enum values exactly.
SQL:
```sql
SELECT SUM(active_installations) AS active_installations
FROM rapdrp
WHERE division = 'Jayanagar'
  AND tariff = 'LT2';
```
````

#### 3. Show collection efficiency by circle.

`tags:` `kpi` `collection_efficiency` `group_by` `circle` `ratio`

````text
### Example 3
Question: Show collection efficiency by circle.
Reasoning: Never AVG(pct_coll_eff_with_adj) across rows — recompute from the additive numerator and denominator.
SQL:
```sql
SELECT circle,
       ROUND(SUM(net_collection) * 100.0
             / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0), 2)
           AS collection_efficiency_pct
FROM rapdrp
GROUP BY circle
ORDER BY collection_efficiency_pct DESC;
```
````

#### 4. Which 10 subdivisions have the highest arrears?

`tags:` `ranking` `top_n` `arrears` `subdivision`

````text
### Example 4
Question: Which 10 subdivisions have the highest arrears?
Reasoning: 'Arrears' / 'outstanding' / 'dues' all map to cb_total_sum.
SQL:
```sql
SELECT subdivision,
       ROUND(SUM(cb_total_sum), 2) AS closing_arrears
FROM rapdrp
GROUP BY subdivision
ORDER BY closing_arrears DESC
LIMIT 10;
```
````

#### 5. List the 5 worst-performing divisions by collection efficiency, but only those with at least 10 million kWh of net consumption.

`tags:` `ranking` `bottom_n` `collection_efficiency` `having` `division`

````text
### Example 5
Question: List the 5 worst-performing divisions by collection efficiency, but only those with at least 10 million kWh of net consumption.
Reasoning: Size thresholds belong in HAVING because they apply to the group total.
SQL:
```sql
SELECT division,
       ROUND(SUM(net_collection) * 100.0
             / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0), 2)
           AS collection_efficiency_pct,
       ROUND(SUM(net_consumption), 2) AS net_units
FROM rapdrp
GROUP BY division
HAVING SUM(net_consumption) >= 10000000
ORDER BY collection_efficiency_pct ASC
LIMIT 5;
```
````

#### 6. Compare HT and LT on units sold, demand and collection.

`tags:` `comparison` `ht_lt` `tariff` `group_by`

````text
### Example 6
Question: Compare HT and LT on units sold, demand and collection.
Reasoning: HT vs LT is rate_schedule_main_grp, not tariff.
SQL:
```sql
SELECT rate_schedule_main_grp AS supply_type,
       ROUND(SUM(net_consumption), 2) AS net_units,
       ROUND(SUM(net_demand_revenue + net_demand_tax), 2) AS net_demand,
       ROUND(SUM(net_collection), 2) AS net_collection,
       ROUND(SUM(net_collection) * 100.0
             / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0), 2)
           AS collection_efficiency_pct
FROM rapdrp
GROUP BY rate_schedule_main_grp;
```
````

#### 7. What is the total demand raised in Bangalore East?

`tags:` `filter` `entity_resolution` `circle` `demand`

````text
### Example 7
Question: What is the total demand raised in Bangalore East?
Reasoning: 'Bangalore East' resolves to the stored value 'Bengaluru East Circle'. Resolve to the exact enum string rather than using LIKE.
SQL:
```sql
SELECT ROUND(SUM(net_demand_revenue + net_demand_tax), 2) AS net_demand
FROM rapdrp
WHERE circle = 'Bengaluru East Circle';
```
````

#### 8. What is the collection efficiency of the Koramangala subdivision for LT2A1-N?

`tags:` `kpi` `single_row` `collection_efficiency` `lookup`

````text
### Example 8
Question: What is the collection efficiency of the Koramangala subdivision for LT2A1-N?
Reasoning: Section and rate schedule are both pinned, so this is exactly one row — reading the pre-computed KPI column is correct here.
SQL:
```sql
SELECT subdivision, rate_schedule_code, pct_coll_eff_with_adj
FROM rapdrp
WHERE subdivision = 'Koramangala'
  AND rate_schedule_code = 'LT2A1-N';
```
````

#### 9. What share of total net consumption does each zone account for?

`tags:` `share` `percentage` `window` `zone` `consumption`

````text
### Example 9
Question: What share of total net consumption does each zone account for?
Reasoning: Share-of-total = a window SUM over the grouped SUM; no self-join needed.
SQL:
```sql
SELECT zone,
       ROUND(SUM(net_consumption), 2) AS net_units,
       ROUND(SUM(net_consumption) * 100.0
             / SUM(SUM(net_consumption)) OVER (), 2) AS pct_of_total
FROM rapdrp
GROUP BY zone
ORDER BY pct_of_total DESC;
```
````

#### 10. Which subdivisions rely most on assessed rather than metered consumption?

`tags:` `consumption` `metering` `assessed` `ranking` `ratio`

````text
### Example 10
Question: Which subdivisions rely most on assessed rather than metered consumption?
Reasoning: Assessed share is a quality red flag; recompute it, don't average the KPI.
SQL:
```sql
SELECT subdivision,
       ROUND(SUM(assessed_taxed_consumption) * 100.0
             / NULLIF(SUM(total_consumption), 0), 2) AS assessed_pct,
       ROUND(SUM(total_consumption), 2) AS total_units
FROM rapdrp
GROUP BY subdivision
HAVING SUM(total_consumption) > 0
ORDER BY assessed_pct DESC
LIMIT 10;
```
````

#### 11. How many installations went unbilled in each circle, and what percent is that?

`tags:` `installations` `billing` `circle` `group_by` `ratio`

````text
### Example 11
Question: How many installations went unbilled in each circle, and what percent is that?
SQL:
```sql
SELECT circle,
       SUM(installations_unbilled) AS unbilled,
       SUM(total_billed_unbilled)  AS total_installations,
       ROUND(SUM(installations_unbilled) * 100.0
             / NULLIF(SUM(total_billed_unbilled), 0), 2) AS unbilled_pct
FROM rapdrp
GROUP BY circle
ORDER BY unbilled_pct DESC;
```
````

#### 12. What is the average realisation per unit for each tariff class?

`tags:` `kpi` `per_unit` `tariff` `ratio` `group_by`

````text
### Example 12
Question: What is the average realisation per unit for each tariff class?
Reasoning: Per-unit figures are weighted ratios: sum the top, sum the bottom, divide.
SQL:
```sql
SELECT tariff,
       ROUND(SUM(net_demand_revenue + net_demand_tax)
             / NULLIF(SUM(net_consumption), 0), 4) AS demand_per_unit,
       ROUND(SUM(net_collection)
             / NULLIF(SUM(net_consumption), 0), 4) AS collection_per_unit
FROM rapdrp
GROUP BY tariff
ORDER BY demand_per_unit DESC;
```
````

#### 13. Give me a DCB scorecard by zone.

`tags:` `dcb` `scorecard` `zone` `group_by` `multi_metric`

````text
### Example 13
Question: Give me a DCB scorecard by zone.
Reasoning: A DCB view always shows opening, demand, collection and closing together.
SQL:
```sql
SELECT zone,
       ROUND(SUM(ob_total_sum), 2)  AS opening_balance,
       ROUND(SUM(net_demand_revenue + net_demand_tax), 2) AS net_demand,
       ROUND(SUM(net_collection), 2) AS net_collection,
       ROUND(SUM(cb_total_sum), 2)   AS closing_balance,
       ROUND(SUM(net_collection) * 100.0
             / NULLIF(SUM(net_demand_revenue + net_demand_tax), 0), 2)
           AS collection_efficiency_pct
FROM rapdrp
GROUP BY zone
ORDER BY net_demand DESC;
```
````

#### 14. Which divisions collected more than the average division?

`tags:` `subquery` `above_average` `division` `collection`

````text
### Example 14
Question: Which divisions collected more than the average division?
Reasoning: Average *of divisions* needs a grouped subquery, not AVG(net_collection).
SQL:
```sql
SELECT division,
       ROUND(SUM(net_collection), 2) AS net_collection
FROM rapdrp
GROUP BY division
HAVING SUM(net_collection) > (
         SELECT AVG(division_total)
         FROM (SELECT SUM(net_collection) AS division_total
               FROM rapdrp GROUP BY division)
       )
ORDER BY net_collection DESC;
```
````

#### 15. Break down net consumption by zone and supply type.

`tags:` `pivot` `crosstab` `zone` `ht_lt` `consumption`

````text
### Example 15
Question: Break down net consumption by zone and supply type.
Reasoning: Pivot with conditional SUM(CASE WHEN ...).
SQL:
```sql
SELECT zone,
       ROUND(SUM(CASE WHEN rate_schedule_main_grp = 'HT'
                      THEN net_consumption ELSE 0 END), 2) AS ht_units,
       ROUND(SUM(CASE WHEN rate_schedule_main_grp = 'LT'
                      THEN net_consumption ELSE 0 END), 2) AS lt_units,
       ROUND(SUM(net_consumption), 2) AS total_units
FROM rapdrp
GROUP BY zone
ORDER BY total_units DESC;
```
````

#### 16. Where are write-offs and bill cancellations highest?

`tags:` `write_off` `bill_cancellation` `leakage` `ranking`

````text
### Example 16
Question: Where are write-offs and bill cancellations highest?
SQL:
```sql
SELECT circle, division,
       ROUND(SUM(write_off), 2) AS write_off,
       ROUND(SUM(bc_total_sum), 2) AS bill_cancellation,
       ROUND(SUM(write_off) + SUM(bc_total_sum), 2) AS total_leakage
FROM rapdrp
GROUP BY circle, division
ORDER BY total_leakage DESC
LIMIT 10;
```
````

#### 17. What is the GST/TCS outstanding by circle?

`tags:` `gst` `tcs` `circle` `outstanding`

````text
### Example 17
Question: What is the GST/TCS outstanding by circle?
Reasoning: The gst_tcs_* block is a parallel DCB; keep it separate from the main one.
SQL:
```sql
SELECT circle,
       ROUND(SUM(gst_tcs_net_demand), 2) AS gst_tcs_demand,
       ROUND(SUM(gst_tcs_collection), 2) AS gst_tcs_collected,
       ROUND(SUM(gst_tcs_cb), 2) AS gst_tcs_outstanding
FROM rapdrp
GROUP BY circle
ORDER BY gst_tcs_outstanding DESC;
```
````

#### 18. How many subdivisions does each zone have?

`tags:` `count` `distinct` `zone` `structure`

````text
### Example 18
Question: How many subdivisions does each zone have?
Reasoning: COUNT(DISTINCT ...) is required — each subdivision appears 12 times, once per rate schedule code.
SQL:
```sql
SELECT zone, COUNT(DISTINCT subdivision) AS subdivisions
FROM rapdrp
GROUP BY zone
ORDER BY subdivisions DESC;
```
````

#### 19. Break down arrears for Tumkur Circle by division and tariff.

`tags:` `drilldown` `arrears` `division` `tariff` `filter`

````text
### Example 19
Question: Break down arrears for Tumkur Circle by division and tariff.
SQL:
```sql
SELECT division, tariff,
       ROUND(SUM(cb_total_sum), 2) AS arrears
FROM rapdrp
WHERE circle = 'Tumkur Circle'
GROUP BY division, tariff
ORDER BY division, arrears DESC;
```
````

#### 20. Which tariff classes fail to recover the average cost of supply?

`tags:` `cost_recovery` `tariff` `kpi` `having`

````text
### Example 20
Question: Which tariff classes fail to recover the average cost of supply?
Reasoning: average_cost_of_supply is a constant 9.92 INR/kWh — inline it.
SQL:
```sql
SELECT tariff,
       ROUND(SUM(net_collection) / NULLIF(SUM(net_consumption), 0), 4)
           AS collection_per_unit,
       9.92 AS average_cost_of_supply,
       ROUND((SUM(net_collection) / NULLIF(SUM(net_consumption), 0))
             * 100.0 / 9.92, 2) AS cost_recovery_pct
FROM rapdrp
GROUP BY tariff
HAVING (SUM(net_collection) / NULLIF(SUM(net_consumption), 0)) < 9.92
ORDER BY cost_recovery_pct ASC;
```
````

---

### Refusal examples (3)

These matter as much as the SQL ones. A model that has never seen a refusal will
invent a `month` column rather than admit the data cannot answer the question.


#### 21. Show the month-on-month trend of collection efficiency for the last year.

`tags:` `refusal` `no_time_dimension` `trend`

````text
### Example 21
Question: Show the month-on-month trend of collection efficiency for the last year.
Reasoning: Refuse rather than inventing a date column. Offer the cross-sectional alternative: efficiency by zone/circle for this snapshot.
SQL:
```sql
-- NOT ANSWERABLE: this table is a single-period snapshot with no date,
-- month or year column, so no trend, YoY or MoM analysis is possible.
```
````

#### 22. Which individual consumer has the largest outstanding bill?

`tags:` `refusal` `grain` `consumer_level`

````text
### Example 22
Question: Which individual consumer has the largest outstanding bill?
Reasoning: Name the actual grain and offer the closest supported answer.
SQL:
```sql
-- NOT ANSWERABLE: the finest grain is (section, rate_schedule_code).
-- There are no consumer-level or RR-number-level records in this table.
```
````

#### 23. What are the AT&C losses and transformer failure rates by division?

`tags:` `refusal` `missing_column` `atc_loss` `technical`

````text
### Example 23
Question: What are the AT&C losses and transformer failure rates by division?
Reasoning: Answer the part that is supported and say plainly which part is not, instead of substituting a similar-sounding column.
SQL:
```sql
-- PARTIALLY ANSWERABLE: transformer failure data is not in this table.
-- AT&C loss needs input energy (units purchased), which is also absent;
-- only billing efficiency and collection efficiency can be computed here.
```
````

---

### Retrieval routing

Score each exemplar by tag overlap (weight 1.5) plus token overlap with the
question. Penalise refusal exemplars by −2.0 unless a refusal tag actually fired,
so they never crowd out real SQL.

| Question contains | Route to tags |
|---|---|
| `trend`, `month`, `yoy`, `over time`, `last year` | `refusal` `no_time_dimension` `trend` |
| `consumer`, `customer`, `rr number`, `individual` | `refusal` `grain` `consumer_level` |
| `at&c`, `transformer`, `feeder`, `outage`, `line loss` | `refusal` `missing_column` |
| `efficien`, `recovery`, `realis` | `kpi` `collection_efficiency` `ratio` |
| `arrear`, `outstanding`, `due`, `pending`, `receivable` | `arrears` `closing_balance` |
| `top`, `highest`, `worst`, `lowest`, `rank` | `ranking` `top_n` |
| `compar`, `versus`, `vs` | `comparison` |
| `share`, `proportion`, `percentage of total` | `share` `percentage` |
| `ht`, `lt`, `high tension`, `low tension` | `ht_lt` `tariff` |
| `per unit`, `per kwh`, `per consumer` | `per_unit` `kpi` |
| `how many`, `count`, `number of` | `count` `installations` |
| `gst`, `tcs` | `gst` `tcs` |
| `write off`, `cancellation`, `leakage` | `write_off` `bill_cancellation` |
| `meter`, `assessed`, `unmetered` | `metering` `assessed` `consumption` |
| `breakdown`, `split`, `by zone/circle/division` | `group_by` `drilldown` |
| `above average`, `more than average` | `subquery` `above_average` |

---
## Layer 10 — Resolved entities

Run entity linking over the question *before* calling the model, and hand it the
exact stored strings. This is what stops `WHERE circle LIKE '%Bangalore%'`
(0 rows — the data says "Bengaluru").

````text
# 10. ENTITIES RESOLVED FROM THIS QUESTION
  "{surface}" -> {column} = '{exact_value}'  ({method} match, confidence {score})
Use these exact strings in WHERE clauses.
````

When a name exists at two hierarchy levels (Koramangala, Hosakote, Malleshwaram,
Tiptur, Jalahalli, Peenya, Kengeri, Harihara, Hiriyur, Kunigal, KGF are all both
a division and a subdivision), append:

````text
      NOTE: '{value}' also exists as a {other_level}. If the user did not say
      which level they meant, prefer the broader one ({column}) and state the
      assumption.
````

When nothing resolves:

````text
# 10. ENTITIES RESOLVED FROM THIS QUESTION
No dimension values were recognised in the question. If the user named a place or
tariff you cannot match to the allowed values above, say so instead of guessing.
````

### Alias table

| User says | Resolves to | Column |
|---|---|---|
| Bangalore East / Bengaluru East / East Circle | `Bengaluru East Circle` | circle |
| Bangalore West / West Circle | `Bengaluru West Circle` | circle |
| Bangalore North / North Circle | `Bengaluru North Circle` | circle |
| Bangalore South / South Circle | `Bengaluru South Circle` | circle |
| Bangalore Rural / Rural Circle | `Bengaluru Rural Circle` | circle |
| Davangere / Davanagere | `Davanagere Circle` | circle |
| Tumakuru Circle / Tumkur | `Tumkur Circle` | circle |
| BMAZ North / North Zone | `BMAZ-North` | zone |
| BMAZ South / South Zone | `BMAZ-South` | zone |
| BRAZ / Rural Zone | `BRAZ` | zone |
| CTAZ / Chitradurga Tumkur Zone | `CTAZ` | zone |
| RR Nagar / RRNagar | `Rajarajeshwarinagar` | division |
| Vidhan Soudha | `Vidhana Soudha` | division |
| Indira Nagar | `Indiranagar` | division |
| Rajaji Nagar | `Rajajinagar` | division |
| E-City / ECity / Electronics City | `Electronic City` | subdivision |
| Bangarpet | `Bangarapete` | subdivision |
| high tension / H.T. | `HT` | rate_schedule_main_grp |
| low tension / L.T. | `LT` | rate_schedule_main_grp |
| domestic / residential | `LT2` | tariff |
| Bhagya Jyothi / Kutir Jyothi | `LT1` | tariff |
| industrial | `LT3` | tariff |
| irrigation / agriculture / agricultural | `LT4` | tariff |
| street light / water supply | `LT5` | tariff |

Fuzzy fallback: match 1–3 word n-grams against the full value list with a 0.86
similarity cutoff. That catches `Koramangla → Koramangala` (0.95),
`Davengere → Davanagere Circle` (0.91), `Rajajingar → Rajajinagar` (0.95).

---

## Layer 11 — Conversation history (optional)

Only include for follow-up turns. Keep the last 6 messages.

````text
# 11. CONVERSATION SO FAR (resolve pronouns and 'that'/'those' against it)
  user: collection efficiency by circle
  assistant: [returned 9 rows, Bengaluru East Circle highest at 88.4%]
  user: now break the worst one down by division
````

---

## Layer 12 — The question

````text
# 12. QUESTION
{user_question}

Think through: (a) what grain is being asked for, (b) which dimension columns to
filter or group by, (c) whether the metric is additive or a ratio that must be
recomputed. Then output only the final query in the required format.
````

---

## Repair prompt (layer 13)

Append to the same user message after a failed execution. One retry is usually
enough; a second rarely helps.

````text
# 13. PREVIOUS ATTEMPT FAILED
Attempt {n} produced:
```sql
{bad_sql}
```
The database returned:
```
{error_message}
```

Diagnose the cause (wrong column name? column not in this table? dialect syntax?
a value that is not in the allowed list?), then output a corrected query in the
required format. If the failure is because the data genuinely cannot answer the
question, emit the `-- NOT ANSWERABLE:` comment instead of guessing again.
````

---

## Answer prompt (separate call)

Once the SQL executes, this second call turns rows back into prose. Fresh system
prompt — do **not** reuse layers 1–9 here.

**System:**

````text
You are a DISCOM commercial-performance analyst. Answer the user's question from
the query result only.
- Lead with the direct answer, then at most three supporting points.
- Money is in INR (rupees, absolute values — NOT lakhs or crores); convert to
  lakh (1e5) or crore (1e7) when that reads better, and say which unit you used.
- Energy is in kWh (units). 1 million units = 1 MU; use MU above 1e6.
- Collection efficiency above 100% is normal (old arrears realised) — do not flag
  it as an error.
- Never state a number that is not in the result set. If the result is empty, say
  so and suggest why (filter too narrow, value not present).
- This is a single-period snapshot: never describe anything as a trend, growth,
  increase or decline.
````

**User:**

````text
Question: {question}

SQL executed:
```sql
{sql}
```

Result ({n} rows{, truncated}):
```
{column_headers}
{rows, max 30}
```

Write the answer.
````

---

## Guardrail checklist

Run before executing anything the model returns:

| Check | Rejects |
|---|---|
| Starts with `SELECT` or `WITH` | procedural preamble |
| No `INSERT UPDATE DELETE DROP ALTER CREATE TRUNCATE GRANT ATTACH PRAGMA VACUUM COPY` | writes and DDL |
| At most one `;` | statement injection |
| Only table `rapdrp` referenced | joins to imagined tables |
| Every `snake_case` identifier is a real column | hallucinated `month`, `atc_loss`, `consumer_id` |
| Append `LIMIT 200` when absent | runaway result sets |
| Pass `-- NOT ANSWERABLE:` comments through untouched | false-positive rejection of a valid refusal |

---

## Placeholder reference

| Placeholder | Filled with |
|---|---|
| `{user_question}` | raw question text |
| `{surface}` | the substring the user typed |
| `{column}` / `{exact_value}` | linked dimension column and its stored string |
| `{method}` / `{score}` | `exact` \| `alias` \| `fuzzy`, and the similarity |
| `{bad_sql}` / `{error_message}` | the failed query and the DB error |
| `{sql}` / `{n}` / `{rows}` | executed query, row count, result preview |

---

## Data quirks the prompt already encodes

- `section` is 100% identical to `subdivision` in this extract.
- 33 columns are constant: all `*_inactive` amounts, `unmetered_installations`,
  `dc_mnr_installations`, `wheeled_energy_units`, all `*_transfer` columns,
  `average_cost_of_supply` (9.92), `pct_live_installations` (100),
  `pct_dc_mnr_installations` (0), several `dr_adj_*` / `bc_*` / `cr_adj_*` /
  `pc_*` / `gst_tcs_*` components.
- `ob_total = ob_revenue`, `demand_total = net_demand_revenue`, and
  `collection_total = coll_revenue` — exactly, on every row.
- The pre-computed KPIs (`pct_coll_eff_*`, `demand_per_unit`, `coll_per_unit_*`,
  `consumption_per_installation`) do **not** reproduce exactly from the base
  columns; the source system applies its own rounding. Reliable for single-row
  lookups, unreliable when aggregated — hence layer 7.
- `voltage_class_kv = '11'` covers exactly HT1, HT2B1, HT2B2, HT2C1; everything
  else is `'Upto 11'`.
- Collection efficiency ranges 76.2%–91.8% across rows and legitimately exceeds
  100% in some aggregates.
