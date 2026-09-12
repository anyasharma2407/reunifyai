"""
The Reunification Engine matcher.

Compares every record in one registry against every record in another and
returns *ranked candidates with reasons*, never a decision. Nothing in this
module is allowed to conclude that two records are the same person; it
produces evidence for a human reviewer to weigh.

Scoring model
-------------
Each field contributes a `Signal`: a 0-100 similarity score plus an
*evidence weight* saying how much that score deserves to count. Separating the
two is what lets the engine behave sensibly on messy data:

  * A missing field gets weight 0 rather than score 0, so an absent birth date
    dilutes the evidence instead of falsely arguing against a match.
  * Two vague dates that overlap ("approx. 1994" vs "1994") score high but
    weigh less than two exact dates that agree, because agreeing at year
    granularity is far weaker evidence than agreeing to the day.

Weighted scores are renormalised over the weight actually available, then
damped by *evidence coverage* so that a pair matched on name alone can never
reach the same confidence as a pair corroborated across every field.

Name similarity fuses three families of algorithm, because transliteration and
OCR damage names in different ways:

  * edit distance (Jaro-Winkler, and Levenshtein ratio via RapidFuzz) catches
    scanner noise and keying slips -- "Behzadi" vs "Behzady"
  * phonetic coding (Metaphone, NYSIIS, Soundex) catches transliteration, where
    the spelling diverges wildly but the sound does not -- "Mohammed" vs
    "Muhammad" vs "Mohamed" all encode to MHMT
  * an order-swap check catches the intake error where given and family name
    are entered in the wrong boxes

Edit distance alone scores Mohammed/Muhammad at 0.85, which is indistinguishable
from noise; phonetic consensus recognises them as the same name. Neither signal
is sufficient alone, so the engine takes the more generous of the two and
records which one fired, so the reviewer can see the reasoning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

import jellyfish
from rapidfuzz import fuzz

from engine.vocab import (
    PUBLIC_NATIONALITY_ALIASES,
    PUBLIC_PLACE_ALIASES,
    RELATIONSHIP_EQUIVALENCE,
)

TODAY_YEAR = 2026

# Base importance of each signal, before availability is taken into account.
#
# Six signals, summing to 1.0. Location is split across the two places a
# registry records, because "same place" is a different claim depending on
# whether it is where someone came from or where they were last seen.
#
# Face is the heaviest single signal but is deliberately left short of a
# majority, so that the combined weight of the other five always exceeds it and
# a face score can never outvote everything else at once.
#
# Weight alone is not enough, though. Scores are renormalised over the evidence
# that was actually available, so a pair whose *only* usable signal is the face
# would still renormalise back up to near the face score. FACE_ONLY_CEILING
# closes that path explicitly: with nothing to corroborate it, a pair cannot
# leave the weak band no matter how alike the two photographs are.
BASE_WEIGHTS = {
    "face": 0.30,
    "name": 0.25,
    "family": 0.15,
    "birth_date": 0.10,
    "origin_place": 0.09,
    "last_seen_place": 0.06,
    "nationality": 0.05,
}

# Sex is deliberately not one of the weighted signals. It carries almost no
# evidence when it agrees -- roughly half the population agrees with any given
# record -- but a genuine disagreement is worth acting on. So instead of
# spending weight on it, a recorded conflict damps the final score and is
# surfaced to the reviewer as a flag.
SEX_CONFLICT_DAMPING = 0.55

# The highest a pair can score when the face is the only evidence available.
# Chosen to sit below the "possible" band, so such a pair is visible to a
# reviewer but never presented as a likely match.
FACE_ONLY_CEILING = 39.0


# --------------------------------------------------------------------------
# Signal container
# --------------------------------------------------------------------------

@dataclass
class Signal:
    field_name: str
    label: str
    score: float            # 0-100 similarity
    weight: float           # 0-1 evidence strength multiplier
    a_value: str
    b_value: str
    detail: str             # human-readable reason, shown in the UI
    status: str             # strong | partial | weak | conflict | unavailable
    method: str = ""        # which technique decided this, for the UI to badge

    def as_dict(self) -> dict:
        return {
            "field": self.field_name,
            "label": self.label,
            "score": round(self.score),
            "weight": round(self.weight, 2),
            "a_value": self.a_value,
            "b_value": self.b_value,
            "detail": self.detail,
            "status": self.status,
            "method": self.method,
        }


def _status(score: float) -> str:
    if score >= 85:
        return "strong"
    if score >= 65:
        return "partial"
    return "weak"


# --------------------------------------------------------------------------
# Date handling: parse messy strings into tolerance windows
# --------------------------------------------------------------------------

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})

PRECISION_WEIGHT = {"day": 1.0, "month": 0.85, "year": 0.70, "approx-year": 0.58}


@dataclass
class DateWindow:
    """A birth date as an interval, because most registry dates are not points."""
    earliest: date
    latest: date
    precision: str
    rendered: str

    @property
    def midpoint(self) -> date:
        return self.earliest + (self.latest - self.earliest) / 2


def _year_window(year: int, slack: int, precision: str, rendered: str) -> DateWindow:
    return DateWindow(date(year - slack, 1, 1), date(year + slack, 12, 31),
                      precision, rendered)


def parse_date(raw: str) -> DateWindow | None:
    """
    Turn whatever the intake desk wrote into an interval.

    Handles ISO dates, day/month and month/day orderings (widening the window
    when the ordering is genuinely ambiguous), long-form dates, bare
    month-and-year, bare years, 'approx.'/'c.' hedges, and stated ages.
    Returns None when nothing usable was recorded.
    """
    if not raw or not raw.strip():
        return None
    s = raw.strip()

    # approx. 1994 / c. 1994 / circa 1994  -> a year, explicitly hedged
    m = re.fullmatch(r"(?:approx\.?|c\.?|circa|ca\.?)\s*(\d{4})", s, re.I)
    if m:
        return _year_window(int(m.group(1)), 1, "approx-year", s)

    # age approx 32 / age 32
    m = re.fullmatch(r"age\s*(?:approx\.?)?\s*(\d{1,3})", s, re.I)
    if m:
        return _year_window(TODAY_YEAR - int(m.group(1)), 1, "approx-year", s)

    # 1994-02-08
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            exact = date(y, mo, d)
            return DateWindow(exact, exact, "day", s)
        except ValueError:
            return _year_window(y, 0, "year", s)

    # 08/02/1994 -- could be D/M or M/D. If both halves are <= 12 the ordering
    # is unrecoverable, so widen the window to cover both readings rather than
    # guessing and silently fabricating precision.
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", s)
    if m:
        p, q, y = (int(g) for g in m.groups())
        candidates = []
        for dd, mm in ((p, q), (q, p)):
            try:
                candidates.append(date(y, mm, dd))
            except ValueError:
                pass
        if not candidates:
            return _year_window(y, 0, "year", s)
        if len(candidates) == 1:
            # One reading is impossible (e.g. 13/02), so the ordering is recoverable.
            return DateWindow(candidates[0], candidates[0], "day", s)
        # Both readings are valid. The window now spans months, so it is scored
        # as month-precision evidence rather than pretending to know the day.
        return DateWindow(min(candidates), max(candidates), "month", s)

    # March 8, 1994  /  March 1994
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)
    if m and m.group(1).lower() in MONTHS:
        mo = MONTHS[m.group(1).lower()]
        try:
            exact = date(int(m.group(3)), mo, int(m.group(2)))
            return DateWindow(exact, exact, "day", s)
        except ValueError:
            pass

    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", s)
    if m and m.group(1).lower() in MONTHS:
        mo, y = MONTHS[m.group(1).lower()], int(m.group(2))
        last = 31
        while last > 27:
            try:
                end = date(y, mo, last)
                break
            except ValueError:
                last -= 1
        return DateWindow(date(y, mo, 1), end, "month", s)

    # bare year
    m = re.fullmatch(r"(\d{4})", s)
    if m:
        return _year_window(int(m.group(1)), 0, "year", s)

    return None


def score_birth_date(a_raw: str, b_raw: str) -> Signal:
    wa, wb = parse_date(a_raw), parse_date(b_raw)

    if wa is None or wb is None:
        missing = "both registries" if wa is None and wb is None else (
            "Registry A" if wa is None else "Registry B")
        return Signal("birth_date", "Date of birth", 0.0, 0.0,
                      a_raw or "—", b_raw or "—",
                      f"Not recorded by {missing} — excluded from scoring",
                      "unavailable", "unavailable")

    # Evidence is only as strong as the vaguer of the two records.
    weight = min(PRECISION_WEIGHT[wa.precision], PRECISION_WEIGHT[wb.precision])

    overlap = not (wa.latest < wb.earliest or wb.latest < wa.earliest)
    if overlap:
        tight = wa.precision == "day" and wb.precision == "day"
        detail = ("Exact dates agree" if tight
                  else f"Tolerance windows overlap "
                       f"({wa.precision} vs {wb.precision} precision)")
        return Signal("birth_date", "Date of birth", 100.0, weight,
                      wa.rendered, wb.rendered, detail, "strong",
                      "exact-date" if tight else "tolerance-window")

    gap = abs((wa.midpoint - wb.midpoint).days)
    # Two precise dates that disagree is real counter-evidence; two vague ones
    # that disagree by a year is routine registry drift.
    half_life = 120 if (wa.precision == "day" and wb.precision == "day") else 400
    score = 100 * (0.5 ** (gap / half_life))
    years = gap / 365.25
    detail = (f"Windows do not overlap — about {years:.1f} year"
              f"{'s' if years >= 1.05 else ''} apart")
    return Signal("birth_date", "Date of birth", score, weight,
                  wa.rendered, wb.rendered, detail,
                  "conflict" if score < 40 else _status(score), "date-gap")


# --------------------------------------------------------------------------
# Name handling
# --------------------------------------------------------------------------

def _clean_name(n: str) -> str:
    return re.sub(r"[^a-z]", "", (n or "").casefold())


def _phonetic_agreement(a: str, b: str) -> tuple[str | None, str, float]:
    """
    Return the strongest phonetic algorithm that considers these the same name,
    the shared code, and the score floor that agreement justifies.

    jellyfish 1.x does not ship Double Metaphone, so instead of relying on one
    coder we ask three and trust consensus: Metaphone is the most discriminating,
    NYSIIS is tuned for non-English surnames, Soundex is the loosest and so the
    least trusted.
    """
    if not a or not b:
        return None, "", 0.0
    for name, fn, floor in (
        ("Metaphone", jellyfish.metaphone, 90.0),
        ("NYSIIS", jellyfish.nysiis, 84.0),
        ("Soundex", jellyfish.soundex, 76.0),
    ):
        try:
            ca, cb = fn(a), fn(b)
        except Exception:
            continue
        if ca and ca == cb:
            return name, ca.upper(), floor
    return None, "", 0.0


def _single_name_score(a: str, b: str) -> tuple[float, str]:
    """Similarity of one name part, fusing edit distance with phonetic coding."""
    ca, cb = _clean_name(a), _clean_name(b)
    if not ca or not cb:
        return 0.0, "missing"
    if ca == cb:
        return 100.0, "identical"

    jw = jellyfish.jaro_winkler_similarity(ca, cb) * 100
    lev = fuzz.ratio(ca, cb)
    edit = 0.6 * jw + 0.4 * lev

    algo, code, floor = _phonetic_agreement(a, b)
    if floor > edit:
        return floor, f"{algo} codes match ({code}); spelling similarity {edit:.0f}%"
    if algo:
        return edit, f"spelling similarity {edit:.0f}%, {algo} codes match ({code})"
    return edit, f"spelling similarity {edit:.0f}%"


def score_name(a_rec: dict, b_rec: dict) -> Signal:
    a_given, a_family = a_rec.get("given_name", ""), a_rec.get("family_name", "")
    b_given, b_family = b_rec.get("given_name", ""), b_rec.get("family_name", "")

    given_s, given_why = _single_name_score(a_given, b_given)
    family_s, family_why = _single_name_score(a_family, b_family)
    straight = 0.5 * given_s + 0.5 * family_s

    # The same two names entered into the wrong boxes.
    sw_given_s, sw_given_why = _single_name_score(a_given, b_family)
    sw_family_s, sw_family_why = _single_name_score(a_family, b_given)
    swapped = 0.5 * sw_given_s + 0.5 * sw_family_s

    a_value = f"{a_given} {a_family}".strip()
    b_value = f"{b_given} {b_family}".strip()

    # A record with no name at all is missing evidence, not evidence against.
    # Every other signal already treats absence this way; name did not, which
    # let an empty name push a pair's score down instead of simply abstaining.
    if not _clean_name(a_given) + _clean_name(a_family) or \
       not _clean_name(b_given) + _clean_name(b_family):
        missing = "Registry A" if not a_value else "Registry B"
        return Signal("name", "Name", 0.0, 0.0, a_value or "—", b_value or "—",
                      f"No name recorded by {missing} — excluded from scoring",
                      "unavailable")

    # Only *claim* a swap when the swapped reading is actually convincing.
    # Otherwise it is merely the less-bad of two poor readings, and announcing
    # a swap would invent a story the evidence does not support.
    method = "edit-distance"
    if "codes match" in given_why or "codes match" in family_why:
        method = "phonetic"

    if swapped > straight + 12 and swapped >= 65:
        method = "name-order-swap"
        score = swapped * 0.96  # small haircut: the swap itself is an anomaly
        detail = (f"Name order appears swapped between registries — "
                  f"given↔family. Given vs family: {sw_given_why}; "
                  f"family vs given: {sw_family_why}")
    else:
        score = straight
        detail = f"Given name — {given_why}. Family name — {family_why}"

    # A name is only weak evidence if one side barely recorded one.
    weight = 1.0 if (_clean_name(a_given) and _clean_name(a_family)
                     and _clean_name(b_given) and _clean_name(b_family)) else 0.6

    return Signal("name", "Name", score, weight, a_value, b_value,
                  detail, _status(score), method)


# --------------------------------------------------------------------------
# Place handling
# --------------------------------------------------------------------------

def _normalise_place(p: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (p or "").casefold())).strip()


# Gazetteer the matcher is permitted to see. Deliberately incomplete, so fuzzy
# matching still has to do real work on the aliases it was never told about.
_ALIAS_TO_CLUSTER: dict[str, str] = {}
for _cluster_id, _variants in PUBLIC_PLACE_ALIASES.items():
    for _v in _variants:
        _ALIAS_TO_CLUSTER[_normalise_place(_v)] = _cluster_id


def score_place(a_raw: str, b_raw: str, field_name: str, label: str) -> Signal:
    na, nb = _normalise_place(a_raw), _normalise_place(b_raw)

    if not na or not nb:
        missing = "both registries" if not na and not nb else (
            "Registry A" if not na else "Registry B")
        return Signal(field_name, label, 0.0, 0.0, a_raw or "—", b_raw or "—",
                      f"Not recorded by {missing} — excluded from scoring",
                      "unavailable", "unavailable")

    if na == nb:
        return Signal(field_name, label, 100.0, 1.0, a_raw, b_raw,
                      "Identical entry", "strong", "exact")

    ca, cb = _ALIAS_TO_CLUSTER.get(na), _ALIAS_TO_CLUSTER.get(nb)
    if ca and ca == cb:
        return Signal(field_name, label, 97.0, 1.0, a_raw, b_raw,
                      f"{a_raw} ≈ {b_raw} — known alias in place-name gazetteer",
                      "strong", "gazetteer-alias")

    token = fuzz.token_set_ratio(na, nb)
    jw = jellyfish.jaro_winkler_similarity(na, nb) * 100
    score = max(token, jw)
    method = "fuzzy"
    reason = f"Fuzzy string similarity {score:.0f}% — not in the gazetteer"

    algo, code, floor = _phonetic_agreement(na.replace(" ", ""), nb.replace(" ", ""))
    if floor > score:
        score = floor
        method = "phonetic"
        reason = (f"{a_raw} ≈ {b_raw} — {algo} codes match ({code}), "
                  f"unlisted alias or spelling drift")

    # Place names are noisier evidence than names or dates; a middling fuzzy
    # score between two different towns should not move the needle much.
    weight = 1.0 if score >= 80 else 0.7
    return Signal(field_name, label, score, weight, a_raw, b_raw,
                  reason, _status(score), method)


def score_sex(a_raw: str, b_raw: str) -> Signal:
    a, b = (a_raw or "").strip().upper(), (b_raw or "").strip().upper()
    if not a or not b:
        missing = "both registries" if not a and not b else (
            "Registry A" if not a else "Registry B")
        return Signal("sex", "Sex", 0.0, 0.0, a_raw or "—", b_raw or "—",
                      f"Not recorded by {missing} — excluded from scoring",
                      "unavailable", "unavailable")
    if a == b:
        return Signal("sex", "Sex", 100.0, 1.0, a, b, "Agrees", "strong", "exact")
    return Signal("sex", "Sex", 0.0, 1.0, a, b,
                  "Recorded differently in each registry", "conflict", "disagree")


def score_nationality(a_raw: str, b_raw: str) -> Signal:
    """
    Compare recorded nationality.

    The weakest signal in the model, and the one most likely to be wrong for
    reasons that have nothing to do with identity: nationality at a registration
    desk is frequently the enumerator's inference rather than a document, and
    "Stateless" or "Undetermined" is recorded when nobody could establish it.
    So agreement is treated as mild corroboration, and disagreement is not
    treated as counter-evidence at all -- it merely fails to corroborate.
    """
    na, nb = (a_raw or "").strip(), (b_raw or "").strip()
    if not na or not nb:
        missing = "both registries" if not na and not nb else (
            "Registry A" if not na else "Registry B")
        return Signal("nationality", "Nationality", 0.0, 0.0, a_raw or "—",
                      b_raw or "—", f"Not recorded by {missing} — excluded from scoring",
                      "unavailable")

    # An undetermined status is an absence of information, not a value that can
    # agree with another absence.
    undetermined = {"stateless", "undetermined", "not established"}
    if na.casefold() in undetermined or nb.casefold() in undetermined:
        return Signal("nationality", "Nationality", 0.0, 0.0, na, nb,
                      "Recorded as undetermined — carries no matching evidence",
                      "unavailable")

    if na.casefold() == nb.casefold():
        return Signal("nationality", "Nationality", 100.0, 1.0, na, nb,
                      "Identical entry", "strong")

    alias = {}
    for cid, variants in PUBLIC_NATIONALITY_ALIASES.items():
        for v in variants:
            alias[v.casefold()] = cid
    ca, cb = alias.get(na.casefold()), alias.get(nb.casefold())
    if ca and ca == cb:
        return Signal("nationality", "Nationality", 97.0, 1.0, na, nb,
                      f"{na} \u2248 {nb} — known equivalent", "strong")

    score = max(fuzz.token_set_ratio(na.casefold(), nb.casefold()),
                jellyfish.jaro_winkler_similarity(na.casefold(), nb.casefold()) * 100)
    # Disagreement must not argue against a match, so the floor is lifted well
    # off zero and the weight drops away rather than the score going negative.
    if score < 70:
        return Signal("nationality", "Nationality", 50.0, 0.35, na, nb,
                      "Different entries — treated as no evidence either way",
                      "weak")
    return Signal("nationality", "Nationality", score, 0.8, na, nb,
                  f"Spelling similarity {score:.0f}% — likely the same nationality",
                  _status(score))


def _relationship_agreement(a: str, b: str) -> tuple[float, str]:
    """How well two relationship terms agree, allowing for vocabulary drift."""
    ca = RELATIONSHIP_EQUIVALENCE.get((a or "").strip().casefold())
    cb = RELATIONSHIP_EQUIVALENCE.get((b or "").strip().casefold())
    if not ca or not cb:
        return 0.85, "relationship not comparable"
    if (a or "").casefold() == (b or "").casefold():
        return 1.0, f"both recorded as {a}"
    if ca == cb:
        return 0.94, f"{a} and {b} are the same relationship, recorded differently"
    return 0.55, f"{a} vs {b} — different relationship"


def score_family(a_rec: dict, b_rec: dict) -> Signal:
    """
    Compare the family members each registry was told about.

    Matching a relative's name is strong corroboration precisely because it is
    independent of everything else: a transliteration that mangles the person's
    own name will not mangle their mother's the same way. It is also the signal
    most likely to be absent, since whether a relative was named at all depends
    on what the person was asked.

    Relatives are matched greedily, best pair first, because the two desks have
    no shared ordering and may have recorded different numbers of people.
    """
    a_fam = [m for m in (a_rec.get("family_members") or []) if m.get("name")]
    b_fam = [m for m in (b_rec.get("family_members") or []) if m.get("name")]

    if not a_fam or not b_fam:
        missing = "both registries" if not a_fam and not b_fam else (
            "Registry A" if not a_fam else "Registry B")
        return Signal("family", "Family members", 0.0, 0.0,
                      "; ".join(m["name"] for m in a_fam) or "—",
                      "; ".join(m["name"] for m in b_fam) or "—",
                      f"No family member recorded by {missing} — excluded from scoring",
                      "unavailable")

    pairs = []
    for i, am in enumerate(a_fam):
        for j, bm in enumerate(b_fam):
            name_score, why = _single_name_score(
                re.sub(r"\s+", "", am["name"]), re.sub(r"\s+", "", bm["name"]))
            rel_factor, rel_why = _relationship_agreement(
                am.get("relation", ""), bm.get("relation", ""))
            pairs.append((name_score * rel_factor, i, j, am, bm, why, rel_why))

    pairs.sort(reverse=True, key=lambda t: t[0])
    used_a: set[int] = set()
    used_b: set[int] = set()
    chosen = []
    for score, i, j, am, bm, why, rel_why in pairs:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        chosen.append((score, am, bm, why, rel_why))

    best = max(c[0] for c in chosen)
    mean = sum(c[0] for c in chosen) / len(chosen)
    # The best-matching relative dominates: one confidently shared relative is
    # the evidence, and a second unmatched one usually means the two desks
    # simply asked different questions.
    score = 0.75 * best + 0.25 * mean

    top = max(chosen, key=lambda c: c[0])
    detail = (f"{top[1]['name']} ({top[1].get('relation', '?')}) vs "
              f"{top[2]['name']} ({top[2].get('relation', '?')}) — "
              f"{top[3]}; {top[4]}")
    if len(chosen) > 1:
        detail += f". {len(chosen)} family members compared"

    # Evidence is stronger when both sides named the same number of people.
    weight = 1.0 if len(a_fam) == len(b_fam) else 0.8

    return Signal("family", "Family members", score, weight,
                  "; ".join(f"{m['name']} ({m.get('relation', '?')})" for m in a_fam),
                  "; ".join(f"{m['name']} ({m.get('relation', '?')})" for m in b_fam),
                  detail, _status(score))


def score_face(a_rec: dict, b_rec: dict, face_index=None) -> Signal:
    """
    Compare the two registries' photographs, via their embeddings.

    The score answers one narrow question: how much more alike are these two
    faces than two unrelated faces in this corpus? It is never a claim that the
    photographs are of the same person, and the detail string is written so
    that a reviewer reading it cannot come away thinking otherwise.
    """
    a_id, b_id = a_rec.get("record_id", ""), b_rec.get("record_id", "")

    if face_index is None:
        return Signal("face", "Facial similarity", 0.0, 0.0, "—", "—",
                      "Face matching not enabled — excluded from scoring",
                      "unavailable")

    result = face_index.compare(a_id, b_id)
    if result is None:
        have_a, have_b = a_id in face_index, b_id in face_index
        missing = "both registries" if not have_a and not have_b else (
            "Registry A" if not have_a else "Registry B")
        return Signal("face", "Facial similarity", 0.0, 0.0,
                      f"Photograph on file ({a_id})" if have_a else "—",
                      f"Photograph on file ({b_id})" if have_b else "—",
                      f"No photograph held by {missing} — excluded from scoring",
                      "unavailable")

    score = float(result["similarity"])
    z = result["z_above_background"]

    # The signal is deliberately asymmetric. Two photographs looking alike is
    # real corroboration; two photographs looking unalike is weak evidence of
    # anything, because the innocent explanations are ordinary -- years between
    # the two captures, injury, illness, a covered head, a bad crop, a camera
    # that could not expose for a dark face. Facial similarity is an indicator
    # and not proof of identity; the corollary is that facial dissimilarity is
    # not proof of non-identity, and the weighting has to say so.
    #
    # Measured on this corpus, giving a low face score full weight costs two of
    # the twenty-six planted pairs their top-1 rank, each one a pair agreeing
    # at 90%+ on name, family, age and location.
    weight = 1.0 if score >= 55.0 else 0.25 + 0.75 * (score / 55.0)

    detail = (f"{result['basis']}; cosine {result['cosine']}, "
              f"{z} SD above the similarity of unrelated faces. "
              f"Indicator only — not proof of identity.")
    if weight < 1.0:
        detail += (" Low similarity is given reduced weight: it does not count "
                   "as evidence against a match.")

    # The displayed values name the photograph rather than its file path: a
    # reviewer comparing two records has no use for "faces/A-0013.png".
    return Signal("face", "Facial similarity", score, weight,
                  f"Photograph on file ({a_id})", f"Photograph on file ({b_id})",
                  detail, _status(score))


# --------------------------------------------------------------------------
# Pair scoring
# --------------------------------------------------------------------------

def band_for(score: float) -> str:
    if score >= 80:
        return "strong"
    if score >= 60:
        return "possible"
    if score >= 40:
        return "weak"
    return "unlikely"


def by_field_weight(signals, field_name: str) -> float:
    for s in signals:
        if s.field_name == field_name:
            return s.weight
    return 0.0


def score_pair(a_rec: dict, b_rec: dict, face_index=None) -> dict:
    """
    Score one candidate pair and explain every contributing signal.

    The headline number is called a *potential match score*, not a confidence
    or a probability. It says how strongly these two records should be
    prioritised for a human to look at. It does not say they are the same
    person, and no threshold in this file is allowed to say that either.
    """
    signals = [
        score_face(a_rec, b_rec, face_index),
        score_name(a_rec, b_rec),
        score_family(a_rec, b_rec),
        score_birth_date(a_rec.get("birth_date_raw", ""), b_rec.get("birth_date_raw", "")),
        score_place(a_rec.get("origin_place", ""), b_rec.get("origin_place", ""),
                    "origin_place", "Place of origin"),
        score_place(a_rec.get("last_seen_place", ""), b_rec.get("last_seen_place", ""),
                    "last_seen_place", "Last seen"),
        score_nationality(a_rec.get("nationality", ""), b_rec.get("nationality", "")),
    ]

    numerator = sum(s.score * BASE_WEIGHTS[s.field_name] * s.weight for s in signals)
    denominator = sum(BASE_WEIGHTS[s.field_name] * s.weight for s in signals)
    raw = numerator / denominator if denominator else 0.0

    # How much of the possible evidence actually existed in both records.
    coverage = denominator / sum(BASE_WEIGHTS.values())

    # Sparse evidence is capped: a pair agreeing on one signal cannot present
    # as strongly as one corroborated across many.
    damping = min(1.0, 0.55 + 0.50 * coverage)
    score = raw * damping

    # Sex is not a weighted signal, but a recorded disagreement is worth acting
    # on; see the note on SEX_CONFLICT_DAMPING.
    sex_signal = score_sex(a_rec.get("sex", ""), b_rec.get("sex", ""))
    flags = []
    if sex_signal.status == "conflict":
        score *= SEX_CONFLICT_DAMPING
        flags.append(f"Sex recorded differently: "
                     f"{sex_signal.a_value} vs {sex_signal.b_value}")

    # Facial similarity on its own is an indicator and nothing more, so it is
    # not allowed to carry a pair out of the weak band unaided.
    non_face_weight = sum(BASE_WEIGHTS[s.field_name] * s.weight
                          for s in signals if s.field_name != "face")
    face_only = non_face_weight <= 1e-9 and by_field_weight(signals, "face") > 0
    if face_only and score > FACE_ONLY_CEILING:
        score = FACE_ONLY_CEILING
        flags.append("Facial similarity is the only available signal — score "
                     "capped pending corroborating evidence")

    by_field = {s.field_name: s for s in signals}

    # A person recorded onward from one place is often recorded as arriving at
    # the other; a cross-link between origin and last-seen is weak but genuine
    # corroboration, so it is surfaced as a note rather than scored.
    cross_notes = []
    for a_field, b_field, text in (
        ("origin_place", "last_seen_place",
         "Registry A origin matches Registry B last-seen location"),
        ("last_seen_place", "origin_place",
         "Registry A last-seen location matches Registry B origin"),
    ):
        if by_field[a_field].status == "strong":
            continue
        av = _normalise_place(a_rec.get(a_field, ""))
        bv = _normalise_place(b_rec.get(b_field, ""))
        if not av or not bv:
            continue
        same_cluster = (_ALIAS_TO_CLUSTER.get(av) is not None
                        and _ALIAS_TO_CLUSTER.get(av) == _ALIAS_TO_CLUSTER.get(bv))
        if av == bv or same_cluster:
            cross_notes.append(f"{text} — consistent with onward movement")

    # Why this pair surfaced: the signals that actually carried it, strongest
    # first, so the reviewer sees the argument rather than just the number.
    reasons = [
        {"field": s.field_name, "label": s.label, "score": round(s.score),
         "contribution": round(s.score * BASE_WEIGHTS[s.field_name] * s.weight
                               / max(denominator, 1e-9), 3)}
        for s in sorted(signals, key=lambda s: -s.score * BASE_WEIGHTS[s.field_name] * s.weight)
        if s.status in ("strong", "partial") and s.weight > 0
    ]

    face = by_field["face"]
    return {
        "b_record": b_rec,
        "potential_match_score": round(score, 1),
        "band": band_for(score),
        "evidence_coverage": round(coverage, 2),
        "signals": [s.as_dict() for s in signals],
        "sex_check": sex_signal.as_dict(),
        "flags": flags,
        "cross_notes": cross_notes,
        "surfaced_because": reasons,
        "face_similarity": round(face.score, 1) if face.weight > 0 else None,
        "face_only": face_only,
        "review_required": True,
        "notice": "Potential match score. Indicates how strongly these records "
                  "should be prioritised for human review — not a confirmed "
                  "identity.",
    }


def top_candidates(a_rec: dict, b_records: Iterable[dict], top_n: int = 3,
                   floor: float = 25.0, face_index=None) -> list[dict]:
    """Rank every Registry B record against one Registry A record."""
    scored = [score_pair(a_rec, b, face_index) for b in b_records]
    scored.sort(key=lambda c: c["potential_match_score"], reverse=True)
    return [c for c in scored[:top_n] if c["potential_match_score"] >= floor]


def match_all(a_records: list[dict], b_records: list[dict], top_n: int = 3,
              floor: float = 25.0, face_index=None) -> list[dict]:
    """
    Full cross-comparison of both registries.

    Every A record is compared against every B record. At demo scale that is a
    few thousand comparisons and takes under a second; see the README for how
    a real deployment would block candidates before scoring.
    """
    return [
        {"a_record": a,
         "candidates": top_candidates(a, b_records, top_n, floor, face_index)}
        for a in a_records
    ]
