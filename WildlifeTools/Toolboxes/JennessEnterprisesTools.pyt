# -*- coding: utf-8 -*-
"""Jenness Enterprises Tools Python toolbox.

A single ArcGIS Pro toolbox that holds the Jenness Enterprises geoprocessing
tools (usable in ModelBuilder and the GP pane). Tools are grouped into
sub-toolboxes (toolsets) via each tool's ``category`` -- e.g. the
"Wildlife Tools" sub-toolbox, which pairs with the WildlifeTools add-in. The
add-in's ribbon buttons invoke these tools so the analysis logic lives in one
place. Add further tools/categories here as more sub-toolboxes are needed.
"""

import os
import sys
import importlib

import arcpy

# Make the helper modules next to this .pyt importable.
_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import locoh   # noqa: E402

import re


def _legalize(name):
    """Fallback name sanitizer if arcpy.ValidateTableName is unavailable."""
    legal = re.sub(r"\W", "_", name)
    if legal and legal[0].isdigit():
        legal = "_" + legal
    return legal or "LoCoH"


class Toolbox(object):
    def __init__(self):
        self.label = "Jenness Enterprises Tools"
        self.alias = "jenness"
        self.tools = [LoCoHStandard]


class LoCoHStandard(object):
    def __init__(self):
        self.label = "Local Convex Hull (LoCoH) - Standard"
        self.description = (
            "Standard (k-) Local Convex Hull home-range estimator (Getz & Wilmers "
            "2004). Builds the convex hull of each point's k nearest neighbours, "
            "sorts the hulls by area, and unions them cumulatively into "
            "utilization isopleths.")
        self.category = "Wildlife Tools"
        self.canRunInBackground = False
        self._auto_name = None

    def getParameterInfo(self):
        in_features = arcpy.Parameter(
            displayName="Input point features",
            name="in_features",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input")
        in_features.filter.list = ["Point", "Multipoint"]

        k = arcpy.Parameter(
            displayName="Number of nearest neighbors (k)",
            name="k",
            datatype="GPLong",
            parameterType="Required",
            direction="Input")
        k.value = 10
        k.filter.type = "Range"
        k.filter.list = [3, 1000000]

        out_isopleths = arcpy.Parameter(
            displayName="Cumulative LoCoH [Standard] Hulls",
            name="out_isopleths",
            datatype="DEFeatureClass",
            parameterType="Required",
            direction="Output")

        out_hulls = arcpy.Parameter(
            displayName="Output individual hulls (optional)",
            name="out_hulls",
            datatype="DEFeatureClass",
            parameterType="Optional",
            direction="Output")

        return [in_features, k, out_isopleths, out_hulls]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        # Default the cumulative output name to
        #   <legal input name>_Cumulative_<k>k
        # in the input's workspace. This overrides ArcGIS Pro's auto-derived
        # "<input>_LoCoHStandard" name and tracks input/k changes, but leaves any
        # genuinely custom name the user has typed.
        p_in, p_k, p_out = parameters[0], parameters[1], parameters[2]
        if p_in.value is None or p_k.value is None:
            return
        try:
            desc = arcpy.Describe(p_in.value)
            base = desc.baseName
            workspace = getattr(desc, "path", None)
        except Exception:
            return
        if not base:
            return
        try:
            legal = arcpy.ValidateTableName(base, workspace) if workspace \
                else arcpy.ValidateTableName(base)
        except Exception:
            legal = _legalize(base)
        proposed = "{}_Cumulative_{}k".format(legal, int(p_k.value))
        full = os.path.join(workspace, proposed) if workspace else proposed

        current = p_out.valueAsText or ""
        cur_name = os.path.basename(current)
        if cur_name.lower().endswith(".shp"):
            cur_name = cur_name[:-4]
        # Overwrite when the box is empty, still holds our last auto value, matches our
        # own pattern (e.g. k changed), or holds Pro's derived "..._LoCoHStandard" name.
        if (cur_name == "" or cur_name == self._auto_name
                or re.search(r"_Cumulative_\d+k$", cur_name, re.IGNORECASE)
                or re.search(r"_LoCoHStandard\d*$", cur_name, re.IGNORECASE)):
            p_out.value = full
            self._auto_name = proposed
        return

    def updateMessages(self, parameters):
        k = parameters[1]
        if k.value is not None and k.value < 3:
            k.setErrorMessage("k must be at least 3 so each hull forms a polygon.")
        return

    def execute(self, parameters, messages):
        importlib.reload(locoh)   # pick up edits without restarting Pro
        iso_lyrx = locoh.run_locoh_standard(
            in_features=parameters[0].valueAsText,
            k=parameters[1].value,
            out_isopleths=parameters[2].valueAsText,
            out_hulls=parameters[3].valueAsText or None,
            messages=messages)
        # Load the cumulative output into the map with the LoCoH symbology. The
        # individual-hulls output is left to ArcGIS's default single-symbol display.
        if iso_lyrx:
            try:
                parameters[2].symbology = iso_lyrx
            except Exception as e:
                messages.addWarningMessage("Could not set output symbology: {}".format(e))
