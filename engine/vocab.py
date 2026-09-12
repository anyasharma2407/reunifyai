"""
Controlled vocabularies for the Reunification Engine.

EVERYTHING HERE IS SYNTHETIC.

Design notes
------------
* Given names are drawn from *variant clusters*: sets of spellings that a
  real transliteration pipeline would plausibly produce for the same spoken
  name. These are common given names, never tied to any real individual.
* Family names are entirely invented, and are also organised into clusters so
  that surnames get the same transliteration treatment as given names.
* Geography is fully fictional. There is no real country, city, camp, conflict
  or organisation referenced anywhere in this project. The fictional setting
  (the "Karavian corridor") exists precisely so that nothing in the demo can be
  mistaken for a real displacement situation.
* PLACE_CLUSTERS is the *ground truth* of which place spellings are the same
  place. The matcher is deliberately given only PUBLIC_PLACE_ALIASES, a
  partial subset, because in reality a gazetteer never covers every alias --
  the fuzzy scorer has to carry the remainder.
"""

# --------------------------------------------------------------------------
# Given names: clusters of transliteration variants
# --------------------------------------------------------------------------

GIVEN_NAME_CLUSTERS = [
    {"id": "gm01", "gender": "M", "variants": ["Mohammed", "Muhammad", "Mohamed", "Mohamad", "Muhammed"]},
    {"id": "gm02", "gender": "M", "variants": ["Yusuf", "Yousef", "Youssef", "Yusif"]},
    {"id": "gm03", "gender": "M", "variants": ["Ibrahim", "Ebrahim", "Ibraheem", "Ibrahem"]},
    {"id": "gm04", "gender": "M", "variants": ["Khalid", "Khaled", "Kalid", "Khaled"]},
    {"id": "gm05", "gender": "M", "variants": ["Omar", "Umar", "Omer", "Oumar"]},
    {"id": "gm06", "gender": "M", "variants": ["Hassan", "Hasan", "Hassen", "Hasaan"]},
    {"id": "gm07", "gender": "M", "variants": ["Karim", "Kareem", "Karym", "Carim"]},
    {"id": "gm08", "gender": "M", "variants": ["Samir", "Sameer", "Samyr"]},
    {"id": "gm09", "gender": "M", "variants": ["Tariq", "Tarek", "Tarik", "Tareq"]},
    {"id": "gm10", "gender": "M", "variants": ["Rashid", "Rasheed", "Rachid", "Rashed"]},
    {"id": "gm11", "gender": "M", "variants": ["Aleksandr", "Alexander", "Alexandr", "Aleksander"]},
    {"id": "gm12", "gender": "M", "variants": ["Dmitri", "Dmitry", "Dmitrii", "Dimitri"]},
    {"id": "gm13", "gender": "M", "variants": ["Sergei", "Sergey", "Serguei", "Sergej"]},
    {"id": "gm14", "gender": "M", "variants": ["Mikhail", "Mihail", "Michail", "Mikhael"]},
    {"id": "gm15", "gender": "M", "variants": ["Andrei", "Andrey", "Andrej", "Andrii"]},
    {"id": "gm16", "gender": "M", "variants": ["Elias", "Ilyas", "Ilias", "Elyas"]},
    {"id": "gm17", "gender": "M", "variants": ["Nadeem", "Nadim", "Nadym"]},
    {"id": "gm18", "gender": "M", "variants": ["Bashir", "Basheer", "Bachir", "Bashyr"]},

    {"id": "gf01", "gender": "F", "variants": ["Aisha", "Ayesha", "Aysha", "Aicha"]},
    {"id": "gf02", "gender": "F", "variants": ["Fatima", "Fatimah", "Fatma", "Fathima"]},
    {"id": "gf03", "gender": "F", "variants": ["Layla", "Leila", "Laila", "Leyla"]},
    {"id": "gf04", "gender": "F", "variants": ["Zainab", "Zaynab", "Zeinab", "Zeynep"]},
    {"id": "gf05", "gender": "F", "variants": ["Amina", "Ameena", "Amna", "Aminah"]},
    {"id": "gf06", "gender": "F", "variants": ["Noor", "Nour", "Nur", "Noura"]},
    {"id": "gf07", "gender": "F", "variants": ["Yasmin", "Yasmeen", "Jasmin", "Yassmine"]},
    {"id": "gf08", "gender": "F", "variants": ["Mariam", "Maryam", "Mariyam", "Marium"]},
    {"id": "gf09", "gender": "F", "variants": ["Rania", "Raniya", "Ranya"]},
    {"id": "gf10", "gender": "F", "variants": ["Salma", "Selma", "Salmah"]},
    {"id": "gf11", "gender": "F", "variants": ["Yelena", "Elena", "Jelena", "Alena"]},
    {"id": "gf12", "gender": "F", "variants": ["Natalya", "Natalia", "Nataliya", "Natalja"]},
    {"id": "gf13", "gender": "F", "variants": ["Ekaterina", "Yekaterina", "Katerina", "Katarina"]},
    {"id": "gf14", "gender": "F", "variants": ["Ksenia", "Kseniya", "Xenia", "Ksenija"]},
    {"id": "gf15", "gender": "F", "variants": ["Sofiya", "Sofia", "Sophia", "Sofija"]},
    {"id": "gf16", "gender": "F", "variants": ["Hadeel", "Hadil", "Hadeal"]},
    {"id": "gf17", "gender": "F", "variants": ["Wafaa", "Wafa", "Wafah"]},
    {"id": "gf18", "gender": "F", "variants": ["Dalia", "Dahlia", "Daliya"]},
]

# --------------------------------------------------------------------------
# Family names: invented, also clustered by transliteration variant
# --------------------------------------------------------------------------

FAMILY_NAME_CLUSTERS = [
    {"id": "fn01", "variants": ["Darwazi", "Derwazi", "Darwazy"]},
    {"id": "fn02", "variants": ["Malkhoun", "Malkhun", "Malkhoon"]},
    {"id": "fn03", "variants": ["Zabrani", "Zabrany", "Zebrani"]},
    {"id": "fn04", "variants": ["Ferrouk", "Farrouk", "Ferruk"]},
    {"id": "fn05", "variants": ["Halvani", "Khalvani", "Halvany"]},
    {"id": "fn06", "variants": ["Ostrovets", "Ostrovetz", "Ostrovec"]},
    {"id": "fn07", "variants": ["Vashenko", "Vaschenko", "Vashenka"]},
    {"id": "fn08", "variants": ["Drazhic", "Drazhich", "Drazic"]},
    {"id": "fn09", "variants": ["Merzouki", "Merzuki", "Marzouki"]},
    {"id": "fn10", "variants": ["Sanbari", "Sanbary", "Senbari"]},
    {"id": "fn11", "variants": ["Kadiroff", "Kadirov", "Qadirov"]},
    {"id": "fn12", "variants": ["Tellawi", "Telawi", "Tellawy"]},
    {"id": "fn13", "variants": ["Nuraldin", "Nooraldin", "Nuraldeen"]},
    {"id": "fn14", "variants": ["Behzadi", "Behzady", "Bahzadi"]},
    {"id": "fn15", "variants": ["Orvanic", "Orvanich", "Orvanik"]},
    {"id": "fn16", "variants": ["Sadraoui", "Sadraui", "Sadrawi"]},
    {"id": "fn17", "variants": ["Lemzouri", "Lemzuri", "Lamzouri"]},
    {"id": "fn18", "variants": ["Pashkov", "Pachkov", "Paskov"]},
    {"id": "fn19", "variants": ["Ghandouri", "Ghanduri", "Gandouri"]},
    {"id": "fn20", "variants": ["Vrelic", "Vrelich", "Vrelik"]},
    {"id": "fn21", "variants": ["Amroush", "Amrouche", "Amrush"]},
    {"id": "fn22", "variants": ["Kerzani", "Kerzany", "Karzani"]},
    {"id": "fn23", "variants": ["Dobrenko", "Dobrenka", "Dobrenkov"]},
    {"id": "fn24", "variants": ["Shalabi", "Chalabi", "Shalaby"]},
    {"id": "fn25", "variants": ["Miroshko", "Miroshkov", "Mirochko"]},
    {"id": "fn26", "variants": ["Hazouri", "Hazuri", "Hazoury"]},
    {"id": "fn27", "variants": ["Yankovic", "Jankovic", "Yankovich"]},
    {"id": "fn28", "variants": ["Belkacem", "Belqacem", "Belkassem"]},
    {"id": "fn29", "variants": ["Tarnov", "Tarnoff", "Ternov"]},
    {"id": "fn30", "variants": ["Ouazzani", "Wazzani", "Ouazani"]},
]

# --------------------------------------------------------------------------
# Fictional geography: the "Karavian corridor"
# --------------------------------------------------------------------------

PLACE_CLUSTERS = [
    {"id": "pl01", "kind": "city",  "variants": ["Qasr Nadeem", "Kasr Nadim", "Qasr Nadim"]},
    {"id": "pl02", "kind": "city",  "variants": ["Bel Haran", "Belharan", "Bal Haran"]},
    {"id": "pl03", "kind": "city",  "variants": ["Tal Mireh", "Tell Mira", "Tal Mira"]},
    {"id": "pl04", "kind": "city",  "variants": ["Zahiryah", "Zahiriya", "Zahirya"]},
    {"id": "pl05", "kind": "city",  "variants": ["Ardavon", "Ardavan", "Ardavon City"]},
    {"id": "pl06", "kind": "city",  "variants": ["Sevrin", "Severin", "Sevrine"]},
    {"id": "pl07", "kind": "city",  "variants": ["Novi Krag", "Novy Krag", "Novikrag"]},
    {"id": "pl08", "kind": "city",  "variants": ["Maskar Dun", "Meskar Doun", "Maskardun"]},
    {"id": "pl09", "kind": "city",  "variants": ["Hulwan Reach", "Hulwan", "Holwan Reach"]},
    {"id": "pl10", "kind": "city",  "variants": ["Petrovka", "Petrowka", "Petrovke"]},
    {"id": "pl11", "kind": "town",  "variants": ["Ilvar", "Ilvarr", "Ilvara"]},
    {"id": "pl12", "kind": "town",  "variants": ["Ghoraya", "Ghuraya", "Goraya"]},
    {"id": "pl13", "kind": "camp",  "variants": ["Camp Thirteen", "Camp 13", "Site 13"]},
    {"id": "pl14", "kind": "camp",  "variants": ["Ilvar Transit Site", "Ilvar Transit Centre", "Ilvar Transit"]},
    {"id": "pl15", "kind": "camp",  "variants": ["North Mereth Camp", "Mereth North Camp", "N. Mereth Camp"]},
    {"id": "pl16", "kind": "camp",  "variants": ["Vantoral Reception Point", "Vantoral Reception", "Vantoral RP"]},
]

# The gazetteer the *matcher* is allowed to see. Deliberately incomplete:
# roughly two-thirds of clusters, and not always every variant within one.
PUBLIC_PLACE_ALIASES = {
    "pl01": ["Qasr Nadeem", "Kasr Nadim"],
    "pl02": ["Bel Haran", "Belharan"],
    "pl03": ["Tal Mireh", "Tell Mira"],
    "pl04": ["Zahiryah", "Zahiriya"],
    "pl06": ["Sevrin", "Severin"],
    "pl07": ["Novi Krag", "Novy Krag"],
    "pl09": ["Hulwan Reach", "Hulwan"],
    "pl13": ["Camp Thirteen", "Camp 13"],
    "pl14": ["Ilvar Transit Site", "Ilvar Transit Centre"],
    "pl16": ["Vantoral Reception Point", "Vantoral Reception"],
}

# --------------------------------------------------------------------------
# Fictional recording organisations
# --------------------------------------------------------------------------

REGISTRY_A_SOURCES = [
    "Northwatch Relief Trust",
    "Blue Meridian Aid",
    "Karavian Civic Tracing Desk",
    "Highland Hope Collective",
]

REGISTRY_B_SOURCES = [
    "Vantoral Reception Registry",
    "Ilvar Field Enumeration Unit",
    "Mereth Shelter Coordination",
    "Sevrin Municipal Records Annex",
]


def variant_lookup(clusters):
    """Map every spelling variant -> its cluster id."""
    out = {}
    for c in clusters:
        for v in c["variants"]:
            out[v.casefold()] = c["id"]
    return out


PLACE_VARIANT_TO_CLUSTER = variant_lookup(PLACE_CLUSTERS)


# --------------------------------------------------------------------------
# Fictional nationalities
# --------------------------------------------------------------------------
#
# Nationality is the weakest signal in the scoring model and the most dangerous
# one to lean on, so it is modelled the same way everything else is: as a label
# two desks may spell differently, not as a fact about a person. Every one of
# these is invented, and they describe the fictional Karavian corridor only.

NATIONALITY_CLUSTERS = [
    {"id": "nt01", "variants": ["Karavian", "Karavien", "Caravian"]},
    {"id": "nt02", "variants": ["Ardavoni", "Ardavonian", "Ardavony"]},
    {"id": "nt03", "variants": ["Meretheen", "Merethene", "Merethin"]},
    {"id": "nt04", "variants": ["Sevrine", "Sevrinian", "Sevrin"]},
    {"id": "nt05", "variants": ["Hulwani", "Hulwanese", "Holwani"]},
    {"id": "nt06", "variants": ["Vantoral", "Vantoralese", "Vantorali"]},
    {"id": "nt07", "variants": ["Stateless", "Undetermined", "Not established"]},
]

# The subset a matcher is allowed to treat as known equivalents. As with
# places, it is deliberately incomplete so fuzzy matching still has work to do.
PUBLIC_NATIONALITY_ALIASES = {
    "nt01": ["Karavian", "Karavien"],
    "nt02": ["Ardavoni", "Ardavonian"],
    "nt03": ["Meretheen", "Merethene"],
    "nt05": ["Hulwani", "Hulwanese"],
}

# --------------------------------------------------------------------------
# Family relationships
# --------------------------------------------------------------------------
#
# Relationship vocabulary is not standardised across humanitarian registries.
# One desk records "mother", another records "parent", a third records
# "guardian" for the same person -- so the matcher has to treat these as a
# graded agreement rather than a string comparison. RELATIONSHIP_EQUIVALENCE
# groups the terms that should be treated as the same claim.

RELATIONSHIP_KINDS = [
    "mother", "father", "parent", "guardian",
    "son", "daughter", "child",
    "brother", "sister", "sibling",
    "spouse", "wife", "husband",
    "aunt", "uncle", "cousin", "grandparent",
]

RELATIONSHIP_EQUIVALENCE = {
    "mother": "parent", "father": "parent", "parent": "parent", "guardian": "parent",
    "son": "child", "daughter": "child", "child": "child",
    "brother": "sibling", "sister": "sibling", "sibling": "sibling",
    "spouse": "spouse", "wife": "spouse", "husband": "spouse",
    "aunt": "extended", "uncle": "extended", "cousin": "extended",
    "grandparent": "extended",
}

# How a desk might re-record a relationship it was told: usually correctly,
# sometimes generalised, occasionally from the other side of the relationship.
RELATIONSHIP_DRIFT = {
    "mother": ["mother", "mother", "parent", "guardian"],
    "father": ["father", "father", "parent", "guardian"],
    "son": ["son", "son", "child"],
    "daughter": ["daughter", "daughter", "child"],
    "brother": ["brother", "brother", "sibling"],
    "sister": ["sister", "sister", "sibling"],
    "wife": ["wife", "spouse"],
    "husband": ["husband", "spouse"],
    "aunt": ["aunt", "aunt", "cousin"],
    "uncle": ["uncle", "uncle", "cousin"],
    "cousin": ["cousin"],
    "grandparent": ["grandparent", "guardian"],
}

NATIONALITY_VARIANT_TO_CLUSTER = variant_lookup(NATIONALITY_CLUSTERS)
