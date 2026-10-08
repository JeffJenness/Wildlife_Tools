# -*- coding: utf-8 -*-
# Wildlife and Forestry Tools for ArcGIS Pro
# Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version, together with the additional terms
# in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
# Memorial Scholarship Fund notice, marking of modified versions, and
# use of names).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""The Animal Unit Months (Grazing) class (Jeff, 2026-10-07).

Base unit: animal unit months (AUM), the forage one animal unit needs for one
month. The animal unit is a 1,000-pound cow with a calf up to 6 months old.

Two kinds of units convert to it:
- grazing by other kinds and classes of animals: one month of a mature mule
  deer is 0.20 AUM (its animal unit equivalent, AUE);
- forage: pounds or kilograms of forage, at a stated number of pounds per AUM.
  Agencies and regions differ here, so each convention is its own unit.

Equivalents from five sources (ANIMALS, ARIZONA): NDSU Extension R1810
Table 1, NRCS National Range and Pasture Handbook Table H-12, Vallentine
(1990) via Rangelands Gateway, NMSU Guide B-829 Table 3 after Holechek
(1988), and Ruyle and Ogden (1993) Table 1. Forage amounts and the federal
grazing-fee units from the research of 2026-10-07 (citation keys defined in
catalog_sources.CITATIONS). Further sourced conventions can be added through
aum_extra.json beside this file.
"""
LB = 0.45359237

NDSU = "ndsu_r1810"
NRCS = "nrcs_nrph2022"
SRM = "srm_ram2017"
FEES = ["cfr43_4130", "cfr36_222"]

# pounds of forage per AUM: (lb, kind of forage, sources, note). Jeff,
# 2026-10-07: the common differences among AUM definitions are the forage per
# AUM ("Some places (like AZ) use 800 lbs, others use 750 lbs").
FORAGE = [
    (913.0, "air-dried forage", [NDSU],
     "NDSU Extension R1810 uses 913 pounds of air-dried forage (about 87 percent dry matter) per AUM, about 30 "
     "pounds a day."),
    (800.0, "air-dried forage", ["ruyle_ogden1993"],
     "Ruyle and Ogden (1993), University of Arizona Cooperative Extension, count 31 days at 26 pounds a day, "
     "about 800 pounds of air-dried forage per AUM, and add that 600 to 1,000 pounds per AUM are common values."),
    (790.0, "oven-dry forage", [NRCS],
     "The NRCS National Range and Pasture Handbook counts 26 pounds of oven-dry forage a day, 790 pounds a month, "
     "for a 1,000-pound cow with her calf."),
    (780.0, "oven-dry forage", [SRM],
     "The Society for Range Management's glossary counts 26 pounds of oven-dry forage a day for a standardized "
     "month of 30 animal unit days, 780 pounds."),
    (750.0, "air-dried forage", ["uwyo_b1320"],
     "University of Wyoming Extension B-1320 (2018) uses 750 pounds of air-dried forage per AUM: a 1,000-pound "
     "cow eating 2.5 percent of her weight a day for 30 days."),
]

# Animal unit equivalents (AUE), the AUM in one month of grazing by one
# animal: (animal, class within the source or None, AUE, source tag). Names
# are animal-first, "mule deer months (mature; NRCS)", so the alphabetical
# unit lists group each animal across sources. Every value was read in its
# document on 2026-10-07. Jeff, 2026-10-07: the mature cattle bull is 1.40
# (R1810 Table 1; its Example 1 used the bison bull's 1.50), and "Add the
# other animals from those documents." Left out: NRCS's swine and poultry
# (animal units from EPA CAFO definitions, no forage data), a forest plan
# glossary whose forest could not be identified, and the SRM article's
# "about" values.
ANIMALS = [
    # NDSU Extension R1810, Table 1
    ("cattle", "cow, 1,000 lb, dry", 0.92, "NDSU"),
    ("cattle", "cow with calf, 1,000 lb", 1.00, "NDSU"),
    ("cattle", "cow with calf, 1,100 lb", 1.07, "NDSU"),
    ("cattle", "cow with calf, 1,200 lb", 1.15, "NDSU"),
    ("cattle", "cow with calf, 1,300 lb", 1.22, "NDSU"),
    ("cattle", "cow with calf, 1,400 lb", 1.29, "NDSU"),
    ("cattle", "cow with calf, 1,500 lb", 1.35, "NDSU"),
    ("cattle", "mature bull", 1.40, "NDSU"),
    ("cattle", "weaned calf to yearling", 0.60, "NDSU"),
    ("cattle", "yearling, 600-800 lb", 0.70, "NDSU"),
    ("cattle", "two-year-old, 800-1,000 lb", 0.90, "NDSU"),
    ("bison", "mature cow", 1.00, "NDSU"),
    ("bison", "mature bull", 1.50, "NDSU"),
    ("horse", "mature", 1.25, "NDSU"),
    ("sheep", "dry", 0.15, "NDSU"),
    ("sheep", "mature, with lamb", 0.20, "NDSU"),
    ("sheep", "ram", 0.25, "NDSU"),
    ("goat", "mature", 0.15, "NDSU"),
    ("white-tailed deer", "mature", 0.15, "NDSU"),
    ("mule deer", "mature", 0.20, "NDSU"),
    ("elk", "mature", 0.60, "NDSU"),
    ("pronghorn antelope", "mature", 0.20, "NDSU"),
    ("bighorn sheep", "mature", 0.20, "NDSU"),
    ("white-tailed jackrabbit", None, 0.02, "NDSU"),
    ("prairie dog", None, 0.004, "NDSU"),
    # NRCS National Range and Pasture Handbook (2022), Table H-12
    ("cattle", "cow, dry", 0.92, "NRCS"),
    ("cattle", "cow with calf", 1.00, "NRCS"),
    ("cattle", "mature bull", 1.35, "NRCS"),
    ("cattle", "1 year old", 0.60, "NRCS"),
    ("cattle", "2 years old", 0.80, "NRCS"),
    ("horse", "mature", 1.25, "NRCS"),
    ("sheep", "mature", 0.20, "NRCS"),
    ("sheep", "lamb, 1 year old", 0.15, "NRCS"),
    ("goat", "mature", 0.15, "NRCS"),
    ("goat", "kid, 1 year old", 0.10, "NRCS"),
    ("white-tailed deer", "mature", 0.15, "NRCS"),
    ("mule deer", "mature", 0.20, "NRCS"),
    ("elk", "mature", 0.60, "NRCS"),
    ("pronghorn antelope", "mature", 0.20, "NRCS"),
    ("bison", "mature", 1.00, "NRCS"),
    ("bighorn sheep", "mature", 0.20, "NRCS"),
    # Vallentine (1990), page 279, as tabulated by Rangelands Gateway
    ("cattle", "mature bull, over 24 months", 1.50, "Vallentine"),
    ("cattle", "young bull, 18-24 months", 1.15, "Vallentine"),
    ("cattle", "cow and calf pair", 1.35, "Vallentine"),
    ("cattle", "mature cow, non-lactating", 1.00, "Vallentine"),
    ("cattle", "pregnant heifer, over 18 months", 1.00, "Vallentine"),
    ("cattle", "yearling, 18-24 months", 0.90, "Vallentine"),
    ("cattle", "yearling, 15-18 months", 0.80, "Vallentine"),
    ("cattle", "yearling, 12-15 months", 0.70, "Vallentine"),
    ("cattle", "calf, weaning to 12 months", 0.60, "Vallentine"),
    ("cattle", "calf at weaning, 8 months", 0.50, "Vallentine"),
    ("sheep", "mature, non-lactating", 0.20, "Vallentine"),
    ("sheep", "ewe and lamb pair", 0.30, "Vallentine"),
    ("horse", "saddle horse, mature", 1.25, "Vallentine"),
    ("elk", "mature", 0.65, "Vallentine"),
    ("mule deer", "mature", 0.23, "Vallentine"),
    ("white-tailed deer", "mature", 0.17, "Vallentine"),
    ("pronghorn antelope", "mature", 0.17, "Vallentine"),
    # NMSU Extension Guide B-829 (2023), Table 3, after Holechek (1988)
    ("cattle", "mature, 1,000 lb", 1.00, "Holechek"),
    ("cattle", "yearling, 750 lb", 0.75, "Holechek"),
    ("sheep", "150 lb", 0.15, "Holechek"),
    ("goat", "100 lb", 0.10, "Holechek"),
    ("horse", "1,200 lb", 1.80, "Holechek"),
    ("donkey", "700 lb", 1.05, "Holechek"),
    ("bison", "1,800 lb", 1.80, "Holechek"),
    ("elk", "700 lb", 0.70, "Holechek"),
    ("moose", "1,200 lb", 1.20, "Holechek"),
    ("bighorn sheep", "180 lb", 0.18, "Holechek"),
    ("mule deer", "150 lb", 0.15, "Holechek"),
    ("white-tailed deer", "100 lb", 0.10, "Holechek"),
    ("pronghorn antelope", "120 lb", 0.12, "Holechek"),
    ("caribou", "400 lb", 0.40, "Holechek"),
]

# Ruyle and Ogden (1993), Table 1: animals per animal unit by the ratio of
# metabolic weights (kg ** 0.75); the AUE is the reciprocal of the printed
# ratio. (animal, class, animals per AU)
ARIZONA = [
    ("cattle", "cow, 1,000 lb", 1.00),
    ("horse", "1,100 lb", 0.93),
    ("elk", "600 lb", 1.46),
    ("mule deer", "125 lb", 4.67),
    ("sheep", "120 lb", 4.90),
    ("pronghorn antelope", "90 lb", 6.13),
]

# one animal, and several, for the definitions
NOUN = {"cattle": "head of cattle", "white-tailed jackrabbit": "white-tailed jackrabbit"}
PLURAL = {"cattle": "cows", "horse": "horses"}

AUE_SOURCES = {
    "NDSU": ([NDSU], "its animal unit equivalent in NDSU Extension R1810, Table 1, adapted from the NRCS National "
                     "Range and Pasture Handbook and Montana State University"),
    "NRCS": ([NRCS], "its animal unit equivalent in the NRCS National Range and Pasture Handbook, Table H-12 "
                     "(2022), a guide that can be adjusted by actual weights"),
    "Vallentine": (["vallentine1990"], "its animal unit equivalent in Vallentine (1990, page 279), as tabulated by "
                                       "Rangelands Gateway, which bases the equivalents primarily on metabolic "
                                       "body weight"),
    "Holechek": (["nmsu_b829"], "its animal unit equivalent in New Mexico State University Extension Guide B-829, "
                                "Table 3, after Holechek (1988): the animal's daily dry-matter intake relative to a "
                                "1,000-pound cow eating 20 pounds a day, about 2 percent of body weight (3 percent "
                                "for horses and donkeys)"),
}

SAME_SOURCE = "Equivalents differ among sources, so convert between units of the same source. "


def animal_units():
    """The AUE units of every source, Arizona's from its ratios."""
    rows = [(a, c, aue, tag, None) for a, c, aue, tag in ANIMALS]
    rows += [(a, c, 1.0 / r, "Arizona", r) for a, c, r in ARIZONA]
    out = []
    for animal, cls, aue, tag, ratio in rows:
        inner = "%s; %s" % (cls, tag) if cls else tag
        name = "%s months (%s)" % (animal, inner)
        singular = "%s month (%s)" % (animal, inner)
        noun = NOUN.get(animal, animal)
        what = "%s (%s)" % (noun, cls) if cls else noun
        if ratio is None:
            sources, note = AUE_SOURCES[tag]
            if tag == "Vallentine":
                sources = sources + (["rg_au"] if animal == "cattle" else ["rg_aue"])
            text = "A month of grazing by one %s equals %g AUM, %s. " % (what, aue, note)
        else:
            sources = ["ruyle_ogden1993"]
            if ratio == 1.0:
                text = ("A month of grazing by one %s equals 1 AUM: Ruyle and Ogden (1993), Table 1, take the "
                        "1,000-pound cow as the standard animal unit. " % what)
            else:
                text = ("A month of grazing by one %s equals %.4f AUM: Ruyle and Ogden (1993), Table 1, count %.2f "
                        "%s of %s per animal unit by the ratio of metabolic weights (weight in kilograms to the "
                        "0.75 power), and the equivalent is its reciprocal. "
                        % (what, aue, ratio, PLURAL.get(animal, animal), cls.replace(" lb", " pounds")))
        out.append((name, singular, aue, text + SAME_SOURCE + AU_DEF, sources))
    names = [o[0] for o in out]
    if len(set(n.lower() for n in names)) != len(names):
        raise SystemExit("duplicate animal unit names")
    return out


AU_DEF = ("The animal unit is a 1,000-pound cow with a calf 6 months old or younger by her side; an animal "
          "unit month (AUM) is the forage it needs for one month.")


def _u(name, singular, factor, definition, sources, aliases=()):
    return {"name": name, "singular": singular, "class": CLASS, "factor": factor,
            "definition": definition, "sources": list(sources), "aliases": list(aliases)}


CLASS = "Animal Unit Months (Grazing)"
BASE = "animal unit months"


def aum_units(extra_equivalents=(), extra_forage=(), days_per_month=365.0 / 12.0, days_source=None):
    """The class's units. extra_equivalents: [(name, singular, aue, definition,
    [sources], [aliases])]; extra_forage: [(lb per AUM, kind of forage,
    [sources], note)]."""
    U = [
        _u(BASE, "animal unit month", 1.0,
           "An animal unit month (AUM) is the amount of forage one animal unit eats in one month, the usual "
           "measure of grazing use and carrying capacity. " + AU_DEF + " The Bureau of Land Management's grazing "
           "regulations define it as the forage needed to sustain one cow or its equivalent for 1 month.",
           [NDSU, NRCS, "cfr43_4100"], ["AUM", "AUMs"]),
        _u("animal unit days", "animal unit day", 1.0 / days_per_month,
           "An animal unit day (AUD) is the forage one animal unit eats in one day. The NRCS handbook's animal unit "
           "year is both 12 animal unit months and 365 animal unit days (9,490 pounds of oven-dry forage at 26 "
           "pounds a day), so a month here is 365/12 = %.2f days. The Society for Range Management's glossary "
           "counts 30 animal unit days in an AUM instead. " % days_per_month + AU_DEF,
           [days_source or NRCS, SRM], ["AUD", "AUDs"]),
        _u("animal unit years", "animal unit year", 12.0,
           "An animal unit year (AUY) is the forage one animal unit eats in a year, 12 animal unit months. "
           + AU_DEF, [NRCS], ["AUY"]),
    ]
    for name, singular, aue, definition, sources in animal_units():
        U.append(_u(name, singular, aue, definition, sources))
    # federal grazing fees count head, not forage (43 CFR 4130.8-1(c),
    # 36 CFR 222.50(c)); the BLM and the Forest Service treat the fee AUM and
    # the head month as the same measure
    U.append(_u("head months (federal grazing fees)", "head month (federal grazing fees)", 1.0,
                "A head month, the unit of federal grazing fees: one month's grazing by one cow, bull, steer, "
                "heifer, horse, burro or mule over 6 months old or weaned (Bureau of Land Management, 43 CFR "
                "4130.8-1), or by one animal other than a sheep or goat (Forest Service, 36 CFR 222.50). The two "
                "agencies treat a head month and an AUM as the same measure for fees. It counts head, not forage: "
                "a bull or a horse counts as 1 here, where the NDSU equivalents count 1.40 and 1.25.",
                FEES, ["HM", "fee AUM", "head month"]))
    U.append(_u("sheep or goat months (federal grazing fees)", "sheep or goat month (federal grazing fees)", 0.2,
                "One month's grazing by one sheep or goat, for federal grazing fees: five sheep or five goats count "
                "as one head month or AUM (43 CFR 4130.8-1; 36 CFR 222.50). The NDSU equivalents count a mature "
                "goat as 0.15 and a mature sheep with lamb as 0.20.",
                FEES, ["sheep fee AUM", "goat fee AUM"]))
    for name, singular, aue, definition, sources, aliases in extra_equivalents:
        U.append(_u(name, singular, aue, definition, sources, aliases))
    # forage: pounds and kilograms per AUM, one pair of units per convention
    for lb, what, sources, note in FORAGE + list(extra_forage):
        tag = "%g lb/AUM" % lb
        U.append(_u("pounds of %s (%s)" % (what, tag), "pound of %s (%s)" % (what, tag), 1.0 / lb,
                    "Pounds of %s, counted in animal unit months at %g pounds per AUM. %s Agencies and regions "
                    "use different amounts; choose the one your agency uses." % (what, lb, note), sources,
                    ["lb forage %g" % lb]))
        U.append(_u("kilograms of %s (%s)" % (what, tag), "kilogram of %s (%s)" % (what, tag), 1.0 / (lb * LB),
                    "Kilograms of %s, counted in animal unit months at %g pounds (%.1f kilograms) per AUM. %s"
                    % (what, lb, lb * LB, note), sources, ["kg forage %g" % lb]))
    return U
