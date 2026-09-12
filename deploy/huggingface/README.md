---
title: ReunifyAI
emoji: 🔎
colorFrom: blue
colorTo: gray
sdk: static
pinned: false
license: mit
short_description: Candidate matching for family tracing
---

# ReunifyAI — Reunification Engine

Decision support for family tracing across humanitarian registries that have
never been connected to each other. It ranks *candidate pairs of records* for a
human caseworker and explains its reasoning. **It does not identify anyone.**

> **FACIAL SIMILARITY IS AN INDICATOR, NOT PROOF OF IDENTITY.**
>
> **HUMAN VERIFICATION REQUIRED.**

Every record, every photograph and every place name in this Space is
**synthetic**. The geography is a fictional setting invented so that nothing
here can be mistaken for a real displacement situation, and every face is drawn
procedurally from a seeded number — no photograph of a real person is used,
downloaded, or shipped.

## Try it

* `/?demo=1` — the scripted walk-through: two synthetic faces side by side,
  facial similarity, then the potential match score.
* `/#A-0013` — the caseworker review screen for that pair, with the full
  evidence breakdown.
* `/api/meta` — corpus summary and the weighting the scorer uses.

## How it scores

Six signals: facial similarity (30%), name (25%), family members (15%),
age (10%), location (15%, split across origin and last-seen) and
nationality (5%).

Three rules sit on top of the weights:

* **A missing field abstains** rather than scoring zero, so absent evidence
  dilutes a match instead of arguing against it.
* **Facial similarity is asymmetric.** A high score corroborates; a low one is
  given reduced weight, because dissimilarity has ordinary innocent
  explanations — years between captures, injury, a covered head.
* **Face alone cannot carry a pair** out of the weak band, however alike two
  photographs are.

Source, measurements and the n8n workflow that runs the same pipeline:
<https://github.com/anyasharma2407/reunifyai>
