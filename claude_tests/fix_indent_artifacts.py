# -*- coding: utf-8 -*-
"""Collapse arcpy's sidecar re-flow artifacts: ImportToolbox
re-syncs .pyt.xml sidecars and wraps long text nodes onto
tab-indented continuation lines, which render as hard breaks in the
help. This joins those continuation lines back (never touching
bullet lines or XML structure) and validates every file still
parses. Rebuilt 2026-08-26 after the scratchpad copy was cleaned;
now lives permanently in claude_tests.
"""
import glob
import io
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
TB = os.path.join(os.path.dirname(HERE), "WildlifeTools",
                  "Toolboxes")

# a continuation line: newline + indentation that is NOT a bullet
# line (optionally indented "•") and NOT XML structure ("<")
_ART = re.compile(r"(\r?\n)[\t ]+(?![\t ]*(?:•|<))")
# script examples keep their own indentation -- never join inside
_CODE = re.compile(r"(<code>.*?</code>)", re.S)


def _fix_seg(seg):
    # sentinel "<" so a segment ENDING in newline+indent (right
    # before a <code> block in the next segment) still sees the tag
    # in the lookahead and is left alone
    return _ART.sub(" ", seg + "<")[:-1]


def _join_outside_code(raw):
    parts = _CODE.split(raw)
    return "".join(seg if seg.startswith("<code>")
                   else _fix_seg(seg) for seg in parts)


fixed = []
for p in sorted(glob.glob(os.path.join(
        TB, "JennessEnterprisesTools.*.pyt.xml"))):
    # newline="" preserves the file's own line endings end to end
    raw = io.open(p, encoding="utf-8", newline="").read()
    new = _join_outside_code(raw)
    if new != raw:
        try:
            ET.fromstring(new.encode("utf-8"))
        except ET.ParseError as e:
            print("SKIP (would break parse):",
                  os.path.basename(p), e)
            continue
        io.open(p, "w", encoding="utf-8", newline="").write(new)
        fixed.append(os.path.basename(p))
print("fixed {} files".format(len(fixed)))
for f in fixed:
    print("  " + f)
bad = 0
for p in sorted(glob.glob(os.path.join(
        TB, "JennessEnterprisesTools.*.pyt.xml"))):
    try:
        ET.parse(p)
    except ET.ParseError as e:
        bad += 1
        print("PARSE ERROR:", os.path.basename(p), e)
print("all parse OK" if bad == 0 else "{} parse errors".format(bad))
sys.exit(1 if bad else 0)
