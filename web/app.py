"""
Case-worker-facing API for the Reunification Engine.

Serves the review interface and exposes the matcher over HTTP. The service is
deliberately read-mostly: the only state it keeps is the reviewer's own triage
decisions, and nothing it returns is framed as a confirmed identification.

Run with:
    uvicorn web.app:app --reload --port 8000
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine.face_embedding import load_face_index
from engine.match import BASE_WEIGHTS, top_candidates

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Reunification Engine",
    description="Decision-support prototype for family tracing across "
                "disconnected registries. Synthetic data only.",
    version="0.1.0",
)


# --------------------------------------------------------------------------
# Corpus loading
# --------------------------------------------------------------------------

def _load():
    missing = [f for f in ("registry_a.json", "registry_b.json", "ground_truth.json")
               if not (DATA_DIR / f).exists()]
    if missing:
        raise RuntimeError(
            f"Missing data file(s): {', '.join(missing)}. "
            f"Generate the corpus first:  python -m engine.generate")
    a = json.loads((DATA_DIR / "registry_a.json").read_text())
    b = json.loads((DATA_DIR / "registry_b.json").read_text())
    gt = json.loads((DATA_DIR / "ground_truth.json").read_text())
    return a, b, gt


REGISTRY_A, REGISTRY_B, GROUND_TRUTH = _load()

# Face embeddings are optional: the engine runs without them and simply reports
# the face signal as unavailable, so a checkout that has not generated faces
# still serves.
try:
    FACE_INDEX = load_face_index()
except RuntimeError as exc:                    # backend mismatch
    print(f"Face matching disabled: {exc}")
    FACE_INDEX = None
A_BY_ID = {r["record_id"]: r for r in REGISTRY_A}
B_BY_ID = {r["record_id"]: r for r in REGISTRY_B}

# Ground truth is loaded ONLY to power the demo-mode overlay and the "show me a
# hard case" shortcut. It is never passed to the matcher.
TRUTH_BY_A = {p["a_record_id"]: p for p in GROUND_TRUTH["pairs"]}

# In-memory triage log. A prototype convenience: it is not a system of record,
# and it is discarded when the process stops.
REVIEW_LOG: dict[str, dict] = {}

# Filled on first request to /api/queue.
_QUEUE_CACHE: list[dict] | None = None


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

class ReviewDecision(BaseModel):
    a_record_id: str
    b_record_id: str
    decision: str        # "refer" | "rule_out"
    reviewer_note: str = ""


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.get("/api/meta")
def meta():
    """Corpus summary and the standing disclaimers the UI must display."""
    return {
        "registry_a_count": len(REGISTRY_A),
        "registry_b_count": len(REGISTRY_B),
        "comparisons": len(REGISTRY_A) * len(REGISTRY_B),
        "true_pairs": len(TRUTH_BY_A),
        "synthetic": True,
        "face_matching": FACE_INDEX is not None,
        "face_backend": FACE_INDEX.meta.get("backend") if FACE_INDEX else None,
        "face_dimensions": FACE_INDEX.meta.get("dimensions") if FACE_INDEX else None,
        "weights": {k: round(v, 2) for k, v in BASE_WEIGHTS.items()},
        "notice": "All records are synthetic. This tool ranks candidates for "
                  "human review and never confirms an identity.",
        "face_notice": "FACIAL SIMILARITY IS AN INDICATOR, NOT PROOF OF IDENTITY.",
        "review_notice": "HUMAN VERIFICATION REQUIRED.",
    }


@app.get("/api/records")
def records(q: str = Query("", description="name, place or record id"),
            limit: int = 60):
    """Search Registry A. This is the reviewer's entry point."""
    needle = q.strip().casefold()
    matched = []
    for r in REGISTRY_A:
        haystack = " ".join(str(r.get(k, "")) for k in (
            "record_id", "given_name", "family_name", "nationality",
            "origin_place", "last_seen_place", "source_org")).casefold()
        if not needle or needle in haystack:
            matched.append({
                **r,
                "reviewed": REVIEW_LOG.get(r["record_id"], {}).get("decision"),
            })
    # `total` is how many matched, `count` how many are being returned. The two
    # differ once the limit bites, and conflating them made the sidebar report
    # "60 records" for an 80-record registry.
    out = matched[:limit]
    return {"count": len(out), "total": len(matched),
            "truncated": len(matched) > len(out), "records": out}


@app.get("/api/records/{record_id}")
def record_detail(record_id: str):
    rec = A_BY_ID.get(record_id)
    if rec is None:
        raise HTTPException(404, f"No record {record_id} in Registry A")
    return rec


@app.get("/api/match/{record_id}")
def match(record_id: str, top_n: int = 3, reveal: bool = False):
    """
    Rank Registry B against one Registry A record.

    `reveal=true` attaches the planted ground truth for demo narration. It is
    applied *after* scoring and never influences the ranking.
    """
    rec = A_BY_ID.get(record_id)
    if rec is None:
        raise HTTPException(404, f"No record {record_id} in Registry A")

    candidates = top_candidates(rec, REGISTRY_B, top_n=top_n, floor=25.0,
                                face_index=FACE_INDEX)
    payload = {
        "a_record": rec,
        "candidates": candidates,
        "compared_against": len(REGISTRY_B),
        "review_required": True,
        "notice": "Ranked candidates for human review. Not an identification.",
        "decision": REVIEW_LOG.get(record_id),
    }

    if reveal:
        truth = TRUTH_BY_A.get(record_id)
        payload["ground_truth"] = {
            "has_true_partner": truth is not None,
            "b_record_id": truth["b_record_id"] if truth else None,
            "difficulty": truth["difficulty"] if truth else None,
            "divergences": truth["divergences"] if truth else [],
            "note": "Demo overlay only — the matcher never sees this.",
        }
    return payload


@app.get("/api/queue")
def queue(limit: int = 12):
    """
    The review queue: which records have a candidate worth a person's time.

    This is the thing the project claims to do -- help an organisation decide
    what to look at first -- so it is the list the interface opens on. Records
    the reviewer has already dealt with drop out; a worklist that keeps showing
    you what you have finished is not a worklist.

    Scored on demand and cached. At demo scale the full cross-comparison takes
    well under a second, so there is nothing to gain from precomputing it here
    and something to lose in staleness.
    """
    global _QUEUE_CACHE
    if _QUEUE_CACHE is None:
        rows = []
        for rec in REGISTRY_A:
            cands = top_candidates(rec, REGISTRY_B, top_n=3, floor=25.0,
                                   face_index=FACE_INDEX)
            if not cands:
                continue
            top = cands[0]
            rows.append({
                "a_record_id": rec["record_id"],
                "display_name": f"{rec['given_name']} {rec['family_name']}",
                "score": top["potential_match_score"],
                "band": top["band"],
                "candidate_count": len(cands),
                "face_similarity": top.get("face_similarity"),
            })
        rows.sort(key=lambda r: -r["score"])
        _QUEUE_CACHE = rows

    pending = [r for r in _QUEUE_CACHE if r["a_record_id"] not in REVIEW_LOG]
    return {
        "queue": pending[:limit],
        "waiting": len(pending),
        "reviewed": len(_QUEUE_CACHE) - len(pending),
        "notice": "Ranked by the strongest candidate found for each record. "
                  "A high score means look sooner, not that a match is confirmed.",
    }


@app.get("/api/showcase")
def showcase(limit: int = 6):
    """
    A few deliberately difficult planted pairs, for the pitch.

    Picks the pairs with the most recorded divergence between registries, so a
    3-minute demo can open on a case that actually looks hard.
    """
    hard = sorted(
        (p for p in GROUND_TRUTH["pairs"] if p["difficulty"] == "hard"),
        key=lambda p: len(p["divergences"]), reverse=True)
    out = []
    for p in hard[:limit]:
        rec = A_BY_ID.get(p["a_record_id"])
        if not rec:
            continue
        out.append({
            "a_record_id": p["a_record_id"],
            "display_name": f"{rec['given_name']} {rec['family_name']}",
            "obstacle_count": len(p["divergences"]),
            "headline": p["divergences"][0] if p["divergences"] else "",
        })
    return {"cases": out}


@app.post("/api/review")
def review(decision: ReviewDecision):
    """
    Record a reviewer's triage decision.

    'refer' means a human thought this pair worth verifying through proper
    channels. It is explicitly not a confirmation, and the prototype stores it
    in memory only.
    """
    if decision.decision not in ("refer", "rule_out"):
        raise HTTPException(400, "decision must be 'refer' or 'rule_out'")
    if decision.a_record_id not in A_BY_ID:
        raise HTTPException(404, f"No record {decision.a_record_id} in Registry A")
    if decision.b_record_id not in B_BY_ID:
        raise HTTPException(404, f"No record {decision.b_record_id} in Registry B")

    entry = {
        **decision.model_dump(),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "referred for human verification" if decision.decision == "refer"
                  else "ruled out by reviewer",
    }
    REVIEW_LOG[decision.a_record_id] = entry
    return entry


@app.delete("/api/review/{a_record_id}")
def clear_review(a_record_id: str):
    REVIEW_LOG.pop(a_record_id, None)
    return {"cleared": a_record_id}


@app.get("/api/review")
def review_log():
    return {"count": len(REVIEW_LOG), "decisions": list(REVIEW_LOG.values())}


class EmbedRequest(BaseModel):
    """Either record ids held by this service, or paths under data/faces."""
    images: list[str]


@app.post("/api/face/embed")
def face_embed(req: EmbedRequest):
    """
    The embedding endpoint the n8n workflow's stage 06 calls.

    It exists for the case where a partner organisation can only send image
    references. The preferred path is the other one: each organisation embeds
    locally and sends vectors, so no photograph of a displaced person crosses
    an organisational boundary at all.

    This prototype will only embed images from its own synthetic corpus. It
    deliberately does not fetch arbitrary URLs -- an endpoint that accepts
    "embed this face for me" from anywhere is a different and far more
    dangerous thing than the one this project is demonstrating.
    """
    if FACE_INDEX is None:
        raise HTTPException(503, "Face matching is not enabled on this instance")
    if len(req.images) != 2:
        raise HTTPException(400, "Exactly two images are required")

    out = []
    for ref in req.images:
        record_id = ref.rsplit("/", 1)[-1].removesuffix(".png")
        vec = FACE_INDEX.get(record_id)
        if vec is None:
            raise HTTPException(
                404, f"No synthetic face held for '{record_id}'. This endpoint "
                     f"only embeds images from its own corpus.")
        out.append([round(float(v), 5) for v in vec])

    return {
        "embeddings": out,
        "backend": FACE_INDEX.meta.get("backend"),
        "dimensions": FACE_INDEX.meta.get("dimensions"),
        "synthetic": True,
        "notice": "Embeddings of procedurally drawn synthetic faces.",
    }


@app.get("/api/demo")
def demo():
    """
    The scripted walk-through: one planted pair, with the pipeline stages the
    UI animates through before revealing the score.

    The stages are labels for work the engine really does -- the scores that
    appear at the end are computed by the same code path as every other match,
    not stored. Picking the pair is the only thing scripted about it.
    """
    best = None
    for pair in GROUND_TRUTH["pairs"]:
        a = A_BY_ID.get(pair["a_record_id"])
        b = B_BY_ID.get(pair["b_record_id"])
        if not a or not b or pair["difficulty"] != "hard":
            continue
        if not (a.get("family_members") and b.get("family_members")):
            continue
        from engine.match import score_pair
        scored = score_pair(a, b, FACE_INDEX)
        # A demo case should be hard on paper but still resolve convincingly.
        if scored["potential_match_score"] < 85:
            continue
        rank = len(pair["divergences"])
        if best is None or rank > best[0]:
            best = (rank, pair, a, b, scored)

    if best is None:
        raise HTTPException(503, "No suitable demo pair in this corpus")

    _, pair, a, b, scored = best
    return {
        "a_record": a,
        "b_record": b,
        "result": scored,
        "obstacles": pair["divergences"],
        "stages": [
            {"key": "ingest", "label": "Receiving records from two registries"},
            {"key": "normalise", "label": "Normalising names, dates and places"},
            {"key": "faces", "label": "Generating face embeddings"},
            {"key": "face_compare", "label": "Comparing faces"},
            {"key": "names", "label": "Comparing names"},
            {"key": "context", "label": "Comparing family, location and age"},
            {"key": "score", "label": "Calculating potential match score"},
        ],
        "notice": "Potential match detected — for human review only.",
        "face_notice": "FACIAL SIMILARITY IS AN INDICATOR, NOT PROOF OF IDENTITY.",
        "review_notice": "HUMAN VERIFICATION REQUIRED.",
    }


@app.get("/")
def index():
    # The page is told never to be served from cache without revalidating.
    # Without this the browser keeps its copy of the HTML, which still points
    # at the previous ?v= asset URLs, so a CSS fix appears not to have worked
    # no matter how many times the stylesheet is cache-busted.
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# The synthetic photographs. Served read-only and clearly namespaced, so it is
# obvious in the network tab that these are corpus assets and not uploads.
if (DATA_DIR / "faces").exists():
    app.mount("/faces", StaticFiles(directory=DATA_DIR / "faces"), name="faces")
