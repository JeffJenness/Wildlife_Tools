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
"""Corrections and Oxford English Dictionary citations for the 2001
descriptions (Jeff, 2026-10-08): "Here are some PDFs for terms I was able to
find in the OED. Go ahead and check them and provide OED citations where
appropriate. If I have the definition wrong then correct it. Also go ahead
and fix all other issues with the citations that you described earlier."

The OED entries were read in the PDFs Jeff saved from oed.com on 8 October
2026 (Reference_Docs/Temp2, text OCRed). Every sentence an OED citation is
attached to was compared with its entry; a sentence the entry contradicts is
corrected to the entry. Other corrections cite what they rest on.

Run by catalog_revisions.apply after the renames; catalog_sources merges the
keys collected in u["_desc_add"] into the unit's descriptionSources, before
the credit to the original Unit Converter.
"""
import re

CONSULTED = "consulted 8 October 2026"

# the OED entries Jeff saved: (citation key suffix, headword as the OED prints
# it, entry link)
OED_ENTRIES = [
    ("acre", "acre, n.", "https://www.oed.com/dictionary/acre_n"),
    ("acre_length", "acre length, n.", "https://www.oed.com/dictionary/acre-length_n"),
    ("angstrom", "angstrom, n.", "https://www.oed.com/dictionary/angstrom_n"),
    ("apothecary", "apothecary, n.", "https://www.oed.com/dictionary/apothecary_n"),
    ("avoirdupois", "avoirdupois, n.", "https://www.oed.com/dictionary/avoirdupois_n"),
    ("barley_corn", "barley-corn, n.", "https://www.oed.com/dictionary/barley-corn_n"),
    ("bushel", "bushel, n.¹", "https://www.oed.com/dictionary/bushel_n1"),
    ("carat", "carat, n.", "https://www.oed.com/dictionary/carat_n"),
    ("chaldron", "chaldron, n.", "https://www.oed.com/dictionary/chaldron_n"),
    ("cubit", "cubit, n.", "https://www.oed.com/dictionary/cubit_n"),
    ("dalton", "dalton, n.²", "https://www.oed.com/dictionary/dalton_n2"),
    ("ell", "ell, n.¹", "https://www.oed.com/dictionary/ell_n1"),
    ("fathom", "fathom, n.", "https://www.oed.com/dictionary/fathom_n"),
    ("fermi", "Fermi, n.", "https://www.oed.com/dictionary/fermi_n"),
    ("firkin", "firkin, n.", "https://www.oed.com/dictionary/firkin_n"),
    ("foot", "foot, n. & int.", "https://www.oed.com/dictionary/foot_n"),
    ("furlong", "furlong, n.", "https://www.oed.com/dictionary/furlong_n"),
    ("gallon", "gallon, n.", "https://www.oed.com/dictionary/gallon_n"),
    ("grade", "grade, n.", "https://www.oed.com/dictionary/grade_n"),
    ("grain", "grain, n.¹", "https://www.oed.com/dictionary/grain_n1"),
    ("hogshead", "hogshead, n.", "https://www.oed.com/dictionary/hogshead_n"),
    ("horsepower", "horsepower, n.", "https://www.oed.com/dictionary/horsepower_n"),
    ("hundredweight", "hundredweight, n.", "https://www.oed.com/dictionary/hundredweight_n"),
    ("kelvin", "Kelvin, n. & adj.", "https://www.oed.com/dictionary/kelvin_n"),
    ("last", "last, n.²", "https://www.oed.com/dictionary/last_n2"),
    ("league", "league, n.¹", "https://www.oed.com/dictionary/league_n1"),
    ("litre", "litre | liter, n.²", "https://www.oed.com/dictionary/litre_n2"),
    ("mile", "mile, n.¹", "https://www.oed.com/dictionary/mile_n1"),
    ("ounce", "ounce, n.¹", "https://www.oed.com/dictionary/ounce_n1"),
    ("parsec", "parsec, n.", "https://www.oed.com/dictionary/parsec_n"),
    ("pascal", "Pascal, n.", "https://www.oed.com/dictionary/pascal_n"),
    ("pole", "pole, n.¹", "https://www.oed.com/dictionary/pole_n1"),
    ("pound", "pound, n.¹", "https://www.oed.com/dictionary/pound_n1"),
    ("puncheon", "puncheon, n.²", "https://www.oed.com/dictionary/puncheon_n2"),
    ("reaumur", "Réaumur, n. & adj.", "https://www.oed.com/dictionary/reaumur_n"),
    ("rod", "rod, n.¹", "https://www.oed.com/dictionary/rod_n1"),
    ("shekel", "shekel, n.", "https://www.oed.com/dictionary/shekel_n"),
    ("ton", "ton, n.¹", "https://www.oed.com/dictionary/ton_n1"),
    ("troy", "troy, n.²", "https://www.oed.com/dictionary/troy_n2"),
    ("tun", "tun, n.¹", "https://www.oed.com/dictionary/tun_n1"),
    # the second set Jeff saved (Reference_Docs/Temp3, 8 October 2026)
    ("bar", "bar, n.⁶", "https://www.oed.com/dictionary/bar_n6"),
    ("calorie", "calorie, n.", "https://www.oed.com/dictionary/calorie_n"),
    ("celsius", "Celsius, n. & adj.", "https://www.oed.com/dictionary/celsius_n"),
    ("cord", "cord, n.¹", "https://www.oed.com/dictionary/cord_n1"),
    ("fahrenheit", "Fahrenheit, n. & adj.", "https://www.oed.com/dictionary/fahrenheit_n"),
    ("oz", "oz, n.¹", "https://www.oed.com/dictionary/oz_n1"),
    ("sign", "sign, n.", "https://www.oed.com/dictionary/sign_n"),
]

CITATIONS = {}
for _k, _head, _url in OED_ENTRIES:
    CITATIONS["oed_" + _k] = {
        "short": "OED, %s" % _head,
        "full": "Oxford English Dictionary. %s Oxford University Press. Online edition, %s." % (_head, CONSULTED),
        "url": _url,
    }
CITATIONS.update({
    # (Fahrenheit and Celsius first rested on the 1911 Encyclopaedia
    # Britannica; the OED entries Jeff saved later replace it, and give
    # Fahrenheit as Daniel Gabriel, where the 1911 article has Gabriel Daniel)
    "agh_ramsden": {
        "short": "Alberta Geomatics Historical Society, Chain, Engineer's",
        "full": ("Alberta Geomatics Historical Society. Chain, Engineer's. Web page: the engineer's chain, also "
                 "called the Ramsden chain, of 100 one-foot links, made by Jesse Ramsden in 1784."),
        "url": "https://albertalandsurveyhistory.ca/?p=2804",
    },
})

# ---------------------------------------------------------------- 1. typing slips
TYPOS = [
    (r"\bus a unit of power\b", "is a unit of power"),            # thrust horsepower
    (r"\ba an area\b", "an area"),                                # acre
    (r"\bmathmetician\b", "mathematician"),                       # pascal
    (r"\bmathemetician\b", "mathematician"),                      # Kelvin (replaced below anyway)
    (r"\bIt's use\b", "Its use"),                                 # chaldron
    (r"\boccuring\b", "occurring"),
    (r"\bdivisable\b", "divisible"),
    (r"\bcalender\b", "calendar"),
    (r"\btwelth\b", "twelfth"),
    (r"\bApothecarie measures\b", "Apothecaries' measures"),
    (r"\bapothecarie's\b", "apothecaries'"),
    (r"\bscottish dray\b", "Scottish dray"),
    (r"\bGerman Physicist\b", "German physicist"),
    (r"\bAndres Celsius\b", "Anders Celsius"),                    # Encyclopaedia Britannica 1911
    (r"\bAn Ramden's\b", "A Ramsden's"),
    (r"\bRamden's\b", "Ramsden's"),
    (r"\(Ramden\)", "(Ramsden)"),
]

# ---------------------------------------------------------------- 2. corrections
# (regex on the definition, replacement, keys of the sources it rests on)
SENTENCE_FIXES = [
    # cut off in the 2001 text: the mile sentence ran into the Circular Area note
    (r'The word "mile" probably descended from the\s+Conversions in the',
     'The word "mile" probably descended from the Roman word describing the distance equivalent to 1,000 '
     'paces.  Conversions in the', ["oed_mile"]),
    (r"distance equivalent to\s+paces\.", "distance equivalent to 1,000 paces.", ["oed_mile"]),
    (r"An anomalistic century is a unit of time defined as 1,000 anomalistic years\.",
     "An anomalistic millennium is a unit of time defined as 1,000 anomalistic years.", []),
    (r"Gabriel Daniel Fahrenheit \(1638-1736\)", "Daniel Gabriel Fahrenheit (1686-1736)", ["oed_fahrenheit"]),
    (r"(Anders Celsius \(1701-1744\)\.)",
     r"\1  Celsius's own scale ran the other way, with 0 at the boiling point of water and 100 at the freezing "
     r"point; it was soon turned around.", ["oed_celsius"]),
    (r"(The term 'bar' is short for 'barometer\.')",
     r"\1  The meteorologist Vilhelm Bjerknes, who coined the name and first used it in 1906, chose it as the "
     r"shortest name derived from that word.", ["oed_bar"]),
    (r"The abbreviation of 'ounce' is 'oz', which derives from the Italian word 'onza', referring to the name of "
     r"a Spanish and Sicilian coin\.",
     "The abbreviation of 'ounce' is 'oz', from oz, the 15th-century Italian abbreviation of onza, ounce; the "
     "ounce was also the name of Spanish and Sicilian coins.", ["oed_oz", "oed_ounce"]),
    (r"The current working definition of the Celsius scale, however, was set by the International Committee on "
     r"Weights and Measures in 1954, and based the scale on the triple point of water \(the temperature at which "
     r"water can exist in a solid, liquid and gaseous state\)\.\s+This triple point of water is equal to 0 "
     r"degrees Celsius\.",
     "In 1954 the General Conference on Weights and Measures based the scale, through the kelvin, on the triple "
     "point of water (the temperature at which water can exist as a solid, a liquid and a gas), set at 273.16 "
     "kelvins, or 0.01 degrees Celsius.  Since 2019 the kelvin, and with it the Celsius scale, has been defined "
     "by a fixed value of the Boltzmann constant.", ["bipm_si", "oed_celsius", "oed_kelvin"]),
    (r"The Kelvin temperature scale was developed by the British mathematician and physicist 1st Baron William "
     r"Thomas Kelvin \(1824-1907\)\.",
     "The Kelvin temperature scale was developed by the Scottish physicist William Thomson, Baron Kelvin "
     "(1824-1907), who described it in 1848.", ["oed_kelvin"]),
    (r'"Tun" comes from the French word "tunnerre", for "thunder", reflecting the rumbling sound the barrel made '
     r'when it was rolled\.',
     "The word tun is Old English (tunne), recorded as early as about 725; its origin is uncertain.", ["oed_tun"]),
    (r"A gallon is a unit of volume derived from the 13th Century Medieval Latin term Galeta pail\.",
     "A gallon is a unit of volume.  The word comes from the Old Northern French galon, related to the medieval "
     "Latin galleta, a measure for wine.", ["oed_gallon"]),
    (r'A firkin is a unit of volume originating in the 14th century, probably from the middle Dutch word '
     r'"veerdel," meaning a "fourth\."',
     'A firkin is a unit of volume first recorded in the 15th century, apparently from a Middle Dutch diminutive '
     'of vierde, "fourth."', ["oed_firkin"]),
    (r"Up until the early 1800s a grade was equal to a degree\.",
     "In the 16th century a grade was another name for a degree; the grade as one hundredth of a right angle "
     "dates from the early 1800s.", ["oed_grade"]),
    (r"The term originated in the 14th century and is used currently as a container to pack tobacco in\.",
     "The term dates from the 14th century.  Hogsheads were also used for dry goods such as tobacco and sugar, "
     "a use now chiefly historical.", ["oed_hogshead"]),
    (r"This length is derived from the length of outstretched arms, and originated with sailors measuring water "
     r"depth with ropes\.",
     "The word is Old English, for the outstretched arms, and the fathom is the length they cover; it is now "
     "used chiefly for the depth of water.", ["oed_fathom"]),
    (r"It is equivalent to the British Winchester bushel, which was used in England between the 15th Century and "
     r"1824\.",
     "It is equivalent to the Winchester bushel of 2,150.42 cubic inches, much used in England from the time of "
     "Henry VIII until the imperial bushel was legally established in 1826.", ["oed_bushel"]),
    (r"It was used in England from the 15th Century until 1824\.",
     "It was much used in England from the time of Henry VIII until the imperial bushel was legally established "
     "in 1826.", ["oed_bushel"]),
    (r"A pole \(or perch, rod or lug\), is an Old English unit of length equal to 16\.5 feet\.",
     "A pole (or perch, rod or lug) is an old English unit of length for land, equal to 16.5 feet, and now "
     "historical.", ["oed_pole", "oed_rod"]),
    (r"A lug \(or perch, rod or pole\), is an Old English unit of length equal to 16\.5 feet\.",
     "A lug (or perch, rod or pole) is an old English unit of length for land, equal to 16.5 feet, and now "
     "historical.", ["oed_pole", "oed_rod"]),
    (r"A rood is a Dutch unit of area equal to 40 square rods",
     "A rood is an old English unit of area equal to 40 square rods", ["oed_rod"]),
    # the avoirdupois sentence tells of the system; the OED gives the word
    (r"(The avoirdupois system of weights probably originated during the Medieval trade fairs held in the former "
     r"French province of Champagne, in the 12th and 13th centuries, and is still in use today in places that "
     r"haven't switched to the metric system\.)",
     r"\1  The name comes from the Anglo-Norman and Old French avoir de pois (pois, weight), which in English "
     r"first meant merchandise sold by weight.", ["oed_avoirdupois"]),
    (r"Ramsden's", "Ramsden's", ["agh_ramsden"]),
]

# ---------------------------------------------------------------- 3. confirmed by the OED
# (regex on the definition, citation keys): the sentence matched agrees with
# the entry, which is cited for the unit's description
OED_SUPPORT = [
    (r"yoke of oxen could plough in a day", ["oed_acre"]),
    (r"derived from an early English definition of an acre", ["oed_acre", "oed_acre_length"]),
    (r"named for Swedish scientist Anders Jonas", ["oed_angstrom"]),
    (r"Apothecaries' measures originated", ["oed_apothecary"]),
    (r"barley corn is an archaic measure of length", ["oed_barley_corn"]),
    (r"carat weight was originally based on the weight of grains or leguminous seeds", ["oed_carat"]),
    (r"chaldron is a unit of volume equal to 3[26] British bushels", ["oed_chaldron"]),
    (r"cubit is an ancient unit of length", ["oed_cubit"]),
    (r"Dalton was named after John Dalton", ["oed_dalton"]),
    (r"ell is a Medieval European unit of length, equal to 45 inches", ["oed_ell"]),
    (r"fathom is a unit of length commonly used to measure water or mine depth", ["oed_fathom"]),
    (r"named for the Italian-born American physicist Enrico Fermi", ["oed_fermi"]),
    (r"A foot is a unit of length, originally based on the length of the human foot", ["oed_foot"]),
    (r"furrow-long", ["oed_furlong"]),
    (r"weight of the ancient grain varied", ["oed_grain"]),
    (r"lift 33,000 pounds a distance of one foot in one minute", ["oed_horsepower"]),
    (r"hundredweight is a unit of weight dating back to the 16th century", ["oed_hundredweight"]),
    (r"The term 'last' probably derived", ["oed_last"]),
    (r"(statute|land) league \(or (land|statute) league\) is a unit of length equal to 3 miles", ["oed_league"]),
    (r"Physics Bull\. Feb\. 58/2, 1969", ["oed_litre"]),
    (r'The word "mile" probably descended from the Roman word describing the distance equivalent to 1,000 paces',
     ["oed_mile"]),
    (r"abbreviation of 'ounce' is 'oz'", ["oed_ounce"]),
    (r"parsec is derived by combining the words parallax and second", ["oed_parsec"]),
    (r"named after French mathematician and physicist Blaise Pascal \(1623-1662\)", ["oed_pascal"]),
    (r"The abbreviation \(lb\) descends from the Roman weight", ["oed_pound"]),
    (r"puncheon is a unit of volume probably originating around the 15th century", ["oed_puncheon"]),
    (r"Antoine Ferchault de Réaumur \(1683-1757\)", ["oed_reaumur"]),
    (r"Hebrew shekel is a unit of weight", ["oed_shekel"]),
    (r'The word "ton" derives from the word "tun"', ["oed_ton"]),
    (r"Troy measurements probably descend from Medieval trade fairs held in the French town of Troyes",
     ["oed_troy"]),
    (r"1706 reference in the Oxford English Dictionary \(under 'pole'\)", ["oed_pole"]),
    (r"A wine gallon is a British term", ["oed_gallon"]),
    (r"A corn gallon is a British term", ["oed_gallon"]),
    # the second set (Temp3)
    (r"The term 'centigrade' is rarely used these days in favor of 'Celsius'", ["oed_celsius"]),
    (r"The term 'cord' is probably derived from an early way to measure", ["oed_cord"]),
    (r"amount of heat required to raise the temperature of one gram of water", ["oed_calorie"]),
    (r"kilogram calorie \(or large calorie\)", ["oed_calorie"]),
    (r"probably derived from the twelve signs of the zodiac", ["oed_sign"]),
]


def _add(u, keys):
    have = u.setdefault("_desc_add", [])
    for k in keys:
        if k not in have:
            have.append(k)


def apply(units, log):
    """Typing slips, corrections and OED citations, in that order. A pattern
    that matches no unit stops the build: the text it was written for has
    changed, and the fix must be looked at again."""
    for pat, rep in TYPOS:
        rx, n = re.compile(pat), 0
        for u in units:
            new, k = rx.subn(rep, u["definition"])
            if k:
                u["definition"], n = new, n + 1
        log("typo      %-34s %3d units" % (pat[:34], n))
    for pat, rep, keys in SENTENCE_FIXES:
        rx, n = re.compile(pat), 0
        for u in units:
            new, k = rx.subn(rep, u["definition"])
            if k:
                u["definition"], n = new, n + 1
                _add(u, keys)
        if n == 0:
            raise SystemExit("text fix matched no definition: " + pat[:80])
        log("correct   %-34s %3d units  %s" % (pat[:34], n, ", ".join(keys)))
    for pat, keys in OED_SUPPORT:
        rx, n = re.compile(pat), 0
        for u in units:
            if rx.search(u["definition"]):
                _add(u, keys)
                n += 1
        if n == 0:
            raise SystemExit("OED citation matched no definition: " + pat[:80])
        log("oed       %-34s %3d units  %s" % (pat[:34], n, ", ".join(keys)))
