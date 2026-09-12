"""
Check the n8n workflow's scoring against the Python reference, across the corpus.

    python scripts/check_parity.py            # every planted pair
    python scripts/check_parity.py --limit 5

The scoring model exists twice in this repository: once in engine/match.py, and
once inside the workflow's Code nodes, which run in a sandbox that cannot import
any of this project's code. Two implementations of one rulebook drift apart
unless something checks them, and the drift is invisible -- both sides keep
returning plausible numbers.

What is asserted here is deliberately not bit-equality. The reference scorer
falls back to NYSIIS and Soundex when Metaphone does not fire, and the workflow
carries Metaphone only, so a handful of pairs differ by a fraction of a point.
What must hold is that no pair differs enough to matter and that the two never
disagree about the band -- because the band is what a caseworker acts on.

Exits non-zero if either invariant breaks.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAX_DIVERGENCE = 1.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="check only the first N pairs")
    ap.add_argument("--max-divergence", type=float, default=MAX_DIVERGENCE)
    args = ap.parse_args()

    from engine.face_embedding import load_face_index
    from engine.match import score_pair

    data = ROOT / "data"
    a_by_id = {r["record_id"]: r for r in json.loads((data / "registry_a.json").read_text())}
    b_by_id = {r["record_id"]: r for r in json.loads((data / "registry_b.json").read_text())}
    pairs = json.loads((data / "ground_truth.json").read_text())["pairs"]
    if args.limit:
        pairs = pairs[: args.limit]
    index = load_face_index()

    print(f"{'pair':<16}{'python':>8}{'n8n':>8}{'delta':>9}   band")
    deltas: list[float] = []
    band_mismatches = 0

    with tempfile.TemporaryDirectory() as tmp:
        payload_path = Path(tmp) / "payload.json"
        for pair in pairs:
            a, b = pair["a_record_id"], pair["b_record_id"]
            built = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "n8n_payload.py"), a, b],
                capture_output=True, text=True, cwd=ROOT,
                env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
            )
            if built.returncode != 0:
                print(f"{a}/{b}: could not build payload", file=sys.stderr)
                return 2
            payload_path.write_text(built.stdout)

            ref = score_pair(a_by_id[a], b_by_id[b], index)
            out = subprocess.run(
                ["node", str(ROOT / "scripts" / "verify_n8n_parity.mjs"), str(payload_path)],
                capture_output=True, text=True, cwd=ROOT,
            )
            line = next((l for l in out.stdout.splitlines()
                         if "potential_match_score:" in l), None)
            if line is None:
                print(f"{a}/{b}: workflow produced no score\n{out.stderr}", file=sys.stderr)
                return 2
            value = line.split("potential_match_score:")[1].strip()
            js_score = float(value.split()[0])
            js_band = value.split("(")[1].rstrip(")")

            delta = js_score - ref["potential_match_score"]
            deltas.append(abs(delta))
            same_band = js_band == ref["band"]
            if not same_band:
                band_mismatches += 1
            print(f"{a}/{b:<7}{ref['potential_match_score']:>8.1f}{js_score:>8.1f}"
                  f"{delta:>+9.2f}   {'ok' if same_band else 'MISMATCH'}")

    worst = max(deltas)
    exact = sum(1 for d in deltas if d == 0)
    print()
    print(f"pairs compared  : {len(deltas)}")
    print(f"exact matches   : {exact}")
    print(f"max divergence  : {worst:.2f}  (allowed {args.max_divergence:.2f})")
    print(f"mean divergence : {sum(deltas) / len(deltas):.3f}")
    print(f"band mismatches : {band_mismatches}")

    failed = False
    if band_mismatches:
        print("\nFAIL: the two implementations disagree about a band. That is the "
              "number a caseworker acts on, so it has to match.", file=sys.stderr)
        failed = True
    if worst > args.max_divergence:
        print(f"\nFAIL: divergence {worst:.2f} exceeds the allowed "
              f"{args.max_divergence:.2f}.", file=sys.stderr)
        failed = True
    if not failed:
        print("\nPASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
