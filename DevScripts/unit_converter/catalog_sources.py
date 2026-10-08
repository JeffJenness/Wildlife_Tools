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
"""Sources for the Unit Converter catalog (Jeff, 2026-10-07): every unit
points to whoever maintains its current standard, or to a source that
confirms its value, and the help page lists them all.

- SI units and their decimal multiples: the BIPM's SI Brochure.
- Units whose value matches NIST SP 811's tables: NIST SP 811
  (nist_matches.json, from the comparison with NIST's tables).
- Units of coordinate systems: the EPSG registry.
- International electrical units: the CRC Handbook, 62nd edition.
- Animal unit months: NDSU Extension R1810, the NRCS handbook, the Society
  for Range Management, Arizona and Wyoming Extension, and the federal
  grazing-fee regulations (catalog_aum.py).
- Topic citations (survey foot, nautical mile, astronomical units, the smoot,
  the vara, chains and townships, board feet and cunits...):
  unit_citations.json, written by the research of 2026-10-07. A unit with a
  topic citation or a NIST match gets no guessed source.
- Every other unit of the 2001 database: the original Unit Converter, whose own
  sources the help page lists.
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

CITATIONS = {
    "jenness_uc": {
        "short": "the original Unit Converter (Jenness 2001)",
        "full": ("Jenness, J. 2001. Unit Converter, version 1.5 (software and database of units). Jenness "
                 "Enterprises, Flagstaff, Arizona. Its definitions draw chiefly on the CRC Handbook of "
                 "Chemistry and Physics (62nd ed.), NIST Special Publication 811 (1995) and the Oxford "
                 "English Dictionary (2nd ed. and OED Online)."),
        "url": "",
    },
    "bipm_si": {
        "short": "BIPM, The International System of Units (SI), 9th ed. (2019)",
        "full": ("Bureau International des Poids et Mesures. 2019. The International System of Units (SI). "
                 "9th edition (SI Brochure). BIPM, Sevres, France."),
        "url": "https://doi.org/10.59161/AUEZ1291",
    },
    "nist_sp811": {
        "short": "NIST SP 811 (2008), Appendix B",
        "full": ("Thompson, A., and B. N. Taylor. 2008. Guide for the Use of the International System of Units "
                 "(SI). NIST Special Publication 811, 2008 edition, Appendix B, Conversion Factors. National "
                 "Institute of Standards and Technology, Gaithersburg, Maryland."),
        "url": "https://www.nist.gov/pml/special-publication-811",
    },
    "nist_hb44": {
        "short": "NIST Handbook 44 (2026), Appendix C",
        "full": ("National Institute of Standards and Technology. 2026. NIST Handbook 44: Specifications, "
                 "Tolerances, and Other Technical Requirements for Weighing and Measuring Devices, 2026 "
                 "edition. Appendix C, General Tables of Units of Measurement. NIST, Gaithersburg, Maryland."),
        "url": "https://www.nist.gov/document/2026-nist-handbook-44-appendix-c",
    },
    "epsg": {
        "short": "the EPSG Geodetic Parameter Registry",
        "full": ("International Association of Oil and Gas Producers (IOGP). EPSG Geodetic Parameter Dataset "
                 "and Registry, units of measure, as installed with ArcGIS Pro in its PROJ database."),
        "url": "https://epsg.org",
    },
    "crc62": {
        "short": "CRC Handbook of Chemistry and Physics, 62nd ed.",
        "full": ("Weast, R. C., and M. J. Astle, editors. 1982. CRC Handbook of Chemistry and Physics. 62nd "
                 "edition. CRC Press, Boca Raton, Florida."),
        "url": "",
    },
    "ndsu_r1810": {
        "short": "NDSU Extension R1810 (Meehan et al. 2018), Table 1",
        "full": ("Meehan, M., K. K. Sedivec, J. Printz, and F. Brummer. 2018. Determining Carrying Capacity and "
                 "Stocking Rates for Range and Pasture in North Dakota. R1810 (revised November 2018). North "
                 "Dakota State University Extension, Fargo. Table 1, Animal unit equivalents (AUE) guide, adapted "
                 "from the NRCS National Range and Pasture Handbook (1997) and Montana State University (1993)."),
        "url": "",
    },
    # animal unit months: the research of 2026-10-07 (every URL checked)
    "nrcs_nrph2022": {
        "short": "NRCS National Range and Pasture Handbook (2022)",
        "full": ("Natural Resources Conservation Service. 2022. National Range and Pasture Handbook. Title 190, "
                 "Part 645 (June 2022): Subpart H, Livestock Nutrition, Husbandry, and Behavior, and Subpart N, "
                 "Glossary of Terms. U.S. Department of Agriculture, Washington, D.C."),
        "url": ("https://rangelandsgateway.org/sites/default/files/2024-02/"
                "National%20Range%20and%20Pasture%20Handbook%20%28Part%20645%29.pdf"),
    },
    "srm_ram2017": {
        "short": "SRM Rangeland Assessment and Monitoring Committee (2017)",
        "full": ("Society for Range Management Rangeland Assessment and Monitoring Committee. 2017. Does size "
                 "matter? Animal units and animal unit months. Rangelands 39(1):17-19. Quotes the definitions of "
                 "the Society's Glossary of Terms Used in Range Management, 4th edition (1998)."),
        "url": "https://doi.org/10.1016/j.rala.2016.12.002",
    },
    "ruyle_ogden1993": {
        "short": "Ruyle and Ogden (1993), What is an A.U.M.?",
        "full": ("Ruyle, G., and P. Ogden. 1993. What is an A.U.M.? In R. Gum, G. Ruyle, and R. Rice, editors. "
                 "Arizona Ranchers' Management Guide, Rangeland Management section. Arizona Cooperative "
                 "Extension, University of Arizona, Tucson."),
        "url": ("https://www.extension.arizona.edu/sites/default/files/2025-03/"
                "01_Rangeland_Management-Full-Section.pdf"),
    },
    "uwyo_b1320": {
        "short": "Stam, Scasta and Thacker (2018), University of Wyoming Extension B-1320",
        "full": ("Stam, B., J. D. Scasta, and E. Thacker. 2018. Animal Unit Month (AUM) Concepts and Applications "
                 "for Grazing Rangelands. B-1320. University of Wyoming Extension, Laramie."),
        "url": "https://wyoextension.org/publications/html/B1320/",
    },
    "cfr43_4100": {
        "short": "43 CFR 4100.0-5 (BLM grazing definitions)",
        "full": ("Code of Federal Regulations. Title 43, Section 4100.0-5, Definitions (Bureau of Land Management, "
                 "grazing administration): an animal unit month is the forage needed to sustain one cow or its "
                 "equivalent for 1 month."),
        "url": "https://www.law.cornell.edu/cfr/text/43/4100.0-5",
    },
    "cfr43_4130": {
        "short": "43 CFR 4130.8-1 (BLM grazing fees)",
        "full": ("Code of Federal Regulations. Title 43, Section 4130.8-1, Payment of fees (Bureau of Land "
                 "Management): for fees, an animal unit month is a month's use by 1 cow, bull, steer, heifer, "
                 "horse, burro or mule, or by 5 sheep or 5 goats."),
        "url": "https://www.law.cornell.edu/cfr/text/43/4130.8-1",
    },
    "cfr36_222": {
        "short": "36 CFR 222.50 (Forest Service grazing fees)",
        "full": ("Code of Federal Regulations. Title 36, Section 222.50, Grazing fees (USDA Forest Service): a "
                 "head month is a month's use by one animal, and five sheep or goats count as one head month."),
        "url": "https://www.law.cornell.edu/cfr/text/36/222.50",
    },
    # animal unit equivalents of other sources (Jeff, 2026-10-07: "Add the
    # other animals from those documents"); every table read in its document
    "vallentine1990": {
        "short": "Vallentine (1990), Grazing Management, p. 279",
        "full": ("Vallentine, J. F. 1990. Grazing Management. Academic Press, San Diego, California. Page 279, as "
                 "tabulated by Rangelands Gateway."),
        "url": "",
    },
    "rg_au": {
        "short": "Rangelands Gateway, Animal Units, Table 1",
        "full": ("Rangelands Gateway. Animal Units. Web page; Table 1, Suggested animal unit conversion factors for "
                 "various classes of cattle, from Vallentine (1990)."),
        "url": "https://rangelandsgateway.org/inventorymonitoring/animalunits",
    },
    "rg_aue": {
        "short": "Rangelands Gateway, Animal Unit Equivalents, Table 1",
        "full": ("Rangelands Gateway. Animal Unit Equivalents. Web page; Table 1, Suggested factors describing animal "
                 "unit equivalents for various herbivores, from Vallentine (1990)."),
        "url": "https://rangelandsgateway.org/inventorymonitoring/unitequivalents",
    },
    # the barn's naming, from its coiners (Jeff, 2026-10-08); read in the open
    # PDF of the letter, which reprints their 1944 Los Alamos report LAMS-523
    "holloway_baker1972": {
        "short": "Holloway and Baker (1972), How the barn was born",
        "full": ("Holloway, M. G., and C. P. Baker. 1972. How the barn was born. Physics Today 25(7):9. The letter "
                 "reprints their Los Alamos report LAMS-523, written in 1944."),
        "url": "https://doi.org/10.1063/1.3070918",
    },
    "nmsu_b829": {
        "short": "Spackman and Ward (2023), NMSU Extension Guide B-829, Table 3",
        "full": ("Spackman, C., and M. Ward. 2023. Estimating Carrying Capacity on Rangelands. Guide B-829. New "
                 "Mexico State University Cooperative Extension Service, Las Cruces. Table 3, Forage demand of "
                 "various rangeland animals, after Holechek (1988)."),
        "url": "https://pubs.nmsu.edu/_b/B-829.pdf",
    },
}

SI_ROOTS = ("meters", "grams", "liters", "seconds", "newtons", "joules", "pascals", "watts",
            "watt-hours", "steres")
SI_PREFIXES = ("yotta", "zetta", "exa", "peta", "tera", "giga", "mega", "kilo", "hecto", "deka", "deca",
               "deci", "centi", "milli", "micro", "nano", "pico", "femto", "atto", "zepto", "yocto")


def _is_si(name):
    n = name.lower()
    for pre in ("sq ", "sq. ", "cu ", "cu. "):
        if n.startswith(pre):
            n = n[len(pre):]
    n = n.split(" / ")[0].split("/")[0].strip()
    if n in SI_ROOTS:
        return True
    for p in SI_PREFIXES:
        if n.startswith(p) and n[len(p):] in SI_ROOTS:
            return True
    return False


def _load(name):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return None
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def apply(catalog, log):
    units = catalog["units"]
    by = {u["name"]: u for u in units}
    cites = dict(CITATIONS)
    import catalog_text_fixes                 # the OED entries and the sources of corrections
    cites.update(catalog_text_fixes.CITATIONS)

    nist = _load("nist_matches.json") or []
    nist_by = {}                         # unit -> set of citation keys
    for m in nist:
        # after the corrections every matched unit agrees with NIST; the
        # few that differed by more than 1e-6 were corrected to NIST's value
        if m.get("unit") in by:
            key = "nist_hb44" if str(m.get("table", "")).startswith("HB") else "nist_sp811"
            nist_by.setdefault(m["unit"], set()).add(key)
    topics = _load("unit_citations.json") or {}

    for u in units:
        src = list(u.get("sources") or [])
        added = u.pop("origin", None) == "added"
        orig_text = u.pop("_orig_text", False)
        nist_keys = sorted(nist_by.get(u["name"], ()))
        topic_keys = [k for k, names in topics.get("units", {}).items() if u["name"] in names]
        if not src:
            d = u.get("definition", "")
            if "EPSG code" in d or "PROJ database" in d:
                src.append("epsg")
            elif "(Int)" in u["name"] and u["class"] in ("Energy/Work", "Power"):
                # the international electrical units (not the international
                # nautical league, whose "(Int)" means international)
                src.append("crc62")
            elif _is_si(u["name"]):
                src.append("bipm_si")
            elif added and not nist_keys and not topic_keys:
                # units added in 2026 with no topic citation: built from
                # exact definitions
                src.append("nist_sp811" if ("Derived from exact" in d or "survey" in u["name"].lower()
                                            or "BTU" in u["name"] or "calorie" in u["name"]
                                            or "nautical" in u["name"]) else "bipm_si")
        for key in nist_keys + topic_keys:
            if key not in src:
                src.append(key)
        if not src:
            src.append("jenness_uc")
        u["sources"] = src
        # Jeff, 2026-10-08: a description from the original program (word
        # origins, history) must not read as the claim of the standard that
        # gives the value; it is credited to the original Unit Converter
        given = u.pop("_desc_sources", None)      # catalog_revisions.DEFINITION_REPLACEMENTS
        extra = u.pop("_desc_add", None) or []    # catalog_text_fixes: OED and corrections
        if given:
            desc = list(given)
        elif orig_text and (src != ["jenness_uc"] or extra):
            desc = ["jenness_uc"]
        else:
            desc = []
        # the sources that confirm or correct a sentence come first, then the
        # credit to the original program for the rest of the description
        desc = [k for k in extra if k not in desc] + desc
        if desc:
            u["descriptionSources"] = desc
        else:
            u.pop("descriptionSources", None)
    for key, c in topics.get("citations", {}).items():
        cites[key] = c
    used = {k for u in units for k in u["sources"] + u.get("descriptionSources", [])}
    catalog["citations"] = {k: v for k, v in cites.items() if k in used}
    counts = {}
    for u in units:
        for k in u["sources"]:
            counts[k] = counts.get(k, 0) + 1
    for k, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        log("source    %-16s %4d units" % (k, n))
