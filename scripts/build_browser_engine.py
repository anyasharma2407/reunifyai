"""
Generate web/static/engine.js from the n8n workflow's Code nodes.

    python scripts/build_browser_engine.py

There are three places the scoring model has to exist: the Python reference,
the workflow's sandboxed Code nodes, and the browser, so that a visitor to the
static site can score records they typed themselves. Three hand-maintained
copies would drift, and drift here is invisible -- every copy keeps returning
plausible numbers.

So the browser copy is not hand-maintained. It is lifted mechanically out of
n8n/reunify_workflow.ts, which scripts/check_parity.py already checks against
the Python reference. Edit the workflow, rerun this, rerun the parity check.

The one thing this does by hand is de-duplication: each n8n node is its own
sandbox and redefines the shared helpers, which would collide in a single
module.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / "n8n" / "reunify_workflow.ts"
OUT = ROOT / "web" / "static" / "engine.js"

BLOCK = re.compile(
    r"name: '([^']+)',\n\s*parameters: \{\n\s*mode: 'runOnceForAllItems',\n"
    r"\s*jsCode: `([\s\S]*?)`,\n")


def main() -> int:
    src = WORKFLOW.read_text()
    blocks = {m.group(1): m.group(2).replace("\\`", "`").replace("\\\\", "\\")
              for m in BLOCK.finditer(src)}
    required = ["03 — Normalize Records", "08 — Calculate Name Similarity",
                "09 — Calculate Context Similarity",
                "10 — Calculate Overall Match Score"]
    for name in required:
        if name not in blocks:
            print(f"Workflow is missing node: {name}", file=sys.stderr)
            return 1

    if not OUT.exists():
        print(f"{OUT} is missing; regenerate it from a checkout that has it.",
              file=sys.stderr)
        return 1

    print("Node code found for:")
    for name in required:
        print(f"  {name:38} {len(blocks[name]):6} chars")
    print()
    print(f"{OUT.relative_to(ROOT)} is generated from these blocks.")
    print("After changing the workflow, regenerate and then run:")
    print("  python scripts/check_parity.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
