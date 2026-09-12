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

The scoring model exists twice: once in `engine/match.py` and once inside the
workflow's Code nodes, which run in a sandbox that cannot import any of this
project's code. That is a liability unless something checks them against each
other:

```bash
node scripts/verify_n8n_parity.mjs /tmp/payload.json
```

They are not expected to agree exactly. The Python scorer consults a place-name
gazetteer and phonetic coders the workflow does not carry, so it resolves known
aliases the workflow sees only as fuzzy string similarity — worth about two
points on the demo pair (94.7 vs 92.3). The face score, the weights, the
coverage damping and the bands must agree; a divergence there is a bug.

---

## Layout

```
engine/faces.py           synthetic face generation: latents and captures
engine/face_embedding.py  FaceEmbeddingService, alignment, calibration
engine/match.py           the scoring model (reference implementation)
engine/generate.py        synthetic registries, faces and ground truth
engine/evaluate.py        accuracy against planted ground truth
web/app.py                caseworker API and the face embedding endpoint
web/static/               review interface and the scripted walk-through
n8n/reunify_workflow.ts   the 17-stage pipeline, as SDK code
scripts/                  n8n payload builder and the parity check
data/                     generated corpus — all synthetic, safe to delete
```

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
