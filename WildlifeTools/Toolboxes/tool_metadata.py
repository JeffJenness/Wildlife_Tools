# -*- coding: utf-8 -*-
"""Shared field-level metadata helpers for the Jenness Enterprises tools.

Writes ArcGIS / FGDC entity and attribute (field) metadata, following the same
structure the SSI add-in's ``AddFieldAttributes`` populates:

  eainfo/detailed/enttyp/enttypd          = entity (table) definition
  eainfo/detailed/enttyp/enttypds         = entity definition source
  eainfo/detailed/attr[i]/attrlabl        = field name
  eainfo/detailed/attr[i]/attrdef         = field description
  eainfo/detailed/attr[i]/attrdefs        = field description source
  .../attrdomv/edom/{edomv,edomvd,edomvds} = coded value / its description / source

Field descriptions and coded-value (enumerated-domain) definitions are authored by
the calling tool; the default source is "Jenness Enterprises".
"""
import xml.etree.ElementTree as ET

import arcpy

JENNESS = "Jenness Enterprises"


def _child(parent, tag):
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    return el


def _set(parent, tag, text):
    el = _child(parent, tag)
    el.attrib.pop("Sync", None)        # drop Esri's Sync flag so our text isn't overwritten
    el.text = text
    return el


def set_entity_and_fields(target, entity=None, entity_source=JENNESS, fields=None,
                          messages=None):
    """Write an entity definition and per-field descriptions into ``target``'s metadata.

    ``fields``: list of dicts ``{"name", "description", "source"(opt), "coded"(opt)}``
    where ``coded`` is a list of ``(value, value_description, value_source(opt))``
    tuples defining an enumerated (coded-value) domain. Field names should match the
    actual output field names (e.g. shapefile short names for a .shp). Never raises.
    """
    try:
        from arcpy import metadata as md
        m = md.Metadata(target)
        m.synchronize("SELECTIVE")         # ensure an <attr> exists for every real field
        xml = m.xml
        root = ET.fromstring(xml)
        detailed = _child(_child(root, "eainfo"), "detailed")

        if entity:
            enttyp = _child(detailed, "enttyp")
            _set(enttyp, "enttypd", entity)
            _set(enttyp, "enttypds", entity_source)

        if fields:
            by_name = {a.findtext("attrlabl"): a for a in detailed.findall("attr")
                       if a.findtext("attrlabl")}
            for f in fields:
                a = by_name.get(f["name"])
                if a is None:
                    a = ET.SubElement(detailed, "attr")
                    ET.SubElement(a, "attrlabl").text = f["name"]
                    by_name[f["name"]] = a
                _set(a, "attrdef", f["description"])
                _set(a, "attrdefs", f.get("source", JENNESS))
                coded = f.get("coded")
                if coded:
                    for old in a.findall("attrdomv"):
                        a.remove(old)
                    for entry in coded:
                        val, vdesc = entry[0], entry[1]
                        vsrc = entry[2] if len(entry) > 2 else JENNESS
                        edom = ET.SubElement(ET.SubElement(a, "attrdomv"), "edom")
                        ET.SubElement(edom, "edomv").text = str(val)
                        ET.SubElement(edom, "edomvd").text = vdesc
                        ET.SubElement(edom, "edomvds").text = vsrc

        m.xml = ET.tostring(root, encoding="unicode")
        m.save()
    except Exception as e:
        if messages:
            messages.addWarningMessage("Could not write field metadata: {}".format(e))
