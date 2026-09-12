"""
Synthetic registry generator for the Reunification Engine.

Produces two mock registries that model the same displaced population as
recorded by two organisations that have never spoken to each other, plus a
ground-truth file saying which record in A is the same person as which record
in B.

NOTHING HERE IS REAL. Names, places, organisations and the whole geography are
invented. The generator exists so the matcher can be tested and scored without
any real person's information ever entering the project.

How the messiness is modelled
-----------------------------
Each synthetic person has one canonical identity. Each registry then *renders*
that person independently, the way a separate intake desk with its own
transliteration habits, its own date conventions and its own data-entry
pressure would. Divergence between the two renderings is therefore generated,
not hand-authored, and every divergence is logged in the ground truth so the
demo can explain exactly which obstacle each match had to overcome.

Usage
-----
    python -m engine.generate --seed 7 --size 80
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import dataclass, asdict, field
from datetime import date, timedelta
from pathlib import Path

from engine.vocab import (
    GIVEN_NAME_CLUSTERS,
    FAMILY_NAME_CLUSTERS,
    NATIONALITY_CLUSTERS,
    PLACE_CLUSTERS,
    RELATIONSHIP_DRIFT,
    REGISTRY_A_SOURCES,
    REGISTRY_B_SOURCES,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

CITY_CLUSTERS = [p for p in PLACE_CLUSTERS if p["kind"] in ("city", "town")]
CAMP_CLUSTERS = [p for p in PLACE_CLUSTERS if p["kind"] == "camp"]


# --------------------------------------------------------------------------
# Canonical synthetic people
# --------------------------------------------------------------------------

@dataclass
class Relative:
    """A family member as the person themselves described them at intake."""
    given_cluster: dict
    family_cluster: dict
    relation: str


@dataclass
class Person:
    person_id: str
    given_cluster: dict
    family_cluster: dict
    sex: str
    birth_date: date
    origin_cluster: dict
    last_seen_cluster: dict
    nationality_cluster: dict
    relatives: list = field(default_factory=list)


def make_person(rng: random.Random, pid: str, family_cluster=None,
                birth_year_near: int | None = None) -> Person:
    given = rng.choice(GIVEN_NAME_CLUSTERS)
    family = family_cluster or rng.choice(FAMILY_NAME_CLUSTERS)
    if birth_year_near is not None:
        year = birth_year_near + rng.choice([-2, -1, 1, 2])
    else:
        year = rng.randint(1958, 2016)
    year = max(1958, min(2016, year))
    birth = date(year, rng.randint(1, 12), rng.randint(1, 28))
    return Person(
        person_id=pid,
        given_cluster=given,
        family_cluster=family,
        sex=given["gender"],
        birth_date=birth,
        origin_cluster=rng.choice(CITY_CLUSTERS),
        last_seen_cluster=rng.choice(CITY_CLUSTERS + CAMP_CLUSTERS),
        nationality_cluster=rng.choice(NATIONALITY_CLUSTERS),
    )


# Which relationship terms carry a gender. The rest -- parent, child, sibling,
# spouse, cousin, guardian -- genuinely do not, and are left free.
RELATION_GENDER = {
    "mother": "F", "sister": "F", "daughter": "F", "wife": "F", "aunt": "F",
    "father": "M", "brother": "M", "son": "M", "husband": "M", "uncle": "M",
}


def attach_relatives(rng: random.Random, person: Person) -> None:
    """
    Give a person one or two family members they named at intake.

    A relative usually shares the family-name cluster -- which is precisely why
    family information is useful evidence and also why it is dangerous on its
    own: in a displaced population many unrelated people share a surname, so a
    matching relative name is corroboration, never proof. A minority are given
    a different family name, so the matcher cannot simply assume the surname
    carries the relationship.
    """
    n = 1 if rng.random() < 0.72 else (2 if rng.random() < 0.55 else 0)
    kinds = list(RELATIONSHIP_DRIFT.keys())
    for _ in range(n):
        relation = rng.choice(kinds)
        # The relationship implies a gender for most terms, so the name has to
        # agree with it: "sister: Omar" is not messy data, it is a bug that
        # would make the corpus look careless to anyone reading a record.
        want = RELATION_GENDER.get(relation)
        pool = [c for c in GIVEN_NAME_CLUSTERS if c["gender"] == want] if want \
            else GIVEN_NAME_CLUSTERS
        shares_surname = rng.random() < 0.82
        person.relatives.append(Relative(
            given_cluster=rng.choice(pool),
            family_cluster=person.family_cluster if shares_surname
                           else rng.choice(FAMILY_NAME_CLUSTERS),
            relation=relation,
        ))


# --------------------------------------------------------------------------
# Perturbations
# --------------------------------------------------------------------------

OCR_SUBSTITUTIONS = [
    ("rn", "m"), ("m", "rn"), ("cl", "d"), ("d", "cl"),
    ("l", "i"), ("i", "l"), ("e", "c"), ("c", "e"),
    ("u", "v"), ("v", "u"), ("h", "b"), ("o", "a"),
]

KEYBOARD_NEIGHBOURS = {
    "a": "qsz", "b": "vgn", "c": "xdv", "d": "sfe", "e": "wrd", "f": "dgr",
    "g": "fht", "h": "gjy", "i": "uok", "j": "hkn", "k": "jli", "l": "kop",
    "m": "nj", "n": "bmh", "o": "ipl", "p": "ol", "q": "wa", "r": "etf",
    "s": "adw", "t": "ryg", "u": "yih", "v": "cfb", "w": "qes", "x": "zsc",
    "y": "tuh", "z": "asx",
}


def ocr_error(name: str, rng: random.Random) -> tuple[str, str]:
    """Apply one scanner-style corruption to a name."""
    mode = rng.choice(["substitute", "double", "drop", "transpose", "keyboard"])

    if mode == "substitute":
        rng.shuffle(pairs := list(OCR_SUBSTITUTIONS))
        for src, dst in pairs:
            idx = name.lower().find(src)
            if idx > 0:  # never corrupt the leading character
                return name[:idx] + dst + name[idx + len(src):], f"ocr-substitution ({src}->{dst})"
        mode = "double"

    if mode == "double" and len(name) > 2:
        i = rng.randrange(1, len(name))
        return name[:i] + name[i] + name[i:], "ocr-doubled-character"

    if mode == "drop" and len(name) > 3:
        i = rng.randrange(1, len(name) - 1)
        return name[:i] + name[i + 1:], "ocr-dropped-character"

    if mode == "transpose" and len(name) > 3:
        i = rng.randrange(1, len(name) - 2)
        return name[:i] + name[i + 1] + name[i] + name[i + 2:], "ocr-transposed-characters"

    if len(name) > 2:
        i = rng.randrange(1, len(name))
        ch = name[i].lower()
        if ch in KEYBOARD_NEIGHBOURS:
            sub = rng.choice(KEYBOARD_NEIGHBOURS[ch])
            return name[:i] + sub + name[i + 1:], "keying-typo"
    return name, "no-op"


def render_birth_date(birth: date, style: str, rng: random.Random) -> str:
    """Render a known date the way a given intake desk would have written it."""
    if style == "iso":
        return birth.isoformat()
    if style == "dmy":
        return f"{birth.day:02d}/{birth.month:02d}/{birth.year}"
    if style == "mdy":
        return f"{birth.month:02d}/{birth.day:02d}/{birth.year}"
    if style == "long":
        return f"{MONTHS[birth.month - 1]} {birth.day}, {birth.year}"
    if style == "month_year":
        return f"{MONTHS[birth.month - 1]} {birth.year}"
    if style == "approx_year":
        return f"approx. {birth.year + rng.choice([-1, 0, 0, 1])}"
    if style == "circa_year":
        return f"c. {birth.year + rng.choice([-1, 0, 1])}"
    if style == "year_only":
        return str(birth.year)
    if style == "age_statement":
        age = 2026 - birth.year + rng.choice([-1, 0, 0, 1])
        return f"age approx {age}"
    return birth.isoformat()


CLEAN_DATE_STYLES = ["iso", "iso", "dmy", "long"]
MESSY_DATE_STYLES = ["month_year", "approx_year", "circa_year", "year_only",
                     "age_statement", "mdy", "dmy"]


# --------------------------------------------------------------------------
# Rendering a person into one registry's record
# --------------------------------------------------------------------------

def render_record(person: Person, registry: str, record_id: str,
                  rng: random.Random, messy: bool) -> tuple[dict, list[str]]:
    """
    Render one canonical person as one registry's record.

    `messy=False` gives the tidy rendering (variant index 0, ISO-ish date,
    canonical spellings). `messy=True` lets this registry drift: a different
    transliteration, a looser date, an alternate place spelling, dropped
    fields, scanner noise, or a swapped name order.

    Returns the record and the list of perturbation labels applied.
    """
    notes: list[str] = []

    # --- names -----------------------------------------------------------
    given_variants = person.given_cluster["variants"]
    family_variants = person.family_cluster["variants"]

    if messy and len(given_variants) > 1 and rng.random() < 0.75:
        given = rng.choice(given_variants[1:])
        notes.append(f"given-name transliteration variant ({given_variants[0]} -> {given})")
    else:
        given = given_variants[0]

    if messy and len(family_variants) > 1 and rng.random() < 0.55:
        family = rng.choice(family_variants[1:])
        notes.append(f"family-name transliteration variant ({family_variants[0]} -> {family})")
    else:
        family = family_variants[0]

    if messy and rng.random() < 0.30:
        target = rng.choice(["given", "family"])
        if target == "given":
            given, label = ocr_error(given, rng)
        else:
            family, label = ocr_error(family, rng)
        if label != "no-op":
            notes.append(f"{target}-name {label}")

    swap_names = messy and rng.random() < 0.10
    if swap_names:
        given, family = family, given
        notes.append("given/family name order swapped at intake")

    # --- birth date ------------------------------------------------------
    if messy and rng.random() < 0.12:
        birth_raw = ""
        notes.append("birth date missing")
    else:
        style = rng.choice(MESSY_DATE_STYLES if messy else CLEAN_DATE_STYLES)
        birth_raw = render_birth_date(person.birth_date, style, rng)
        if messy and style in ("month_year", "approx_year", "circa_year",
                               "year_only", "age_statement"):
            notes.append(f"birth date recorded imprecisely ({style})")

    # --- places ----------------------------------------------------------
    def place(cluster, label):
        variants = cluster["variants"]
        if messy and len(variants) > 1 and rng.random() < 0.65:
            value = rng.choice(variants[1:])
            notes.append(f"{label} alternate spelling ({variants[0]} -> {value})")
        else:
            value = variants[0]
        if messy and rng.random() < 0.12:
            value, lbl = ocr_error(value, rng)
            if lbl != "no-op":
                notes.append(f"{label} {lbl}")
        return value

    origin = place(person.origin_cluster, "origin")
    last_seen = place(person.last_seen_cluster, "last-seen location")

    def drop_field(label):
        """Clear a field and retract any note about how it had been spelled."""
        notes[:] = [n for n in notes if not n.startswith(label)]
        notes.append(f"{label} missing")

    if messy and rng.random() < 0.14:
        origin = ""
        drop_field("origin")
    if messy and rng.random() < 0.10:
        last_seen = ""
        drop_field("last-seen location")

    sex = person.sex
    if messy and rng.random() < 0.05:
        sex = ""
        notes.append("sex not recorded")

    # --- nationality -----------------------------------------------------
    nat_variants = person.nationality_cluster["variants"]
    if messy and len(nat_variants) > 1 and rng.random() < 0.45:
        nationality = rng.choice(nat_variants[1:])
        notes.append(f"nationality recorded as a variant "
                     f"({nat_variants[0]} -> {nationality})")
    else:
        nationality = nat_variants[0]
    if messy and rng.random() < 0.10:
        nationality = ""
        notes.append("nationality missing")

    # --- family members --------------------------------------------------
    # Each desk re-records the relatives it was told about, with the same
    # transliteration drift the main name is subject to, and sometimes with a
    # generalised relationship ("mother" written down as "parent").
    family_members = []
    for rel in person.relatives:
        if messy and rng.random() < 0.18:
            notes.append("a named family member was not recorded")
            continue
        gv = rel.given_cluster["variants"]
        fv = rel.family_cluster["variants"]
        g = rng.choice(gv[1:]) if (messy and len(gv) > 1 and rng.random() < 0.60) else gv[0]
        f2 = rng.choice(fv[1:]) if (messy and len(fv) > 1 and rng.random() < 0.45) else fv[0]
        if messy and rng.random() < 0.15:
            g, lbl = ocr_error(g, rng)
        relation = rng.choice(RELATIONSHIP_DRIFT.get(rel.relation, [rel.relation])) \
            if messy else rel.relation
        if relation != rel.relation:
            notes.append(f"relationship generalised ({rel.relation} -> {relation})")
        family_members.append({"name": f"{g} {f2}".strip(), "relation": relation})

    sources = REGISTRY_A_SOURCES if registry == "A" else REGISTRY_B_SOURCES
    intake = date(2025, 1, 1) + timedelta(days=rng.randint(0, 620))

    record = {
        "record_id": record_id,
        "registry": registry,
        "given_name": given,
        "family_name": family,
        "sex": sex,
        "birth_date_raw": birth_raw,
        "origin_place": origin,
        "last_seen_place": last_seen,
        "nationality": nationality,
        "family_members": family_members,
        "source_org": rng.choice(sources),
        "intake_date": intake.isoformat(),
        "face_image": f"faces/{record_id}.png",
    }
    return record, notes


# --------------------------------------------------------------------------
# Corpus assembly
# --------------------------------------------------------------------------

def difficulty_of(notes: list[str]) -> str:
    hard_signals = sum(
        1 for n in notes
        if "ocr" in n or "typo" in n or "missing" in n or "swapped" in n
    )
    if hard_signals >= 2 or len(notes) >= 5:
        return "hard"
    if hard_signals >= 1 or len(notes) >= 2:
        return "moderate"
    return "easy"


def build(seed: int, size: int, overlap_ratio: float):
    rng = random.Random(seed)

    n_overlap = round(size * overlap_ratio)
    n_a_only = size - n_overlap
    n_b_only = size - n_overlap

    registry_a: list[dict] = []
    registry_b: list[dict] = []
    ground_truth: list[dict] = []
    # What to photograph, resolved after the registries are built so that no
    # record ever has to carry the canonical person id.
    face_jobs: list[dict] = []

    def photograph_job(person: Person, record_id: str, registry: str):
        # Registry A is a reception centre with a fixed booth; Registry B is a
        # field enumeration unit working outdoors, so its captures are rougher.
        face_jobs.append({
            "record_id": record_id,
            "person_id": person.person_id,
            "sex": person.sex,
            "birth_year": person.birth_date.year,
            "registry": registry,
            "intensity": 0.75 if registry == "A" else 1.20,
        })

    # --- people present in both registries (the true matches) ------------
    shared_people = [make_person(rng, f"P-{i:04d}") for i in range(n_overlap)]
    for person in shared_people:
        attach_relatives(rng, person)

    for i, person in enumerate(shared_people):
        a_id = f"A-{i + 1:04d}"
        b_id = f"B-{i + 1:04d}"

        # Registry A is the tidier desk; a minority of its records drift too,
        # so the matcher can never assume one side is canonical.
        # Registry B is usually the messier desk, but not always -- a handful of
        # pairs stay clean so the demo shows the easy case alongside the hard one.
        a_messy = rng.random() < 0.25
        b_messy = rng.random() < 0.85
        a_rec, a_notes = render_record(person, "A", a_id, rng, messy=a_messy)
        b_rec, b_notes = render_record(person, "B", b_id, rng, messy=b_messy)

        registry_a.append(a_rec)
        registry_b.append(b_rec)
        photograph_job(person, a_id, "A")
        photograph_job(person, b_id, "B")

        notes = [f"[A] {n}" for n in a_notes] + [f"[B] {n}" for n in b_notes]
        ground_truth.append({
            "pair_id": f"GT-{i + 1:04d}",
            "person_id": person.person_id,
            "a_record_id": a_id,
            "b_record_id": b_id,
            "difficulty": difficulty_of(notes),
            "divergences": notes,
        })

    # --- decoys: people in only one registry ------------------------------
    # A third of them are deliberate near-twins of a shared person: same family
    # name cluster, birth year within two years. These are what a naive matcher
    # gets wrong, and they are the reason the precision number means anything.
    def add_singletons(count, registry, records, start_index):
        for j in range(count):
            if shared_people and rng.random() < 0.33:
                twin_of = rng.choice(shared_people)
                person = make_person(
                    rng,
                    f"P-{registry}{j:04d}",
                    family_cluster=twin_of.family_cluster,
                    birth_year_near=twin_of.birth_date.year,
                )
            else:
                person = make_person(rng, f"P-{registry}{j:04d}")
            attach_relatives(rng, person)
            rid = f"{registry}-{start_index + j:04d}"
            rec, _ = render_record(person, registry, rid, rng,
                                   messy=rng.random() < 0.55)
            records.append(rec)
            photograph_job(person, rid, registry)

    add_singletons(n_a_only, "A", registry_a, n_overlap + 1)
    add_singletons(n_b_only, "B", registry_b, n_overlap + 1)

    rng.shuffle(registry_a)
    rng.shuffle(registry_b)

    return registry_a, registry_b, ground_truth, face_jobs


def write_csv(path: Path, rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def render_faces(face_jobs: list[dict]) -> dict:
    """
    Photograph every record's subject, then embed the photographs.

    Each registry photographs the person independently, so a matched pair is
    two different images of one synthetic face rather than the same PNG twice.
    Comparing a file with itself would score a perfect 1.0 and demonstrate
    nothing; the whole point is that the embedder has to recover identity
    across lighting, framing and focus.

    The embeddings are written out separately from the images because that is
    the architecture the real thing would use. Two organisations comparing
    notes on whether they hold records for the same person can exchange
    embeddings without either of them ever sending a photograph of a displaced
    person over the wire.
    """
    from engine.faces import photograph
    from engine.face_embedding import FaceEmbeddingService, calibrate, save_calibration

    faces_dir = DATA_DIR / "faces"
    faces_dir.mkdir(parents=True, exist_ok=True)

    # Clear faces left by a previous run. Record ids are reused across
    # generations, so a smaller corpus would otherwise leave the surplus
    # photographs of a larger one lying around -- stale images that belong to
    # records that no longer exist, in a directory whose whole purpose is to
    # correspond one-to-one with the registries.
    stale = 0
    for old_face in faces_dir.glob("*.png"):
        old_face.unlink()
        stale += 1

    service = FaceEmbeddingService()
    embeddings: dict[str, list[float]] = {}
    by_registry: dict[str, list] = {"A": [], "B": []}

    for job in face_jobs:
        img = photograph(job["person_id"], job["sex"], job["birth_year"],
                         job["registry"], job["intensity"])
        img.save(faces_dir / f"{job['record_id']}.png", optimize=True)
        vec = service.generate_embedding(img)
        embeddings[job["record_id"]] = [round(float(v), 5) for v in vec]
        by_registry[job["registry"]].append(vec)

    # Calibrate against this corpus: how similar do two unrelated faces look?
    cal = calibrate(by_registry["A"], by_registry["B"], service.backend.name)
    save_calibration(cal)

    (DATA_DIR / "face_embeddings.json").write_text(json.dumps({
        "_meta": {
            "synthetic": True,
            "warning": "Every image is drawn by engine.faces from a seeded "
                       "number. No photograph of a real person is used.",
            "backend": service.backend.name,
            "dimensions": service.backend.dimensions,
        },
        "embeddings": embeddings,
    }))
    return {"count": len(embeddings), "removed_stale": stale, "calibration": cal,
            "backend": service.backend.name,
            "dimensions": service.backend.dimensions}


def main():
    ap = argparse.ArgumentParser(description="Generate synthetic registries.")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--size", type=int, default=80,
                    help="records per registry (50-100 recommended)")
    ap.add_argument("--no-faces", action="store_true",
                    help="skip face rendering and embedding")
    ap.add_argument("--overlap", type=float, default=0.32,
                    help="fraction of each registry that is the same person as "
                         "someone in the other registry")
    args = ap.parse_args()

    a, b, gt, face_jobs = build(args.seed, args.size, args.overlap)
    DATA_DIR.mkdir(exist_ok=True)

    meta = {
        "synthetic": True,
        "warning": "All records are computer-generated. No real person, place, "
                   "conflict or organisation is represented.",
        "seed": args.seed,
        "records_per_registry": args.size,
        "true_pairs": len(gt),
    }

    (DATA_DIR / "registry_a.json").write_text(json.dumps(a, indent=2))
    (DATA_DIR / "registry_b.json").write_text(json.dumps(b, indent=2))
    (DATA_DIR / "ground_truth.json").write_text(
        json.dumps({"_meta": meta, "pairs": gt}, indent=2))
    # CSV is a flat view for spreadsheet users; the nested family list only
    # survives in the JSON.
    def flatten(rows):
        return [{**r, "family_members": "; ".join(
            f"{m['relation']}: {m['name']}" for m in r["family_members"])}
            for r in rows]

    write_csv(DATA_DIR / "registry_a.csv", flatten(a))
    write_csv(DATA_DIR / "registry_b.csv", flatten(b))

    if not args.no_faces:
        print("Rendering synthetic faces and embedding them...")
        faces = render_faces(face_jobs)
        cal = faces["calibration"]
        if faces["removed_stale"]:
            print(f"Faces     : cleared {faces['removed_stale']} stale image(s) "
                  f"from a previous run")
        print(f"Faces     : {faces['count']} drawn, embedded with "
              f"{faces['backend']} ({faces['dimensions']}-d)")
        print(f"Calibrated: unrelated faces sit at "
              f"{cal.background_mean:.3f} +/- {cal.background_sd:.3f} cosine")

    by_difficulty: dict[str, int] = {}
    for pair in gt:
        by_difficulty[pair["difficulty"]] = by_difficulty.get(pair["difficulty"], 0) + 1

    print(f"Registry A : {len(a)} records")
    print(f"Registry B : {len(b)} records")
    print(f"True pairs : {len(gt)} "
          f"({len(gt) / len(a):.0%} of Registry A)")
    print(f"Difficulty : " + ", ".join(
        f"{k} {v}" for k, v in sorted(by_difficulty.items())))
    print(f"Comparisons the matcher must sift: {len(a) * len(b):,}")
    print(f"Written to {DATA_DIR}")


if __name__ == "__main__":
    main()
