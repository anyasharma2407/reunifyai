"""
Build a request body for the n8n workflow's webhook.

    python scripts/n8n_payload.py A-0013 B-0013 > /tmp/payload.json
    curl -X POST -H 'Content-Type: application/json' \
         -d @/tmp/payload.json <your n8n webhook url>

The payload carries the face *embeddings* rather than the images, which is the
arrangement the pipeline is built around: two organisations can ask whether
they hold records for the same person without either of them transmitting a
photograph. It also carries the calibration constants, because a similarity
percentage is only meaningful relative to the population it was measured
against -- shipping the score without the baseline it was calibrated on would
invite the reader to treat it as a probability of identity.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("a_record_id", help="e.g. A-0013")
    ap.add_argument("b_record_id", help="e.g. B-0013")
    ap.add_argument("--threshold", type=float, default=60.0,
                    help="score at or above which a review case is opened")
    ap.add_argument("--no-embeddings", action="store_true",
                    help="send image references instead, so stage 06 has to run")
    args = ap.parse_args()

    a_by_id = {r["record_id"]: r for r in json.loads((DATA / "registry_a.json").read_text())}
    b_by_id = {r["record_id"]: r for r in json.loads((DATA / "registry_b.json").read_text())}

    a = a_by_id.get(args.a_record_id)
    b = b_by_id.get(args.b_record_id)
    if a is None or b is None:
        missing = args.a_record_id if a is None else args.b_record_id
        print(f"No such record: {missing}", file=sys.stderr)
        return 1

    embeddings = json.loads((DATA / "face_embeddings.json").read_text())["embeddings"]
    calibration = json.loads((DATA / "face_calibration.json").read_text())

    def payload_for(rec: dict) -> dict:
        out = {k: v for k, v in rec.items() if k != "face_image"}
        if args.no_embeddings:
            out["face_image_url"] = f"http://localhost:8000/{rec['face_image']}"
        else:
            out["face_embedding"] = embeddings.get(rec["record_id"])
        return out

    json.dump({
        "record_a": payload_for(a),
        "record_b": payload_for(b),
        "options": {
            "review_threshold": args.threshold,
            "face_calibration": {
                "background_mean": calibration["background_mean"],
                "background_sd": calibration["background_sd"],
            },
            "face_service_url": "http://localhost:8000/api/face/embed",
        },
        "_notice": "Synthetic records and procedurally drawn faces. No real person is represented.",
    }, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
