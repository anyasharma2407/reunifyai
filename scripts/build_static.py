"""
Build a static copy of the review interface.

    python scripts/build_static.py --out dist

The matcher is deterministic over a fixed corpus, so every answer the API could
give is computable in advance. This precomputes all of them -- 6,400
comparisons -- and writes the results as JSON next to the frontend, which then
runs with no server at all.

Why bother: free hosting for a long-running container now generally wants a
card, and a demo that falls over because a billing account lapsed is a bad
demo. A static build has no cold start, nothing to keep awake, and nothing to
pay for.

What is lost: the reviewer's triage log, which the server kept in memory and
discarded on restart. In the static build it lives in the visitor's browser.
Nothing else changes -- the same app.js, demo.js and stylesheet are shipped, so
there is only ever one frontend to maintain.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
STATIC = ROOT / "web" / "static"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="dist", help="output directory")
    ap.add_argument("--top-n", type=int, default=3)
    args = ap.parse_args()

    from engine.face_embedding import load_face_index
    from engine.match import BASE_WEIGHTS, score_pair, top_candidates

    missing = [f for f in ("registry_a.json", "registry_b.json", "ground_truth.json")
               if not (DATA / f).exists()]
    if missing:
        print(f"Missing {', '.join(missing)}. Run: python -m engine.generate",
              file=sys.stderr)
        return 1

    registry_a = json.loads((DATA / "registry_a.json").read_text())
    registry_b = json.loads((DATA / "registry_b.json").read_text())
    truth = json.loads((DATA / "ground_truth.json").read_text())
    a_by_id = {r["record_id"]: r for r in registry_a}
    b_by_id = {r["record_id"]: r for r in registry_b}
    truth_by_a = {p["a_record_id"]: p for p in truth["pairs"]}

    face_index = load_face_index()

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "api" / "match").mkdir(parents=True)

    # --- frontend ---------------------------------------------------------
    (out / "static").mkdir(parents=True)
    for name in ("app.js", "demo.js", "style.css", "static-mode.js",
                 "engine.js", "tryit.js", "nearby.js"):
        shutil.copy2(STATIC / name, out / "static" / name)

    html = (STATIC / "index.html").read_text()
    # The shim has to be installed before app.js runs, because app.js fetches
    # on load.
    html = html.replace('<script src="/static/app.js',
                        '<script src="/static/static-mode.js?v=6"></script>\n'
                        '<script src="/static/app.js')
    (out / "index.html").write_text(html)

    # --- faces ------------------------------------------------------------
    faces_src = DATA / "faces"
    if faces_src.exists():
        shutil.copytree(faces_src, out / "faces")
        face_count = len(list((out / "faces").glob("*.png")))
    else:
        face_count = 0

    # --- precomputed responses -------------------------------------------
    (out / "api" / "meta.json").write_text(json.dumps({
        "registry_a_count": len(registry_a),
        "registry_b_count": len(registry_b),
        "comparisons": len(registry_a) * len(registry_b),
        "true_pairs": len(truth_by_a),
        "synthetic": True,
        "face_matching": face_index is not None,
        "face_backend": face_index.meta.get("backend") if face_index else None,
        "face_dimensions": face_index.meta.get("dimensions") if face_index else None,
        "weights": {k: round(v, 2) for k, v in BASE_WEIGHTS.items()},
        "notice": "All records are synthetic. This tool ranks candidates for "
                  "human review and never confirms an identity.",
        "face_notice": "FACIAL SIMILARITY IS AN INDICATOR, NOT PROOF OF IDENTITY.",
        "review_notice": "HUMAN VERIFICATION REQUIRED.",
        "static_build": True,
    }))

    (out / "api" / "records.json").write_text(json.dumps({"records": registry_a}))

    # Face embeddings, for scoring records a visitor types in. Loaded lazily by
    # tryit.js -- it is 2.4 MB, and most visitors never open that panel.
    if face_index is not None:
        cal = face_index.service.calibration
        (out / "api" / "embeddings.json").write_text(json.dumps({
            "embeddings": {k: [round(float(x), 5) for x in v]
                           for k, v in face_index.embeddings.items()},
            "calibration": {"background_mean": cal.background_mean,
                            "background_sd": cal.background_sd},
            "backend": face_index.meta.get("backend"),
            "dimensions": face_index.meta.get("dimensions"),
            "notice": "Embeddings of procedurally drawn synthetic faces.",
        }))

    print(f"Scoring {len(registry_a)} x {len(registry_b)} "
          f"= {len(registry_a) * len(registry_b):,} comparisons...")
    for rec in registry_a:
        rid = rec["record_id"]
        candidates = top_candidates(rec, registry_b, top_n=args.top_n,
                                    floor=25.0, face_index=face_index)
        gt = truth_by_a.get(rid)
        payload = {
            "a_record": rec,
            "candidates": candidates,
            "compared_against": len(registry_b),
            "review_required": True,
            "notice": "Ranked candidates for human review. Not an identification.",
            "decision": None,
            "ground_truth": {
                "has_true_partner": gt is not None,
                "b_record_id": gt["b_record_id"] if gt else None,
                "difficulty": gt["difficulty"] if gt else None,
                "divergences": gt["divergences"] if gt else [],
                "note": "Demo overlay only — the matcher never sees this.",
            },
        }
        (out / "api" / "match" / f"{rid}.json").write_text(json.dumps(payload))

    # --- showcase ---------------------------------------------------------
    hard = sorted((p for p in truth["pairs"] if p["difficulty"] == "hard"),
                  key=lambda p: len(p["divergences"]), reverse=True)
    cases = []
    for p in hard[:6]:
        rec = a_by_id.get(p["a_record_id"])
        if rec:
            cases.append({
                "a_record_id": p["a_record_id"],
                "display_name": f"{rec['given_name']} {rec['family_name']}",
                "obstacle_count": len(p["divergences"]),
                "headline": p["divergences"][0] if p["divergences"] else "",
            })
    (out / "api" / "showcase.json").write_text(json.dumps({"cases": cases}))

    # --- demo -------------------------------------------------------------
    best = None
    for pair in truth["pairs"]:
        a, b = a_by_id.get(pair["a_record_id"]), b_by_id.get(pair["b_record_id"])
        if not a or not b or pair["difficulty"] != "hard":
            continue
        if not (a.get("family_members") and b.get("family_members")):
            continue
        scored = score_pair(a, b, face_index)
        if scored["potential_match_score"] < 85:
            continue
        rank = len(pair["divergences"])
        if best is None or rank > best[0]:
            best = (rank, pair, a, b, scored)

    if best is None:
        print("No suitable demo pair in this corpus", file=sys.stderr)
        return 1

    _, pair, a, b, scored = best
    (out / "api" / "demo.json").write_text(json.dumps({
        "a_record": a, "b_record": b, "result": scored,
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
    }))

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"Records    : {len(registry_a)} match files")
    print(f"Faces      : {face_count}")
    print(f"Demo pair  : {a['record_id']}/{b['record_id']} -> "
          f"{scored['potential_match_score']} "
          f"(face {scored['face_similarity']})")
    print(f"Output     : {out}  ({total / 1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
