"""
datasets.py — loads the three (schema, prompt) module pairs for the unified backend
==================================================================================
- RAPDRP      : reuses RAPDRP/backend/{schema_metadata,prompt_assembler}.py
- Non-RAPDRP  : reuses Non-RAPDRP/backend/{schema_metadata,prompt_assembler}.py
- Combined    : unified/backend/{schema_combined,prompt_combined}.py

The two per-dataset backends were written as standalone apps whose prompt_assembler
does `import schema_metadata`. To load BOTH without their `schema_metadata` names
clashing (and without editing those files), each pair is imported inside a temporary
sys.path + sys.modules swap so its `import schema_metadata` resolves to ITS OWN copy;
we then stash the resolved modules under unique names.

Also defines the keyword ROUTER that maps a question to 'RAPDRP' | 'Non-RAPDRP' |
'combined'.
"""
import os
import re
import sys
import importlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))


def _load_pair(dir_path: str, tag: str):
    """Import schema_metadata + prompt_assembler from dir_path as a private pair."""
    saved_path = list(sys.path)
    saved_sm = sys.modules.pop("schema_metadata", None)
    saved_pa = sys.modules.pop("prompt_assembler", None)
    try:
        sys.path.insert(0, dir_path)
        sm = importlib.import_module("schema_metadata")
        sm = importlib.reload(sm)                     # ensure it's THIS dir's copy
        pa = importlib.import_module("prompt_assembler")
        pa = importlib.reload(pa)                     # rebinds its `import schema_metadata`
    finally:
        # Stash under unique names so a later load can't overwrite these objects.
        sys.modules[f"_sm_{tag}"] = sys.modules.pop("schema_metadata")
        sys.modules[f"_pa_{tag}"] = sys.modules.pop("prompt_assembler")
        sys.path[:] = saved_path
        if saved_sm is not None:
            sys.modules["schema_metadata"] = saved_sm
        if saved_pa is not None:
            sys.modules["prompt_assembler"] = saved_pa
    return sm, pa


# --- RAPDRP and Non-RAPDRP: reuse the existing per-dataset modules ---
_r_sm, _r_pa = _load_pair(os.path.join(REPO, "RAPDRP", "backend"), "rapdrp")
_n_sm, _n_pa = _load_pair(os.path.join(REPO, "Non-RAPDRP", "backend"), "nonrapdrp")

# --- Combined: local modules (import normally; no schema_metadata clash) ---
import schema_combined as _c_sm       # noqa: E402
import prompt_combined as _c_pa       # noqa: E402


# A dataset descriptor = the label plus its schema + prompt modules.
DATASETS = {
    "RAPDRP":     {"label": "RAPDRP",     "sm": _r_sm, "pa": _r_pa},
    "Non-RAPDRP": {"label": "Non-RAPDRP", "sm": _n_sm, "pa": _n_pa},
    "combined":   {"label": "Combined (RAPDRP + Non-RAPDRP)", "sm": _c_sm, "pa": _c_pa},
}


# --------------------------------------------------------------------------- #
# Keyword router: rapdrp / non-rapdrp / combined
# --------------------------------------------------------------------------- #
# Order matters: "non rapdrp" / "non-rapdrp" must be tested BEFORE plain "rapdrp",
# because the RAPDRP pattern is a substring of the Non-RAPDRP one.
_NON_RE = re.compile(r"\bnon[\s\-_]*r\.?a\.?p\.?d\.?r\.?p\b", re.IGNORECASE)
_RAP_RE = re.compile(r"\br\.?a\.?p\.?d\.?r\.?p\b", re.IGNORECASE)


def route(question: str) -> str:
    """Return 'RAPDRP' | 'Non-RAPDRP' | 'combined' for a question.

    Mentions BOTH datasets ('RAPDRP and Non-RAPDRP ...') -> combined
        (the combined view carries a `dataset` tag column, so the model can
        GROUP BY dataset to show the two side by side / split by source).
    Mentions ONLY 'non-rapdrp' (any spacing/punctuation)  -> Non-RAPDRP.
    Mentions ONLY 'rapdrp'                                 -> RAPDRP.
    Mentions neither                                       -> combined (sum both).
    """
    q = question or ""
    has_non = _NON_RE.search(q) is not None
    # A plain-RAPDRP mention is any \brapdrp\b that is NOT part of a "non-rapdrp".
    # Blank out the non-rapdrp spans first so they don't count as bare RAPDRP.
    q_wo_non = _NON_RE.sub(" ", q)
    has_rap = _RAP_RE.search(q_wo_non) is not None

    # Both named -> combined, so a single query can split by the `dataset` tag.
    if has_non and has_rap:
        return "combined"
    if has_non:
        return "Non-RAPDRP"
    if has_rap:
        return "RAPDRP"
    return "combined"
