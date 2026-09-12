/*
 * Run the n8n workflow's Code-node logic outside n8n and compare it with the
 * Python engine on the same pair.
 *
 *     python scripts/n8n_payload.py A-0013 B-0013 > /tmp/payload.json
 *     node scripts/verify_n8n_parity.mjs /tmp/payload.json
 *
 * There are two implementations of the scoring model in this repository and
 * they must not drift apart: the Python engine is the reference, and the
 * workflow is a portable reimplementation that runs inside n8n's sandbox,
 * where it cannot import any of this project's code. Two implementations of
 * one rulebook is a liability unless something checks them against each other,
 * which is what this script is for.
 *
 * They are not expected to agree exactly. The Python scorer consults a
 * place-name gazetteer and phonetic coders that the workflow does not carry,
 * so it resolves known aliases the workflow can only see as fuzzy string
 * similarity. On the demo pair that is worth roughly two points. What must
 * agree are the face score, the weights, the coverage damping and the bands --
 * a divergence there is a bug.
 */
import { readFileSync } from 'node:fs';

const src = readFileSync('n8n/reunify_workflow.ts', 'utf8');

// Pull each jsCode template literal out of the SDK file, in order.
const blocks = [];
const re = /name: '([^']+)',\n\s*parameters: \{\n\s*mode: 'runOnceForAllItems',\n\s*jsCode: `([\s\S]*?)`,\n/g;
let m;
while ((m = re.exec(src)) !== null) {
  // The file holds the TEMPLATE LITERAL SOURCE, so escapes are still doubled.
  // The SDK evaluates the literal before storing jsCode, and so must we --
  // otherwise every regex in the node body is compiled with a stray backslash
  // and silently fails to match, which looks exactly like a scoring bug.
  const code = m[2].replace(/\\`/g, '`').replace(/\\\\/g, '\\');
  blocks.push({ name: m[1], code });
}
console.log('extracted code nodes:', blocks.map(b => b.name).join('\n  '));

const payloadPath = process.argv[2] || '/tmp/payload.json';
const payload = JSON.parse(readFileSync(payloadPath, 'utf8'));

// Minimal n8n Code-node harness.
let current = [{ json: { body: payload } }];
const history = {};
function run(block) {
  const $input = {
    first: () => current[0],
    all: () => current,
  };
  const $ = (name) => ({ first: () => history[name][0] });
  const fn = new Function('$input', '$', `${block.code}`);
  current = fn($input, $);
  history[block.name] = current;
}

for (const b of blocks) {
  if (b.name === '05 — Prepare Face Images') {
    // Stand in for stage 04. An LLM chain replaces the item with the model's
    // reply, and stage 05 has to survive that: skipping the LLM nodes here
    // once hid a crash that only appeared in n8n.
    history['03 — Normalize Records'] = current;
    current = [{ json: { text: '{"a":{},"b":{}}' } }];
  }
  if (b.name === 'Attach Explanation') continue;   // needs the LLM node's output
  if (b.name === '16 — Update Case Status') break; // needs the human callback
  run(b);
}

const r = current[0].json;
console.log('\n--- n8n pipeline result ---');
console.log('potential_match_score:', r.potential_match_score, '(' + r.band + ')');
console.log('evidence_coverage    :', r.evidence_coverage);
for (const s of r.signals) {
  console.log(`  ${s.field.padEnd(16)} ${String(Math.round(s.score)).padStart(3)}%  w=${s.weight}`);
}
console.log('flags:', JSON.stringify(r.flags));
