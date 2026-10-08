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
"""Builds the Unit Converter's bundled catalog (units.json) from Jeff's
Unit Converter database of 2000-2001 (conversions.mdb).

Step 1, once, with the 32-bit PowerShell (the Access driver is 32-bit):
  C:\\Windows\\SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe -File read_mdb.ps1
      -Mdb <copy of conversions.mdb> -OutDir <folder>
  which writes Allconversions.csv and Primaryconversion.csv.
Step 2:
  python build_unit_catalog.py <folder> <path to units.json>

The old database stored, for every unit, a factor to one of its class's
"primary" units, and a table of conversions between primary units. Here every
unit gets ONE factor (and offset) to its class's base unit instead, built from
the stored unit factor and an EXACT definition of each primary unit (the
international foot, the US and imperial gallons, the avoirdupois pound and the
standard acceleration of gravity are all exact by definition). The script then
recomputes every conversion between two units of a class both ways, the old
three-step way and the new way, and reports the largest differences: those are
where the old primary table carried rounded factors.
"""
import csv
import io
import json
import math
import re
import os
import sys
from fractions import Fraction

# ----------------------------------------------------------- the classes
# base = the unit every conversion in the class passes through; the anchors
# are the old primary units and their exact definitions in the base unit.
# transform: None (base = x * factor + offset), "circle_diameter" (base is the
# area of a circle whose diameter is x * factor meters), "slope_percent"
# (base is the angle in degrees of a slope of x percent).
FT = 0.3048                      # international foot, exact (1959)
SQFT = FT * FT
CUFT = 28.316846592              # liters, exact
GAL_US = 3.785411784             # liters, exact (231 cubic inches)
GAL_UK = 4.54609                 # liters, exact (1985)
LB = 0.45359237                  # kilograms, exact (1959)
G0 = 9.80665                     # standard gravity, exact
LBF = LB * G0                    # newtons
ACRE_M2 = 43560 * SQFT           # 4046.8564224 square meters
WATT_IT = 1.00066921606119       # as stored in the 2001 database (flagged for review)

CLASSES = [
    # name, base unit name, {anchor: (factor, offset, transform)}
    ("Length", "meters", {"meters": (1, 0, None), "feet": (FT, 0, None)}),
    ("Area", "sq meters", {"sq meters": (1, 0, None), "sq feet": (SQFT, 0, None)}),
    ("Volume", "liters", {"liters": (1, 0, None), "cu. feet": (CUFT, 0, None),
                          "gallons (US fluid)": (GAL_US, 0, None), "gallons (Brit)": (GAL_UK, 0, None)}),
    ("Flow", "liters / min", {"liters / min": (1, 0, None), "cu. feet / min": (CUFT, 0, None),
                              "gall. (US) / min": (GAL_US, 0, None), "gall. (Brit) / min": (GAL_UK, 0, None)}),
    ("Weight/Mass", "kilograms", {"kilograms": (1, 0, None), "pounds": (LB, 0, None)}),
    ("Force", "newtons", {"newtons": (1, 0, None), "pounds-force (avdp)": (LBF, 0, None)}),
    ("Velocity", "kilometers/hr", {"kilometers/hr": (1, 0, None), "miles/hr": (1.609344, 0, None)}),
    ("Time", "seconds", {"seconds": (1, 0, None)}),
    ("Energy/Work", "joules", {"joules": (1, 0, None)}),
    ("Pressure/Stress", "pascals", {"pascals": (1, 0, None)}),
    ("Power", "watts", {"watts": (1, 0, None), "watts (it)": (WATT_IT, 0, None)}),
    ("Temperature", "degrees Celsius", {"celsius": (1, 0, None), "fahrenheit": (5.0 / 9.0, -160.0 / 9.0, None),
                                        "r\u00e9aumur": (1.25, 0, None)}),
    ("Circular", "circ. degrees", {"circ. degrees": (1, 0, None), "% (slope)": (1, 0, "slope_percent")}),
    ("Circular Area", "sq m (CA)", {"sq m (circular)": (1, 0, None), "sq ft (circular)": (SQFT, 0, None),
                                    "meters diameter": (1, 0, "circle_diameter"),
                                    "feet diameter": (FT, 0, "circle_diameter")}),
    ("dbh (Forestry)", "sq m (BA/tree)", {"sq ft (BA/tree)": (SQFT, 0, None),
                                          "feet dbh": (FT, 0, "circle_diameter")}),
    ("Basal Area (Forestry)", "BA (sq m/hectare)", {"BA (sq ft/acre)": (SQFT / ACRE_M2 * 10000, 0, None),
                                                    "`in` (5BAF-US)": (5 * SQFT / ACRE_M2 * 10000, 0, None)}),
]
# the old database spelled some anchors in lower case in one table and not the other
ANCHOR_ALIASES = {"r\ufffdaumur": "r\u00e9aumur"}


# Exact building blocks: powers of the international foot times the pound
# and standard gravity. Every customary factor in the database is a simple
# ratio of one of these.
_KNOWN = sorted({FT ** a * LB ** b * G0 ** c for a in range(-3, 4) for b in (0, 1) for c in (0, 1)},
                key=lambda k: (k != 1.0, abs(math.log(k))))   # the plain ratio first


def _ratio(x0, tol):
    """x0 as an exact value: a decimal of at most 7 significant digits or a
    fraction with a denominator of at most 2000, times a power of ten. The
    candidates are few enough (one decade at a time) that a chance match
    within the tolerance is very unlikely. Returns a Fraction or None."""
    e = math.floor(math.log10(abs(x0)))
    x = x0 / 10.0 ** e                      # 1 <= |x| < 10
    scale = Fraction(10) ** e
    dec = Fraction(round(x, 6)).limit_denominator(10 ** 6)
    if dec and abs(float(dec) - x) <= tol * abs(x):
        return dec * scale
    fr = Fraction(x).limit_denominator(2000)
    if fr and abs(float(fr) - x) <= tol * abs(x):
        return fr * scale
    return None


def snap(v):
    """A factor within 2e-11 of a simple ratio times an exact building block
    (a power of ten, pi or 1/pi, powers of the foot, the pound, gravity) is
    replaced by that exact value. The old factors were computed with doubles
    from exact relations and stored to 15 digits, so their products come out
    a few units off in the last places; this puts them back."""
    if v == 0 or not math.isfinite(v):
        return v
    for k in _KNOWN:
        for mult in (1.0, 1.0 / math.pi, math.pi):
            r = _ratio(v / k * mult, 2e-11)
            if r is not None:
                return float(r) * k / mult
    return v


def _is_exact(v):
    for k in _KNOWN:
        for mult in (1.0, 1.0 / math.pi, math.pi):
            if _ratio(v / k * mult, 2e-11) is not None:
                return True
    return False


def near_exact(v):
    """For the factor review: a factor that is NOT exact but lies within
    5e-8 of a short exact value built from the foot, the pound and gravity
    (per second, minute or hour) was probably rounded from a published
    table. Returns (exact value, how it is built, relative difference) or
    None."""
    if v == 0 or not math.isfinite(v) or _is_exact(v):
        return None
    blocks = [(LB * G0, "lbf"), (LB * G0 * FT, "ft*lbf"), (LB * G0 / FT ** 2, "lbf/ft^2"),
              (FT, "ft"), (FT ** 2, "ft^2"), (FT ** 3, "ft^3"), (LB, "lb")]
    for k, kn in blocks:
        for per, pn in ((1.0, ""), (60.0, " per min"), (3600.0, " per hr")):
            x0 = v / k * per
            e = math.floor(math.log10(abs(x0)))
            x = x0 / 10.0 ** e
            for cand in (Fraction(round(x, 3)).limit_denominator(1000), Fraction(x).limit_denominator(48)):
                if not cand:
                    continue
                rel = abs(float(cand) - x) / abs(x)
                if rel <= 5e-8:
                    r = cand * Fraction(10) ** e
                    return float(r) * k / per, "%.10g %s%s" % (float(r), kn, pn), rel
    return None


def read_csv(folder, name):
    with io.open(os.path.join(folder, name), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build(folder):
    rows = read_csv(folder, "Allconversions.csv")
    prim = read_csv(folder, "Primaryconversion.csv")
    classes = {c[0]: c for c in CLASSES}
    units, seen = [], set()
    for r in sorted(rows, key=lambda r: float(r["Index"] or 0)):
        cls = r["Class"]
        name = r["Input Units"].strip()
        if name.lower() in seen:
            raise SystemExit("duplicate unit name: " + name)
        seen.add(name.lower())
        anchor = ANCHOR_ALIASES.get(r["USMetric"], r["USMetric"])
        anchors = {k.lower(): v for k, v in classes[cls][2].items()}
        fa, oa, tr = anchors[anchor.lower()]
        c_in = float(r["Input Conversion"])
        a_in = float(r["Input Addition"] or 0)
        if tr is None:
            # base = ((x + a_in) * c_in) * fa + oa
            factor = snap(c_in * fa)
            offset = a_in * c_in * fa + oa
            # -160/9 and -273.15 come out a few units off in the last place
            fr = Fraction(offset).limit_denominator(1000)
            if abs(float(fr) - offset) <= 1e-9:
                offset = float(fr)
        else:
            if a_in:
                raise SystemExit("offset on a transformed unit: " + name)
            factor = snap(c_in * fa)
            offset = 0.0
        u = {"name": name, "singular": r["Input Unit"].strip(), "class": cls,
             "factor": factor}
        if offset:
            u["offset"] = offset
        if tr:
            u["transform"] = tr
        u["definition"] = clean_definition(r["Comments"] or "")
        units.append(u)
    for c in CLASSES:
        if not any(u["name"] == c[1] for u in units):
            raise SystemExit("base unit not in the catalog: %s (%s)" % (c[1], c[0]))
    catalog = {
        "schemaVersion": 1,
        "title": "Unit Converter catalog",
        "source": ("Jeff Jenness's Unit Converter database (conversions.mdb, 2000-2001), with "
                   "every unit's factor rebuilt against its class's base unit from exact "
                   "definitions (international foot 0.3048 m, US gallon 231 cubic inches, imperial "
                   "gallon 4.54609 L, avoirdupois pound 0.45359237 kg, standard gravity 9.80665 m/s2)."),
        "classes": [{"name": c[0], "base": c[1]} for c in CLASSES],
        "units": units,
    }
    return catalog, rows, prim


# ------------------------------------------------- converting, both ways
def to_base(u, x):
    t = u.get("transform")
    if t == "circle_diameter":
        d = x * u["factor"]
        return math.pi / 4.0 * d * d
    if t == "slope_percent":
        return math.degrees(math.atan(x * u["factor"] / 100.0))
    return x * u["factor"] + u.get("offset", 0.0)


def from_base(u, b):
    t = u.get("transform")
    if t == "circle_diameter":
        if b < 0:
            return float("nan")
        return math.sqrt(4.0 * b / math.pi) / u["factor"]
    if t == "slope_percent":
        return 100.0 * math.tan(math.radians(b)) / u["factor"]
    return (b - u.get("offset", 0.0)) / u["factor"]


def old_convert(rows_by_name, prim_by_key, a, b, x):
    """The 2000 program's formula (cmdConvert_Click), linear classes only."""
    ra, rb = rows_by_name[a], rows_by_name[b]
    key = (ra["Class"] + ra["USMetric"] + rb["USMetric"]).lower()
    p = prim_by_key.get(key)
    if p is None:
        return None
    k, add = float(p["Conversion"]), float(p["Addition"] or 0)
    if k == 0:
        return None            # a special case in the old code (slope, circles)
    return (((x + float(ra["Input Addition"] or 0)) * float(ra["Input Conversion"]))
            * k * float(rb["Output Conversion"])) + float(rb["Output Addition"] or 0) + add


def compare(catalog, rows, prim):
    by_name = {r["Input Units"].strip(): r for r in rows}
    prim_by_key = {p["Conversion name"].lower(): p for p in prim}
    units = catalog["units"]
    worst = []
    n = 0
    for cls in {u["class"] for u in units}:
        us = [u for u in units if u["class"] == cls]
        for a in us:
            for b in us:
                for x in (1.0, 37.5):
                    old = old_convert(by_name, prim_by_key, a["name"], b["name"], x)
                    if old is None:
                        continue
                    new = from_base(b, to_base(a, x))
                    n += 1
                    rel = abs(new - old) / max(abs(new), abs(old), 1e-300)
                    if not (abs(new - old) < 1e-9 and abs(old) < 1e-6):
                        worst.append((rel, cls, a["name"], b["name"], x, old, new))
    worst.sort(reverse=True)
    return n, worst


# a sentence ends at . ! or ? (and a closing quote) before the next one's
# capital, quote, parenthesis or digit
_SENTENCE = re.compile(r'(?<=[.!?])(["\u201d)]?)(\s+)(?=["\u201c(A-Z0-9])')
# typing slips in the 2001 text (Jeff, 2026-10-08: fix what the checks find)
_TYPOS = [(re.compile(r"\bInternation\b"), "International")]


def clean_definition(text):
    """The 2001 definition, with a sentence that repeats an earlier one
    dropped (Jeff, 2026-10-08: the mile's word-origin sentence appeared
    twice) and typing slips fixed. Spacing between sentences is kept."""
    text = text.strip()
    for pat, rep in _TYPOS:
        text = pat.sub(rep, text)
    parts = _SENTENCE.split(text)      # sentence, closer, space, sentence, ...
    out, seen = [], set()
    i = 0
    while i < len(parts):
        sent = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
        space = parts[i + 2] if i + 2 < len(parts) else ""
        key = " ".join(sent.lower().split())
        if key and key in seen:
            if out:                    # drop the repeat and the space before it
                out[-1] = (out[-1][0], space)
        else:
            seen.add(key)
            out.append((sent, space))
        i += 3
    return "".join(s + sp for s, sp in out).strip()


PROJ_DB = r"C:\Program Files\ArcGIS\Pro\Resources\pedata\gdaldata\proj.db"


def main():
    folder, out = sys.argv[1], sys.argv[2]
    catalog, rows, prim = build(folder)
    n, worst = compare(catalog, rows, prim)
    # Jeff's rulings of 2026-10-07 and the units ArcGIS Pro uses (after the
    # comparison with the old formula, which needs the old unit names)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    # the descriptions of the 2001 database are Jeff's own (many from the
    # full OED, which cannot be checked again): catalog_sources credits them
    # to the original Unit Converter, apart from the value's sources
    for u in catalog["units"]:
        u["_orig_text"] = True
    import catalog_revisions
    print("revisions:")
    added = catalog_revisions.apply(catalog, sys.argv[3] if len(sys.argv) > 3 else PROJ_DB,
                                    lambda s: print("  " + s))
    print("  %d units added; %d units in all" % (added, len(catalog["units"])))
    import catalog_sources
    catalog_sources.apply(catalog, lambda s: print("  " + s))
    # one line per pair of anchors: the largest difference for each class pair
    seen = {}
    for w in worst:
        k = (w[1], by_anchor(rows, w[2]), by_anchor(rows, w[3]))
        if k not in seen:
            seen[k] = w
    print("%d units in %d classes; %d conversions compared with the old formula" %
          (len(catalog["units"]), len(catalog["classes"]), n))
    print("largest relative differences, one per pair of old primary units:")
    for k, w in sorted(seen.items(), key=lambda kv: -kv[1][0]):
        if w[0] < 1e-12:
            continue
        print("  %.2e  %-18s %-24s -> %-24s old %-22.15g new %.15g" % (w[0], w[1], w[2], w[3], w[5], w[6]))
    review = []
    for u in catalog["units"]:
        ne = near_exact(u["factor"]) if not u.get("transform") else None
        if ne:
            review.append((ne[2], u["class"], u["name"], u["factor"], ne[0], ne[1]))
    review.sort(key=lambda r: (r[1], -r[0]))
    print()
    print("%d factors that look ROUNDED from an exact value (left as stored, for review):" % len(review))
    for r in review:
        print("  %-16s %-34s stored %-22.12g exact %-22.15g (%s; differs by %.1e)" % (r[1], r[2], r[3], r[4], r[5], r[0]))
    with io.open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("wrote", out)


_ANCHOR = {}


def by_anchor(rows, name):
    if not _ANCHOR:
        for r in rows:
            _ANCHOR[r["Input Units"].strip()] = r["USMetric"]
    return _ANCHOR[name]


if __name__ == "__main__":
    main()
