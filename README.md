# BESCOM NLP-to-SQL — auto-routed (RAPDRP + Non-RAPDRP)

Ask questions about BESCOM DCB (Demand-Collection-Balance) data in plain English.
A single backend turns the question into SQL with **Gemma** (an OpenAI-compatible
LLM endpoint), runs it against the live Neon Postgres database, and returns a
plain-language answer plus a table and charts.

**The dataset is chosen automatically from the question — there is no dataset tab:**

| The question… | is answered from… |
|---|---|
| mentions **RAPDRP** | the `"RAPDRP"` table |
| mentions **Non-RAPDRP** | the `"Non RAPDRP"` table |
| mentions **neither** | the `bescom_combined` view — **both datasets summed together** |

```
Frontend (chat UI)  ──►  POST /api/query   (one unified backend, port 8000)
                          ├─ route(): keyword match on the question
                          │    "rapdrp" → RAPDRP · "non-rapdrp" → Non-RAPDRP · else → combined
                          ├─ pick that dataset's schema + prompt
                          ├─ Gemma writes SQL  (or CLARIFY: / REFUSE:)
                          ├─ guardrails: read-only, single statement, that table/view only
                          ├─ execute on Neon  (one auto-retry on error)
                          └─ Gemma formats the answer + follow-up (in parallel)
                     ◄──  { answer, sql, rows, kind, dataset, follow_up }
```

## How "combine both" works

The two tables have **different schemas** (different column names, geography,
tariff codes), so "sum both" is not a naive query over two tables. A Postgres
**view** `bescom_combined` UNIONs them over their shared, canonical columns
(aliasing the differently-named ones, e.g. RAPDRP `net_consumption` and
Non-RAPDRP `net_consumption_33_34` both become `net_consumption`) and adds a
`dataset` tag column. Because the view stacks both tables' rows, an ordinary
`SUM`/`COUNT`/`GROUP BY` over it adds the datasets together — and "split by
dataset" is just `GROUP BY dataset`.

Create/refresh the view with:
```
python -c "import psycopg2,os; from dotenv import load_dotenv; load_dotenv('unified/.env'); \
c=psycopg2.connect(os.environ['DATABASE_URL']); c.cursor().execute(open('shared/create_combined_view.sql').read()); c.commit()"
```

## Layout

| Path | Purpose |
|---|---|
| `unified/backend/app.py` | The single backend: router + `/api/query`, `/api/summary`, `/api/health`, serves the frontend |
| `unified/backend/datasets.py` | Loads the 3 (schema, prompt) pairs and the keyword `route()` |
| `unified/backend/schema_combined.py`, `prompt_combined.py` | Schema + prompts for the `bescom_combined` view |
| `unified/.env` | Neon URL + Gemma key/base-URL/model |
| `RAPDRP/backend/`, `Non-RAPDRP/backend/` | The per-dataset schema + prompt modules (reused by the unified backend) |
| `shared/create_combined_view.sql` | Defines the `bescom_combined` view |
| `Frontend/index.html` | Shared chat UI (no dataset tab; shows which dataset each answer used) |

All data lives in one Neon database (`neondb`): tables `"RAPDRP"` and `"Non RAPDRP"`
plus the `bescom_combined` view.

**LLM:** an OpenAI-compatible **Gemma** endpoint (vLLM behind the `enlight.dev`
gateway) via the `openai` SDK. Auth uses a custom `apikey` header (not
`Authorization: Bearer`), so the client is built with `default_headers={"apikey": …}`.
`gemma-4-12b` has a **4096-token total context**, so prompts inject a compact
few-shot subset and output budgets are small (SQL ≤ 400 tokens, follow-up ≤ 48).

## Setup

`unified/.env` already contains the Neon URL and Gemma settings. To change them:
```
GEMMA_API_KEY=your_key_here
GEMMA_BASE_URL=https://poc-aiops.enlight.dev/openai/v1
GEMMA_MODEL=gemma-4-12b
```

Install dependencies (first time only):
```
python -m pip install -r unified/backend/requirements.txt
```

## Run

```
cd unified/backend && python app.py        # -> http://127.0.0.1:8000
```
Open http://127.0.0.1:8000/ and just ask.

## Try these

- What is the total number of active installations?  *(combines both datasets)*
- Show collection efficiency by circle  *(combined; recomputes the ratio)*
- Total net collection in **RAPDRP**  *(routes to RAPDRP only)*
- Top 10 sections by revenue in **Non-RAPDRP**  *(routes to Non-RAPDRP only)*
- Split total net collection by dataset  *(one row per source)*
- Show the month-over-month trend  *(correctly refused — no time column)*

## Safety

The backend only ever runs a **single read-only `SELECT`** on the routed
dataset's own table/view (the LLM only proposes SQL; these guardrails decide what
runs). Writes/DDL, multiple statements, other tables, and blank/junk columns are
all rejected before anything touches the database.
