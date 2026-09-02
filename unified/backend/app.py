"""
app.py — UNIFIED BESCOM NLP-to-SQL backend  (RAPDRP + Non-RAPDRP, auto-routed)
=============================================================================
One backend, one port. The dataset is chosen FROM THE QUESTION:

    "...rapdrp..."      -> query the "RAPDRP" table
    "...non-rapdrp..."  -> query the "Non RAPDRP" table
    (neither mentioned) -> query the bescom_combined VIEW, which stacks both
                           tables so SUM/COUNT/GROUP BY adds them together.

Pipeline for POST /api/query (unchanged per dataset, just the schema+prompt swap):
    question
      -> route() picks the (schema, prompt) pair
      -> Gemma writes SQL (or CLARIFY: / REFUSE:)
      -> guardrails (read-only, single stmt, that dataset's table only)
      -> execute on Neon  (one recovery attempt on error)
      -> Gemma formats the answer + suggests a follow-up (in parallel)
    => { answer, sql, columns, rows, row_count, kind, follow_up, dataset }

Also: GET /api/summary (combined headline metrics), GET /api/health, GET /.
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

import datasets as D

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEFAULT_PORT = 8000
HERE = os.path.dirname(os.path.abspath(__file__))
# .env lives in the unified folder (one level up from backend/).
load_dotenv(os.path.join(HERE, "..", ".env"))

DATABASE_URL = os.environ.get("DATABASE_URL", "")
GEMMA_API_KEY = os.environ.get("GEMMA_API_KEY", "").strip()
if GEMMA_API_KEY.upper().startswith("PASTE_"):
    GEMMA_API_KEY = ""
GEMMA_BASE_URL = os.environ.get("GEMMA_BASE_URL", "https://poc-aiops.enlight.dev/openai/v1").strip()
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "gemini-3-flash-preview")

# Output budget for NL->SQL generation (and its recovery retry). Reasoning models
# such as Gemini count internal thinking tokens against this, so it must exceed the
# visible SQL length by a wide margin — see the note at the call site.
SQL_MAX_TOKENS = int(os.environ.get("SQL_MAX_TOKENS", "800"))
# Same reasoning-token caveat applies to the prose answer and the follow-up: the
# old 220/48 budgets were Gemma-era and truncated Gemini mid-sentence ("4,90").
ANSWER_MAX_TOKENS = int(os.environ.get("ANSWER_MAX_TOKENS", "900"))
FOLLOWUP_MAX_TOKENS = int(os.environ.get("FOLLOWUP_MAX_TOKENS", "300"))
FRONTEND_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "Frontend"))

# The combined view's physical name — for the summary query.
COMBINED_VIEW = D.DATASETS["combined"]["sm"].TABLE_NAME

# --------------------------------------------------------------------------- #
# LLM client (lazy)
# --------------------------------------------------------------------------- #
# Two OpenAI-compatible backends are supported, chosen by GEMMA_BASE_URL:
#   * Google Gemini  (generativelanguage.googleapis.com/.../openai/) -> standard
#     `Authorization: Bearer <key>`.
#   * enlight.dev vLLM gateway -> a CUSTOM `apikey` header, NOT Bearer.
# Everything downstream (prompts, guardrails, parsing) is identical, so only the
# auth style and model name differ.
_llm_client = None


def _is_google_endpoint(url: str) -> bool:
    return "generativelanguage.googleapis.com" in (url or "").lower()


def get_llm():
    global _llm_client
    if not GEMMA_API_KEY:
        raise RuntimeError("GEMMA_API_KEY is not set. Add it to unified/.env.")
    if _llm_client is None:
        from openai import OpenAI
        if _is_google_endpoint(GEMMA_BASE_URL):
            # Gemini's OpenAI-compat layer takes the key as a normal Bearer token.
            _llm_client = OpenAI(base_url=GEMMA_BASE_URL, api_key=GEMMA_API_KEY)
        else:
            _llm_client = OpenAI(
                base_url=GEMMA_BASE_URL,
                api_key="unused",
                default_headers={"apikey": GEMMA_API_KEY},
            )
    return _llm_client


def llm_generate(system_prompt: str, user_prompt: str, retries: int = 3,
                 think: bool = False, max_tokens: int = 512) -> str:
    """Call the chat endpoint with a short backoff on transient errors.
    Callers pass an explicit max_tokens; note that reasoning models bill their
    internal thinking against it, so budgets must exceed the visible output."""
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
# DB helpers — pooled connections
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
# Guardrails — reject anything that isn't a single read-only SELECT on the
# dataset's own table/view.
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


def _table_token(sm) -> str:
    """The distinctive token the SQL must reference for this dataset.
    RAPDRP/Non-RAPDRP -> 'rapdrp'; combined -> 'bescom_combined'."""
    label = sm.TABLE_LABEL.lower()
    return "rapdrp" if "rapdrp" in label else label


def validate_sql(sql: str, sm):
    """Only a single read-only SELECT that references this dataset's table passes."""
    s = sql.strip().rstrip(";").strip()
    if not s:
        return False, "Empty query."
    if ";" in s:
        return False, "Multiple statements are not allowed."
    if not re.match(r"^\s*(select|with)\b", s, re.IGNORECASE):
        return False, "Only SELECT queries are allowed."
    if FORBIDDEN.search(s):
        return False, "Query contains a forbidden (non-read-only) keyword."
    if _table_token(sm) not in s.lower():
        return False, f"Query must read from {sm.TABLE_LABEL}."
    for junk in getattr(sm, "JUNK_COLUMNS", []):
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
    if kind == "empty":
        return ("No records matched that query. The filter may be too narrow, or the "
                "value isn't present (geography names vary in casing across datasets).")
    if kind == "scalar":
        val = list(rows[0].values())[0]
        return f"{_pretty_col(cols[0])} is {_fmt_indian(val)}."
    n = len(rows)
    lead = ""
    if rows and len(cols) >= 2:
        top = rows[0]
        lead = (f" The leading result is {top.get(cols[0])} "
                f"({_pretty_col(cols[1])}: {_fmt_indian(top.get(cols[1]))}).")
    return (f"The query returned {n} result{'s' if n != 1 else ''}, broken down by "
            f"{_pretty_col(cols[0])}.{lead} Full details are shown in the table below.")


# --------------------------------------------------------------------------- #
# Post-query Gemma work — answer + follow-up run concurrently
# --------------------------------------------------------------------------- #
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")
atexit.register(lambda: _POOL.shutdown(wait=False))


def _format_answer(pa, question, preview, cols, rows, kind: str) -> str:
    try:
        return llm_generate(
            "You explain DISCOM DCB results in clear, concise business English.",
            pa.build_formatter_prompt(question, json.dumps(preview[:15], default=str), len(rows)),
            max_tokens=ANSWER_MAX_TOKENS,
        )
    except Exception:
        return _fallback_answer(question, cols, rows, kind)


def _suggest_follow_up(sm, question, cols, rows, kind: str, asked, label: str) -> str:
    if kind not in ("table", "scalar") or not rows:
        return ""
    already = [a.strip() for a in (asked or []) if a and a.strip()]
    avoid_block = ""
    if already:
        avoid_block = ("\nAlready asked this session (do NOT repeat/paraphrase):\n- "
                       + "\n- ".join(already[-15:]) + "\n")
    try:
        fu = llm_generate(
            f"You suggest the single most useful NEXT question a BESCOM {label} "
            "commercial-performance officer would ask, building on the question just "
            "asked and its results. It must be a brand-new angle.\n"
            "DOMAIN: single-period DCB snapshot; unit of business is the INSTALLATION "
            "(one metered connection). NO consumer-level detail, NO date/time column — "
            "never suggest trend/growth/over-time questions. The suggestion MUST be "
            "answerable using ONLY the columns below (a single SELECT).\n"
            "AVAILABLE COLUMNS (name: meaning):\n" + sm.follow_up_topics_text(),
            "Question just asked: " + question + "\n"
            "Result columns: " + ", ".join(cols) + "\n"
            "Row count: " + str(len(rows)) + "\n" + avoid_block +
            "\nReply with ONE short follow-up question (max ~12 words), no quotes, no "
            "preamble. It MUST use only the AVAILABLE COLUMNS and differ from every "
            "already-asked question above.",
            max_tokens=FOLLOWUP_MAX_TOKENS,
        )
        follow_up = fu.strip().strip('"').split("\n")[0][:120]
        if any(follow_up.lower() == a.lower() for a in already):
            return ""
        return follow_up
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
app = FastAPI(title="BESCOM NLP-to-SQL (unified)")
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
        "llm_configured": bool(GEMMA_API_KEY),
        "db_configured": bool(DATABASE_URL),
        "model": GEMMA_MODEL,
        "datasets": list(D.DATASETS.keys()),
    }


@app.get("/api/summary")
def summary():
    """Combined headline metrics (both datasets) for the landing page."""
    try:
        with db_cursor() as cur:
            cur.execute(f"""
                SELECT
                    COUNT(*)                                        AS records,
                    COUNT(DISTINCT zone)                            AS zones,
                    COUNT(DISTINCT circle)                          AS circles,
                    COUNT(DISTINCT division)                        AS divisions,
                    SUM(total_installations)                        AS total_installations,
                    SUM(active_installations)                       AS active_installations,
                    SUM(metered_installations)                      AS metered_installations,
                    ROUND(SUM(net_consumption)::numeric / 1e6, 1)   AS net_consumption_mu,
                    ROUND(SUM(net_collection)::numeric / 1e7, 1)    AS net_revenue_cr
                FROM {COMBINED_VIEW}
            """)
            r = cur.fetchone()
        return {k: _jsonable(v) for k, v in r.items()} | {"utility": "BESCOM", "dataset": "combined"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/query")
def query(inp: QueryIn):
    question = (inp.question or "").strip()
    if not question:
        return JSONResponse(status_code=400, content={"error": "Empty question."})

    if not GEMMA_API_KEY:
        return JSONResponse(status_code=503, content={
            "kind": "error",
            "answer": "The Gemma API key isn't configured yet. Add GEMMA_API_KEY "
                      "to unified/.env and restart the server.",
        })

    # ---- Route the question to a dataset (keyword match; neither -> combined) ----
    route = D.route(question)
    ds = D.DATASETS[route]
    sm, pa, label = ds["sm"], ds["pa"], ds["label"]

    history = [t.model_dump() for t in (inp.history or [])]
    system_prompt = pa.build_sql_system_prompt()
    user_prompt = pa.build_sql_user_prompt(question, history)

    # 1) NL -> SQL
    # NOTE: the 320-token cap here dated from gemma-4-12b's 4096-token TOTAL budget.
    # Gemini has a far larger context AND bills internal reasoning tokens against
    # max_tokens, so 320 truncated the combined wide-pivot query mid-literal (before
    # its FROM clause), which then failed the guardrail. 800 leaves ample headroom —
    # the pivot completes in ~94 visible tokens.
    try:
        raw = strip_fences(llm_generate(system_prompt, user_prompt, max_tokens=SQL_MAX_TOKENS))
    except Exception as e:
        return JSONResponse(status_code=502, content={
            "kind": "error", "dataset": route, "answer": f"Gemma request failed: {e}"})

    # 2) CLARIFY / REFUSE
    if raw.upper().startswith("CLARIFY:"):
        return {"kind": "clarify", "dataset": route, "answer": raw[len("CLARIFY:"):].strip(), "sql": None}
    if raw.upper().startswith("REFUSE:"):
        return {"kind": "refuse", "dataset": route, "answer": raw[len("REFUSE:"):].strip(), "sql": None}

    sql = raw

    # 3) Guardrails
    ok, reason = validate_sql(sql, sm)
    if not ok:
        return {"kind": "error", "dataset": route, "sql": sql,
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
                think=True, max_tokens=SQL_MAX_TOKENS,
            ))
        except Exception as e:
            return JSONResponse(status_code=502, content={
                "kind": "error", "dataset": route, "sql": sql,
                "answer": f"Query failed and recovery request errored: {e}"})
        if fixed.upper().startswith("REFUSE:"):
            return {"kind": "refuse", "dataset": route, "answer": fixed[len("REFUSE:"):].strip(), "sql": sql}
        ok2, reason2 = validate_sql(fixed, sm)
        if not ok2:
            return {"kind": "error", "dataset": route, "sql": fixed,
                    "answer": f"Recovery produced an unsafe query: {reason2}"}
        attempted.append(fixed)
        sql = fixed
        try:
            cols, rows = run_select(sql)
        except Exception as e2:
            return {"kind": "error", "dataset": route, "sql": sql,
                    "answer": f"The query still failed after one retry: {e2}"}

    kind = classify_result_kind(cols, rows)

    # 5+6) Answer + follow-up in parallel.
    preview = rows[:50]
    fut_answer = _POOL.submit(_format_answer, pa, question, preview, cols, rows, kind)
    fut_follow = _POOL.submit(_suggest_follow_up, sm, question, cols, rows, kind, inp.asked, label)
    answer = fut_answer.result()
    follow_up = fut_follow.result()

    return {
        "kind": kind,
        "dataset": route,
        "dataset_label": label,
        "answer": answer,
        "sql": sql,
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "follow_up": follow_up,
        "attempts": attempted,
    }


# --------------------------------------------------------------------------- #
# Serve the shared frontend
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
