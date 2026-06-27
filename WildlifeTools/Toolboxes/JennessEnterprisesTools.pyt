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

import locoh          # noqa: E402
import error_report   # noqa: E402

import re


def _legalize(name):
    """Fallback name sanitizer if arcpy.ValidateTableName is unavailable."""
    legal = re.sub(r"\W", "_", name)
    if legal and legal[0].isdigit():
        legal = "_" + legal
    return legal or "LoCoH"


def _confirm(text, caption="Wildlife Tools"):
    """Show a Yes/No confirmation dialog; return True if the user confirms.

    Uses the native Windows dialog. If no UI is available (e.g. a headless script),
    returns True so batch runs are not blocked.
    """
    try:
        import ctypes
        MB_YESNO = 0x4
        MB_ICONQUESTION = 0x20
        MB_SETFOREGROUND = 0x10000
        MB_TOPMOST = 0x40000
        IDYES = 6
        flags = MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST
        return ctypes.windll.user32.MessageBoxW(0, text, caption, flags) == IDYES
    except Exception:
        return True


def _resolve_length(in_value, p_len, messages):
    """GPLinearUnit -> (meters, meters_per_unit, unit_name).

    If the unit is 'Unknown' (or unrecognized), fall back to the dataset coordinate
    system's linear unit and ask the user to confirm; if they decline, the run is
    cancelled (returns to the tool pane). Shared by the Adaptive and Fixed-Sphere tools.
    """
    parts = (p_len.valueAsText or "").strip().split()
    value = float(parts[0])
    unit = parts[1] if len(parts) > 1 else "Unknown"
    if unit and unit.lower() != "unknown":
        try:
            factor = arcpy.LinearUnitConversionFactor(unit, "Meters")   # meters per unit
            return value * factor, factor, unit
        except Exception:
            pass   # unrecognized -> resolve from the dataset below

    try:
        sr = arcpy.Describe(in_value).spatialReference
    except Exception:
        sr = None
    if sr is not None and sr.type == "Projected":
        resolved_unit = sr.linearUnitName or "Meters"
        factor = float(sr.metersPerUnit)
        prompt = ("The unit is 'Unknown'. It will be interpreted as the dataset's "
                  "linear unit: {}.\n\nContinue?".format(resolved_unit))
    else:
        resolved_unit, factor = "Meters", 1.0
        prompt = ("The unit is 'Unknown'. The dataset uses a geographic coordinate "
                  "system (no linear unit), so the value will be interpreted as "
                  "meters.\n\nContinue?")

    if not _confirm(prompt):
        raise arcpy.ExecuteError(
            "Run cancelled: please choose a unit and run the tool again.")
    if messages is not None:
        messages.addMessage("Unknown unit interpreted as {}.".format(resolved_unit))
    return value * factor, factor, resolved_unit


class Toolbox(object):
    def __init__(self):
        self.label = "Jenness Enterprises Tools"
        self.alias = "jenness"
        self.tools = [LoCoHStandard, LoCoHAdaptive, LoCoHFixedSphere]


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
        try:
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
        except arcpy.ExecuteError:
            raise   # clean validation / GP messages pass through unchanged
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class LoCoHAdaptive(object):
    A_MAX = "Maximum distance between any pair of points"
    A_SPECIFY = "Specify a length"

    def __init__(self):
        self.label = "Local Convex Hull (LoCoH) - Adaptive"
        self.description = (
            "Adaptive (a-) Local Convex Hull home-range estimator (Getz et al. 2007). "
            "Each hull is built from a focal point and its nearest neighbours, added in "
            "order of increasing distance until their cumulative distance reaches the "
            "maximum cumulative length a; hulls are sorted by point count and unioned "
            "cumulatively into utilization isopleths.")
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

        a_method = arcpy.Parameter(
            displayName="Maximum cumulative length (a)",
            name="a_method",
            datatype="GPString",
            parameterType="Required",
            direction="Input")
        a_method.filter.type = "ValueList"
        a_method.filter.list = [self.A_MAX, self.A_SPECIFY]
        a_method.value = self.A_MAX

        a_length = arcpy.Parameter(
            displayName="Length",
            name="a_length",
            datatype="GPLinearUnit",
            parameterType="Optional",
            direction="Input")
        a_length.enabled = False

        out_isopleths = arcpy.Parameter(
            displayName="Cumulative LoCoH [Adaptive] Hulls",
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

        return [in_features, a_method, a_length, out_isopleths, out_hulls]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        p_in, p_method, p_len, p_out = (parameters[0], parameters[1],
                                        parameters[2], parameters[3])
        # The length box is only relevant when the user chooses to specify a length.
        p_len.enabled = (p_method.valueAsText == self.A_SPECIFY)

        if p_in.value is None:
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
        proposed = "{}_Adaptive_Cumulative".format(legal)
        full = os.path.join(workspace, proposed) if workspace else proposed

        current = p_out.valueAsText or ""
        cur_name = os.path.basename(current)
        if cur_name.lower().endswith(".shp"):
            cur_name = cur_name[:-4]
        if (cur_name == "" or cur_name == self._auto_name
                or re.search(r"_Adaptive_Cumulative$", cur_name, re.IGNORECASE)
                or re.search(r"_LoCoHAdaptive\d*$", cur_name, re.IGNORECASE)):
            p_out.value = full
            self._auto_name = proposed
        return

    def updateMessages(self, parameters):
        p_method, p_len = parameters[1], parameters[2]
        if p_method.valueAsText == self.A_SPECIFY:
            text = p_len.valueAsText
            if not text:
                p_len.setErrorMessage(
                    "Specify a length (with units) for the maximum cumulative length.")
            else:
                parts = str(text).split()
                try:
                    if float(parts[0]) <= 0:
                        p_len.setErrorMessage("Length must be greater than zero.")
                    elif len(parts) > 1 and parts[1].lower() == "unknown":
                        p_len.setWarningMessage(
                            "Unit is 'Unknown'. When you run, you will be asked to confirm "
                            "interpreting the length in the dataset's linear unit.")
                except Exception:
                    pass
        return

    def execute(self, parameters, messages):
        try:
            importlib.reload(locoh)
            if parameters[1].valueAsText == self.A_SPECIFY:
                a_meters, report_factor, report_unit = _resolve_length(
                    parameters[0].valueAsText, parameters[2], messages)
            else:
                a_meters, report_factor, report_unit = None, 1.0, "Meters"
            iso_lyrx = locoh.run_locoh_adaptive(
                in_features=parameters[0].valueAsText,
                a_meters=a_meters,
                report_factor=report_factor,
                report_unit=report_unit,
                out_isopleths=parameters[3].valueAsText,
                out_hulls=parameters[4].valueAsText or None,
                messages=messages)
            if iso_lyrx:
                try:
                    parameters[3].symbology = iso_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class LoCoHFixedSphere(object):
    R_AUTO = "Automatic (half the maximum nearest-neighbor distance)"
    R_SPECIFY = "Specify a radius"

    def __init__(self):
        self.label = "Local Convex Hull (LoCoH) - Fixed Sphere"
        self.description = (
            "Fixed-sphere-of-influence (r-) Local Convex Hull home-range estimator "
            "(Getz et al. 2007). Each hull is the convex hull of a focal point and all "
            "points within radius r (always including the focal point and its two nearest "
            "neighbours); hulls are sorted by point count and unioned cumulatively into "
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

        r_method = arcpy.Parameter(
            displayName="Sphere radius (r)",
            name="r_method",
            datatype="GPString",
            parameterType="Required",
            direction="Input")
        r_method.filter.type = "ValueList"
        r_method.filter.list = [self.R_AUTO, self.R_SPECIFY]
        r_method.value = self.R_AUTO

        r_radius = arcpy.Parameter(
            displayName="Radius",
            name="r_radius",
            datatype="GPLinearUnit",
            parameterType="Optional",
            direction="Input")
        r_radius.enabled = False

        out_isopleths = arcpy.Parameter(
            displayName="Cumulative LoCoH [Fixed Sphere] Hulls",
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

        return [in_features, r_method, r_radius, out_isopleths, out_hulls]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        p_in, p_method, p_r, p_out = (parameters[0], parameters[1],
                                      parameters[2], parameters[3])
        p_r.enabled = (p_method.valueAsText == self.R_SPECIFY)

        if p_in.value is None:
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
        proposed = "{}_FixedSphere_Cumulative".format(legal)
        full = os.path.join(workspace, proposed) if workspace else proposed

        current = p_out.valueAsText or ""
        cur_name = os.path.basename(current)
        if cur_name.lower().endswith(".shp"):
            cur_name = cur_name[:-4]
        if (cur_name == "" or cur_name == self._auto_name
                or re.search(r"_FixedSphere_Cumulative$", cur_name, re.IGNORECASE)
                or re.search(r"_LoCoHFixedSphere\d*$", cur_name, re.IGNORECASE)):
            p_out.value = full
            self._auto_name = proposed
        return

    def updateMessages(self, parameters):
        p_method, p_r = parameters[1], parameters[2]
        if p_method.valueAsText == self.R_SPECIFY:
            text = p_r.valueAsText
            if not text:
                p_r.setErrorMessage("Specify a radius (with units) for the sphere of influence.")
            else:
                parts = str(text).split()
                try:
                    if float(parts[0]) <= 0:
                        p_r.setErrorMessage("Radius must be greater than zero.")
                    elif len(parts) > 1 and parts[1].lower() == "unknown":
                        p_r.setWarningMessage(
                            "Unit is 'Unknown'. When you run, you will be asked to confirm "
                            "interpreting the radius in the dataset's linear unit.")
                except Exception:
                    pass
        return

    def execute(self, parameters, messages):
        try:
            importlib.reload(locoh)
            if parameters[1].valueAsText == self.R_SPECIFY:
                r_meters, report_factor, report_unit = _resolve_length(
                    parameters[0].valueAsText, parameters[2], messages)
            else:
                r_meters, report_factor, report_unit = None, 1.0, "Meters"
            iso_lyrx = locoh.run_locoh_fixed_sphere(
                in_features=parameters[0].valueAsText,
                r_meters=r_meters,
                report_factor=report_factor,
                report_unit=report_unit,
                out_isopleths=parameters[3].valueAsText,
                out_hulls=parameters[4].valueAsText or None,
                messages=messages)
            if iso_lyrx:
                try:
                    parameters[3].symbology = iso_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")
