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
"""Unit Converter catalog and the Convert Field Units engine.

The same catalog as the Unit Converter window (C#, UnitConverter/UnitCatalog.cs):
the bundled units.json beside this file plus the user's own classes, units and
saved conversions in
    %LOCALAPPDATA%\\JennessEnterprises\\WildlifeTools\\unit_converter\\my_units.json

Every unit converts to its measurement class's base unit and back:
    linear and affine units   base = x * factor + offset
    circle diameters          base = pi / 4 * (x * factor) ** 2   (an area)
    percent slope             base = atan(x * factor / 100), in degrees
A user unit is defined against any unit of its class ("1 rod = 16.5 feet")
and converts through it.
"""
import io
import json
import math
import os

import arcpy

_HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED_PATH = os.path.join(_HERE, "unit_converter", "units.json")
NULL_STANDIN = -999.0


def user_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "JennessEnterprises", "WildlifeTools", "unit_converter", "my_units.json")


class Unit(object):
    __slots__ = ("name", "singular", "cls", "factor", "offset", "transform",
                 "definition", "ref", "k", "o", "is_user", "aliases", "sources", "desc_sources")

    def __init__(self, name, singular, cls, factor=1.0, offset=0.0, transform=None,
                 definition="", ref=None, k=1.0, o=0.0, is_user=False, aliases=()):
        self.name, self.singular, self.cls = name, singular or name, cls
        self.aliases = list(aliases or ())
        self.sources = []
        self.desc_sources = []      # sources of the description, when not those of the value
        self.factor, self.offset, self.transform = float(factor), float(offset), transform
        self.definition = definition or ""
        self.ref, self.k, self.o, self.is_user = ref, float(k), float(o), is_user

    @property
    def nonlinear(self):
        return self.ref.nonlinear if self.ref is not None else self.transform is not None

    def to_base(self, x):
        if self.ref is not None:
            return self.ref.to_base(x * self.k + self.o)
        if self.transform == "circle_diameter":
            d = x * self.factor
            return math.pi / 4.0 * d * d
        if self.transform == "slope_percent":
            return math.degrees(math.atan(x * self.factor / 100.0))
        return x * self.factor + self.offset

    def from_base(self, b):
        if self.ref is not None:
            return (self.ref.from_base(b) - self.o) / self.k
        if self.transform == "circle_diameter":
            if b < 0:
                return float("nan")
            return math.sqrt(4.0 * b / math.pi) / self.factor
        if self.transform == "slope_percent":
            return 100.0 * math.tan(math.radians(b)) / self.factor
        return (b - self.offset) / self.factor


class Catalog(object):
    def __init__(self):
        self.classes = []          # [(name, base unit name)]
        self.units = []
        self.by_name = {}
        self.by_alias = {}
        self.citations = {}
        self.warnings = []

    def find(self, name):
        """A unit by its name, or by one of the names ArcGIS Pro and EPSG use
        for it (FeetUS, Foot_US, US survey foot)."""
        key = (name or "").strip().lower()
        return self.by_name.get(key) or self.by_alias.get(key)

    def class_names(self):
        return [c[0] for c in self.classes]

    def units_in(self, cls=None):
        us = [u for u in self.units if not cls or u.cls.lower() == cls.lower()]
        return sorted(us, key=lambda u: u.name.lower())

    def _add(self, u):
        self.units.append(u)
        self.by_name[u.name.lower()] = u
        for a in u.aliases:
            self.by_alias.setdefault(a.strip().lower(), u)

    def convert(self, x, a, b):
        ua, ub = (a if isinstance(a, Unit) else self.find(a)), (b if isinstance(b, Unit) else self.find(b))
        if ua is None or ub is None:
            raise ValueError("Unknown unit: %r" % (a if ua is None else b))
        if ua.cls.lower() != ub.cls.lower():
            raise ValueError("'%s' (%s) and '%s' (%s) are in different measurement classes."
                             % (ua.name, ua.cls, ub.name, ub.cls))
        if ua is ub:
            return x
        return ub.from_base(ua.to_base(x))


def load_catalog(bundled=BUNDLED_PATH, user=None):
    """The bundled catalog plus the user's own units (pass user=False to skip them)."""
    cat = Catalog()
    with io.open(bundled, encoding="utf-8") as f:
        root = json.load(f)
    for c in root["classes"]:
        cat.classes.append((c["name"], c["base"]))
    cat.citations = root.get("citations") or {}
    for u in root["units"]:
        unit = Unit(u["name"], u.get("singular"), u["class"], u["factor"], u.get("offset", 0.0),
                    u.get("transform"), u.get("definition", ""), aliases=u.get("aliases"))
        unit.sources = list(u.get("sources") or [])
        unit.desc_sources = list(u.get("descriptionSources") or [])
        cat._add(unit)
    if user is False:
        return cat
    path = user or user_path()
    if not os.path.exists(path):
        return cat
    try:
        with io.open(path, encoding="utf-8-sig") as f:
            mine = json.load(f)
    except Exception as ex:          # a damaged file must not stop the tool
        cat.warnings.append("Your own units could not be read (%s): %s" % (ex, path))
        return cat
    for c in mine.get("classes") or []:
        name, base = c.get("name"), c.get("base")
        if not name or not base or cat.find(base) or name.lower() in [x[0].lower() for x in cat.classes]:
            continue
        cat.classes.append((name, base))
        cat._add(Unit(base, c.get("baseSingular"), name, definition=c.get("definition", ""), is_user=True))
    pending = [u for u in mine.get("units") or [] if u.get("definedAs")]
    progress = True
    while pending and progress:
        progress = False
        for u in list(pending):
            d = u["definedAs"]
            ref = cat.find(d.get("unit"))
            if ref is None:
                continue
            pending.remove(u)
            progress = True
            k, o = float(d.get("factor", 1.0)), float(d.get("offset", 0.0))
            if not u.get("name") or cat.find(u["name"]) or k == 0 or (o and ref.nonlinear):
                cat.warnings.append("Skipped your unit '%s'." % u.get("name"))
                continue
            cat._add(Unit(u["name"], u.get("singular"), ref.cls, definition=u.get("definition", ""),
                          ref=ref, k=k, o=o, is_user=True))
    for u in pending:
        cat.warnings.append("Skipped your unit '%s': the unit it is defined against, '%s', is not in the catalog."
                            % (u.get("name"), u["definedAs"].get("unit")))
    return cat


# --------------------------------------------------------------------------
#  Convert Field Units
# --------------------------------------------------------------------------
TOOL_CREDIT = ("the Convert Field Units tool (Wildlife and Forestry Tools for ArcGIS Pro, "
               "Jenness Enterprises, www.jennessent.com)")
CATALOG_SOURCE = ("Unit Converter catalog, Wildlife and Forestry Tools for ArcGIS Pro (Jenness "
                  "Enterprises), from Jeff Jenness's Unit Converter (2000-2001); customary units "
                  "through their exact legal definitions.")


def unit_statement(cat, u):
    """'1 inch = 0.0254 meter' (its class's base unit), or the curve it
    converts through, for a field description."""
    base = cat.find(dict(cat.classes).get(u.cls))
    if base is None or base is u:
        return "%s is the base unit of the %s class." % (u.singular[:1].upper() + u.singular[1:], u.cls)
    if u.nonlinear:
        return ("1 %s converts to %.15g %s; the units of this class convert through a curve "
                "(a circle's diameter to its area, or a percent slope to an angle), not by a factor."
                % (u.singular, cat.convert(1.0, u, base), base.name))
    v = cat.convert(1.0, u, base)
    zero = cat.convert(0.0, u, base)
    text = "1 %s = %.15g %s" % (u.singular, v, base.singular if v == 1 else base.name)
    if zero != 0:
        text += ", and 0 %s = %.15g %s" % (u.name, zero, base.name)
    return text + "."


def document_field(in_table, field, in_field, ua, ub, cat, in_place, lo=None, hi=None, when=None):
    """Writes the field's description into the dataset's metadata (the
    Fields section of ArcGIS item metadata, eainfo/detailed/attr): what it
    holds, the tool that converted it and when, the unit's definition and its
    factor to the class's base unit, the unit as the range domain's units of
    measure, and the range of the converted values. Returns the dataset path."""
    import datetime
    import xml.etree.ElementTree as ET
    from arcpy import metadata as md_mod
    path = arcpy.Describe(in_table).catalogPath
    md = md_mod.Metadata(path)
    xml = md.xml
    root = ET.fromstring(xml) if xml else ET.Element("metadata")
    eainfo = root.find("eainfo")
    if eainfo is None:
        eainfo = ET.SubElement(root, "eainfo")
    detailed = eainfo.find("detailed")
    if detailed is None:
        detailed = ET.SubElement(eainfo, "detailed", Name=os.path.basename(path))
    attr = None
    for a in detailed.findall("attr"):
        lab = a.find("attrlabl")
        if lab is not None and (lab.text or "").strip().lower() == field.lower():
            attr = a
            break
    if attr is None:
        attr = ET.SubElement(detailed, "attr")
        ET.SubElement(attr, "attrlabl").text = field

    def put(parent, tag, text):
        el = parent.find(tag)
        if el is None:
            el = ET.SubElement(parent, tag)
        el.text = text
        return el

    when = when or datetime.date.today().isoformat()
    if in_place:
        what = ("This field held values in %s; on %s %s converted them to %s."
                % (ua.name, when, TOOL_CREDIT, ub.name))
    else:
        what = ("The values of the field %s, converted from %s to %s by %s on %s."
                % (in_field, ua.name, ub.name, TOOL_CREDIT, when))
    defn = (ub.definition or "").strip()
    text = "%s Units: %s (%s). %s%s" % (what, ub.name, ub.cls, unit_statement(cat, ub),
                                        (" " + defn) if defn else "")
    put(attr, "attrdef", text)
    src = [cat.citations.get(k, {}).get("full") or k for k in getattr(ub, "sources", [])]
    dsrc = [cat.citations.get(k, {}).get("full") or k for k in getattr(ub, "desc_sources", [])]
    if src and dsrc:
        # the description of the original program is credited to it, apart
        # from the standard that gives the value (Jeff, 2026-10-08)
        src_text = " Source of the unit's value: " + " ".join(src) + " Source of its description: " + " ".join(dsrc)
    else:
        src_text = (" Unit sources: " + " ".join(src)) if src else ""
    put(attr, "attrdefs", CATALOG_SOURCE + src_text)
    dom = attr.find("attrdomv")
    if dom is None:
        dom = ET.SubElement(attr, "attrdomv")
    for child in list(dom):
        if child.tag != "rdom":
            dom.remove(child)
    rdom = dom.find("rdom")
    if rdom is None:
        rdom = ET.SubElement(dom, "rdom")
    if lo is not None and hi is not None:
        put(rdom, "rdommin", "%.15g" % lo)
        put(rdom, "rdommax", "%.15g" % hi)
    put(rdom, "attrunit", ub.name)
    md.xml = ET.tostring(root, encoding="unicode")
    md.save()
    return path
NUMERIC_TYPES = ("SmallInteger", "Integer", "BigInteger", "Single", "Double")


def _field(in_table, name):
    for f in arcpy.ListFields(in_table):
        if f.name.lower() == (name or "").lower():
            return f
    return None


def _workspace_of(in_table):
    try:
        d = arcpy.Describe(in_table)
        return getattr(d, "catalogPath", None) or getattr(d, "path", "")
    except Exception:
        return ""


def _is_dbf_target(in_table):
    """A shapefile or dBASE table: no null values, field names of 10 characters."""
    p = _workspace_of(in_table).lower().replace("/", "\\")
    return p.endswith(".shp") or p.endswith(".dbf")


def suggest_field_name(in_field, to_unit, dbf):
    """A name for the new field: the input field's name and a short tag of the
    TO unit, legal in shapefiles when the table is one."""
    tag = "".join(ch for ch in (to_unit or "") if ch.isalnum())[:6] or "conv"
    name = "%s_%s" % (in_field, tag)
    if dbf:
        name = name[:10]
    return name


def run_convert_field(in_table, in_field, from_unit, to_unit, out_field, messages=None):
    """Converts every value of in_field from one unit to another into
    out_field (added as a Double field if it does not exist; out_field equal
    to in_field converts the values in place). A layer or table view input
    converts only the records it shows: its selection and definition query
    are honored. Returns (converted, skipped)."""
    def say(t):
        if messages:
            messages.addMessage(t)

    def warn(t):
        if messages:
            messages.addWarningMessage(t)

    cat = load_catalog()
    for w in cat.warnings:
        warn(w)
    ua, ub = cat.find(from_unit), cat.find(to_unit)
    if ua is None or ub is None:
        raise arcpy.ExecuteError("Unknown unit: '%s'." % (from_unit if ua is None else to_unit))
    if ua.cls.lower() != ub.cls.lower():
        raise arcpy.ExecuteError("'%s' is a unit of %s and '%s' a unit of %s; a value can only be converted "
                                 "between units of the same measurement class." % (ua.name, ua.cls, ub.name, ub.cls))
    fin = _field(in_table, in_field)
    if fin is None:
        raise arcpy.ExecuteError("The field '%s' is not in the table." % in_field)
    if fin.type not in NUMERIC_TYPES:
        raise arcpy.ExecuteError("The field '%s' is not numeric (%s)." % (in_field, fin.type))
    dbf = _is_dbf_target(in_table)
    out_field = (out_field or "").strip() or suggest_field_name(fin.name, ub.name, dbf)
    in_place = out_field.lower() == fin.name.lower()
    fout = fin if in_place else _field(in_table, out_field)
    if fout is None:
        ws = os.path.dirname(_workspace_of(in_table))
        legal = arcpy.ValidateFieldName(out_field, ws) if ws else out_field
        if dbf and len(legal) > 10:
            legal = legal[:10]
        if legal != out_field:
            warn("The new field is named '%s' (the name '%s' is not legal in this table)." % (legal, out_field))
            out_field = legal
        alias = ("%s in %s (from %s)" % (fin.name, ub.name, ua.name))[:255]
        arcpy.management.AddField(in_table, out_field, "DOUBLE", field_alias=None if dbf else alias)
        fout = _field(in_table, out_field)
        say("Added the field '%s' (Double)." % out_field)
    else:
        if fout.type not in NUMERIC_TYPES:
            raise arcpy.ExecuteError("The output field '%s' exists and is not numeric (%s)." % (out_field, fout.type))
        if in_place:
            warn("Converting the values of '%s' in place: the values in %s are replaced by the values in %s."
                 % (fin.name, ua.name, ub.name))
        else:
            warn("The field '%s' already exists; its values are replaced by the converted values." % out_field)
    integer_out = fout.type in ("SmallInteger", "Integer", "BigInteger")
    if integer_out:
        warn("'%s' is an integer field, so the converted values are rounded to whole numbers." % fout.name)
    nullable = bool(getattr(fout, "isNullable", True)) and not dbf

    n_in = int(arcpy.management.GetCount(in_table)[0])
    say("Converting %s record%s of '%s' from %s to %s (%s)." % (
        "{:,}".format(n_in), "" if n_in == 1 else "s", fin.name, ua.name, ub.name, ua.cls))
    arcpy.SetProgressor("step", "Converting %s..." % fin.name, 0, max(n_in, 1), max(1, n_in // 100))
    converted = skipped = 0
    lo = hi = None
    fields = [fin.name] if in_place else [fin.name, fout.name]
    with arcpy.da.UpdateCursor(in_table, fields) as cur:
        for i, row in enumerate(cur):
            x = row[0]
            y = None
            if x is not None:
                try:
                    y = cat.convert(float(x), ua, ub)
                except (ValueError, OverflowError, ZeroDivisionError):
                    y = None
                if y is not None and not math.isfinite(y):
                    y = None
            if y is None:
                skipped += 1
                y = None if nullable else NULL_STANDIN
            else:
                converted += 1
                if integer_out:
                    y = int(round(y))
                lo = y if lo is None or y < lo else lo
                hi = y if hi is None or y > hi else hi
            if in_place:
                row[0] = y
            else:
                row[1] = y
            cur.updateRow(row)
            if i % 100 == 0:
                arcpy.SetProgressorPosition(i)
    arcpy.ResetProgressor()
    say("Converted %s value%s." % ("{:,}".format(converted), "" if converted == 1 else "s"))
    try:
        document_field(in_table, fout.name, fin.name, ua, ub, cat, in_place, lo, hi)
        say("Described the field '%s' in the dataset's metadata (Fields section): the units, their "
            "definition and the conversion." % fout.name)
    except Exception as ex:          # metadata is a courtesy; the values are converted
        warn("The field's description could not be written to the dataset's metadata (%s)." % ex)
    if skipped:
        warn("%s record%s had no value or no converted value (an empty field, or a negative area that has "
             "no diameter) and %s." % ("{:,}".format(skipped), "" if skipped == 1 else "s",
                                       "were left empty" if nullable else
                                       "were given %g, since this table cannot hold empty values" % NULL_STANDIN))
    return converted, skipped
