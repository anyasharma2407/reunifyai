import { workflow, node, trigger, ifElse, merge, expr, languageModel } from '@n8n/workflow-sdk';

const receive = trigger({
  type: 'n8n-nodes-base.webhook',
  version: 2.1,
  config: {
    name: '01 — Receive NGO Records',
    parameters: {
      httpMethod: 'POST',
      path: 'reunify/compare',
      responseMode: 'responseNode',
    },
  },
});

const validate = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: '02 — Validate Input',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `// Reject anything malformed at the door. A tracing pipeline that
// silently scores half a record is worse than one that refuses it.
const body = $input.first().json.body || $input.first().json;
const a = body.record_a;
const b = body.record_b;
const problems = [];
if (!a) problems.push("record_a missing");
if (!b) problems.push("record_b missing");
if (a && !a.record_id) problems.push("record_a.record_id missing");
if (b && !b.record_id) problems.push("record_b.record_id missing");
if (problems.length) {
  return [{ json: { ok: false, problems: problems, received: body } }];
}
return [{ json: {
  ok: true,
  record_a: a,
  record_b: b,
  options: body.options || {},
  received_at: new Date().toISOString(),
} }];`,
    },
  },
});

const isValid = ifElse({
  version: 2.3,
  config: {
    name: 'Input usable?',
    parameters: {
      conditions: {
        options: { caseSensitive: true, typeValidation: 'loose', version: 2 },
        conditions: [
          {
            id: 'valid',
            leftValue: expr('{{ $json.ok }}'),
            rightValue: true,
            operator: { type: 'boolean', operation: 'true', singleValue: true },
          },
        ],
        combinator: 'and',
      },
    },
  },
});

const rejectInput = node({
  type: 'n8n-nodes-base.respondToWebhook',
  version: 1.5,
  config: {
    name: 'Reject Malformed Request',
    parameters: {
      respondWith: 'json',
      responseCode: 400,
      responseBody: expr('{{ JSON.stringify({ error: "invalid request", problems: $json.problems }) }}'),
    },
  },
});

const normalize = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: '03 — Normalize Records',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `// Put both registries on the same footing before anything is compared.
// Registries disagree about case, punctuation, whitespace and date
// format, and none of that is evidence about a person.
const p = $input.first().json;
const clean = (s) => String(s == null ? "" : s).normalize("NFKD")
  .replace(/[\\u0300-\\u036f]/g, "").replace(/\\s+/g, " ").trim();
const key = (s) => clean(s).toLowerCase().replace(/[^a-z0-9 ]/g, "");

// Dates arrive as ISO, d/m/y, "approx. 1994" or "age approx 32".
// Anything vaguer than a year becomes a year plus a tolerance, so a
// vague date dilutes the evidence rather than faking precision.
const parseYear = (raw) => {
  const s = clean(raw);
  if (!s) return null;
  const age = s.match(/age\\s*(?:approx\\.?)?\\s*(\\d{1,3})/i);
  if (age) return { year: new Date().getFullYear() - Number(age[1]), slack: 1 };
  const approx = s.match(/(?:approx\\.?|c\\.?|circa)\\s*(\\d{4})/i);
  if (approx) return { year: Number(approx[1]), slack: 1 };
  const y = s.match(/(\\d{4})/);
  return y ? { year: Number(y[1]), slack: /^\\d{4}-\\d{2}-\\d{2}$/.test(s) ? 0 : 0.5 } : null;
};

const shape = (r) => ({
  record_id: r.record_id,
  registry: r.registry || null,
  given_name: clean(r.given_name),
  family_name: clean(r.family_name),
  given_key: key(r.given_name),
  family_key: key(r.family_name),
  sex: clean(r.sex).toUpperCase().slice(0, 1),
  birth_raw: clean(r.birth_date_raw),
  birth: parseYear(r.birth_date_raw),
  origin_place: clean(r.origin_place),
  origin_key: key(r.origin_place),
  last_seen_place: clean(r.last_seen_place),
  last_seen_key: key(r.last_seen_place),
  nationality: clean(r.nationality),
  nationality_key: key(r.nationality),
  family_members: (r.family_members || []).map((m) => ({
    name: clean(m.name), name_key: key(m.name),
    relation: key(m.relation),
  })),
  notes: clean(r.notes),
  face_embedding: r.face_embedding || null,
  face_image_url: r.face_image_url || null,
});

return [{ json: Object.assign({}, p, {
  a: shape(p.record_a), b: shape(p.record_b),
}) }];`,
    },
  },
});

const claude = languageModel({
  type: '@n8n/n8n-nodes-langchain.lmChatAnthropic',
  version: 1.6,
  config: {
    name: 'Claude',
    parameters: { model: { __rl: true, mode: 'list', value: 'claude-sonnet-5' } },
  },
});

const claudeExplain = languageModel({
  type: '@n8n/n8n-nodes-langchain.lmChatAnthropic',
  version: 1.6,
  config: {
    name: 'Claude (explanation)',
    parameters: { model: { __rl: true, mode: 'list', value: 'claude-sonnet-5' } },
  },
});

const extract = node({
  type: '@n8n/n8n-nodes-langchain.chainLlm',
  version: 1.9,
  config: {
    name: '04 — AI Entity Extraction',
    executeOnce: true,
    onError: 'continueRegularOutput',
    parameters: {
      promptType: 'define',
      text: expr(`Two humanitarian registries each hold a free-text intake note. Pull out only what is explicitly stated. Never infer, never fill gaps.

Return strict JSON: {"a":{"places":[],"relations":[],"dates":[]},"b":{"places":[],"relations":[],"dates":[]}}

Note A: {{ $json.a.notes }}
Note B: {{ $json.b.notes }}`),
    },
    subnodes: { model: claude },
  },
});

const prepareFaces = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: '05 — Prepare Face Images',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `// Decide how the two faces will be compared, if at all.
//
// The preferred path is that each organisation sends the embedding it
// computed locally and keeps the photograph. Two agencies can then ask
// "do we hold records for the same person?" without either of them
// transmitting a picture of a displaced person. An embedding is still
// biometric data and still needs protecting, but it narrows what has to
// cross an organisational boundary to run the comparison.
//
// The node upstream is an LLM chain, and a chain returns the model's reply
// rather than the item it was given — so the normalised records have to be
// pulled back from stage 03 by name. Reading $input here instead gets the
// model's text and nothing else.
const p = $('03 — Normalize Records').first().json;
const llm = $input.first().json;
const extraction = llm.text || llm.output || llm.response || null;
const haveBoth = !!(p.a.face_embedding && p.b.face_embedding);
const haveUrls = !!(p.a.face_image_url && p.b.face_image_url);
return [{ json: Object.assign({}, p, {
  ai_extraction: extraction,
  face_plan: {
    mode: haveBoth ? "embeddings-supplied" : (haveUrls ? "embed-remotely" : "unavailable"),
    needs_embedding: !haveBoth && haveUrls,
    reason: haveBoth
      ? "Both registries supplied embeddings; no image is transmitted."
      : (haveUrls
          ? "Only image references supplied; the embedding service must be called."
          : "No face data from at least one registry — the face signal will abstain."),
  },
}) }];`,
    },
  },
});

const embedFaces = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.3,
  config: {
    name: '06 — Generate Face Embeddings',
    onError: 'continueRegularOutput',
    parameters: {
      method: 'POST',
      url: expr('{{ $json.options.face_service_url || "http://localhost:8000/api/face/embed" }}'),
      sendBody: true,
      specifyBody: 'json',
      jsonBody: expr('{{ JSON.stringify({ images: [$json.a.face_image_url, $json.b.face_image_url] }) }}'),
      options: { timeout: 20000 },
    },
  },
});

const collectFaces = merge({
  version: 3.2,
  config: {
    name: 'Collect Face Vectors',
    parameters: { mode: 'combine', combineBy: 'combineAll' },
  },
});

const faceSimilarity = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: '07 — Calculate Facial Similarity',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `// Cosine distance between two face embeddings, then calibration.
//
// Raw cosine is not a percentage anyone should read. Two unrelated
// faces still agree substantially, because both are faces: in this
// corpus strangers sit around 0.81, not near zero. Reporting that as
// "81% similar" would badly mislead a caseworker. So the score says how
// far above the stranger baseline this pair sits, not how alike the two
// vectors are in the abstract.
// Same reasoning as stage 05: read the payload from the stage that produced
// it rather than from whatever the merge happened to hand over.
const p = $('05 — Prepare Face Images').first().json;

// Vectors either came in the request or came back from the embedding service,
// which the merge folds into the incoming item.
const items = $input.all();
let svc = null;
for (let i = 0; i < items.length; i++) {
  if (items[i].json && items[i].json.embeddings) svc = items[i].json;
}
const va = p.a.face_embedding || (svc && svc.embeddings ? svc.embeddings[0] : null);
const vb = p.b.face_embedding || (svc && svc.embeddings ? svc.embeddings[1] : null);

const cal = p.options.face_calibration || { background_mean: 0.814, background_sd: 0.068 };
const MID = 0.72;
const SPREAD = 0.295;

if (!Array.isArray(va) || !Array.isArray(vb) || va.length !== vb.length || !va.length) {
  return [{ json: Object.assign({}, p, { face: {
    available: false, score: 0, weight: 0,
    detail: "No comparable face embedding — excluded from scoring.",
  } }) }];
}

let dot = 0, na = 0, nb = 0;
for (let i = 0; i < va.length; i++) {
  dot += va[i] * vb[i]; na += va[i] * va[i]; nb += vb[i] * vb[i];
}
const cos = (na && nb) ? dot / (Math.sqrt(na) * Math.sqrt(nb)) : 0;
const z = (cos - cal.background_mean) / Math.max(cal.background_sd, 1e-6);
const score = 100 / (1 + Math.exp(-(z - MID) / SPREAD));

// Asymmetric on purpose. Two photographs looking alike is real
// corroboration; looking unalike is weak evidence of anything, because
// the innocent explanations are ordinary — years between captures,
// injury, a covered head, a camera that could not expose for a dark
// face. Facial similarity is an indicator and not proof of identity;
// the corollary is that dissimilarity is not proof of non-identity.
const weight = score >= 55 ? 1 : 0.25 + 0.75 * (score / 55);

return [{ json: Object.assign({}, p, { face: {
  available: true,
  score: Math.round(score * 10) / 10,
  weight: Math.round(weight * 100) / 100,
  cosine: Math.round(cos * 10000) / 10000,
  z_above_background: Math.round(z * 100) / 100,
  dimensions: va.length,
  detail: "Cosine distance over " + va.length + "-d embeddings; " +
          (Math.round(z * 100) / 100) + " SD above unrelated faces. " +
          "Indicator only — not proof of identity.",
} }) }];`,
    },
  },
});

const nameSimilarity = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: '08 — Calculate Name Similarity',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `// Transliteration and OCR damage names in different ways, so edit
// distance alone is not enough: it scores Mohammed/Muhammad at about
// 0.85, indistinguishable from noise. A phonetic code recognises them
// as the same name. The scorer takes the more generous of the two and
// records which one fired, so a reviewer sees the reasoning.
const p = $input.first().json;

const jaro = (s1, s2) => {
  if (!s1.length || !s2.length) return 0;
  if (s1 === s2) return 1;
  const win = Math.max(0, Math.floor(Math.max(s1.length, s2.length) / 2) - 1);
  const f1 = new Array(s1.length).fill(false);
  const f2 = new Array(s2.length).fill(false);
  let m = 0;
  for (let i = 0; i < s1.length; i++) {
    const lo = Math.max(0, i - win), hi = Math.min(i + win + 1, s2.length);
    for (let j = lo; j < hi; j++) {
      if (f2[j] || s1[i] !== s2[j]) continue;
      f1[i] = true; f2[j] = true; m++; break;
    }
  }
  if (!m) return 0;
  let k = 0, t = 0;
  for (let i = 0; i < s1.length; i++) {
    if (!f1[i]) continue;
    while (!f2[k]) k++;
    if (s1[i] !== s2[k]) t++;
    k++;
  }
  t = t / 2;
  return (m / s1.length + m / s2.length + (m - t) / m) / 3;
};

const jaroWinkler = (a, b) => {
  const j = jaro(a, b);
  let pre = 0;
  while (pre < 4 && pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  return j + pre * 0.1 * (1 - j);
};

// A deliberately crude consonant-skeleton coder. It collapses the vowel
// and voicing choices that transliteration is least consistent about,
// which is most of the difference between Mohammed and Muhammad.
const phonetic = (s) => s.toLowerCase()
  .replace(/[^a-z]/g, "")
  .replace(/^(kn|gn|pn|wr)/, "n")
  .replace(/ph/g, "f").replace(/ck/g, "k").replace(/sch/g, "sk")
  .replace(/[aeiouyhw]/g, "")
  .replace(/([bcdfgjklmnpqrstvxz])\\1+/g, "$1")
  .replace(/[dt]/g, "t").replace(/[bpv]/g, "b").replace(/[gkq]/g, "k")
  .replace(/[sz]/g, "s").replace(/[mn]/g, "n");

const part = (x, y) => {
  if (!x || !y) return { score: 0, why: "missing" };
  if (x === y) return { score: 100, why: "identical" };
  const edit = jaroWinkler(x, y) * 100;
  const px = phonetic(x), py = phonetic(y);
  if (px && px === py && edit < 90) {
    return { score: 90, why: "phonetic codes match (" + px.toUpperCase() + ")" };
  }
  return { score: edit, why: "spelling similarity " + Math.round(edit) + "%" };
};

const a = p.a, b = p.b;
const hasA = !!(a.given_key || a.family_key);
const hasB = !!(b.given_key || b.family_key);
if (!hasA || !hasB) {
  return [{ json: Object.assign({}, p, { name: {
    available: false, score: 0, weight: 0,
    detail: "No name recorded by at least one registry — excluded from scoring.",
  } }) }];
}

const g = part(a.given_key, b.given_key);
const f = part(a.family_key, b.family_key);
const straight = 0.5 * g.score + 0.5 * f.score;

// The same two names typed into the wrong boxes at intake.
const sg = part(a.given_key, b.family_key);
const sf = part(a.family_key, b.given_key);
const swapped = 0.5 * sg.score + 0.5 * sf.score;

const isSwap = swapped > straight + 12 && swapped >= 65;
const score = isSwap ? swapped * 0.96 : straight;
const detail = isSwap
  ? "Name order appears swapped between registries — given vs family: " + sg.why
  : "Given name — " + g.why + ". Family name — " + f.why;

return [{ json: Object.assign({}, p, { name: {
  available: true,
  score: Math.round(score * 10) / 10,
  weight: (a.given_key && a.family_key && b.given_key && b.family_key) ? 1 : 0.6,
  swapped: isSwap,
  detail: detail,
} }) }];`,
    },
  },
});

const contextSimilarity = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: '09 — Calculate Context Similarity',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `// Family, location, age and nationality.
//
// Every one of these abstains when a registry did not record it. A
// missing field must dilute the evidence, never argue against a match:
// scoring an absent birth date as zero would push apart two records
// that agree on everything anyone actually wrote down.
const p = $input.first().json;
const a = p.a, b = p.b;

const ratio = (x, y) => {
  if (!x || !y) return 0;
  if (x === y) return 100;
  const longer = x.length >= y.length ? x : y;
  const shorter = x.length >= y.length ? y : x;
  const d = [];
  for (let i = 0; i <= shorter.length; i++) d.push([i]);
  for (let j = 0; j <= longer.length; j++) d[0][j] = j;
  for (let i = 1; i <= shorter.length; i++) {
    for (let j = 1; j <= longer.length; j++) {
      const cost = shorter[i - 1] === longer[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
    }
  }
  return (1 - d[shorter.length][longer.length] / longer.length) * 100;
};

const place = (x, y) => (!x || !y)
  ? { available: false, score: 0, weight: 0, detail: "Not recorded — excluded." }
  : (x === y
      ? { available: true, score: 100, weight: 1, detail: "Identical entry" }
      : { available: true, score: ratio(x, y), weight: ratio(x, y) >= 80 ? 1 : 0.7,
          detail: "Fuzzy similarity " + Math.round(ratio(x, y)) + "%" });

const origin = place(a.origin_key, b.origin_key);
const lastSeen = place(a.last_seen_key, b.last_seen_key);

// Age: overlapping tolerance windows agree; the evidence is only as
// strong as the vaguer of the two records.
let age;
if (!a.birth || !b.birth) {
  age = { available: false, score: 0, weight: 0, detail: "Not recorded — excluded." };
} else {
  const gap = Math.abs(a.birth.year - b.birth.year);
  const slack = a.birth.slack + b.birth.slack;
  const precise = a.birth.slack === 0 && b.birth.slack === 0;
  const w = precise ? 1 : 0.58;
  age = gap <= slack
    ? { available: true, score: 100, weight: w,
        detail: precise ? "Exact dates agree" : "Tolerance windows overlap" }
    : { available: true, score: 100 * Math.pow(0.5, gap / (precise ? 1.2 : 3.5)), weight: w,
        detail: "About " + gap + " year(s) apart" };
}

// Nationality is the weakest signal and the most dangerous to lean on:
// at a registration desk it is often the enumerator's inference rather
// than a document. Agreement is mild corroboration; disagreement is not
// treated as counter-evidence at all.
let nat;
const undetermined = ["stateless", "undetermined", "not established"];
if (!a.nationality_key || !b.nationality_key ||
    undetermined.indexOf(a.nationality_key) >= 0 ||
    undetermined.indexOf(b.nationality_key) >= 0) {
  nat = { available: false, score: 0, weight: 0,
          detail: "Not recorded or undetermined — excluded." };
} else if (a.nationality_key === b.nationality_key) {
  nat = { available: true, score: 100, weight: 1, detail: "Identical entry" };
} else {
  const r = ratio(a.nationality_key, b.nationality_key);
  nat = r < 70
    ? { available: true, score: 50, weight: 0.35,
        detail: "Different entries — treated as no evidence either way" }
    : { available: true, score: r, weight: 0.8,
        detail: "Spelling similarity " + Math.round(r) + "%" };
}

// Family: match relatives greedily, best pair first. The two desks have
// no shared ordering and may have recorded different numbers of people.
const RELATION_GROUP = {
  mother: "parent", father: "parent", parent: "parent", guardian: "parent",
  son: "child", daughter: "child", child: "child",
  brother: "sibling", sister: "sibling", sibling: "sibling",
  spouse: "spouse", wife: "spouse", husband: "spouse",
  aunt: "extended", uncle: "extended", cousin: "extended", grandparent: "extended",
};
let family;
const fa = a.family_members || [], fb = b.family_members || [];
if (!fa.length || !fb.length) {
  family = { available: false, score: 0, weight: 0,
             detail: "No family member recorded by at least one registry — excluded." };
} else {
  const pairs = [];
  for (let i = 0; i < fa.length; i++) {
    for (let j = 0; j < fb.length; j++) {
      const ga = RELATION_GROUP[fa[i].relation];
      const gb = RELATION_GROUP[fb[j].relation];
      const relFactor = (!ga || !gb) ? 0.85
        : (fa[i].relation === fb[j].relation ? 1 : (ga === gb ? 0.94 : 0.55));
      pairs.push({ s: ratio(fa[i].name_key, fb[j].name_key) * relFactor,
                   i: i, j: j, am: fa[i], bm: fb[j] });
    }
  }
  pairs.sort((x, y) => y.s - x.s);
  const usedA = {}, usedB = {}, chosen = [];
  for (let k = 0; k < pairs.length; k++) {
    if (usedA[pairs[k].i] || usedB[pairs[k].j]) continue;
    usedA[pairs[k].i] = true; usedB[pairs[k].j] = true; chosen.push(pairs[k]);
  }
  const best = chosen[0].s;
  let sum = 0;
  for (let k = 0; k < chosen.length; k++) sum += chosen[k].s;
  // One confidently shared relative is the evidence; a second unmatched
  // one usually means the desks asked different questions.
  const score = 0.75 * best + 0.25 * (sum / chosen.length);
  family = { available: true, score: score,
    weight: fa.length === fb.length ? 1 : 0.8,
    detail: chosen[0].am.name + " (" + chosen[0].am.relation + ") vs " +
            chosen[0].bm.name + " (" + chosen[0].bm.relation + ")" };
}

const round = (o) => Object.assign({}, o, { score: Math.round(o.score * 10) / 10 });
return [{ json: Object.assign({}, p, {
  family: round(family), origin_place: round(origin),
  last_seen_place: round(lastSeen), birth_date: round(age), nationality: round(nat),
  sex_check: (a.sex && b.sex) ? (a.sex === b.sex ? "agrees" : "conflict") : "unavailable",
}) }];`,
    },
  },
});

const overallScore = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: '10 — Calculate Overall Match Score',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `// The headline number is a POTENTIAL MATCH SCORE, never a confidence or
// a probability of identity. It says how strongly these two records
// should be prioritised for a human to look at, and nothing else.
const p = $input.first().json;

const WEIGHTS = {
  face: 0.30, name: 0.25, family: 0.15,
  birth_date: 0.10, origin_place: 0.09, last_seen_place: 0.06, nationality: 0.05,
};

// Face is the heaviest single signal but deliberately short of a
// majority, so the other five together always outweigh it.
const FACE_ONLY_CEILING = 39;
const SEX_CONFLICT_DAMPING = 0.55;

const fields = ["face", "name", "family", "birth_date", "origin_place",
               "last_seen_place", "nationality"];
const signals = [];
let num = 0, den = 0, total = 0;
for (let i = 0; i < fields.length; i++) {
  const f = fields[i];
  const s = p[f] || { available: false, score: 0, weight: 0, detail: "not evaluated" };
  const w = WEIGHTS[f] * (s.weight || 0);
  num += (s.score || 0) * w;
  den += w;
  total += WEIGHTS[f];
  signals.push({ field: f, score: s.score || 0, weight: s.weight || 0,
                 available: !!s.available, detail: s.detail });
}

const raw = den ? num / den : 0;
const coverage = den / total;
// Sparse evidence is capped: a pair agreeing on one signal cannot
// present as strongly as one corroborated across many.
let score = raw * Math.min(1, 0.55 + 0.5 * coverage);

const flags = [];
if (p.sex_check === "conflict") {
  score = score * SEX_CONFLICT_DAMPING;
  flags.push("Sex recorded differently between registries");
}

// Scores are renormalised over the evidence actually available, so a
// pair whose only usable signal is the face would renormalise straight
// back up to the face score. Close that path explicitly.
let nonFace = 0;
for (let i = 0; i < signals.length; i++) {
  if (signals[i].field !== "face") nonFace += WEIGHTS[signals[i].field] * signals[i].weight;
}
const faceOnly = nonFace <= 1e-9 && (p.face && p.face.weight > 0);
if (faceOnly && score > FACE_ONLY_CEILING) {
  score = FACE_ONLY_CEILING;
  flags.push("Facial similarity is the only available signal — score capped pending corroboration");
}

const band = score >= 80 ? "strong" : (score >= 60 ? "possible" : (score >= 40 ? "weak" : "unlikely"));
const surfaced = signals.filter((s) => s.available && s.score >= 65)
  .sort((x, y) => y.score - x.score);

return [{ json: Object.assign({}, p, {
  potential_match_score: Math.round(score * 10) / 10,
  band: band,
  evidence_coverage: Math.round(coverage * 100) / 100,
  signals: signals,
  surfaced_because: surfaced,
  flags: flags,
  face_only: faceOnly,
  weights: WEIGHTS,
  review_required: true,
  notice: "Potential match score — how strongly these records should be " +
          "prioritised for human review. Not a confirmed identity.",
  face_notice: "FACIAL SIMILARITY IS AN INDICATOR, NOT PROOF OF IDENTITY.",
  review_notice: "HUMAN VERIFICATION REQUIRED.",
}) }];`,
    },
  },
});

const explain = node({
  type: '@n8n/n8n-nodes-langchain.chainLlm',
  version: 1.9,
  config: {
    name: '11 — Generate Explanation',
    executeOnce: true,
    onError: 'continueRegularOutput',
    parameters: {
      promptType: 'define',
      text: expr(`You are writing one short paragraph for a humanitarian caseworker explaining why two records were put in front of them.

Rules you must follow:
- Never state or imply the records are the same person.
- Say plainly that facial similarity alone does not establish identity or a family relationship.
- Describe only the signals below. Do not invent evidence.
- End by stating that human verification is required.

Potential match score: {{ $json.potential_match_score }} ({{ $json.band }})
Evidence coverage: {{ $json.evidence_coverage }}
Signals: {{ JSON.stringify($json.signals) }}
Flags: {{ JSON.stringify($json.flags) }}`),
    },
    subnodes: { model: claudeExplain },
  },
});

const attachExplanation = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Attach Explanation',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `// The LLM node is allowed to fail without taking the pipeline with it.
// An explanation is a convenience for the reviewer; the score and the
// signals behind it are the actual product, and they are already
// computed. Losing the prose must never lose the case.
const items = $input.all();
const scored = $("10 — Calculate Overall Match Score").first().json;
const llm = items.length ? items[0].json : {};
const text = llm.text || llm.output || llm.response || null;
return [{ json: Object.assign({}, scored, {
  explanation: text || ("These records were surfaced because they are similar across " +
    "several independent signals. Facial similarity alone does not establish " +
    "identity or a family relationship. Human verification is required."),
  explanation_generated: !!text,
}) }];`,
    },
  },
});

const filterPriority = node({
  type: 'n8n-nodes-base.filter',
  version: 2.3,
  config: {
    name: '12 — Filter Priority Matches',
    parameters: {
      conditions: {
        options: { caseSensitive: true, typeValidation: 'loose', version: 2 },
        conditions: [
          {
            id: 'above-threshold',
            leftValue: expr('{{ $json.potential_match_score }}'),
            rightValue: expr('{{ $json.options.review_threshold || 60 }}'),
            operator: { type: 'number', operation: 'gte' },
          },
        ],
        combinator: 'and',
      },
    },
  },
});

const createCase = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: '13 — Create Review Case',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `// A case is a request for a person to look, with everything they need
// to disagree with the engine attached to it.
const p = $input.first().json;
const id = "CASE-" + p.a.record_id + "-" + p.b.record_id;
return [{ json: Object.assign({}, p, {
  review_case: {
    case_id: id,
    status: "awaiting_human_review",
    opened_at: new Date().toISOString(),
    potential_match_score: p.potential_match_score,
    band: p.band,
    records: [p.a.record_id, p.b.record_id],
    face_similarity: p.face && p.face.available ? p.face.score : null,
    explanation: p.explanation,
    flags: p.flags,
    decision: null,
    decided_by: null,
    notice: "No identification has been made. This case exists so a person can decide.",
  },
}) }];`,
    },
  },
});

const respond = node({
  type: 'n8n-nodes-base.respondToWebhook',
  version: 1.5,
  config: {
    name: 'Respond to Calling System',
    parameters: {
      respondWith: 'json',
      responseCode: 202,
      responseBody: expr('{{ JSON.stringify({ case_id: $json.review_case.case_id, potential_match_score: $json.potential_match_score, band: $json.band, face_similarity: $json.review_case.face_similarity, status: "awaiting_human_review", notice: $json.notice, face_notice: $json.face_notice, review_notice: $json.review_notice }) }}'),
    },
  },
});

const notify = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.3,
  config: {
    name: '14 — Notify Caseworker',
    onError: 'continueRegularOutput',
    parameters: {
      method: 'POST',
      url: expr('{{ $json.options.caseworker_webhook_url || "https://example.invalid/caseworker" }}'),
      sendBody: true,
      specifyBody: 'json',
      jsonBody: expr('{{ JSON.stringify({ text: "Potential match for review: " + $json.review_case.case_id + " (" + $json.potential_match_score + "/100). HUMAN VERIFICATION REQUIRED.", review_case: $json.review_case }) }}'),
      options: { timeout: 15000 },
    },
  },
});

const waitForReview = node({
  type: 'n8n-nodes-base.wait',
  version: 1.1,
  config: {
    name: '15 — Wait for Human Review',
    parameters: {
      resume: 'webhook',
      // The resume webhook defaults to GET. Left at the default, a reviewer's
      // decision sent as a JSON body is simply not there when stage 16 looks
      // for it, and every review silently records as "undecided".
      httpMethod: 'POST',
      responseMode: 'lastNode',
      options: {},
    },
  },
});

const updateCase = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: '16 — Update Case Status',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `// Record what the human decided. "referred" means a person judged the
// pair worth verifying through the originating organisations — it is
// explicitly not a confirmation, and nothing downstream may treat it
// as one.
//
// A Wait node resumed by webhook puts a JSON body under .body and query
// string parameters under .query. Accept either: the resume URL gets called
// by hand as often as by a caseworker tool, and a decision silently read as
// "undecided" is the worst possible failure here — it looks like the review
// happened while leaving the case untouched.
const resumed = $input.first().json;
const hasKeys = (o) => !!o && typeof o === 'object' && Object.keys(o).length > 0;
const src = hasKeys(resumed.body) ? resumed.body
          : (hasKeys(resumed.query) ? resumed.query : resumed);

const prior = $("13 — Create Review Case").first().json;
const decision = src.decision === "referred" || src.decision === "ruled_out"
  ? src.decision : "undecided";

return [{ json: Object.assign({}, prior, {
  review_case: Object.assign({}, prior.review_case, {
    status: decision === "undecided" ? "awaiting_human_review" : "reviewed",
    decision: decision,
    decided_by: src.reviewer || null,
    reviewer_note: src.note || "",
    decided_at: new Date().toISOString(),
    notice: decision === "referred"
      ? "Referred for verification through the originating organisations. Not a confirmed identity."
      : (decision === "ruled_out"
          ? "Ruled out by the reviewer."
          : "No usable decision was supplied — the case remains open. Send "
            + "{\"decision\":\"referred\"} or {\"decision\":\"ruled_out\"}."),
  }),
}) }];`,
    },
  },
});

const auditLog = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: '17 — Write Audit Log',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode: `// An append-only record of what the system showed a human and what
// that human did with it. In a domain where a wrong match can send a
// child to the wrong adults, being able to reconstruct a decision
// afterwards matters as much as making it well the first time.
const p = $input.first().json;
return [{ json: {
  audit: {
    case_id: p.review_case.case_id,
    logged_at: new Date().toISOString(),
    records_compared: p.review_case.records,
    potential_match_score: p.potential_match_score,
    band: p.band,
    evidence_coverage: p.evidence_coverage,
    signals: p.signals,
    face_backend_dimensions: p.face && p.face.dimensions ? p.face.dimensions : null,
    face_similarity: p.review_case.face_similarity,
    flags: p.flags,
    explanation_was_generated: p.explanation_generated,
    human_decision: p.review_case.decision,
    decided_by: p.review_case.decided_by,
    reviewer_note: p.review_case.reviewer_note,
    system_made_no_identification: true,
  },
} }];`,
    },
  },
});

export default workflow('reunifyai-face-matching', 'ReunifyAI — Cross-Registry Potential Match Pipeline')
  .add(receive)
  .to(validate)
  .to(isValid
    .onFalse(rejectInput)
    .onTrue(normalize
      .to(extract)
      .to(prepareFaces)
      .to(collectFaces.input(0))))
  .add(prepareFaces)
  .to(embedFaces)
  .to(collectFaces.input(1))
  .add(collectFaces)
  .to(faceSimilarity)
  .to(nameSimilarity)
  .to(contextSimilarity)
  .to(overallScore)
  .to(explain)
  .to(attachExplanation)
  .to(filterPriority)
  .to(createCase)
  .to(respond)
  .to(notify)
  .to(waitForReview)
  .to(updateCase)
  .to(auditLog)
  // Two rules shape these groups: a trigger cannot be a group member, and a
  // group may not cross an ai_languageModel connection — so each chain's model
  // subnode sits inside the same group as the chain it feeds.
  .group('Ingest', [validate, isValid, rejectInput, normalize, extract, claude], {
    description: 'Accept two NGO records, reject malformed input, and put both registries on the same footing',
  })
  .group('Face matching', [prepareFaces, embedFaces, collectFaces, faceSimilarity], {
    description: 'Compare faces via embeddings — preferably supplied, so no photograph crosses an organisational boundary',
  })
  .group('Scoring', [nameSimilarity, contextSimilarity, overallScore, explain, attachExplanation, claudeExplain], {
    description: 'Weighted multi-signal potential match score, with a written explanation of it',
  })
  .group('Human review', [filterPriority, createCase, respond, notify, waitForReview, updateCase, auditLog], {
    description: 'Nothing is decided here without a person: the pipeline opens a case, waits, and logs what they chose',
  });
