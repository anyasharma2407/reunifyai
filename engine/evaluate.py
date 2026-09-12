"""
Evaluation harness: runs the matcher against the ground truth and reports
precision, recall and F1 so the demo can quote measured numbers.

The ground-truth file is read *only here* and in the reporting layer. The
matcher never sees it.

Two things are measured, because they answer different questions:

  * Top-1 accuracy -- if a case worker only ever looked at the single highest
    ranked candidate, how often would the right person be there? This is the
    honest number for an automated system, which is exactly what this is not.
  * Top-3 recall -- how often does the right person appear anywhere in the
    shortlist the reviewer is actually shown? This is the number that matters
    for a decision-support tool, and it is the one the demo should quote.

A score threshold sweep is also printed, because precision and recall are
meaningless without saying where the cut was made.

Usage
-----
    python -m engine.evaluate --threshold 60
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from engine.face_embedding import load_face_index
from engine.match import match_all

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load():
    a = json.loads((DATA_DIR / "registry_a.json").read_text())
    b = json.loads((DATA_DIR / "registry_b.json").read_text())
    gt = json.loads((DATA_DIR / "ground_truth.json").read_text())
    truth = {p["a_record_id"]: p["b_record_id"] for p in gt["pairs"]}
    detail = {p["a_record_id"]: p for p in gt["pairs"]}
    return a, b, truth, detail


def evaluate(results, truth, threshold):
    """
    Score the top-1 decision at a given potential-match-score threshold.

    A record is 'claimed' when its best candidate clears the threshold.
      * true positive  -- claimed, and it is the right person
      * false positive -- claimed, but it is the wrong person
      * false negative -- a real pair the engine failed to claim correctly
    Records with no true partner that the engine correctly leaves unclaimed are
    true negatives and are counted separately.
    """
    tp = fp = fn = tn = 0
    errors = []

    for res in results:
        a_id = res["a_record"]["record_id"]
        expected = truth.get(a_id)
        top = res["candidates"][0] if res["candidates"] else None
        claimed = top is not None and top["potential_match_score"] >= threshold
        predicted = top["b_record"]["record_id"] if claimed else None

        if claimed and predicted == expected:
            tp += 1
        elif claimed and predicted != expected:
            fp += 1
            if expected is not None:
                fn += 1
            errors.append({
                "a_record_id": a_id,
                # Distinguish "picked the wrong person" from "this person has no
                # partner in Registry B at all and the engine claimed one anyway".
                "kind": "wrong-person" if expected else "false-claim",
                "predicted": predicted, "expected": expected,
                "score": top["potential_match_score"],
            })
        elif not claimed and expected is not None:
            fn += 1
            errors.append({
                "a_record_id": a_id, "kind": "missed",
                "predicted": None, "expected": expected,
                "score": top["potential_match_score"] if top else 0.0,
            })
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "threshold": threshold, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1, "errors": errors,
    }


def topk_recall(results, truth, k):
    """Share of true pairs whose correct partner appears in the top k shown."""
    hit = total = 0
    for res in results:
        expected = truth.get(res["a_record"]["record_id"])
        if expected is None:
            continue
        total += 1
        if any(c["b_record"]["record_id"] == expected
               for c in res["candidates"][:k]):
            hit += 1
    return hit / total if total else 0.0, hit, total


def bar(value, width=28):
    filled = round(value * width)
    return "█" * filled + "·" * (width - filled)


def main():
    ap = argparse.ArgumentParser(description="Score the matcher against ground truth.")
    ap.add_argument("--threshold", type=float, default=75.0,
                    help="potential match score at which a top-1 candidate counts as a claim")
    ap.add_argument("--no-faces", action="store_true",
                    help="score without the facial similarity signal")
    ap.add_argument("--json", type=str, default=None,
                    help="also write the full report to this path")
    args = ap.parse_args()

    a, b, truth, detail = load()

    start = time.perf_counter()
    # Run with and without the face signal, so the contribution of facial
    # similarity is a measured number rather than a claim.
    face_index = None if args.no_faces else load_face_index()
    results = match_all(a, b, top_n=3, floor=0.0, face_index=face_index)
    elapsed = time.perf_counter() - start

    print("=" * 68)
    print("  REUNIFICATION ENGINE — evaluation against synthetic ground truth")
    print("=" * 68)
    print(f"  Registry A: {len(a)} records   Registry B: {len(b)} records")
    print(f"  Comparisons: {len(a) * len(b):,} in {elapsed:.2f}s "
          f"({len(a) * len(b) / elapsed:,.0f}/sec)")
    print(f"  True pairs planted: {len(truth)}")
    print()

    print("  TOP-3 SHORTLIST RECALL  (the decision-support number)")
    for k in (1, 2, 3):
        r, hit, total = topk_recall(results, truth, k)
        print(f"    top-{k}: {bar(r)}  {r:6.1%}  ({hit}/{total})")
    print()

    print("  TOP-1 PRECISION / RECALL BY THRESHOLD")
    print(f"    {'thresh':>7} {'precision':>10} {'recall':>8} {'F1':>7} "
          f"{'TP':>4} {'FP':>4} {'FN':>4}")
    for t in (40, 50, 55, 60, 65, 70, 75, 80):
        m = evaluate(results, truth, t)
        marker = "  <-- reported" if t == args.threshold else ""
        print(f"    {t:>7} {m['precision']:>9.1%} {m['recall']:>8.1%} "
              f"{m['f1']:>7.3f} {m['tp']:>4} {m['fp']:>4} {m['fn']:>4}{marker}")
    print()

    main_metrics = evaluate(results, truth, args.threshold)
    print(f"  AT THRESHOLD {args.threshold:g}")
    print(f"    Precision {main_metrics['precision']:.1%}   "
          f"Recall {main_metrics['recall']:.1%}   "
          f"F1 {main_metrics['f1']:.3f}")
    print()

    # Where the engine struggles, broken out by how damaged the pair was.
    by_diff: dict[str, list[int]] = {}
    for res in results:
        a_id = res["a_record"]["record_id"]
        if a_id not in truth:
            continue
        d = detail[a_id]["difficulty"]
        found = any(c["b_record"]["record_id"] == truth[a_id]
                    for c in res["candidates"][:3])
        by_diff.setdefault(d, []).append(1 if found else 0)

    print("  TOP-3 RECALL BY PLANTED DIFFICULTY")
    for d in ("easy", "moderate", "hard"):
        if d in by_diff:
            vals = by_diff[d]
            r = sum(vals) / len(vals)
            print(f"    {d:<9} {bar(r)}  {r:6.1%}  ({sum(vals)}/{len(vals)})")
    print()

    if main_metrics["errors"]:
        print(f"  ERRORS AT THRESHOLD {args.threshold:g} "
              f"({len(main_metrics['errors'])})")
        for e in main_metrics["errors"][:10]:
            exp = e["expected"] or "(no true partner)"
            print(f"    {e['a_record_id']}  {e['kind']:<12} "
                  f"predicted {str(e['predicted']):<8} expected {exp:<14} "
                  f"@ {e['score']:.0f}")
        if len(main_metrics["errors"]) > 10:
            print(f"    ... and {len(main_metrics['errors']) - 10} more")
        print()

    print("  Reminder: every record above is synthetic. These numbers describe")
    print("  the algorithm on generated data, not real-world performance.")
    print("=" * 68)

    if args.json:
        Path(args.json).write_text(json.dumps({
            "threshold": args.threshold,
            "elapsed_seconds": elapsed,
            "metrics": {k: v for k, v in main_metrics.items() if k != "errors"},
            "errors": main_metrics["errors"],
            "top_k_recall": {str(k): topk_recall(results, truth, k)[0] for k in (1, 2, 3)},
        }, indent=2))
        print(f"  Report written to {args.json}")


if __name__ == "__main__":
    main()
