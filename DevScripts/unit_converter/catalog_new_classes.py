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
"""New measurement classes for the Unit Converter (Jeff, 2026-10-07):
Count per Area, Volume per Area, Mass per Area, Density, Fireline Intensity
and Heat per Area, plus wood volume units for Volume and the fire rate of
spread for Velocity. Every factor is built from exact definitions: the
international foot, the avoirdupois pound, the acre (43,560 square feet), the
cord (128 cubic feet), the board foot (144 cubic inches), the IT British
thermal unit (1055.05585262 joules) and the IT and thermochemical calories.
"""
FT = 0.3048
IN = 0.0254
LB = 0.45359237
ACRE = 43560 * FT * FT                  # 4046.8564224 square meters
HA = 10000.0
SQMI = (5280 * FT) ** 2
CUFT = FT ** 3                          # cubic meters
CUYD = 27 * CUFT
CUIN = IN ** 3
GAL_US = 231 * CUIN
GAL_UK = 0.00454609
BF = 144 * CUIN                         # board foot, cubic meters
CORD = 128 * CUFT
SHORT_TON = 2000 * LB
LONG_TON = 2240 * LB
OZ = LB / 16
BTU_IT = 1055.05585262                  # joules
CAL_IT = 4.1868
CAL_TH = 4.184

DERIVED = (" Derived from exact definitions: the international foot (0.3048 meter), the "
           "avoirdupois pound (0.45359237 kilogram) and the acre (43,560 square feet).")


def _u(name, singular, cls, factor, definition, aliases=()):
    return {"name": name, "singular": singular, "class": cls, "factor": factor,
            "definition": definition, "aliases": list(aliases)}


def new_classes():
    """[(class name, base unit name)], [units]"""
    classes = [
        ("Count per Area", "count per sq meter"),
        ("Volume per Area", "cu meters per hectare"),
        ("Mass per Area", "kilograms per hectare"),
        ("Density", "kilograms per cu meter"),
        ("Fireline Intensity (Fire)", "kilowatts per meter"),
        ("Heat per Area (Fire)", "kilojoules per sq meter"),
    ]
    U = []

    # ---- Count per Area (base: count per square meter)
    C = "Count per Area"
    cnt = (" A count per unit of area: trees, stems, seedlings, animals, nests, plots or anything else "
           "counted. Converting between these units only rescales the area.")
    for name, sing, area_m2, al in (
            ("count per sq meter", "count per sq meter", 1.0, ["per square meter", "per m2"]),
            ("count per sq foot", "count per sq foot", FT * FT, ["per square foot"]),
            ("count per sq yard", "count per sq yard", 9 * FT * FT, ["per square yard"]),
            ("count per acre", "count per acre", ACRE, ["per acre", "trees per acre", "TPA", "stems per acre"]),
            ("count per hectare", "count per hectare", HA, ["per hectare", "trees per hectare", "stems per hectare", "per ha"]),
            ("count per sq kilometer", "count per sq kilometer", 1e6, ["per square kilometer", "per km2", "animals per sq km"]),
            ("count per sq mile", "count per sq mile", SQMI, ["per square mile", "animals per sq mile"]),
            ("count per 1/10-acre plot", "count per 1/10-acre plot", ACRE / 10, ["per tenth acre"]),
            ("count per 1/20-acre plot", "count per 1/20-acre plot", ACRE / 20, ["per twentieth acre"]),
            ("count per 1/50-acre plot", "count per 1/50-acre plot", ACRE / 50, ["per fiftieth acre"]),
            ("count per 1/100-hectare plot", "count per 1/100-hectare plot", HA / 100, ["per 100 sq m plot"])):
        what = name.replace("count per ", "")
        U.append(_u(name, sing, C, 1.0 / area_m2,
                    "A count of things in each %s.%s%s" % (what, cnt, DERIVED if "acre" in name or "foot" in name
                                                            or "yard" in name or "mile" in name else ""), al))

    # ---- Volume per Area (base: cubic meters per hectare)
    C = "Volume per Area"
    vpa = " A volume per unit of area, as for wood volume per acre or per hectare."
    for name, sing, vol_m3, area_m2, al, extra in (
            ("cu meters per hectare", "cu meter per hectare", 1.0, HA, ["m3/ha", "cubic meters per hectare"], ""),
            ("cu feet per acre", "cu foot per acre", CUFT, ACRE, ["ft3/ac", "cubic feet per acre"], DERIVED),
            ("cu yards per acre", "cu yard per acre", CUYD, ACRE, ["cubic yards per acre"], DERIVED),
            ("cu meters per acre", "cu meter per acre", 1.0, ACRE, ["cubic meters per acre"], DERIVED),
            ("board feet per acre", "board foot per acre", BF, ACRE, ["BF/ac", "bd ft per acre"],
             " A board foot is 144 cubic inches (1 foot by 1 foot by 1 inch), a nominal volume; it is not "
             "the board feet a log rule estimates from a log's size." + DERIVED),
            ("MBF per acre", "MBF per acre", 1000 * BF, ACRE, ["thousand board feet per acre", "MBF/ac"],
             " MBF is a thousand board feet, each 144 cubic inches." + DERIVED),
            ("cunits per acre", "cunit per acre", 100 * CUFT, ACRE, ["CCF per acre"],
             " A cunit is 100 cubic feet of solid wood." + DERIVED),
            ("cords per acre", "cord per acre", CORD, ACRE, ["cords/ac"],
             " A cord is a stack of wood 4 by 4 by 8 feet, 128 cubic feet of wood and air." + DERIVED),
            ("steres per hectare", "stere per hectare", 1.0, HA, ["steres/ha"],
             " A stere is one cubic meter of stacked wood.")):
        U.append(_u(name, sing, C, vol_m3 / area_m2 * HA,
                    "%s per %s.%s%s" % (name.split(" per ")[0][:1].upper() + name.split(" per ")[0][1:],
                                        name.split(" per ")[1], vpa, extra), al))

    # ---- Mass per Area (base: kilograms per hectare)
    C = "Mass per Area"
    mpa = " A mass per unit of area, as for fuel loading, biomass, forage or yield."
    for name, sing, kg, area_m2, al, extra in (
            ("kilograms per hectare", "kilogram per hectare", 1.0, HA, ["kg/ha"], ""),
            ("tonnes per hectare", "tonne per hectare", 1000.0, HA, ["Mg/ha", "metric tons per hectare", "t/ha"],
             " A tonne (metric ton, megagram) is 1,000 kilograms."),
            ("kilograms per sq meter", "kilogram per sq meter", 1.0, 1.0, ["kg/m2"], ""),
            ("grams per sq meter", "gram per sq meter", 0.001, 1.0, ["g/m2"], ""),
            ("pounds per acre", "pound per acre", LB, ACRE, ["lb/ac", "lbs per acre"], DERIVED),
            ("tons per acre", "ton per acre", SHORT_TON, ACRE, ["short tons per acre", "t/ac"],
             " A ton here is the short ton of 2,000 pounds." + DERIVED),
            ("long tons per acre", "long ton per acre", LONG_TON, ACRE, [],
             " A long ton is 2,240 pounds." + DERIVED),
            ("pounds per sq foot", "pound per sq foot", LB, FT * FT, ["lb/ft2"], DERIVED),
            ("ounces per sq yard", "ounce per sq yard", OZ, 9 * FT * FT, ["oz/yd2"],
             " An ounce here is the avoirdupois ounce, one sixteenth of a pound." + DERIVED)):
        U.append(_u(name, sing, C, kg / area_m2 * HA,
                    "%s per %s.%s%s" % (name.split(" per ")[0][:1].upper() + name.split(" per ")[0][1:],
                                        name.split(" per ")[1], mpa, extra), al))

    # ---- Density (base: kilograms per cubic meter)
    C = "Density"
    den = " A mass per unit of volume (density), as for wood, soil, water or snow."
    for name, sing, kg, m3, al, extra in (
            ("kilograms per cu meter", "kilogram per cu meter", 1.0, 1.0, ["kg/m3"], ""),
            ("grams per cu centimeter", "gram per cu centimeter", 0.001, 1e-6, ["g/cm3", "g/cc", "grams per cc"],
             " Water at about 4 degrees Celsius has a density close to 1 gram per cubic centimeter."),
            ("grams per liter", "gram per liter", 0.001, 0.001, ["g/L"], ""),
            ("kilograms per liter", "kilogram per liter", 1.0, 0.001, ["kg/L"], ""),
            ("tonnes per cu meter", "tonne per cu meter", 1000.0, 1.0, ["t/m3", "Mg/m3"], ""),
            ("pounds per cu foot", "pound per cu foot", LB, CUFT, ["lb/ft3", "pcf"], DERIVED),
            ("pounds per cu inch", "pound per cu inch", LB, CUIN, ["lb/in3"], DERIVED),
            ("pounds per cu yard", "pound per cu yard", LB, CUYD, ["lb/yd3"], DERIVED),
            ("pounds per gallon (US)", "pound per gallon (US)", LB, GAL_US, ["lb/gal"],
             " The U.S. gallon is 231 cubic inches." + DERIVED),
            ("pounds per gallon (Brit)", "pound per gallon (Brit)", LB, GAL_UK, [],
             " The imperial gallon is 4.54609 liters." + DERIVED),
            ("ounces per cu inch", "ounce per cu inch", OZ, CUIN, ["oz/in3"], DERIVED),
            ("tons per cu yard", "ton per cu yard", SHORT_TON, CUYD, ["short tons per cubic yard"],
             " A ton here is the short ton of 2,000 pounds." + DERIVED)):
        U.append(_u(name, sing, C, kg / m3,
                    "%s per %s.%s%s" % (name.split(" per ")[0][:1].upper() + name.split(" per ")[0][1:],
                                        name.split(" per ")[1], den, extra), al))

    # ---- Fireline Intensity (base: kilowatts per meter of fire front)
    C = "Fireline Intensity (Fire)"
    fli = (" A fireline intensity: the rate of heat released per unit length of the fire front (Byram's "
           "fireline intensity), an energy per unit time per unit length.")
    for name, sing, joules, per_s, m, al in (
            ("kilowatts per meter", "kilowatt per meter", 1000.0, 1.0, 1.0, ["kW/m"]),
            ("watts per meter", "watt per meter", 1.0, 1.0, 1.0, ["W/m"]),
            ("megawatts per meter", "megawatt per meter", 1e6, 1.0, 1.0, ["MW/m"]),
            ("BTU per foot per second", "BTU per foot per second", BTU_IT, 1.0, FT, ["BTU/ft/s", "Btu/ft/sec"]),
            ("BTU per foot per minute", "BTU per foot per minute", BTU_IT, 60.0, FT, ["BTU/ft/min"]),
            ("kilocalories per meter per second", "kilocalorie per meter per second", 1000 * CAL_IT, 1.0, 1.0, ["kcal/m/s"])):
        extra = ""
        if "BTU" in name:
            extra = " The BTU here is the International Table British thermal unit, 1055.05585262 joules."
        if "kilocal" in name:
            extra = " The kilocalorie here is the International Table kilocalorie, 4,186.8 joules."
        U.append(_u(name, sing, C, joules / per_s / m / 1000.0,
                    "%s.%s%s" % (name[:1].upper() + name[1:], fli, extra), al))

    # ---- Heat per Area (base: kilojoules per square meter)
    C = "Heat per Area (Fire)"
    hpa = " Heat per unit of area, as for the heat released per unit area by a fire, or radiant energy received."
    for name, sing, joules, m2, al, extra in (
            ("kilojoules per sq meter", "kilojoule per sq meter", 1000.0, 1.0, ["kJ/m2"], ""),
            ("joules per sq meter", "joule per sq meter", 1.0, 1.0, ["J/m2"], ""),
            ("megajoules per sq meter", "megajoule per sq meter", 1e6, 1.0, ["MJ/m2"], ""),
            ("BTU per sq foot", "BTU per sq foot", BTU_IT, FT * FT, ["BTU/ft2"],
             " The BTU here is the International Table British thermal unit, 1055.05585262 joules."),
            ("calories per sq centimeter", "calorie per sq centimeter", CAL_IT, 1e-4, ["cal/cm2"],
             " The calorie here is the International Table calorie, 4.1868 joules."),
            ("kilocalories per sq meter", "kilocalorie per sq meter", 1000 * CAL_IT, 1.0, ["kcal/m2"],
             " The kilocalorie here is the International Table kilocalorie, 4,186.8 joules."),
            ("langleys", "langley", CAL_TH, 1e-4, ["langley", "Ly"],
             " A langley is one thermochemical calorie (4.184 joules) per square centimeter, a unit long used for "
             "solar radiation.")):
        U.append(_u(name, sing, C, joules / m2 / 1000.0,
                    "%s.%s%s" % (name[:1].upper() + name[1:], hpa, extra), al))

    # ---- additions to existing classes
    U.append(_u("cunits", "cunit", "Volume", 100 * CUFT * 1000, (
        "A cunit is 100 cubic feet of solid wood, a unit of timber volume." + DERIVED), ["CCF", "hundred cubic feet"]))
    U.append(_u("MBF (thousand board feet)", "MBF (thousand board feet)", "Volume", 1000 * BF * 1000, (
        "MBF is a thousand board feet, each board foot 144 cubic inches (1 foot by 1 foot by 1 inch), a nominal "
        "volume of sawn wood. Not the board feet a log rule (Scribner, Doyle, International 1/4-inch) estimates "
        "from a log's size, which depend on the log." + DERIVED), ["MBF", "thousand board feet", "Mbf"]))
    U.append(_u("MMBF (million board feet)", "MMBF (million board feet)", "Volume", 1e6 * BF * 1000, (
        "MMBF is a million board feet, each board foot 144 cubic inches." + DERIVED), ["MMBF", "million board feet"]))
    U.append(_u("chains/hr", "chain/hr", "Velocity", 66 * FT / 1000.0, (
        "Chains per hour: a speed of one surveyor's chain (66 feet) each hour, the usual unit for the rate of "
        "spread of a wildland fire in the United States." + DERIVED.replace(", the avoirdupois pound (0.45359237 kilogram) and the acre (43,560 square feet)", "")),
        ["ch/h", "chains per hour", "rate of spread"]))
    U.append(_u("chains/min", "chain/min", "Velocity", 66 * FT * 60 / 1000.0, (
        "Chains per minute: a speed of one surveyor's chain (66 feet) each minute." +
        DERIVED.replace(", the avoirdupois pound (0.45359237 kilogram) and the acre (43,560 square feet)", "")),
        ["ch/min", "chains per minute"]))
    return classes, U
