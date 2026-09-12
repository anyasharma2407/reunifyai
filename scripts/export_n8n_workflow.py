"""
Emit n8n/reunify_workflow.json — the pipeline in n8n's own import format.

    python scripts/export_n8n_workflow.py

The repository already holds the pipeline as SDK code, which is the readable
form: the reasoning lives in the comments next to the rules. That is the right
artefact for someone auditing the logic and the wrong one for someone who just
wants to run it, because n8n imports JSON and nothing else.

So this emits the importable copy rather than it being maintained by hand.
Every Code node's body is read out of n8n/reunify_workflow.ts, so the two can
never disagree about what the pipeline actually does; only the graph and the
node settings are described here.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "n8n" / "reunify_workflow.ts"
TARGET = ROOT / "n8n" / "reunify_workflow.json"

CODE_BLOCK = re.compile(
    r"name: '([^']+)',\n\s*parameters: \{\n\s*mode: 'runOnceForAllItems',\n"
    r"\s*jsCode: `([\s\S]*?)`,\n")

EXPR = lambda s: "=" + s          # n8n marks expressions with a leading '='

# Nodes that are not Code nodes. Code nodes are generated from the source.
STATIC_NODES = [
    ("01 — Receive NGO Records", "n8n-nodes-base.webhook", 2.1, [0, 208], {
        "httpMethod": "POST", "path": "reunify/compare",
        "responseMode": "responseNode"}, {}),
    ("Input usable?", "n8n-nodes-base.if", 2.3, [448, 208], {
        "conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
            "conditions": [{"id": "valid", "leftValue": "={{ $json.ok }}",
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true",
                                         "singleValue": True}}],
            "combinator": "and"}}, {}),
    ("Reject Malformed Request", "n8n-nodes-base.respondToWebhook", 1.5, [672, 304], {
        "respondWith": "json", "responseCode": 400,
        "responseBody": EXPR('{{ JSON.stringify({ error: "invalid request", '
                             'problems: $json.problems }) }}')}, {}),
    ("Claude", "@n8n/n8n-nodes-langchain.lmChatAnthropic", 1.6, [976, 336], {
        "model": {"__rl": True, "mode": "list", "value": "claude-sonnet-5"}}, {}),
    ("Claude (explanation)", "@n8n/n8n-nodes-langchain.lmChatAnthropic", 1.6, [2896, 336], {
        "model": {"__rl": True, "mode": "list", "value": "claude-sonnet-5"}}, {}),
    ("04 — AI Entity Extraction", "@n8n/n8n-nodes-langchain.chainLlm", 1.9, [896, 112], {
        "promptType": "define",
        "text": EXPR("Two humanitarian registries each hold a free-text intake note. "
                     "Pull out only what is explicitly stated. Never infer, never fill "
                     "gaps.\n\nReturn strict JSON: "
                     '{"a":{"places":[],"relations":[],"dates":[]},'
                     '"b":{"places":[],"relations":[],"dates":[]}}\n\n'
                     "Note A: {{ $json.a.notes }}\nNote B: {{ $json.b.notes }}")},
     {"executeOnce": True, "onError": "continueRegularOutput"}),
    ("06 — Generate Face Embeddings", "n8n-nodes-base.httpRequest", 4.3, [1472, 176], {
        "method": "POST",
        "url": EXPR('{{ $json.options.face_service_url || '
                    '"http://localhost:8000/api/face/embed" }}'),
        "sendBody": True, "specifyBody": "json",
        "jsonBody": EXPR("{{ JSON.stringify({ images: [$json.a.face_image_url, "
                         "$json.b.face_image_url] }) }}"),
        "options": {"timeout": 20000}}, {"onError": "continueRegularOutput"}),
    ("Collect Face Vectors", "n8n-nodes-base.merge", 3.2, [1696, 112], {
        "mode": "combine", "combineBy": "combineAll"}, {}),
    ("11 — Generate Explanation", "@n8n/n8n-nodes-langchain.chainLlm", 1.9, [2816, 112], {
        "promptType": "define",
        "text": EXPR("You are writing one short paragraph for a humanitarian caseworker "
                     "explaining why two records were put in front of them.\n\n"
                     "Rules you must follow:\n"
                     "- Never state or imply the records are the same person.\n"
                     "- Say plainly that facial similarity alone does not establish "
                     "identity or a family relationship.\n"
                     "- Describe only the signals below. Do not invent evidence.\n"
                     "- End by stating that human verification is required.\n\n"
                     "Potential match score: {{ $json.potential_match_score }} "
                     "({{ $json.band }})\n"
                     "Evidence coverage: {{ $json.evidence_coverage }}\n"
                     "Signals: {{ JSON.stringify($json.signals) }}\n"
                     "Flags: {{ JSON.stringify($json.flags) }}")},
     {"executeOnce": True, "onError": "continueRegularOutput"}),
    ("12 — Filter Priority Matches", "n8n-nodes-base.filter", 2.3, [3392, 112], {
        "conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
            "conditions": [{"id": "above-threshold",
                            "leftValue": "={{ $json.potential_match_score }}",
                            "rightValue": "={{ $json.options.review_threshold || 60 }}",
                            "operator": {"type": "number", "operation": "gte"}}],
            "combinator": "and"}}, {}),
    ("Respond to Calling System", "n8n-nodes-base.respondToWebhook", 1.5, [3840, 112], {
        "respondWith": "json", "responseCode": 202,
        "responseBody": EXPR("{{ JSON.stringify({ case_id: $json.review_case.case_id, "
                             "potential_match_score: $json.potential_match_score, "
                             "band: $json.band, "
                             "face_similarity: $json.review_case.face_similarity, "
                             'status: "awaiting_human_review", notice: $json.notice, '
                             "face_notice: $json.face_notice, "
                             "review_notice: $json.review_notice }) }}")}, {}),
    ("14 — Notify Caseworker", "n8n-nodes-base.httpRequest", 4.3, [4064, 112], {
        "method": "POST",
        "url": EXPR('{{ $json.options.caseworker_webhook_url || '
                    '"https://example.invalid/caseworker" }}'),
        "sendBody": True, "specifyBody": "json",
        "jsonBody": EXPR('{{ JSON.stringify({ text: "Potential match for review: " + '
                         '$json.review_case.case_id + " (" + '
                         '$json.potential_match_score + "/100). HUMAN VERIFICATION '
                         'REQUIRED.", review_case: $json.review_case }) }}'),
        "options": {"timeout": 15000}}, {"onError": "continueRegularOutput"}),
    ("15 — Wait for Human Review", "n8n-nodes-base.wait", 1.1, [4288, 112], {
        "resume": "webhook", "httpMethod": "POST", "responseMode": "lastNode",
        "options": {}}, {}),
]

CODE_POSITIONS = {
    "02 — Validate Input": [224, 208],
    "03 — Normalize Records": [672, 112],
    "05 — Prepare Face Images": [1248, 112],
    "07 — Calculate Facial Similarity": [1920, 112],
    "08 — Calculate Name Similarity": [2144, 112],
    "09 — Calculate Context Similarity": [2368, 112],
    "10 — Calculate Overall Match Score": [2592, 112],
    "Attach Explanation": [3168, 112],
    "13 — Create Review Case": [3616, 112],
    "16 — Update Case Status": [4512, 112],
    "17 — Write Audit Log": [4736, 112],
}

CONNECTIONS = [
    ("01 — Receive NGO Records", 0, "02 — Validate Input", 0, "main"),
    ("02 — Validate Input", 0, "Input usable?", 0, "main"),
    ("Input usable?", 0, "03 — Normalize Records", 0, "main"),
    ("Input usable?", 1, "Reject Malformed Request", 0, "main"),
    ("03 — Normalize Records", 0, "04 — AI Entity Extraction", 0, "main"),
    ("Claude", 0, "04 — AI Entity Extraction", 0, "ai_languageModel"),
    ("04 — AI Entity Extraction", 0, "05 — Prepare Face Images", 0, "main"),
    ("05 — Prepare Face Images", 0, "Collect Face Vectors", 0, "main"),
    ("05 — Prepare Face Images", 0, "06 — Generate Face Embeddings", 0, "main"),
    ("06 — Generate Face Embeddings", 0, "Collect Face Vectors", 1, "main"),
    ("Collect Face Vectors", 0, "07 — Calculate Facial Similarity", 0, "main"),
    ("07 — Calculate Facial Similarity", 0, "08 — Calculate Name Similarity", 0, "main"),
    ("08 — Calculate Name Similarity", 0, "09 — Calculate Context Similarity", 0, "main"),
    ("09 — Calculate Context Similarity", 0, "10 — Calculate Overall Match Score", 0, "main"),
    ("10 — Calculate Overall Match Score", 0, "11 — Generate Explanation", 0, "main"),
    ("Claude (explanation)", 0, "11 — Generate Explanation", 0, "ai_languageModel"),
    ("11 — Generate Explanation", 0, "Attach Explanation", 0, "main"),
    ("Attach Explanation", 0, "12 — Filter Priority Matches", 0, "main"),
    ("12 — Filter Priority Matches", 0, "13 — Create Review Case", 0, "main"),
    ("13 — Create Review Case", 0, "Respond to Calling System", 0, "main"),
    ("Respond to Calling System", 0, "14 — Notify Caseworker", 0, "main"),
    ("14 — Notify Caseworker", 0, "15 — Wait for Human Review", 0, "main"),
    ("15 — Wait for Human Review", 0, "16 — Update Case Status", 0, "main"),
    ("16 — Update Case Status", 0, "17 — Write Audit Log", 0, "main"),
]


def main() -> int:
    src = SOURCE.read_text()
    bodies = {m.group(1): m.group(2).replace("\\`", "`").replace("\\\\", "\\")
              for m in CODE_BLOCK.finditer(src)}

    missing = [n for n in CODE_POSITIONS if n not in bodies]
    if missing:
        print("No code found in the source for: " + ", ".join(missing), file=sys.stderr)
        return 1

    nodes = []
    for name, ntype, version, pos, params, extra in STATIC_NODES:
        nodes.append(dict({"parameters": params, "type": ntype,
                           "typeVersion": version, "position": pos,
                           "name": name}, **extra))
    for name, pos in CODE_POSITIONS.items():
        nodes.append({"parameters": {"mode": "runOnceForAllItems",
                                     "jsCode": bodies[name]},
                      "type": "n8n-nodes-base.code", "typeVersion": 2,
                      "position": pos, "name": name})

    by_name = {n["name"] for n in nodes}
    unknown = {e for c in CONNECTIONS for e in (c[0], c[2])} - by_name
    if unknown:
        print("Connections name nodes that do not exist: " + ", ".join(sorted(unknown)),
              file=sys.stderr)
        return 1

    connections: dict = {}
    for source, out_index, target, in_index, kind in CONNECTIONS:
        slot = connections.setdefault(source, {}).setdefault(kind, [])
        while len(slot) <= out_index:
            slot.append([])
        slot[out_index].append({"node": target, "type": kind, "index": in_index})

    workflow = {
        "name": "ReunifyAI — Cross-Registry Potential Match Pipeline",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
        "meta": {
            "synthetic": True,
            "notice": "Compares two humanitarian records and produces a potential "
                      "match score for human review. It never establishes identity. "
                      "Built and tested against synthetic data only.",
            "requires": "An Anthropic credential on the two Claude nodes. Everything "
                        "else runs without credentials.",
            "source": "https://github.com/anyasharma2407/reunifyai",
        },
    }

    TARGET.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n")
    code_nodes = sum(1 for n in nodes if n["type"] == "n8n-nodes-base.code")
    print(f"Nodes       : {len(nodes)} ({code_nodes} Code nodes, "
          f"bodies read from {SOURCE.name})")
    print(f"Connections : {len(CONNECTIONS)}")
    print(f"Written     : {TARGET.relative_to(ROOT)} "
          f"({TARGET.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
