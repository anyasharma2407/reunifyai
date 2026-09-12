#!/usr/bin/env bash
# Send a pair of records to the live n8n pipeline.
#
#   scripts/run_pipeline.sh                 # the demo pair, A-0013 and B-0013
#   scripts/run_pipeline.sh A-0001 B-0001   # any planted pair
#   WEBHOOK=https://...  scripts/run_pipeline.sh
#
# This calls the PRODUCTION webhook, which is always listening while the
# workflow is published. There is no "Execute workflow" to press first -- that
# button arms the separate test webhook, waits about two minutes, and accepts a
# single call, which is the wrong thing to rely on in front of an audience.
#
# The run takes roughly ten seconds: two of the stages call Claude. It ends
# parked at stage 15, waiting for a human, which is the point rather than a
# hang. Open the Executions tab in n8n to see where it stopped.

set -euo pipefail

A="${1:-A-0013}"
B="${2:-B-0013}"
WEBHOOK="${WEBHOOK:-https://anyasharma2407.app.n8n.cloud/webhook/reunify/compare}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "Building the request for $A and $B..."
PYTHONPATH="$ROOT" "$PY" "$ROOT/scripts/n8n_payload.py" "$A" "$B" > "$ROOT/.payload.json"
echo "  $(wc -c < "$ROOT/.payload.json" | tr -d ' ') bytes — the face embeddings travel in the request,"
echo "  so no photograph is sent anywhere."
echo
echo "Sending to the pipeline. This takes about ten seconds."
echo

curl -s --max-time 120 -X POST "$WEBHOOK" \
     -H 'Content-Type: application/json' \
     --data-binary "@$ROOT/.payload.json" > "$ROOT/.response.json"

"$PY" - "$ROOT/.response.json" <<'PY'
import json, sys
raw = open(sys.argv[1], encoding="utf-8").read()
try:
    d = json.loads(raw)
except Exception:
    print("  The pipeline did not return JSON:")
    print("  " + raw[:300])
    print()
    print("  Is the workflow published? Open it in n8n and check the Active toggle.")
    raise SystemExit(1)

if "case_id" not in d:
    print(json.dumps(d, indent=2))
    raise SystemExit(1)

print(f"  Case            {d['case_id']}")
print(f"  Match score     {d['potential_match_score']} / 100   ({d['band']})")
print(f"  Facial          {d['face_similarity']}%")
print(f"  Status          {d['status']}")
print()
print(f"  {d['face_notice']}")
print(f"  {d['review_notice']}")
PY

echo
echo "The run is now parked at stage 15, waiting for a human."
echo "See it in n8n: Overview -> Executions -> the newest row."
