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
"""Revisions to the Unit Converter catalog, applied by build_unit_catalog.py
after the 2001 database is read. Jeff's rulings of 2026-10-07:

1. Exact definitions wherever they exist: the foot-pound-force family, the
   horsepower, pressures in pounds-force per area, the knot and the
   electron-volt replace their 7- to 9-digit published values.
2. The international electrical units: the international joule and the
   international watt are both 1.000165 of the SI unit (CRC Handbook of
   Chemistry and Physics, 62nd edition), and the watts were mislabeled
   "(IT)" (International Table, which belongs to the calorie and the Btu):
   they become "(Int)".
3. Units defined by the U.S. survey foot say that it was retired at the end
   of 2022.
4. Every unit ArcGIS Pro uses: the length and angle units of the PROJ/EPSG
   database Pro ships (Resources\\pedata\\gdaldata\\proj.db) and the US survey
   variants arcpy's LinearUnitConversionFactor and ArealUnitConversionFactor
   accept, each with the names ArcGIS uses as search aliases.
"""
import math

FT = 0.3048
SFT = 1200.0 / 3937.0              # U.S. survey foot, meters
LB = 0.45359237
G0 = 9.80665
LBF = LB * G0
IN = 0.0254
INT_ELEC = 1.000165                # international joule and watt (CRC 62nd ed.)

SURVEY_NOTE = (" The U.S. survey foot was retired by the National Institute of Standards and "
               "Technology and the National Geodetic Survey at the end of 2022; since then, U.S. "
               "measurements use the international foot of exactly 0.3048 meter. This unit is kept "
               "for older surveys and records.")


def _u(name, singular, cls, factor, definition, aliases=()):
    return {"name": name, "singular": singular, "class": cls, "factor": factor,
            "definition": definition, "aliases": list(aliases)}


def _epsg(code, meters):
    return (" Equal to %s meter; EPSG code %s in the EPSG geodetic parameter registry, and a linear "
            "unit of coordinate systems in ArcGIS Pro." % (_num(meters), code))


def _num(v):
    s = repr(v)
    if "e" in s or "E" in s:
        return "%.15g" % v
    return s.rstrip("0").rstrip(".") if "." in s else s


# ------------------------------------------------------------------ 1. exact values
# name -> exact factor in the class's base unit. Each must already lie within
# 1e-6 of the stored value, or the build stops: these are corrections of
# rounding, never changes of meaning.
def exact_values():
    psi = LBF / IN ** 2
    psf = LBF / FT ** 2
    ftlbf = FT * LBF
    return {
        # Energy/Work (joules)
        "foot-pounds": ftlbf,
        "inch-pounds": ftlbf / 12,
        "oz-inches": ftlbf / 12 / 16,
        "electron-volts": 1.602176634e-19,
        # Power (watts)
        "ft-lbs / sec": ftlbf, "ft-lbs / min": ftlbf / 60, "ft-lbs / hr": ftlbf / 3600,
        "inch-pounds / sec": ftlbf / 12, "inch-pounds / min": ftlbf / 12 / 60, "inch-pounds / hr": ftlbf / 12 / 3600,
        "oz-inches / sec": ftlbf / 192, "oz-inches / min": ftlbf / 192 / 60, "oz-inches / hr": ftlbf / 192 / 3600,
        "mile-lbs / sec": 5280 * ftlbf, "mile-lbs / min": 5280 * ftlbf / 60, "mile-lbs / hr": 5280 * ftlbf / 3600,
        "horsepower (mech)": 550 * ftlbf, "horsepower (thrust)": 550 * ftlbf,
        # Pressure/Stress (pascals)
        "psi (lbf / sq. in)": psi, "kips / sq. in": 1000 * psi, "tons / sq. inch": 2000 * psi,
        "lbf / sq. ft": psf, "kips / sq. ft": 1000 * psf, "tons / sq. foot": 2000 * psf,
        "lbf / sq. yd": psf / 9, "kips / sq. yd": 1000 * psf / 9, "tons / sq. yd": 2000 * psf / 9,
        # Velocity (kilometers/hr)
        "knots": 1.852,
        # Length (meters): the international nautical league, three
        # international nautical miles of 1,852 meters (stored from feet)
        "leagues (Int)": 3 * 1852.0,
        # the U.S. survey foot and its square and cube (1200/3937 meter)
        "feet (US Survey)": SFT,
        "sq feet (Survey)": SFT ** 2,
        "cu. feet (Survey)": SFT ** 3 * 1000.0,
    }


def nist_corrections():
    """Values NIST's tables showed to be wrong or rounded in the 2001 data
    (the comparison of 2026-10-07): the U.S. dry measures from the Winchester
    bushel of 2150.42 cubic inches, the slug and the poundal from the pound
    and the foot, and the conventional millimeter of mercury of NIST SP 811
    (13,595.1 kg/m3 under standard gravity). Liters and pascals."""
    cuin_l = IN ** 3 * 1000.0                 # liters per cubic inch
    bushel = 2150.42 * cuin_l
    poundal = LB * FT
    mmhg = 133.322387415
    return {
        "bushels (US level)": bushel, "bushels (Winch.)": bushel,
        "pecks (US)": bushel / 4, "gallons (US dry)": bushel / 8,
        "quarts (US dry)": bushel / 32, "pints (US dry)": bushel / 64,
        "slugs": LBF / FT, "geepounds": LBF / FT,
        "poundals": poundal, "poundals / sq. in": poundal / IN ** 2,
        "poundals / sq. ft": poundal / FT ** 2, "poundals / sq. yd": poundal / (9 * FT ** 2),
        "foot-poundals": poundal * FT,
        "ft-poundals / sec": poundal * FT, "ft-poundals / min": poundal * FT / 60,
        "ft-poundals / hr": poundal * FT / 3600,
        "mmHg (conv.)": mmhg, "cmHg (conv.)": mmhg * 10, "inHg (conv.)": mmhg * 25.4,
        "ftHg (conv.)": mmhg * 304.8,
        "mmHg (0° C)": 133.3224, "cmHg (0° C)": 1333.224,
    }


EXACT_NOTES = {
    "electron-volts": " Exact since the 2019 redefinition of the SI, which fixed the charge of the electron: "
                      "1.602176634 x 10^-19 joule.",
    "knots": " One international nautical mile (1,852 meters exactly) per hour.",
    "leagues (Int)": " Three international nautical miles of 1,852 meters each: 5,556 meters exactly.",
}

# ------------------------------------------------------------------ 2. international electrical units
INT_DEFS = {
    "joules (Int)": ("An international joule (Int) is a unit of work or energy from the international "
                     "electrical units in use before the present SI units, equal to 1.000165 joules "
                     "(CRC Handbook of Chemistry and Physics, 62nd edition)."),
    "watts (Int)": ("An international watt (Int) is a unit of power from the international electrical "
                    "units in use before the present SI units, equal to 1.000165 watts, or one "
                    "international joule per second (CRC Handbook of Chemistry and Physics, 62nd "
                    "edition)."),
}

# ------------------------------------------------------------------ 4. ArcGIS Pro's units
AREA_US = [
    ("acres (US survey)", "acre (US survey)", 43560 * SFT ** 2, ["AcresUS", "Acres (US)", "US survey acre"],
     "A U.S. survey acre is a unit of area equal to 43,560 square U.S. survey feet, slightly more than an "
     "international acre. ArcGIS Pro calls it AcresUS."),
    ("sq inches (US survey)", "sq inch (US survey)", (SFT / 12) ** 2, ["SquareInchesUS", "Square Inches (US)"],
     "A square U.S. survey inch is a unit of area equal to a square one U.S. survey inch on a side. "
     "ArcGIS Pro calls it SquareInchesUS."),
    ("sq yards (US survey)", "sq yard (US survey)", (3 * SFT) ** 2, ["SquareYardsUS", "Square Yards (US)"],
     "A square U.S. survey yard is a unit of area equal to a square one U.S. survey yard on a side. "
     "ArcGIS Pro calls it SquareYardsUS."),
    ("sq miles (US survey)", "sq mile (US survey)", (5280 * SFT) ** 2, ["SquareMilesUS", "Square Miles (US)"],
     "A square U.S. survey mile is a unit of area equal to a square one U.S. survey mile on a side, the "
     "section of the U.S. Public Land Survey System as originally measured. ArcGIS Pro calls it SquareMilesUS."),
    ("sq nautical miles", "sq nautical mile", 1852.0 ** 2, ["SquareNauticalMiles", "Square Nautical Miles"],
     "A square nautical mile is a unit of area equal to a square one international nautical mile (1,852 "
     "meters) on a side. ArcGIS Pro calls it SquareNauticalMiles."),
    ("sq nautical miles (US)", "sq nautical mile (US)", 1853.248 ** 2, ["SquareNauticalMilesUS"],
     "A square U.S. nautical mile is a unit of area equal to a square one U.S. nautical mile (1,853.248 "
     "meters) on a side. ArcGIS Pro calls it SquareNauticalMilesUS."),
]

LENGTH_US = [
    ("inches (US survey)", "inch (US survey)", SFT / 12, ["InchesUS", "Inch_US", "US survey inch", "Inches (US)"],
     "A U.S. survey inch is a unit of length equal to one twelfth of a U.S. survey foot, or 100/3937 meter. "
     "ArcGIS Pro calls it Inch_US."),
    ("yards (US survey)", "yard (US survey)", 3 * SFT, ["YardsUS", "Yard_US", "US survey yard", "Yards (US)"],
     "A U.S. survey yard is a unit of length equal to 3 U.S. survey feet, or 3600/3937 meter. ArcGIS Pro "
     "calls it Yard_US."),
    ("miles (US survey)", "mile (US survey)", 5280 * SFT, ["MilesUS", "US survey mile", "Miles (US)"],
     "A U.S. survey mile is a unit of length equal to 5,280 U.S. survey feet, or 6336/3.937 meters "
     "(EPSG code 9035). ArcGIS Pro calls it MilesUS."),
    ("chains (US survey)", "chain (US survey)", 66 * SFT, ["US survey chain"],
     "A U.S. survey chain is a surveyor's chain of 66 U.S. survey feet (EPSG code 9033)."),
    ("links (US survey)", "link (US survey)", 0.66 * SFT, ["US survey link"],
     "A U.S. survey link is one hundredth of a U.S. survey chain, or 0.66 U.S. survey foot (EPSG code 9034)."),
    ("rods (US survey)", "rod (US survey)", 16.5 * SFT, ["Rod_US", "US survey rod"],
     "A U.S. survey rod is a unit of length equal to 16.5 U.S. survey feet. ArcGIS Pro calls it Rod_US."),
    ("nautical miles (US)", "nautical mile (US)", 1853.248, ["NauticalMilesUS", "Nautical_Mile_US", "Nautical Miles (US)"],
     "The U.S. nautical mile is a unit of length equal to 1,853.248 meters (6,080.20 feet), used in the "
     "United States before the international nautical mile of 1,852 meters was adopted there in 1954. "
     "ArcGIS Pro calls it Nautical_Mile_US."),
]

# the 2001 class names and the names they have now (Jeff agreed 2026-10-07)
CLASS_RENAMES = {
    "Length": "Length and Distance",
    "Circular": "Angle and Slope",
    "Circular Area": "Circle Area and Diameter",
    "dbh (Forestry)": "Tree Diameter and Basal Area (Forestry)",
    "Basal Area (Forestry)": "Stand Basal Area (Forestry)",
    "Flow": "Flow Rate",
    "Velocity": "Speed and Velocity",
}

# 2001 unit names and the names they have now; the old name stays an alias,
# so Find and saved conversions still know it. Jeff, 2026-10-08: "% (Percent)
# Slope", so a search for the word "percent" finds it
UNIT_RENAMES = {
    "% (slope)": ("% (Percent) Slope", "% (Percent) Slope"),
    # Jeff, 2026-10-08: the engineer's chain is Ramsden's (Jesse Ramsden, 1784)
    "links (Ramden)": ("links (Ramsden)", "link (Ramsden)"),
    "chains (Ramden)": ("chains (Ramsden)", "chain (Ramsden)"),
    "sq chains (Ramden)": ("sq chains (Ramsden)", "sq chain (Ramsden)"),
    "sq links (Ramden)": ("sq links (Ramsden)", "sq link (Ramsden)"),
}

# Jeff, 2026-10-08: "cir." read as "circle", so 640 sq cm (CA) -> 0.0815
# cir. m (CA) looked like a diameter; these are the CIRCULAR units (the area
# of a circle one unit across), spelled out. Not yet released: no aliases.
_CIRCULAR = [("millimeters", "millimeter"), ("centimeters", "centimeter"), ("decimeters", "decimeter"),
             ("meters", "meter"), ("dekameters", "dekameter"), ("hectometers", "hectometer"),
             ("kilometers", "kilometer")]
UNIT_RENAMES_NO_ALIAS = {}
for _pl, _sg in _CIRCULAR:                                  # the Area class
    UNIT_RENAMES_NO_ALIAS["cir. %s" % _pl] = ("circular %s" % _pl, "circular %s" % _sg)
for _old, (_pl, _sg) in [("mm", ("millimeters", "millimeter")), ("cm", ("centimeters", "centimeter")),
                         ("decim.", ("decimeters", "decimeter")), ("m", ("meters", "meter")),
                         ("dekam.", ("dekameters", "dekameter")), ("hm", ("hectometers", "hectometer")),
                         ("km", ("kilometers", "kilometer"))]:   # the Circle Area and Diameter class
    UNIT_RENAMES_NO_ALIAS["cir. %s (CA)" % _old] = ("circular %s (CA)" % _pl, "circular %s (CA)" % _sg)
for _pl, _sg in [("mils", "mil"), ("inches", "inch"), ("feet", "foot"), ("yards", "yard"), ("miles", "mile")]:
    UNIT_RENAMES_NO_ALIAS["cir. %s (CA)" % _pl] = ("circular %s (CA)" % _pl, "circular %s (CA)" % _sg)

# 2001 descriptions replaced from a primary source: (new definition, keys of
# the sources of the description, in catalog_sources.CITATIONS). Jeff,
# 2026-10-08: the barn's text from its coiners' own account, which does not
# give the "broad side of a barn" origin the 2001 text gave.
DEFINITION_REPLACEMENTS = {
    "barns": (
        "A barn is a very small unit of area equal to 1.0 e-28 square meters, used to measure the cross "
        "sections of atomic nuclei when studying interactions between the nuclei and other particles.  The "
        "physicists M. G. Holloway and C. P. Baker named it over dinner at Purdue University in December "
        "1942.  They tried the names Oppenheimer, Bethe, Manley and John, and one author's rural background "
        "led them from John to barn.  A cross section of 10⁻²⁴ square centimeters was, for "
        "nuclear processes, \"as big as a barn.\"  Their account does not mention the phrase \"the broad "
        "side of a barn.\"",
        ["holloway_baker1972", "jenness_uc"]),
}

# survey-foot based units, existing and new, that get SURVEY_NOTE
SURVEY_UNITS = ["feet (US Survey)", "sq feet (Survey)", "cu. feet (Survey)",
                "acres (US survey)", "sq inches (US survey)", "sq yards (US survey)", "sq miles (US survey)",
                "inches (US survey)", "yards (US survey)", "miles (US survey)", "chains (US survey)",
                "links (US survey)", "rods (US survey)"]

# aliases for units already in the catalog: the names ArcGIS Pro and EPSG use
ALIASES = {
    "feet": ["Feet (Int)", "FeetInt", "foot", "International feet"],
    "feet (US Survey)": ["FeetUS", "Foot_US", "Feet (US)", "US survey foot", "US survey feet"],
    "inches": ["InchesInt", "Inch", "International inches"],
    "yards": ["YardsInt", "Yard", "International yards"],
    "miles": ["MilesInt", "Statute mile", "International miles", "Miles (Int)"],
    "miles, nautical": ["NauticalMilesInt", "nautical mile", "Nautical Miles (Int)", "international nautical mile"],
    "miles (Brit. naut)": ["Nautical_Mile_UK", "UK nautical mile"],
    "meters": ["metre", "Meter"],
    "kilometers": ["kilometre", "Kilometer"],
    "centimeters": ["centimetre", "Centimeter"],
    "millimeters": ["millimetre", "Millimeter"],
    "decimeters": ["decimetre", "Decimeter"],
    "micrometers": ["Micrometer"],
    "nanometers": ["Nanometer"],
    "points": ["Point"],
    "rods": ["Rod"],
    "fathoms": ["fathom"],
    "chains (Survey)": ["chain"],
    "links (Survey)": ["link"],
    "sq feet": ["SquareFeet", "SquareFeetInt", "Square Feet"],
    "sq feet (Survey)": ["SquareFeetUS", "Square Feet (US)"],
    "sq meters": ["SquareMeters", "Square Meters"],
    "sq kilometers": ["SquareKilometers", "Square Kilometers"],
    "sq miles": ["SquareMiles", "SquareMilesInt", "Square Miles"],
    "sq yards": ["SquareYards", "SquareYardsInt"],
    "sq inches": ["SquareInches", "SquareInchesInt"],
    "sq centimeters": ["SquareCentimeters"],
    "sq millimeters": ["SquareMillimeters"],
    "sq decimeters": ["SquareDecimeters"],
    "acres": ["Acres", "AcresInt"],
    "hectares": ["Hectares"],
    "ares": ["Ares"],
    "circ. degrees": ["degree", "Degree", "Decimal degrees", "degrees"],
    "circ. minutes": ["arc-minute", "arc minutes", "minutes of arc"],
    "circ. seconds": ["arc-second", "arc seconds", "seconds of arc"],
    "radians": ["radian", "Radian"],
    "grades": ["grad", "gradians", "Grad"],
    "gons": ["gon", "Gon"],
    "kilometers/hr": ["kilometers per hour", "Kilometers per hour", "km/h"],
    "miles/hr": ["miles per hour", "Miles per hour", "mph"],
    "meters/sec": ["meters per second", "Meters per second", "m/s"],
    "feet/second": ["feet per second", "Feet per second"],
    "knots": ["Knots", "nautical miles per hour"],
}


def pro_angle_units():
    """The angle units of proj.db not already in the catalog (base: degrees)."""
    deg = 180.0 / math.pi
    return [
        ("microradians", "microradian", 1e-6 * deg, ["microradian"],
         "A microradian is one millionth of a radian. EPSG code 9109."),
        ("milliarc-seconds", "milliarc-second", 1.0 / 3600000.0, ["milliarc-second", "mas"],
         "A milliarc-second is one thousandth of a second of arc, or 1/3,600,000 of a degree. EPSG code 1031."),
        ("centesimal minutes", "centesimal minute", 0.009, ["centesimal minute"],
         "A centesimal minute is one hundredth of a grad (gon), or 0.009 degree. EPSG code 9112."),
        ("centesimal seconds", "centesimal second", 0.00009, ["centesimal second"],
         "A centesimal second is one hundredth of a centesimal minute, or one ten-thousandth of a grad (gon). "
         "EPSG code 9113."),
        ("mils (angular, 6400)", "mil (angular, 6400)", 360.0 / 6400.0, ["mil_6400", "angular mil", "NATO mil"],
         "An angular mil (6400 to a circle) is 1/6,400 of a full circle, or 0.05625 degree, the mil of "
         "NATO artillery and compasses. EPSG code 9114."),
    ]


def pro_length_units(db_path):
    """The length units of proj.db (the EPSG, ESRI and PROJ entries Pro ships)
    that the catalog does not have yet, as catalog units. Velocities and the
    deprecated bin widths are left out."""
    import sqlite3
    rows = sqlite3.connect(db_path).execute(
        "SELECT auth_name, code, name, conv_factor FROM unit_of_measure "
        "WHERE type='length' AND deprecated=0 AND conv_factor IS NOT NULL").fetchall()
    names = {
        "9005": ("Clarke's feet", "Clarke's foot"), "9037": ("Clarke's yards", "Clarke's yard"),
        "9038": ("Clarke's chains", "Clarke's chain"), "9039": ("Clarke's links", "Clarke's link"),
        "9080": ("Indian feet", "Indian foot"), "9081": ("Indian feet (1937)", "Indian foot (1937)"),
        "9082": ("Indian feet (1962)", "Indian foot (1962)"), "9083": ("Indian feet (1975)", "Indian foot (1975)"),
        "9084": ("Indian yards", "Indian yard"), "9085": ("Indian yards (1937)", "Indian yard (1937)"),
        "9086": ("Indian yards (1962)", "Indian yard (1962)"), "9087": ("Indian yards (1975)", "Indian yard (1975)"),
        "IND_CH": ("Indian chains", "Indian chain"),
        "9040": ("British yards (Sears 1922)", "British yard (Sears 1922)"),
        "9041": ("British feet (Sears 1922)", "British foot (Sears 1922)"),
        "9042": ("British chains (Sears 1922)", "British chain (Sears 1922)"),
        "9043": ("British links (Sears 1922)", "British link (Sears 1922)"),
        "9099": ("British yards (Sears 1922 truncated)", "British yard (Sears 1922 truncated)"),
        "9300": ("British feet (Sears 1922 truncated)", "British foot (Sears 1922 truncated)"),
        "9301": ("British chains (Sears 1922 truncated)", "British chain (Sears 1922 truncated)"),
        "9302": ("British links (Sears 1922 truncated)", "British link (Sears 1922 truncated)"),
        "9050": ("British yards (Benoit 1895 A)", "British yard (Benoit 1895 A)"),
        "9051": ("British feet (Benoit 1895 A)", "British foot (Benoit 1895 A)"),
        "9052": ("British chains (Benoit 1895 A)", "British chain (Benoit 1895 A)"),
        "9053": ("British links (Benoit 1895 A)", "British link (Benoit 1895 A)"),
        "9060": ("British yards (Benoit 1895 B)", "British yard (Benoit 1895 B)"),
        "9061": ("British feet (Benoit 1895 B)", "British foot (Benoit 1895 B)"),
        "9062": ("British chains (Benoit 1895 B)", "British chain (Benoit 1895 B)"),
        "9063": ("British links (Benoit 1895 B)", "British link (Benoit 1895 B)"),
        "9070": ("British feet (1865)", "British foot (1865)"),
        "9095": ("British feet (1936)", "British foot (1936)"),
        "9094": ("Gold Coast feet", "Gold Coast foot"),
        "9031": ("German legal meters", "German legal meter"),
        "109015": ("varas (Texas)", "vara (Texas)"),
        "109014": ("smoots", "smoot"),
        "109030": ("50-kilometer units", "50-kilometer unit"),
        "109031": ("150-kilometer units", "150-kilometer unit"),
    }
    extra = {
        "109015": " The Texas vara, 33 1/3 U.S. survey inches, used in Texas land surveys. ArcGIS Pro calls it Vara_TX.",
        "109014": " A unit of length equal to the height of Oliver Smoot (1.7018 meters), with which the Harvard "
                  "Bridge in Cambridge, Massachusetts, was measured in 1958. ArcGIS Pro calls it Smoot.",
        "109030": " ArcGIS Pro offers it as the linear unit of a few coordinate systems.",
        "109031": " ArcGIS Pro offers it as the linear unit of a few coordinate systems.",
    }
    out = []
    for auth, code, name, f in rows:
        code = str(code)                 # EPSG codes are stored as integers, PROJ's as text
        if code not in names:
            continue
        plural, singular = names[code]
        if code in extra:
            d = "A %s is a unit of length equal to %s meter.%s" % (singular, _num(f), extra[code])
        elif auth == "EPSG":
            d = ("A %s is a historical unit of length used in the coordinate systems of some countries." % singular
                 + _epsg(code, f))
        else:
            d = ("A %s is a historical unit of length used in the coordinate systems of some countries. Equal "
                 "to %s meter (%s in the PROJ database that ArcGIS Pro ships), and a linear unit of coordinate "
                 "systems in ArcGIS Pro." % (singular, _num(f), name))
        out.append((plural, singular, f, [name], d))
    return out


def apply(catalog, proj_db, log):
    units = catalog["units"]
    by = {u["name"]: u for u in units}

    # 1. exact values
    for name, exact in exact_values().items():
        u = by.get(name)
        if u is None:
            raise SystemExit("exact value for a unit not in the catalog: " + name)
        rel = abs(u["factor"] - exact) / exact
        if rel > 1e-6:
            raise SystemExit("exact value for '%s' differs from the stored %r by %.2e: not a rounding" % (name, u["factor"], rel))
        log("exact     %-24s %-22.12g -> %.15g  (differed by %.1e)" % (name, u["factor"], exact, rel))
        u["factor"] = exact
        if name in EXACT_NOTES:
            u["definition"] = u["definition"].rstrip() + EXACT_NOTES[name]

    # 1b. corrections from the comparison with NIST's tables (up to 1.3e-6)
    for name, exact in nist_corrections().items():
        u = by.get(name)
        if u is None:
            raise SystemExit("NIST correction for a unit not in the catalog: " + name)
        rel = abs(u["factor"] - exact) / exact
        if rel > 5e-6:
            raise SystemExit("NIST correction for '%s' differs from the stored %r by %.2e" % (name, u["factor"], rel))
        log("nist      %-24s %-22.12g -> %.15g  (differed by %.1e)" % (name, u["factor"], exact, rel))
        u["factor"] = exact

    # 2. international electrical units: 1.000165; "(IT)" watts become "(Int)"
    old_watt = by["watts (IT)"]["factor"]
    old_joule = by["joules (Int)"]["factor"]
    for u in units:
        if u["class"] not in ("Energy/Work", "Power"):
            continue
        base_old = old_watt if u["class"] == "Power" else old_joule
        ratio = u["factor"] / base_old
        k = None
        for m in (1e-24, 1e-21, 1e-18, 1e-15, 1e-12, 1e-9, 1e-6, 1e-3, 1e-2, 1e-1, 1, 10, 100, 1e3, 1e4, 1e6, 1e9,
                  1e12, 1e15, 1e18, 1e21, 1e24, 1 / 60, 1 / 3600, 3600):
            if abs(ratio - m) <= 1e-9 * m:
                k = m
                break
        is_int_family = k is not None and ("(IT)" in u["name"] or "(Int)" in u["name"]) \
            and not any(w in u["name"].lower() for w in ("btu", "cal"))
        if u["name"] == "watt-hours (IT)":       # stored from a slightly different value
            is_int_family, k = True, 3600
        if not is_int_family:
            continue
        old_name = u["name"]
        u["name"] = old_name.replace("(IT)", "(Int)")
        u["singular"] = u["singular"].replace("(IT)", "(Int)")
        u["factor"] = INT_ELEC * k
        d = (u["definition"].replace("International Steam Table", "international")
             .replace("slightly less than a standard joule", "slightly more than a standard joule")
             .replace("(IT)", "(Int)"))
        u["definition"] = INT_DEFS.get(u["name"], d)
        if u["name"] in INT_DEFS:
            u.pop("_orig_text", None)        # a new, sourced description
        if old_name != u["name"]:
            u.setdefault("aliases", []).append(old_name)
        log("int-elec  %-24s -> %-24s %.15g" % (old_name, u["name"], u["factor"]))

    # 4. ArcGIS Pro's units
    have = {u["name"].lower() for u in units}
    added = []
    for plural, singular, f, aliases, d in LENGTH_US + pro_length_units(proj_db):
        if plural.lower() in have:
            continue
        added.append(_u(plural, singular, "Length", f, d, aliases))
    for plural, singular, f, aliases, d in AREA_US:
        if plural.lower() not in have:
            added.append(_u(plural, singular, "Area", f, d, aliases))
    for plural, singular, f, aliases, d in pro_angle_units():
        if plural.lower() not in have:
            added.append(_u(plural, singular, "Circular", f, d, aliases))
    for a in added:
        a["origin"] = "added"
        log("added     %-36s %-10s %.15g" % (a["name"], a["class"], a["factor"]))
    units.extend(added)
    by = {u["name"]: u for u in units}

    # new measurement classes and the wood and fire units (Jeff, 2026-10-07)
    import catalog_new_classes
    nclasses, nunits = catalog_new_classes.new_classes()
    have_cls = {c["name"] for c in catalog["classes"]}
    for name, base in nclasses:
        if name not in have_cls:
            catalog["classes"].append({"name": name, "base": base})
    have = {u["name"].lower() for u in units}
    for u in nunits:
        u["origin"] = "added"
        if u["name"].lower() in have:
            raise SystemExit("new unit already in the catalog: " + u["name"])
        units.append(u)
        log("new       %-36s %-26s %.15g" % (u["name"], u["class"], u["factor"]))
    added.extend(nunits)

    # animal unit months (Jeff, 2026-10-07): NDSU Extension R1810 Table 1,
    # plus any further sourced conventions in aum_extra.json beside this file
    import catalog_aum
    import json as _json
    import os as _os
    extra_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "aum_extra.json")
    extra = {}
    if _os.path.exists(extra_path):
        with open(extra_path, encoding="utf-8") as f:
            extra = _json.load(f)
    aum = catalog_aum.aum_units(
        [tuple(e) for e in extra.get("equivalents", [])],
        [tuple(e) for e in extra.get("forage", [])],
        extra.get("days_per_month", 365.0 / 12.0), extra.get("days_source"))
    if catalog_aum.CLASS not in {c["name"] for c in catalog["classes"]}:
        catalog["classes"].append({"name": catalog_aum.CLASS, "base": catalog_aum.BASE})
    for u in aum:
        if u["name"].lower() in {x["name"].lower() for x in units}:
            raise SystemExit("AUM unit already in the catalog: " + u["name"])
        units.append(u)
    added.extend(aum)
    log("aum       %d units in %s" % (len(aum), catalog_aum.CLASS))
    by = {u["name"]: u for u in units}

    # aliases for units already there
    for name, al in ALIASES.items():
        u = by.get(name)
        if u is None:
            raise SystemExit("alias for a unit not in the catalog: " + name)
        u["aliases"] = sorted(set(u.get("aliases", []) + al), key=lambda s: (s.lower(), s))   # a total order: same output every build

    # 5. class names (Jeff, 2026-10-07): last, after everything that refers
    # to the classes by their 2001 names
    for c in catalog["classes"]:
        c["name"] = CLASS_RENAMES.get(c["name"], c["name"])
    for u in units:
        u["class"] = CLASS_RENAMES.get(u["class"], u["class"])

    # 5b. unit names (Jeff, 2026-10-08), the old name kept as an alias
    for renames, keep_alias in ((UNIT_RENAMES, True), (UNIT_RENAMES_NO_ALIAS, False)):
        for old, (plural, singular) in renames.items():
            u = by.get(old)
            if u is None:
                raise SystemExit("rename of a unit not in the catalog: " + old)
            if any(x["name"].lower() == plural.lower() for x in units):
                raise SystemExit("renamed unit already in the catalog: " + plural)
            u["name"], u["singular"] = plural, singular
            if keep_alias:
                u["aliases"] = sorted(set(u.get("aliases", []) + [old]), key=lambda s: (s.lower(), s))
            log("rename    %-24s -> %s" % (old, plural))
    by = {u["name"]: u for u in units}

    # 5c. descriptions replaced from a primary source; catalog_sources
    # credits the description to _desc_sources instead of the 2001 text alone
    for name, (text, desc_sources) in DEFINITION_REPLACEMENTS.items():
        u = by.get(name)
        if u is None:
            raise SystemExit("new description for a unit not in the catalog: " + name)
        u["definition"] = text
        u["_desc_sources"] = list(desc_sources)
        log("describe  %-24s from %s" % (name, ", ".join(desc_sources)))

    # 5d. corrections and OED citations for the 2001 descriptions (Jeff,
    # 2026-10-08; catalog_text_fixes.py)
    import catalog_text_fixes
    catalog_text_fixes.apply(units, log)

    # 3. survey note
    for name in SURVEY_UNITS:
        u = by[name]
        if "retired" not in u["definition"]:
            u["definition"] = u["definition"].rstrip() + SURVEY_NOTE

    # no two units, or a unit and an alias, may share a name
    seen = {}
    for u in units:
        for n in [u["name"]] + u.get("aliases", []):
            k = n.lower()
            if k in seen and seen[k] is not u and k == u["name"].lower():
                raise SystemExit("name clash: %s" % n)
            seen.setdefault(k, u)
    return len(added)
