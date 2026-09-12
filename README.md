# ReunifyAI — Reunification Engine

Decision support for family tracing across humanitarian registries that have
never been connected to each other. It ranks *candidate pairs of records* for a
human caseworker and explains its reasoning. It does not identify anyone.

**Every record, every photograph and every place name in this project is
synthetic.** The geography is a fictional setting called the Karavian corridor,
invented so that nothing here can be mistaken for a real displacement
situation. The faces are drawn by `engine/faces.py` from seeded numbers; no
photograph of a real person is used, downloaded, or shipped.

---

## Positioning

This is **not** "AI identifies refugees."

It is: **AI helps humanitarian organisations prioritise potentially connected
records across fragmented databases.** Facial similarity is one signal within a
multi-factor matching system.

The headline number is a **Potential Match Score**, never an identity
confidence. It says how strongly two records should be prioritised for a person
to look at. Two statements are attached to it everywhere it appears:

> **FACIAL SIMILARITY IS AN INDICATOR, NOT PROOF OF IDENTITY.**
>
> **HUMAN VERIFICATION REQUIRED.**

---

## Running it

```bash
python -m engine.generate --seed 7 --size 80   # synthetic registries + faces
uvicorn web.app:app --reload --port 8000       # review interface on :8000
python -m engine.evaluate                      # accuracy against ground truth
```

`http://localhost:8000/?demo=1` opens straight into the scripted walk-through.
`http://localhost:8000/#A-0013` opens a specific record's review screen.

---

## The signals

| Signal | Weight |
| --- | ---: |
| Facial similarity | 30% |
| Name | 25% |
| Family members | 15% |
| Age / date of birth | 10% |
| Place of origin | 9% |
| Last seen | 6% |
| Nationality | 5% |

Location is split across the two places a registry records, because "same
place" is a different claim depending on whether it is where someone came from
or where they were last seen.

Three rules sit on top of the weights, and they are the part worth arguing
about:

**A missing field abstains.** Every signal reports a score *and* an evidence
weight. An unrecorded birth date gets weight 0, so it dilutes the evidence
instead of arguing against a match. Scores are then renormalised over the
evidence that actually existed and damped by coverage, so a pair agreeing on
one field can never present as confidently as one corroborated across six.

**Facial similarity is asymmetric.** A high face score is real corroboration. A
low one is weak evidence of anything, because the innocent explanations are
ordinary: years between captures, injury, illness, a covered head, a camera
that could not expose for a dark face. So low face scores are given reduced
weight. Facial similarity is an indicator and not proof of identity; the
corollary is that facial *dissimilarity* is not proof of non-identity, and the
weighting says so. Measured on this corpus, giving low face scores full weight
costs two of the twenty-six planted pairs their top-1 rank.

**Face alone cannot carry a pair.** Because scores renormalise over available
evidence, a pair whose only usable signal is the face would renormalise back up
to the face score. `FACE_ONLY_CEILING` closes that path: with nothing to
corroborate it, such a pair is capped at 39 and cannot leave the weak band, no
matter how alike the two photographs are.

Sex is deliberately *not* a weighted signal. Agreement carries almost no
evidence — roughly half the population agrees with any given record — but a
genuine disagreement is worth acting on, so it damps the score and is flagged.

---

## How the faces work

`engine/faces.py` splits generation in two, and the split is the whole point.

A **latent** is the person: about twenty numbers describing face geometry,
colouring and hair, derived deterministically from a person id. A **capture**
is one photograph of that person: a particular exposure, white balance, head
angle, framing and focus. Each registry photographs the person independently,
so a matched pair is two genuinely different images of one synthetic face.

If both registries held the identical PNG, cosine similarity would be exactly
1.0 and the demo would prove nothing. Because each side holds its own capture,
the embedder has to recover identity across lighting, framing and noise —
which is the actual problem a face-matching system solves.

The embedder never sees a latent. It is handed pixels, like any real system.

### FaceEmbeddingService

`engine/face_embedding.py` exposes exactly two operations —
`generate_embedding(image)` and `compare_embeddings(a, b)` — and deliberately
no `identify()` or `is_same_person()`, because there is no threshold at which
this system is entitled to make that claim.

The pipeline is the classical one: **detect the eyes, align, describe, compare.**

*Detection* finds the one place on a face where a bright patch and a dark patch
sit within a few pixels of each other — white sclera against black pupil — and
requires the bright half to be near-neutral in colour, which is what separates
an eye from a lit cheek against dark hair. Candidates are then chosen *as a
pair*: level with each other, plausibly spaced, straddling the middle of the
head.

*Alignment* is a similarity transform onto the eye positions — rotate, scale,
translate, never stretch. Squeezing every head into a square erases the
width-to-height ratio of the face, which is one of the strongest things telling
two people apart; measured here, doing so costs about a third of the correct
retrievals.

*Description* is a histogram of oriented gradients over a grid of face regions,
plus a small white-balanced colour descriptor. Gradient *orientation* is
invariant to exposure, so what survives is the shape of the edges — jawline,
hairline, brow angle, eye spacing, nose width. This is not pixel comparison,
and it is not a learned embedding either; it is the descriptor family that
preceded deep face recognition.

*Comparison* is cosine distance, then calibration.

### Why the percentage is calibrated

Raw cosine is not a number anyone should read as a percentage. Two unrelated
faces still agree substantially, because both are faces: in this corpus
strangers sit at **0.81**, not near zero. Reporting that as "81% similar" would
badly mislead a caseworker.

So the service measures the distribution of similarity across all cross-registry
pairs — overwhelmingly strangers — and reports how far above that background a
given pair sits. A score near zero means "as alike as two strangers". The
calibration constants are written to `data/face_calibration.json` and reported
in the API, so the number is never mistaken for a probability of identity.

Measured on 40 identities with two independent captures each: **34/40 rank-1
retrieval**, genuine pairs at a median 95% calibrated similarity, unrelated
pairs typically under 30%.

### Swapping in a real model

The backend is pluggable. `ArcFaceOnnxBackend` is used automatically when
`onnxruntime` is installed and `models/arcface.onnx` exists; set
`FACE_BACKEND=arcface-onnx` to require it. Any ONNX model taking a 112×112 face
and returning a vector will work, and nothing else in the project changes. The
default needs no model file and no network, so the demo cannot fail on a
conference wifi.

---

## What the measurements actually say

The evaluator scores the top-1 decision against planted ground truth
(`python -m engine.evaluate`, add `--no-faces` to ablate the face signal).

On the demo corpus, **with** faces: 100% top-3 shortlist recall, and at the
reported threshold precision 100% / recall 84.6% (F1 0.917) versus 0.894
without faces.

**But the honest finding is that facial similarity does not currently improve
this corpus.** Text-only scoring reaches F1 **1.000** at its own optimal
threshold; adding a 30%-weighted face signal cannot improve on that and
slightly blurs the separation. Degrading the text makes it worse, not better:
dropping text fields, corrupting names, even removing names entirely, text-only
stays at 96–100% top-1 while faces pull it down. The same holds at 300 records
and 90,000 comparisons.

The reason is that this synthetic corpus's non-face fields are too distinctive.
Family-member names, fictional place clusters and birth dates are individually
near-identifying, so there is no headroom for a face signal to contribute, and
an ~85%-accurate descriptor can only add noise.

That is a finding about the corpus and the descriptor, not a verdict on face
matching. It is also the reason three safety properties in this codebase exist
rather than being decorative: the asymmetric weighting, the face-only ceiling,
and mandatory human review. A facial signal earns its 30% only when the
embedding is strong, which is exactly why the backend is swappable and why
nothing here is ever auto-decided.

---

## The n8n workflow

`n8n/reunify_workflow.ts` builds the pipeline with the n8n Workflow SDK, as 17
named stages plus routing:

```
01 Receive NGO Records → 02 Validate → 03 Normalize → 04 AI Entity Extraction
→ 05 Prepare Face Images → 06 Generate Face Embeddings → 07 Facial Similarity
→ 08 Name Similarity → 09 Context Similarity → 10 Overall Match Score
→ 11 Generate Explanation → 12 Filter Priority → 13 Create Review Case
→ 14 Notify Caseworker → 15 Wait for Human Review → 16 Update Case Status
→ 17 Write Audit Log
```

The pipeline is built around organisations exchanging **embeddings rather than
photographs**. Two agencies can ask "do we hold records for the same person?"
without either transmitting a picture of a displaced person. An embedding is
still biometric data and still needs protecting, but it narrows what has to
cross an organisational boundary. Stage 06 exists for the fallback case where a
partner can only send image references.

To exercise it:

```bash
python scripts/n8n_payload.py A-0013 B-0013 > /tmp/payload.json
curl -X POST -H 'Content-Type: application/json' -d @/tmp/payload.json <webhook-url>
```

### Running the live pipeline

```bash
scripts/run_pipeline.sh                 # the demo pair
scripts/run_pipeline.sh A-0001 B-0001   # any planted pair
```

This calls the production webhook, which listens continuously while the
workflow is published. The *Execute workflow* button on the canvas is a
different thing: it arms a separate test webhook, waits about two minutes and
accepts one call. Useful while building, the wrong thing to depend on in front
of an audience.

A run takes roughly ten seconds, because two stages call Claude, and it ends
parked at stage 15 waiting for a human. That is the design rather than a hang.
To see where it stopped, open **Executions** in n8n and click the newest row:
the canvas replays with the data that actually flowed through it, and any node
can be opened to see its input and output.

### Running it yourself

`n8n/reunify_workflow.ts` is the readable copy: the reasoning behind each rule
sits in the comments beside it. n8n imports JSON and nothing else, so the same
pipeline is also committed as `n8n/reunify_workflow.json` — **Import from File**
in the n8n canvas menu.

The only thing it needs is an Anthropic credential on the two Claude nodes.
Every other node runs without credentials, and the two that reach outward — the
face-embedding call and the caseworker notification — are set to continue on
error, so the pipeline still completes when they cannot be reached.

```bash
python scripts/export_n8n_workflow.py    # regenerate the JSON after editing the SDK code
```

The exporter reads each Code node's body straight out of the SDK source, so the
two copies cannot disagree about what the pipeline does; only the graph and the
node settings are described in the exporter itself.

### Completing a human review

Stage 15 parks the execution and waits. n8n puts a **signed** resume URL in the
execution's metadata — the execution id alone is rejected with
`{"error":"Invalid token"}` — so take the URL from the Wait node in the n8n UI,
or from the execution's `resumeToken`, and post the decision to it:

```bash
curl -X POST "https://<instance>/webhook-waiting/<executionId>?signature=<token>" \
     -H 'Content-Type: application/json' \
     -d '{"decision":"referred","reviewer":"name","note":"..."}'
```

`decision` takes `referred` or `ruled_out`. Anything else is recorded as
`undecided` and the case deliberately stays open, because a review that quietly
records nothing is worse than one that never happened. Stage 16 accepts the
decision from a JSON body or from query parameters, so either call style works.

Note that `referred` means a person judged the pair worth verifying through the
originating organisations. It is not a confirmation, and stage 17 writes
`system_made_no_identification: true` into every audit record to keep that
distinction on the permanent record.

### Two implementations, one rulebook

The scoring model exists twice: once in `engine/match.py`, and once inside the
workflow's Code nodes, which run in a sandbox that cannot import any of this
project's code. Two implementations of one rulebook drift apart unless
something checks them, and the drift is invisible -- both sides keep returning
plausible numbers.

```bash
python scripts/check_parity.py        # every planted pair; exits non-zero on drift
```

Across all 26 planted pairs: **21 agree exactly, the worst case differs by 0.9
points, and the two never disagree about the band.** The check fails the build
if either of the last two stops being true, because the band is what a
caseworker actually acts on.

Getting there meant matching the reference scorer detail for detail, and the
details were not cosmetic:

* **Metaphone, not a hand-rolled consonant skeleton.** The JS coder now agrees
  with the Python one on 200 of the 201 distinct names in the corpus.
* **Indel ratio, not Levenshtein.** RapidFuzz's `ratio` counts only insertions
  and deletions -- it is `2*LCS/(len1+len2)` -- and substituting classic edit
  distance moved name scores by several points.
* **The place-name gazetteer**, so both sides resolve a known alias outright
  instead of one of them seeing 82% string similarity.
* **Four-level date precision** (day, month, year, approx-year) rather than a
  single slack number, so `June 1, 2000` and `2000-06-01` are recognised as
  equally precise and carry equal weight.

What still differs: the Python scorer falls back to NYSIIS and Soundex when
Metaphone does not fire, and the workflow carries Metaphone only. That is the
residual 0.9 points, and it is documented rather than papered over.

---

## Finding help nearby

Everything else here is a caseworker's tool running on synthetic data. The
"Find help near you" panel is the one feature an affected person might actually
act on, and that changes what is acceptable in it.

Two rules follow, and both are load-bearing:

**The data is real or there is no data.** Showing someone a fictional shelter
while they are looking for one is the worst thing this application could do.
The synthetic corridor stays in the matching demo, where nobody is going to
walk to it. If a lookup fails, the panel says so rather than falling back to
something invented.

**Places are labelled as what the map says they are.** OpenStreetMap knows
about pharmacies, clinics and community centres; it does not know which of them
is running a relief operation today. Calling a pharmacy a "rescue camp" because
the surrounding page is about displacement would be a lie with consequences, so
every result carries its own category and the provenance is stated where it
cannot be missed.

Results come from the Overpass API over OpenStreetMap: emergency shelters,
assembly points, refugee sites, food banks, hospitals, clinics, doctors,
pharmacies, social facilities, community centres, drinking water and police,
within 6 km, ranked by how directly relevant the category is and then by
distance.

On location: coordinates are rounded to about 100 m before they are sent, the
query goes to OpenStreetMap's public service and nowhere else, nothing is
stored — the page has no server — and searching by place name is offered as an
equal alternative. For someone who may be fleeing, not wanting to share GPS is
a reasonable position, not an edge case.

`?help=1` opens the panel; `?help=Manchester` opens it with that search already
run.

---

## Deploying

It is live here, as a **static** Hugging Face Space:

**<https://anyasharma2407-reunifyai.static.hf.space>**
&nbsp;&nbsp;([`/?demo=1`](https://anyasharma2407-reunifyai.static.hf.space/?demo=1) for the walk-through)

Note the URL: static Spaces are served from `*.static.hf.space`. The plain
`*.hf.space` address returns 404.

```bash
HF_TOKEN=hf_xxx deploy/huggingface/push_space.sh     # build and deploy
```

### Why static

Both container routes are paywalled. Cloud Run refuses to deploy without an
active billing account even for free-tier usage, and Hugging Face now requires
a paid subscription to run a Docker Space on free hardware. A demo that falls
over because a billing account lapsed is a bad demo.

Static works here because the matcher is deterministic over a fixed corpus, so
every answer the API could give is computable in advance.
`scripts/build_static.py` scores all 6,400 comparisons up front and writes them
as JSON; `web/static/static-mode.js` intercepts `fetch` so the interface reads
those files instead of calling the API.

The frontend is deliberately not forked. `app.js` and `demo.js` are
byte-identical in both modes -- the shim is the only difference -- so there is
one frontend to maintain, and running `uvicorn` locally still exercises the
real service rather than a copy that has quietly drifted from it.

The result has no cold start, nothing to keep awake and nothing to pay for.
What is lost is the reviewer's triage log, which moves from server memory to
the visitor's browser; the server's copy was discarded on restart anyway.

```bash
python scripts/build_static.py --out dist    # build without deploying
cd dist && python -m http.server 8000        # and serve it locally
```

### Running the container instead

The corpus is generated at **build** time, not at boot: generation draws 160
faces and embeds them, and doing that on every container start would make each
deploy and restart sit there not serving.

```bash
docker build -t reunifyai .
docker run -p 8000:8000 reunifyai
```

The image is ~99 MB, runs as UID 1000, and the service is stateless, so the
smallest instance any host offers is enough. `render.yaml` is a Render
blueprint; Fly.io, Railway and Cloud Run all build the `Dockerfile` directly
and bind to `$PORT`, which the CMD already honours. `Procfile` covers buildpack
hosts, but there the corpus has to be generated at boot, so expect a slow cold
start.

One caveat worth knowing before a demo: a deployed instance and the n8n
workflow still do not talk to each other. Stage 06 of the workflow points at a
face-embedding service, and the preferred path is that embeddings arrive in the
request rather than being fetched, so nothing needs to reach back into this app.

---

## Layout

```
engine/vocab.py             controlled vocabularies — names, places, nationalities,
                            relationships. All fictional.
engine/generate.py          builds the two registries, the faces and the ground truth
engine/faces.py             synthetic face generation: latents and captures
engine/face_embedding.py    FaceEmbeddingService — eye detection, alignment,
                            the descriptor, and calibration
engine/match.py             the scoring model. This is the reference implementation;
                            the other two copies are checked against it
engine/evaluate.py          accuracy against the planted ground truth

web/app.py                  caseworker API, review queue, face embedding endpoint
web/static/app.js           the review interface
web/static/demo.js          the scripted walk-through
web/static/engine.js        the scoring model in the browser — GENERATED, do not edit
web/static/tryit.js         score records a visitor types in
web/static/nearby.js        find help near you, over OpenStreetMap
web/static/static-mode.js   serves precomputed answers when there is no server
serve.py                    demo-day launcher: binds the LAN, prints a QR code

n8n/reunify_workflow.ts     the 17-stage pipeline as SDK code — the readable copy
n8n/reunify_workflow.json   the same pipeline, importable into n8n

scripts/build_static.py         precompute every answer and build the static site
scripts/build_browser_engine.py how web/static/engine.js is produced
scripts/export_n8n_workflow.py  how n8n/reunify_workflow.json is produced
scripts/check_parity.py         assert the three implementations still agree
scripts/n8n_payload.py          build a request body for the pipeline
scripts/verify_n8n_parity.mjs   run the workflow's Code nodes outside n8n

data/                       generated corpus — synthetic, gitignored, rebuilt by
                            `python -m engine.generate`
dist/                       the static build — generated, gitignored
```

The scoring model exists three times: in `engine/match.py`, in the workflow's
Code nodes, and in the browser. Only the first is written by hand. The other
two are generated from it or checked against it, and `scripts/check_parity.py`
fails the build if they drift.

---

## Limits

- Comparison is all-pairs. At demo scale that is a few thousand comparisons in
  under a second; a real deployment would block candidates before scoring.
- The review log is in memory and is discarded when the process stops. It is a
  prototype convenience, not a system of record.
- The default face descriptor is classical, not learned, and is the weakest
  part of the system. See the measurements above before trusting it.
- Nothing here has been tested against real humanitarian data, and it should
  not be, in this form, without a protection assessment and the involvement of
  the organisations whose records it would touch.
