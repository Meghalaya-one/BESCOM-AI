# BESCOM NLP-to-SQL

Ask questions about BESCOM DCB (Demand-Collection-Balance) audit data in plain
English. The backend turns the question into SQL with Gemini, runs it against the
live Neon Postgres database, and returns a plain-language answer plus a table.

```
Frontend (chat UI)  ──►  POST /api/query
                          ├─ prompt_assembler builds the system prompt from
                          │  schema_metadata.py (REAL column names)
                          ├─ Gemini writes SQL  (or CLARIFY: / REFUSE:)
                          ├─ guardrails: read-only, single statement, our table only
                          ├─ execute on Neon  (one auto-retry on error)
                          └─ Gemini formats the answer
                     ◄──  { answer, sql, columns, rows, kind }
```

## Files

| File | Purpose |
|---|---|
| `Backend/app.py` | FastAPI server: `/api/query`, `/api/summary`, `/api/health`, serves the frontend |
| `Backend/prompt_assembler.py` | Builds the system prompt, few-shots, recovery + formatter prompts |
| `Backend/schema_metadata.py` | The **real** DB schema (table `bescom_database`) with column meanings & rules |
| `Backend/.env` | Neon URL + your Gemini key |
| `Backend/requirements.txt` | Python dependencies |
| `Frontend/index.html` | Chat UI, wired to the backend |
| `Backend/NLP_to_SQL_Prompt_Engineering_Guide_BESCOM.md` | The original prompt-engineering reference |

> **Note:** the guide uses idealized column names (`bescom_dcb_audit`,
> `tariff_category`, `net_collection_final`). The live DB uses different physical
> names (`bescom_database`, `tariff`, `net_collection_collection_...`). All prompts
> are built from `schema_metadata.py`, which maps the guide's rules onto the real
> names, so generated SQL always references columns that exist.

## Setup

1. **Add your Gemini API key.** Edit `Backend/.env` and replace the placeholder:
   ```
   GEMINI_API_KEY=your_real_key_here
   ```
   Get one free at https://aistudio.google.com/apikey

2. **Install dependencies** (first time only):
   ```
   cd "Backend"
   python -m pip install -r requirements.txt
   ```

3. **Run the server:**
   ```
   cd "Backend"
   python -m uvicorn app:app --host 127.0.0.1 --port 8000
   ```

4. **Open the app:** http://127.0.0.1:8000/

## Try these

- How many active installations are there in each zone?
- Which circle has the highest net collection?
- What's the total write-off for LT tariffs in the BRAZ zone?
- Show all rows for Tumakuru division. *(catches the casing trap)*
- How has net collection trended month over month? *(correctly refused — no time column)*
- How many active installations does Malleshwaram have? *(asks which of the 4 installation columns)*

## Safety

The backend only ever runs a **single read-only `SELECT`** on `bescom_database`.
`INSERT/UPDATE/DELETE/DROP/ALTER/...`, multiple statements, other tables, and the
junk columns `c_2`/`c_4` are all rejected before anything touches the database.
