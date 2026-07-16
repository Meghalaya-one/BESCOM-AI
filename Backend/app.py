"""
app.py — BESCOM NLP-to-SQL FastAPI backend
==========================================
Pipeline for POST /api/query:
    question
      -> Gemini (system + few-shot)  => SQL | CLARIFY: | REFUSE:
      -> guardrails (read-only, single stmt, table whitelist)
      -> execute on Neon Postgres
         (on error -> one recovery attempt via Gemini)
      -> Gemini formats a plain-language answer
    => { answer, sql, columns, rows, row_count, kind, notes }

Also:
    GET  /api/summary   -> the headline metrics for the landing page
    GET  /api/health    -> liveness + whether Gemini key is configured
    GET  /              -> serves the frontend
"""
import os
import re
import json
import time
import decimal
import datetime
import atexit
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import schema_metadata as sm
import prompt_assembler as pa

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

DATABASE_URL = os.environ.get("DATABASE_URL", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# Treat the shipped placeholder as "not configured" so the UI shows a clear hint.
if GEMINI_API_KEY.upper().startswith("PASTE_"):
    GEMINI_API_KEY = ""
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
FRONTEND_DIR = os.path.normpath(os.path.join(HERE, "..", "Frontend"))

# --------------------------------------------------------------------------- #
# Gemini client (lazy — so the server still boots without a key)
# --------------------------------------------------------------------------- #
_genai_client = None


def get_gemini():
    global _genai_client
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to Backend/.env to enable NLP-to-SQL."
        )
    if _genai_client is None:
        from google import genai
        _genai_client = genai.Client(api_key=GEMINI_API_KEY)
    return _genai_client


def gemini_generate(system_prompt: str, user_prompt: str, retries: int = 3,
                    think: bool = False) -> str:
    """Call Gemini with a short exponential backoff on transient errors
    (503 "high demand" / 429 rate-limit / 500), so a momentary capacity blip
    doesn't drop us into an ugly fallback answer.

    think=False disables the model's internal reasoning budget. Every task here
    is tightly constrained by its prompt (emit one SELECT / summarise rows in a
    few sentences / propose one question), so the thinking tokens buy no accuracy
    and cost seconds of latency."""
    from google.genai import types
    client = get_gemini()
    last_err = None
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.0,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=-1 if think else 0
                    ),
                ),
            )
            return (resp.text or "").strip()
        except Exception as e:
            last_err = e
            msg = str(e)
            transient = any(code in msg for code in ("503", "429", "500", "UNAVAILABLE",
                                                     "RESOURCE_EXHAUSTED", "overloaded"))
            if transient and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))  # 1.5s, 3s, ...
                continue
            raise last_err


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
# Neon is remote, so a fresh psycopg2.connect() costs ~1.8s of TCP+TLS handshake.
# Hold the connections open in a pool and hand them out instead of reconnecting.
_pool = None


def get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=8, dsn=DATABASE_URL,
            # Don't let a silently-dropped Neon connection hang a request.
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
        )
    return _pool


@atexit.register
def _close_pool():
    if _pool is not None:
        _pool.closeall()


@contextmanager
def db_cursor():
    pool = get_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        yield cur
        conn.commit()
    except Exception:
        # A poisoned connection must be rolled back before it goes back in the
        # pool, or the next borrower inherits the aborted transaction.
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn)


def _jsonable(v):
    if isinstance(v, decimal.Decimal):
        f = float(v)
        return int(f) if f.is_integer() else round(f, 4)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def run_select(sql: str, limit_rows: int = 500):
    with db_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()[:limit_rows]
        cols = list(rows[0].keys()) if rows else [d[0] for d in (cur.description or [])]
        clean = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
        return cols, clean


# --------------------------------------------------------------------------- #
# Guardrails — reject anything that isn't a single read-only SELECT on our table
# --------------------------------------------------------------------------- #
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"copy|merge|call|do|vacuum|comment|reindex)\b",
    re.IGNORECASE,
)


def strip_fences(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:sql)?", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"```$", "", t).strip()
    return t


def validate_sql(sql: str):
    """Return (ok, reason). Only a single read-only SELECT on our table passes."""
    s = sql.strip().rstrip(";").strip()
    if not s:
        return False, "Empty query."
    if ";" in s:
        return False, "Multiple statements are not allowed."
    if not re.match(r"^\s*(select|with)\b", s, re.IGNORECASE):
        return False, "Only SELECT queries are allowed."
    if FORBIDDEN.search(s):
        return False, "Query contains a forbidden (non-read-only) keyword."
    # must reference our table and nothing that looks like another one
    if sm.TABLE_NAME not in s.lower():
        return False, f"Query must read from {sm.TABLE_NAME}."
    for junk in sm.JUNK_COLUMNS:
        if re.search(rf"\b{junk}\b", s, re.IGNORECASE):
            return False, f"Column '{junk}' is empty/junk and must not be queried."
    return True, ""


def classify_result_kind(cols, rows):
    if not rows:
        return "empty"
    if len(rows) == 1 and len(cols) == 1:
        return "scalar"
    return "table"


def _pretty_col(name: str) -> str:
    """Turn a physical column name into a readable label for prose."""
    return name.replace("_", " ").strip().capitalize()


def _fmt_indian(v):
    """Format a number with Indian-style thousands separators (e.g. 67,12,80,410)."""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if not isinstance(v, (int, float)):
        return str(v)
    neg = v < 0
    if isinstance(v, float):
        s = f"{abs(v):,.2f}".rstrip("0").rstrip(".")
        int_part, _, dec_part = s.partition(".")
        int_part = int_part.replace(",", "")
    else:
        int_part, dec_part = str(abs(v)), ""
    if len(int_part) > 3:  # Indian grouping: last 3, then pairs
        head, tail = int_part[:-3], int_part[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        int_part = f"{head},{tail}"
    out = int_part + (f".{dec_part}" if dec_part else "")
    return f"-{out}" if neg else out


def _fallback_answer(question: str, cols, rows, kind: str) -> str:
    """A professional, data-aware answer used only when the Gemini formatter
    is unavailable. Reads well on its own — never a bare 'Returned N rows.'"""
    if kind == "empty":
        return ("No records matched that query. Please check the division name "
                "spelling (casing can vary) or try a broader geography.")
    if kind == "scalar":
        val = list(rows[0].values())[0]
        label = _pretty_col(cols[0])
        return f"{label} is {_fmt_indian(val)}."
    # Table: describe what was returned and lead with the top row's key figures.
    n = len(rows)
    lead = ""
    if rows and len(cols) >= 2:
        top = rows[0]
        label_col, value_col = cols[0], cols[1]
        lead = (f" The leading result is {top.get(label_col)} "
                f"({_pretty_col(value_col)}: {_fmt_indian(top.get(value_col))}).")
    return (f"The query returned {n} result{'s' if n != 1 else ''}, broken down by "
            f"{_pretty_col(cols[0])}.{lead} Full details are shown in the table below.")


# --------------------------------------------------------------------------- #
# Post-query Gemini work — the answer and the follow-up are independent of each
# other, so they run concurrently on this pool instead of one after the other.
# Both calls are blocking HTTP (google-genai), so threads are the right tool.
# --------------------------------------------------------------------------- #
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gemini")
atexit.register(lambda: _POOL.shutdown(wait=False))


def _format_answer(question, preview, cols, rows, kind: str) -> str:
    """Plain-language answer. Falls back to a data-aware summary if Gemini fails."""
    try:
        return gemini_generate(
            "You explain database results in clear, concise business English.",
            pa.build_formatter_prompt(question, json.dumps(preview, default=str), len(rows)),
        )
    except Exception:
        # Formatter call failed (e.g. Gemini still unavailable after retries).
        # Compose a professional, data-aware fallback rather than a bare count.
        return _fallback_answer(question, cols, rows, kind)


def _suggest_follow_up(question, cols, rows, kind: str, asked) -> str:
    """One natural next question, specific to THIS result and never already asked.
    Purely additive: any failure just yields no suggestion, never an error."""
    if kind not in ("table", "scalar") or not rows:
        return ""
    already = [a.strip() for a in (asked or []) if a and a.strip()]
    avoid_block = ""
    if already:
        avoid_block = (
            "\nAlready asked this session (do NOT repeat or paraphrase any of these):\n- "
            + "\n- ".join(already[-15:]) + "\n"
        )
    try:
        fu = gemini_generate(
            "You suggest the single most useful NEXT question a BESCOM electricity "
            "utility officer would ask, that naturally builds on the question they "
            "just asked and its results. It must be a brand-new angle, never a "
            "repeat of anything already asked.\n"
            "DOMAIN: BESCOM's unit of business is the electrical INSTALLATION "
            "(one metered service connection). There is NO consumer/customer/"
            "beneficiary/account entity — treat those words as installations. "
            "There is NO date/time column, so never suggest trend, growth, "
            "month/year, or over-time questions.\n"
            "CRITICAL: The ONLY thing that exists in this database is the columns "
            "listed below. Your suggested question MUST be fully answerable using "
            "ONLY these columns — a single SELECT with filtering/grouping/aggregation "
            "over them. Do NOT propose anything about tiers, slabs, rate structure, "
            "unit-price bands, targets, forecasts, reasons/causes, comparisons to "
            "other years, or any concept not represented by a column below. If the "
            "only good next step would need data that isn't here, instead pick a "
            "simpler grouping/ranking/filter question that these columns CAN answer.\n"
            "AVAILABLE COLUMNS (name: meaning):\n"
            + sm.follow_up_topics_text(),
            "Question just asked: " + question + "\n"
            "Result columns: " + ", ".join(cols) + "\n"
            "Row count: " + str(len(rows)) + "\n"
            + avoid_block +
            "\nReply with ONE short follow-up question (max ~12 words), no quotes, "
            "no preamble. It MUST be answerable using only the AVAILABLE COLUMNS "
            "(no tiers/slabs/rate-structure/targets/trends), must build on what was "
            "just asked, and must differ from every already-asked question above.",
        )
        follow_up = fu.strip().strip('"').split("\n")[0][:120]
        # Final guard: if the model still echoed a prior question, drop it.
        if any(follow_up.lower() == a.lower() for a in already):
            return ""
        return follow_up
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
app = FastAPI(title="BESCOM NLP-to-SQL")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


class HistoryTurn(BaseModel):
    """One prior exchange, so back-references ("that circle") can be resolved."""
    question: str = ""
    sql: str | None = None
    rows: list[dict] = []


class QueryIn(BaseModel):
    question: str
    # Questions + follow-ups already used this session, so we never repeat one.
    asked: list[str] = []
    # Recent turns (question + SQL + a few result rows) for follow-up resolution.
    history: list[HistoryTurn] = []


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "gemini_configured": bool(GEMINI_API_KEY),
        "db_configured": bool(DATABASE_URL),
        "model": GEMINI_MODEL,
        "table": sm.TABLE_NAME,
    }


@app.get("/api/summary")
def summary():
    """Headline metrics for the landing page, computed live from the DB."""
    try:
        with db_cursor() as cur:
            cur.execute(f"""
                SELECT
                    COUNT(*)                                    AS records,
                    COUNT(DISTINCT zone)                        AS zones,
                    COUNT(DISTINCT circle)                      AS circles,
                    COUNT(DISTINCT UPPER(division))             AS divisions,
                    SUM(total_10_11)                            AS total_installations,
                    SUM(active_installations)                   AS active_installations,
                    SUM(metered_installations)                  AS metered_installations,
                    ROUND(SUM({pa.NET_CONSUMPTION})::numeric / 1e6, 1) AS net_consumption_mu,
                    ROUND(SUM({pa.NET_DEMAND_REVENUE})::numeric / 1e7, 1) AS net_revenue_cr
                FROM {sm.TABLE_NAME}
            """)
            r = cur.fetchone()
        return {k: _jsonable(v) for k, v in r.items()} | {"utility": "BESCOM"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/query")
def query(inp: QueryIn):
    question = (inp.question or "").strip()
    if not question:
        return JSONResponse(status_code=400, content={"error": "Empty question."})

    if not GEMINI_API_KEY:
        return JSONResponse(status_code=503, content={
            "kind": "error",
            "answer": "The Gemini API key isn't configured yet. Add GEMINI_API_KEY "
                      "to Backend/.env and restart the server.",
        })

    # Prior turns let the model resolve "that circle" / "those divisions" instead
    # of emitting CLARIFY at a pronoun it has no referent for.
    history = [t.model_dump() for t in (inp.history or [])]

    system_prompt = pa.build_sql_system_prompt()
    user_prompt = pa.build_sql_user_prompt(question, history)

    # 1) NL -> SQL
    try:
        raw = strip_fences(gemini_generate(system_prompt, user_prompt))
    except Exception as e:
        return JSONResponse(status_code=502, content={
            "kind": "error", "answer": f"Gemini request failed: {e}"})

    # 2) Handle CLARIFY / REFUSE contract outputs
    if raw.upper().startswith("CLARIFY:"):
        return {"kind": "clarify", "answer": raw[len("CLARIFY:"):].strip(), "sql": None}
    if raw.upper().startswith("REFUSE:"):
        return {"kind": "refuse", "answer": raw[len("REFUSE:"):].strip(), "sql": None}

    sql = raw

    # 3) Guardrails
    ok, reason = validate_sql(sql)
    if not ok:
        return {"kind": "error", "sql": sql,
                "answer": f"I generated a query I won't run for safety: {reason}"}

    # 4) Execute (with one recovery attempt on DB error)
    attempted = [sql]
    try:
        cols, rows = run_select(sql)
    except Exception as e1:
        try:
            # Recovery is rare and only runs after a query already failed —
            # it's worth letting the model reason here. Keep the conversation
            # context so a follow-up doesn't lose its referent on the retry.
            fixed = strip_fences(gemini_generate(
                pa.build_sql_system_prompt(),
                pa.build_context_block(history)
                + pa.build_recovery_prompt(question, sql, str(e1)),
                think=True,
            ))
        except Exception as e:
            return JSONResponse(status_code=502, content={
                "kind": "error", "sql": sql,
                "answer": f"Query failed and recovery request errored: {e}"})
        if fixed.upper().startswith("REFUSE:"):
            return {"kind": "refuse", "answer": fixed[len("REFUSE:"):].strip(), "sql": sql}
        ok2, reason2 = validate_sql(fixed)
        if not ok2:
            return {"kind": "error", "sql": fixed,
                    "answer": f"Recovery produced an unsafe query: {reason2}"}
        attempted.append(fixed)
        sql = fixed
        try:
            cols, rows = run_select(sql)
        except Exception as e2:
            return {"kind": "error", "sql": sql,
                    "answer": f"The query still failed after one retry: {e2}"}

    kind = classify_result_kind(cols, rows)

    # 5+6) The plain-language answer and the follow-up suggestion depend only on
    #      (question, cols, rows) — neither reads the other's output. Run them at
    #      the same time so we pay for the slower one, not the sum of both.
    preview = rows[:50]
    fut_answer = _POOL.submit(_format_answer, question, preview, cols, rows, kind)
    fut_follow = _POOL.submit(_suggest_follow_up, question, cols, rows, kind, inp.asked)
    answer = fut_answer.result()
    follow_up = fut_follow.result()

    return {
        "kind": kind,
        "answer": answer,
        "sql": sql,
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "follow_up": follow_up,
        "attempts": attempted,
    }


# --------------------------------------------------------------------------- #
# Serve the frontend (so everything runs from one origin, no CORS/file:// issues)
# --------------------------------------------------------------------------- #
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    @app.get("/BESCOM-logo.png")
    def logo():
        return FileResponse(os.path.join(FRONTEND_DIR, "BESCOM-logo.png"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
