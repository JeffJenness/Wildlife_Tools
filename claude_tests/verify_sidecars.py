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

"""Suite-wide check of all tool sidecars against the house style:
summary/usage must be bare text + inline b/i only (summary may be
<para>-wrapped, and MUST be when styled), no leading whitespace on
lines (except bullet lines), and no ALL-CAPS shouting in summary,
usage, or parameter hovers. Rebuilt 2026-08-26 (hardened version);
now lives permanently in claude_tests.
"""
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
TB = os.path.join(os.path.dirname(HERE), "WildlifeTools",
                  "Toolboxes")

# genuine acronyms / uppercase literals allowed anywhere
OK_CAPS = set("""
NODATA DEM DEMS GIS USGS VAT CTI TWI TPI TRI VRM ACR LSRI MDOW HSM
HSMS MCP LOCOH LAEA UTM ASCII TIFF GEOTIFF CSV OID FID OBJECTID MEAN
STD SQL RAM KDE PPS D8 DINF DBC TPM SD CI CIS FPC XML JSON URL DBF
GDB SDE II III IV NAD WGS TRUE FALSE AND OR NOT XTH RGB PDF HTML N/A
OK USA EDT SCA SRTM LIDAR ID IDS X Y Z XY
ESRI GDAL GUID CRAN IEEE SIAM MSHC UOGML ECEF CATMOG AEQD RASTERVALU
DATA DEGREE DOUBLE COUNT MIN MAX RANGE SUM VARIANCE MEDIAN NULL UNION
ACR-L SAPR-L VRM-L X/Y/Z GRID ISBN GRIDCODE NONE EIGHT FOUR
SODA ACM CGAL QGIS GRAPP SFCGAL
AC1 GPS CEP WHR USFS RMRS FCE
""".split())

B64 = re.compile(r"data:image/[^\"'\s]*")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_'/-]*")


def shouts(text):
    """Runs of >=3 consecutive all-caps words (len>=2) not
    whitelisted."""
    out = []
    run = []
    for m in WORD.finditer(B64.sub(" ", text)):
        w = m.group(0)
        if (w.isupper() and len(w) >= 2 and "_" not in w
                and w.upper().strip("'-/") not in OK_CAPS):
            run.append(w)
        else:
            if len(run) >= 3:
                out.append(" ".join(run))
            run = []
    if len(run) >= 3:
        out.append(" ".join(run))
    return out


def single_shouts(text):
    """Lone all-caps prose words (len>=4, no digits/underscores) not
    whitelisted -- e.g. FIELDS, BOUNDARIES, EXACTLY."""
    out = []
    for m in WORD.finditer(B64.sub(" ", text)):
        w = m.group(0)
        if (w.isupper() and len(w) >= 4 and "_" not in w
                and not any(ch.isdigit() for ch in w)
                and w.strip("'-/") not in OK_CAPS
                and w not in OK_CAPS):
            out.append(w)
    return out


def flat_text(el):
    return "".join(el.itertext())


def prose_text(el):
    """itertext, skipping <code> subtrees (script samples hold
    legitimate uppercase option strings and literals)."""
    parts = []

    def walk(e):
        if e.tag == "code":
            return
        if e.text:
            parts.append(e.text)
        for c in e:
            walk(c)
            if c.tail:
                parts.append(c.tail)

    walk(el)
    return "".join(parts)


problems = []
n = 0
for p in sorted(glob.glob(os.path.join(
        TB, "JennessEnterprisesTools.*.pyt.xml"))):
    n += 1
    name = os.path.basename(p)
    try:
        t = ET.parse(p)
    except ET.ParseError as e:
        problems.append((name, "XML PARSE ERROR: %s" % e))
        continue
    for tag in ("summary", "usage"):
        el = t.find(".//" + tag)
        if el is None:
            problems.append((name, "missing <%s>" % tag))
            continue
        # 2026-10-09: a summary is ONE <para> that starts with the
        # "View online help files" link and a <br/><br/> (the GP-pane
        # hover keeps only the summary's first child node), and Pro's
        # own round-trip re-indents the text after the link
        allowed = ("b", "i", "para", "a", "br") if tag == "summary" \
            else ("b", "i")
        if tag == "summary":
            first = el[0] if len(el) else None
            if (len(el) != 1 or first.tag != "para"
                    or not (first.text or "").lstrip().startswith(
                        "View online help files:")
                    or first.find("br") is None):
                problems.append((name, "summary must be ONE <para> "
                                       "starting with the help link "
                                       "and a <br/><br/>"))
            for c in el.iter():
                if c.tag in ("a", "br"):
                    c.tail = (c.tail or "").lstrip()
        bad = sorted({c.tag for c in el.iter()
                      if c is not el and c.tag not in allowed})
        if bad:
            problems.append((name, "%s has structural tags: %s"
                             % (tag, bad)))
        if tag == "summary":
            styled = any(c.tag in ("b", "i") for c in el.iter()
                         if c is not el)
            wrapped = all(c.tag == "para" for c in el) \
                and (el.text or "").strip() == "" and len(el)
            if styled and not wrapped:
                problems.append((name, "summary has inline b/i but "
                                       "is not <para>-wrapped -- "
                                       "the hover will truncate "
                                       "it"))
        txt = flat_text(el)
        for ln in txt.split("\n"):
            if ln and ln[0] in " \t" \
                    and not ln.startswith("  •  "):
                problems.append((name, "%s line starts with "
                                       "whitespace: %r"
                                 % (tag, ln[:60])))
                break
        sh = shouts(txt)
        if sh:
            problems.append((name, "%s shouting: %s"
                             % (tag, "; ".join(sh[:4]))))
        ss = single_shouts(prose_text(el))
        if ss:
            problems.append((name, "%s caps shouting: %s"
                             % (tag, "; ".join(sorted(set(ss))[:6]))))
    for pm in t.iter("param"):
        dr = pm.find("dialogReference")
        if dr is None:
            continue
        txt = prose_text(dr)
        ss = single_shouts(txt) + shouts(txt)
        if ss:
            problems.append((name, "hover (%s) caps shouting: %s"
                             % (pm.get("name", "?"),
                                "; ".join(sorted(set(ss))[:6]))))

print("checked", n, "sidecars")
if problems:
    for name, msg in problems:
        print("PROBLEM", name, "--", msg)
    sys.exit(1)
print("ALL CLEAN")
