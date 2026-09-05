"""
app.py — RAPDRP NLP-to-SQL FastAPI backend
==========================================
Pipeline for POST /api/query:
    question
      -> Gemma  (system + few-shot)  => SQL | CLARIFY: | REFUSE:
      -> guardrails (read-only, single stmt, table whitelist)
      -> execute on Neon Postgres  (on error -> one recovery attempt via Gemma)
      -> Gemma formats a plain-language answer
    => { answer, sql, columns, rows, row_count, kind, follow_ups }

Also:
    GET  /api/summary   -> headline metrics for the landing page
    GET  /api/health    -> liveness + whether Gemma key is configured
    GET  /              -> serves the shared frontend

This is the RAPDRP twin of the Non-RAPDRP backend. Only schema_metadata.py,
prompt_assembler.py, the summary query and DATASET differ between them.
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
DATASET = "RAPDRP"
DEFAULT_PORT = 8001

HERE = os.path.dirname(os.path.abspath(__file__))
# .env lives in the RAPDRP folder (one level up from backend/).
load_dotenv(os.path.join(HERE, "..", ".env"))

DATABASE_URL = os.environ.get("DATABASE_URL", "")
GEMMA_BASE_URL = os.environ.get("GEMMA_BASE_URL", "https://34.135.75.215/api/v1").strip()
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
# Shared frontend lives at <repo>/Frontend (two levels up from backend/).
FRONTEND_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "Frontend"))

# --------------------------------------------------------------------------- #
# LLM client (lazy) — self-hosted Qwen endpoint, no API key required
# --------------------------------------------------------------------------- #
# OpenAI-compatible vLLM server on a self-signed certificate, so TLS
# verification is disabled for this client only.
_llm_client = None


def get_llm():
    global _llm_client
    if _llm_client is None:
        from openai import OpenAI, DefaultHttpxClient
        _llm_client = OpenAI(
            base_url=GEMMA_BASE_URL,
            api_key="dummy_key",
            http_client=DefaultHttpxClient(verify=False),
        )
    return _llm_client


# gemma-4-12b on this vLLM has a 4096-token TOTAL context (prompt + output), so
# output budgets are deliberately small — SQL and short answers need little.
def llm_generate(system_prompt: str, user_prompt: str, retries: int = 3,
                 think: bool = False, max_tokens: int = 512) -> str:
    """Call the Gemma chat endpoint with a short exponential backoff on transient
    errors so a momentary capacity blip doesn't drop us into an ugly fallback answer.
    The `think` flag is accepted for call-site compatibility but has no effect —
    Gemma has no reasoning-budget knob."""
    client = get_llm()
    last_err = None
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=GEMMA_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            last_err = e
            msg = str(e)
            transient = any(code in msg for code in ("503", "429", "500", "UNAVAILABLE",
                                                     "RESOURCE_EXHAUSTED", "overloaded",
                                                     "timeout", "Timeout"))
            if transient and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last_err


# --------------------------------------------------------------------------- #
# DB helpers — pooled connections (Neon is remote; a fresh connect costs ~1.8s)
# --------------------------------------------------------------------------- #
_pool = None


def get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=8, dsn=DATABASE_URL,
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
    r"copy|merge|call|do|vacuum|comment|reindex|attach|pragma)\b",
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
    # Must reference our table (matched on the unquoted label, case-insensitively).
    if sm.TABLE_LABEL.lower() not in s.lower():
        return False, f"Query must read from {sm.TABLE_LABEL}."
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
    return name.replace("_", " ").strip().capitalize()


def _fmt_indian(v):
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
    if len(int_part) > 3:
        head, tail = int_part[:-3], int_part[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        int_part = f"{head},{tail}"
    out = int_part + (f".{dec_part}" if dec_part else "")
    return f"-{out}" if neg else out


def _fallback_answer(question: str, cols, rows, kind: str) -> str:
    """Professional, data-aware answer used only when the Gemma formatter fails."""
    if kind == "empty":
        return ("No records matched that query. The filter may be too narrow, or "
                "the value isn't present (the data says 'Bengaluru', not 'Bangalore').")
    if kind == "scalar":
        val = list(rows[0].values())[0]
        label = _pretty_col(cols[0])
        return f"{label} is {_fmt_indian(val)}."
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
# Post-query Gemma work — answer + follow-up run concurrently (both blocking HTTP)
# --------------------------------------------------------------------------- #
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")
atexit.register(lambda: _POOL.shutdown(wait=False))


def _format_answer(question, preview, cols, rows, kind: str) -> str:
    try:
        # Cap the rows shown to the formatter: gemma-4-12b has a 4096-token window,
        # and the answer only needs the headline figures, not every row.
        return llm_generate(
            "You explain DISCOM DCB results in clear, concise business English.",
            pa.build_formatter_prompt(question, json.dumps(preview[:15], default=str), len(rows)),
            max_tokens=220,   # answers are 1-3 sentences; shorter output = faster decode
        )
    except Exception:
        return _fallback_answer(question, cols, rows, kind)


def _suggest_follow_ups(question, cols, rows, kind: str, asked) -> list:
    """Two natural next questions, specific to THIS result and never already asked."""
    if kind not in ("table", "scalar") or not rows:
        return []
    already = [a.strip() for a in (asked or []) if a and a.strip()]
    avoid_block = ""
    if already:
        avoid_block = (
            "\nAlready asked this session (do NOT repeat or paraphrase any of these):\n- "
            + "\n- ".join(already[-15:]) + "\n"
        )
    try:
        fu = llm_generate(
            "You suggest the two most useful NEXT questions a BESCOM RAPDRP "
            "commercial-performance officer would ask, that naturally build on the "
            "question just asked and its results. Each must be a brand-new angle, and "
            "the two must differ from each other.\n"
            "DOMAIN: RAPDRP is a single-period DCB snapshot. The unit of business is "
            "the electrical INSTALLATION (one metered connection). There is NO "
            "consumer-level detail and NO date/time column, so never suggest trend, "
            "growth, month/year, or over-time questions.\n"
            "CRITICAL: The ONLY things that exist are the columns listed below. Each "
            "suggested question MUST be answerable using ONLY these columns — a single "
            "SELECT with filtering/grouping/aggregation. Do NOT propose anything about "
            "targets, forecasts, causes, AT&C losses, transformers, or any concept not "
            "represented by a column below.\n"
            "AVAILABLE COLUMNS (name: meaning):\n"
            + sm.follow_up_topics_text(),
            "Question just asked: " + question + "\n"
            "Result columns: " + ", ".join(cols) + "\n"
            "Row count: " + str(len(rows)) + "\n"
            + avoid_block +
            "\nReply with exactly TWO short follow-up questions (max ~12 words each), "
            "one per line, no numbering, no quotes, no preamble. Each MUST be answerable "
            "using only the AVAILABLE COLUMNS, must build on what was just asked, and "
            "must differ from every already-asked question above and from each other.",
            max_tokens=96,
        )
        lines = [l.strip().strip('"').lstrip("-•").strip()[:120] for l in fu.strip().split("\n")]
        out, seen = [], set()
        for l in lines:
            if not l or l.lower() in seen or any(l.lower() == a.lower() for a in already):
                continue
            seen.add(l.lower())
            out.append(l)
            if len(out) == 2:
                break
        return out
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
app = FastAPI(title=f"{DATASET} NLP-to-SQL")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


class HistoryTurn(BaseModel):
    question: str = ""
    sql: str | None = None
    rows: list[dict] = []


class QueryIn(BaseModel):
    question: str
    asked: list[str] = []
    history: list[HistoryTurn] = []


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "dataset": DATASET,
        "llm_configured": bool(GEMMA_BASE_URL),
        "db_configured": bool(DATABASE_URL),
        "model": GEMMA_MODEL,
        "table": sm.TABLE_LABEL,
    }


@app.get("/api/summary")
def summary():
    """Headline metrics for the landing page, computed live from the DB."""
    try:
        with db_cursor() as cur:
            cur.execute(f"""
                SELECT
                    COUNT(*)                                       AS records,
                    COUNT(DISTINCT zone)                           AS zones,
                    COUNT(DISTINCT circle)                         AS circles,
                    COUNT(DISTINCT division)                       AS divisions,
                    SUM(total_installations)                       AS total_installations,
                    SUM(active_installations)                      AS active_installations,
                    SUM(metered_installations)                     AS metered_installations,
                    ROUND(SUM(net_consumption)::numeric / 1e6, 1)  AS net_consumption_mu,
                    ROUND(SUM(net_collection)::numeric / 1e7, 1)   AS net_revenue_cr
                FROM {sm.TABLE_NAME}
            """)
            r = cur.fetchone()
        return {k: _jsonable(v) for k, v in r.items()} | {"utility": "BESCOM", "dataset": DATASET}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/query")
def query(inp: QueryIn):
    question = (inp.question or "").strip()
    if not question:
        return JSONResponse(status_code=400, content={"error": "Empty question."})

    if not GEMMA_BASE_URL:
        return JSONResponse(status_code=503, content={
            "kind": "error",
            "answer": "The LLM endpoint isn't configured yet. Add GEMMA_BASE_URL "
                      "to RAPDRP/.env and restart the server.",
        })

    history = [t.model_dump() for t in (inp.history or [])]

    system_prompt = pa.build_sql_system_prompt()
    user_prompt = pa.build_sql_user_prompt(question, history)

    # 1) NL -> SQL  (SQL is short; keep the output budget small for the 4096 window)
    try:
        raw = strip_fences(llm_generate(system_prompt, user_prompt, max_tokens=400))
    except Exception as e:
        return JSONResponse(status_code=502, content={
            "kind": "error", "answer": f"Gemma request failed: {e}"})

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
            fixed = strip_fences(llm_generate(
                pa.build_sql_system_prompt(),
                pa.build_context_block(history)
                + pa.build_recovery_prompt(question, sql, str(e1)),
                think=True, max_tokens=400,
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

    # 5+6) Answer + follow-up depend only on (question, cols, rows) — run together.
    preview = rows[:50]
    fut_answer = _POOL.submit(_format_answer, question, preview, cols, rows, kind)
    fut_follow = _POOL.submit(_suggest_follow_ups, question, cols, rows, kind, inp.asked)
    answer = fut_answer.result()
    follow_ups = fut_follow.result()

    return {
        "kind": kind,
        "answer": answer,
        "sql": sql,
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "follow_ups": follow_ups,
        "attempts": attempted,
    }


# --------------------------------------------------------------------------- #
# Serve the shared frontend (so everything runs from one origin)
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
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    uvicorn.run("app:app", host="127.0.0.1", port=port, reload=False)
