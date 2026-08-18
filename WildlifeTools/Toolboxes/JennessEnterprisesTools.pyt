# -*- coding: utf-8 -*-
"""Jenness Enterprises Tools Python toolbox.

A single ArcGIS Pro toolbox that holds the Jenness Enterprises geoprocessing
tools (usable in ModelBuilder and the GP pane). Tools are grouped into
sub-toolboxes (toolsets) via each tool's ``category`` -- e.g. the
"Wildlife Tools" sub-toolbox, which pairs with the WildlifeTools add-in. The
add-in's ribbon buttons invoke these tools so the analysis logic lives in one
place. Add further tools/categories here as more sub-toolboxes are needed.
"""

import itertools
import math
import os
import sys
import importlib

import arcpy

# Make the helper modules next to this .pyt importable.
_HERE = os.path.dirname(__file__)
_PYT_PATH = os.path.abspath(__file__)   # this .pyt's own path (for the scripting hint)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _scripting_hint(messages, tool_name):
    """Print the exact lines needed to run this tool from a script or the Python
    window. An add-in Python toolbox is not on arcpy's path automatically, and
    Pro's 'Copy Python Command' may reference the active project toolbox rather
    than this add-in -- so the user needs this .pyt's real path (below)."""
    if messages is None:
        return
    try:
        messages.addMessage(
            "To run this tool from a script or the Python window, import this "
            "toolbox first (if you used 'Copy Python Command', replace its "
            "first line with the ImportToolbox line below):\n"
            "    import arcpy\n"
            "    arcpy.ImportToolbox(r\"{}\")\n"
            "    arcpy.jenness.{}(...)".format(_PYT_PATH, tool_name))
    except Exception:
        pass

import locoh          # noqa: E402
import mahalanobis    # noqa: E402
import kernel_density # noqa: E402
import cartography_tools  # noqa: E402
import topographic_tools  # noqa: E402
import hillshade      # noqa: E402
import aspect_tools    # noqa: E402
import lsri           # noqa: E402
import tpi_tools      # noqa: E402
import classification_store  # noqa: E402
import line_surface_tools  # noqa: E402
import diversity_tools  # noqa: E402
import land_facet_density  # noqa: E402
import land_facet_linkage  # noqa: E402
import land_facet_mahalanobis  # noqa: E402
import invert_raster  # noqa: E402
import land_facet_termini  # noqa: E402
import least_cost_corridor  # noqa: E402
import fill_corridor_holes  # noqa: E402
import union_corridors  # noqa: E402
import hsm_tools       # noqa: E402
import clip_tools      # noqa: E402
import patch_analysis  # noqa: E402
import bottleneck_analysis  # noqa: E402
import raster_extremes  # noqa: E402
import tool_metadata  # noqa: E402
import error_report   # noqa: E402

import re


def _legalize(name):
    """Fallback name sanitizer if arcpy.ValidateTableName is unavailable."""
    legal = re.sub(r"\W", "_", name)
    if legal and legal[0].isdigit():
        legal = "_" + legal
    return legal or "LoCoH"


def _unique_name(base, ws):
    """First free dataset name in ``ws``: ``base``, then ``base_2``, ``base_3``, ...

    Matches the requested convention (no "_1"; the first duplicate becomes "_2"),
    unlike arcpy.CreateUniqueName which appends "0".
    """
    def taken(name):
        try:
            return bool(ws) and arcpy.Exists(os.path.join(ws, name))
        except Exception:
            return False
    name = base
    if taken(base):
        i = 2
        while taken("{}_{}".format(base, i)):
            i += 1
        name = "{}_{}".format(base, i)
    return os.path.join(ws, name) if ws else name


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


def _tool_defaults_path():
    """The per-project JSON where Jenness tools remember last-used choices
    (stored beside the .aprx). None when no project is open (standalone runs)."""
    try:
        aprx = arcpy.mp.ArcGISProject("CURRENT")
        p = aprx.filePath
        if p:
            return os.path.splitext(p)[0] + ".jenness_tool_defaults.json"
    except Exception:
        pass
    return None


def _load_tool_defaults(tool_name):
    """Last-used parameter values saved for ``tool_name`` in this project."""
    path = _tool_defaults_path()
    if not path or not os.path.isfile(path):
        return {}
    try:
        import json
        with open(path) as f:
            return json.load(f).get(tool_name, {}) or {}
    except Exception:
        return {}


def _save_tool_defaults(tool_name, values):
    """Persist ``values`` (a plain dict) for ``tool_name`` with the project."""
    path = _tool_defaults_path()
    if not path:
        return
    try:
        import json
        data = {}
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        data[tool_name] = values
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


_DETREND_CATEGORY = "Local detrending (Dilts et al. 2023)"


def _detrend_params():
    """The three shared 'local detrending' (Dilts et al. 2023) parameters used
    by the Surface Area, VRM, and ACR tools: an on/off toggle plus a smoothing
    radius and its units. Subtracting a circular neighborhood mean before the
    index is computed removes the smooth underlying topography so the metric
    measures fine-scale surface roughness (the '...L' local variants). A radius
    of 1.5 cells (the default) is exactly the 3x3 mean Dilts used."""
    detrend = arcpy.Parameter(
        displayName="Remove smooth topography first (local ruggedness)",
        name="detrend", datatype="GPBoolean", parameterType="Optional",
        direction="Input", category=_DETREND_CATEGORY)
    detrend.value = False
    detrend_radius = arcpy.Parameter(
        displayName="Smoothing (detrending) radius", name="detrend_radius",
        datatype="GPDouble", parameterType="Optional", direction="Input",
        category=_DETREND_CATEGORY)
    detrend_radius.value = 1.5
    detrend_units = arcpy.Parameter(
        displayName="Smoothing radius units", name="detrend_units",
        datatype="GPString", parameterType="Optional", direction="Input",
        category=_DETREND_CATEGORY)
    detrend_units.filter.type = "ValueList"
    detrend_units.filter.list = topographic_tools.RADIUS_UNITS
    detrend_units.value = "Cells"
    return detrend, detrend_radius, detrend_units


def _detrend_enable(P):
    """Enable the detrend radius/units only when detrending is on."""
    on = bool(P["detrend"].value)
    P["detrend_radius"].enabled = on
    P["detrend_units"].enabled = on


def _detrend_check(P):
    """Validate the detrend radius when detrending is on and applicable (the
    radius parameter is enabled only then)."""
    if P["detrend_radius"].enabled:
        r = P["detrend_radius"].value
        if r is None or r <= 0:
            P["detrend_radius"].setErrorMessage(
                "The smoothing (detrending) radius must be positive.")


def _writeback_resolved_raster(P, name):
    """Point a raster output parameter at the dataset actually
    delivered: a folder output typed without an extension becomes a
    GeoTIFF (mahalanobis.resolve_raster_output), so Pro's add-to-map
    must target the .tif, not the extensionless name."""
    p = P.get(name)
    if p is None or not p.valueAsText:
        return
    actual = mahalanobis.resolve_raster_output(p.valueAsText, None)
    if actual != p.valueAsText:
        try:
            p.value = actual
        except Exception:
            pass


def _resolve_raster_path(param):
    """A usable catalog path for a raster input parameter. A GPRasterLayer bound
    to a map layer can report its value as a 'CIMPATH=...' string (or a bare
    layer name) that arcpy.Describe/CopyRaster cannot open, so resolve through
    the value OBJECT first and fall back to the text. Returns None if empty."""
    if param is None or not param.valueAsText:
        return None
    try:
        cp = getattr(arcpy.Describe(param.value), "catalogPath", None)
        if cp:
            return cp
    except Exception:
        pass
    return param.valueAsText


def _writeback_saved_output(P, name):
    """After an optional intermediate save: point the output parameter at the
    delivered dataset ONLY if it actually exists on disk; otherwise clear it, so
    a failed save never hands Pro a broken layer to add to the map."""
    p = P.get(name)
    if p is None or not p.valueAsText:
        return
    resolved = mahalanobis.resolve_raster_output(p.valueAsText, None)
    try:
        p.value = resolved if arcpy.Exists(resolved) else None
    except Exception:
        pass


class Toolbox(object):
    def __init__(self):
        self.label = "Jenness Enterprises Tools"
        self.alias = "jenness"
        self.tools = [LoCoHStandard, LoCoHAdaptive, LoCoHFixedSphere,
                      MahalanobisRaster, MahalanobisPoints, MahalanobisCovariance,
                      MahalanobisChiSquare,
                      KernelDensityGenerate, KernelDensityProportion, KernelDensityContours,
                      ShadowPolygons, EnhancedHillshade, MDOWHillshade,
                      MultiSunHillshade, SimpleHypsometricShading,
                      BlurredHillshade, SurfaceAreaRatio, VRM,
                      ArcChordRatio, TRI, SlopeZonalStatisticsAsTable,
                      TopographyAlongLines,
                      SplitLinesIntoSegments,
                      FractalDimension, TopographicWetnessIndex,
                      AspectFocalStatistics, ExtractAspectValuesToPoints,
                      AspectTransformation, AspectZonalStatisticsAsTable,
                      SimpleTPI,
                      SlopePositionClassification, LandformClassification,
                      GeneralRasterClassification,
                      LSRIOnDEM, LSRIOnContourPolylines,
                      DiversityIndices, LandFacetDensity, LandFacetMahalanobis,
                      InvertRaster, IdentifyTerminiPolygons,
                      LeastCostCorridor, FillCorridorHoles, UnionCorridors,
                      HabitatSuitabilityModel, CombineHabitatFactors,
                      NormalizeExistingHSM, ReclassifyFeaturesInHSM,
                      CriticalHabitatFeature, HabitatPatches,
                      ClipDataToAnalysisArea, PatchAnalysis,
                      BottleneckAnalysis,
                      FindRasterExtremeValues]


class LoCoHStandard(object):
    def __init__(self):
        self.label = "Local Convex Hull (LoCoH) - Standard"
        self.description = (
            "Standard (k-) Local Convex Hull home-range estimator (Getz & Wilmers "
            "2004). Builds the convex hull of each point's k nearest neighbours, "
            "sorts the hulls by area, and unions them cumulatively into "
            "utilization isopleths.")
        self.category = "Local Convex Hull Tools"
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
            _scripting_hint(messages, type(self).__name__)
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
        self.category = "Local Convex Hull Tools"
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
            _scripting_hint(messages, type(self).__name__)
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
        self.category = "Local Convex Hull Tools"
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
            _scripting_hint(messages, type(self).__name__)
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


# ---------------------------------------------------------------------------
# Shared helpers for the two Mahalanobis tools
# ---------------------------------------------------------------------------
# Multiband rasters are NOT expanded into per-band rows in the dialog: a value-table
# column that accepts both rasters and bands becomes a GPComposite, whose value cannot
# be reassigned in updateParameters (it clears to empty -- ERROR 000735). Instead the
# raster list holds whole rasters, multiband rasters are expanded to bands at run time,
# and an optional "Bands to exclude" control drops specific bands.
_RASTER_DT = ("RasterDataset", "RasterLayer", "RasterBand")

# (generate-checkbox name, output-table name, name suffix, engine save_opts key)
_MAHAL_TABLE_SPECS = [("gen_mean", "save_mean", "Means", "mean"),
                      ("gen_cov", "save_cov", "Covariance", "cov"),
                      ("gen_invcov", "save_invcov", "Inv_Covariance", "invcov"),
                      ("gen_corr", "save_corr", "Correlation", "corr")]


def _mahal_var_name(ds):
    """Resolved variable name; a band '<raster>\\Band_X' becomes '<raster>_Band_X'."""
    try:
        path = arcpy.Describe(ds).catalogPath
        d = arcpy.Describe(path)
        if d.dataType == "RasterBand":
            try:
                parent = arcpy.Describe(os.path.dirname(path)).baseName
            except Exception:
                parent = os.path.splitext(os.path.basename(os.path.dirname(path)))[0]
            return "{}_{}".format(parent, d.baseName)
        return d.baseName
    except Exception:
        return str(ds)


def _make_raster_vars_param():
    """Required multi-value raster list (whole rasters, including multiband)."""
    return arcpy.Parameter(displayName="Raster Landscape Variables", name="raster_vars",
                           datatype="GPRasterLayer", parameterType="Required",
                           direction="Input", multiValue=True)


def _make_poly_vars_param():
    """Optional polygon variable table with a real (validated) Field dropdown."""
    p = arcpy.Parameter(displayName="Polygon Landscape Variables (Optional)",
                        name="poly_vars", datatype="GPValueTable",
                        parameterType="Optional", direction="Input")
    p.columns = [["GPFeatureLayer", "Polygon feature class"], ["Field", "Numeric field"]]
    try:
        p.filters[0].list = ["Polygon"]
    except Exception:
        pass
    return p


def _make_exclude_bands_param():
    """Optional table of (multiband raster, band number) pairs to exclude."""
    p = arcpy.Parameter(displayName="Bands to exclude from multiband rasters (Optional)",
                        name="exclude_bands", datatype="GPValueTable",
                        parameterType="Optional", direction="Input")
    p.columns = [["GPRasterLayer", "Multiband raster"], ["GPLong", "Band number to exclude"]]
    return p


def _make_mahal_table_params(category):
    """The four generate-checkbox + output-table parameter pairs (in order)."""
    labels = {
        "gen_mean": "Generate mean vector table", "save_mean": "Mean vector table",
        "gen_cov": "Generate covariance matrix table", "save_cov": "Covariance matrix table",
        "gen_invcov": "Generate inverse covariance matrix table",
        "save_invcov": "Inverse covariance matrix table",
        "gen_corr": "Generate correlation matrix table", "save_corr": "Correlation matrix table"}
    out = []
    for gen, tbl, _suffix, _key in _MAHAL_TABLE_SPECS:
        pg = arcpy.Parameter(displayName=labels[gen], name=gen, datatype="GPBoolean",
                             parameterType="Optional", direction="Input", category=category)
        pg.value = False
        pt = arcpy.Parameter(displayName=labels[tbl], name=tbl, datatype="DETable",
                             parameterType="Optional", direction="Output", category=category)
        out += [pg, pt]
    return out


def _raster_list(p_raster):
    try:
        return list(p_raster.values) if p_raster.values else []
    except Exception:
        return []


def _combined_var_rows(p_raster, p_poly):
    """Engine var_rows: rasters first (each expands to its bands at run time), then polygons."""
    rows = [(r, None) for r in _raster_list(p_raster)]
    for r in (p_poly.value or []):
        fld = r[1] if len(r) > 1 else None
        rows.append((r[0], str(fld) if fld not in (None, "") else None))
    return rows


def _build_exclude(p_excl):
    """Set of (raster_catalogPath_lower, band_number) from the exclude-bands table."""
    excl = set()
    for r in (p_excl.value or []):
        try:
            excl.add((arcpy.Describe(r[0]).catalogPath.lower(), int(r[1])))
        except Exception:
            pass
    return excl


def _expanded_var_names(p_raster, p_poly, exclude):
    """Variable names exactly as the engine will build them (multiband expanded)."""
    names = []
    for r in _raster_list(p_raster):
        try:
            path = arcpy.Describe(r).catalogPath
            d = arcpy.Describe(path)
            if d.dataType in ("RasterDataset", "RasterLayer") and getattr(d, "bandCount", 1) > 1:
                for i, ch in enumerate(d.children, start=1):
                    if (path.lower(), i) in exclude:
                        continue
                    names.append("{}_{}".format(d.baseName, ch.name))
            else:
                names.append(d.baseName)
        except Exception:
            names.append(str(r))
    for r in (p_poly.value or []):
        names.append(_mahal_var_name(r[0]))
    return names


def _rat_flagged(p_raster):
    flagged = []
    for r in _raster_list(p_raster):
        try:
            d = arcpy.Describe(r)
            if d.dataType in _RASTER_DT and getattr(d, "hasRAT", False):
                flagged.append(d.baseName)
        except Exception:
            pass
    return flagged


def _any_multiband(p_raster):
    """True if any selected raster in the list has more than one band.

    Used to enable the Bands-to-exclude control only when there is actually a multiband
    raster to exclude bands from (a Python toolbox can grey a parameter out but cannot
    hide it outright).
    """
    for r in _raster_list(p_raster):
        try:
            d = arcpy.Describe(r)
            if d.dataType in _RASTER_DT and getattr(d, "bandCount", 1) > 1:
                return True
        except Exception:
            pass
    return False


def _mahal_update(tool, P, out_name, out_base, derived_re):
    """Shared updateParameters: sample always on; output + table autofill; enabling."""
    P["sample_method"].enabled = True
    # The Bands-to-exclude control is only meaningful when a multiband raster is present.
    P["exclude_bands"].enabled = _any_multiband(P["raster_vars"])

    source = P["stats_source"].valueAsText
    is_pts = (source == mahalanobis.SRC_POINTS)
    is_cat = (source == mahalanobis.SRC_CATEGORICAL)
    is_tab = (source == mahalanobis.SRC_TABLES)

    p_out = P[out_name]
    cur = p_out.valueAsText or ""
    cur_name = os.path.basename(cur)
    if cur_name.lower().endswith(".shp"):
        cur_name = cur_name[:-4]
    if (cur == "" or cur_name == tool._auto_out
            or re.search(derived_re, cur_name, re.IGNORECASE)):
        ws = arcpy.env.workspace or arcpy.env.scratchGDB
        unique = _unique_name(out_base, ws)
        p_out.value = unique
        tool._auto_out = os.path.basename(unique)

    obase = os.path.splitext(os.path.basename(p_out.valueAsText or out_base))[0]
    tbl_ws = os.path.dirname(p_out.valueAsText) if p_out.valueAsText else None
    tbl_ws = tbl_ws or arcpy.env.workspace or arcpy.env.scratchGDB
    # The output statistics tables are redundant when the stats come from existing
    # tables, so disable the whole section in that case.
    for gen, tbl, suffix, key in _MAHAL_TABLE_SPECS:
        p_chk, p_tbl = P[gen], P[tbl]
        p_chk.enabled = not is_tab
        p_tbl.enabled = (not is_tab) and bool(p_chk.value)
        cur_tbl = p_tbl.valueAsText or ""
        cur_tname = os.path.splitext(os.path.basename(cur_tbl))[0]
        if p_chk.value and not is_tab:
            if cur_tbl == "" or cur_tname == tool._auto_tables.get(key):
                full = _unique_name("{}_{}".format(obase, suffix), tbl_ws)
                p_tbl.value = full
                tool._auto_tables[key] = os.path.splitext(os.path.basename(full))[0]
        else:
            # a disabled OUTPUT param must be EMPTIED unconditionally (not only
            # when auto-named): any leftover value -- e.g. the stats tables a
            # previous run generated and this run now SUPPLIES as inputs --
            # would be pre-deleted by the GP framework on Run (overwrite).
            if cur_tbl:
                p_tbl.value = None
            tool._auto_tables.pop(key, None)

    if "points" in P:
        P["points"].enabled = is_pts
    if "stats_points" in P:
        P["stats_points"].enabled = is_pts
    P["use_selected"].enabled = is_pts
    if "cat_raster" in P:
        P["cat_raster"].enabled = is_cat
        P["cat_field"].enabled = is_cat
        P["cat_value"].enabled = is_cat
    P["mean_table"].enabled = is_tab
    P["cov_table"].enabled = is_tab


def _mahal_messages(tool, P):
    """Shared updateMessages: RAT warning, polygon-field check, source + order checks."""
    p_raster, p_poly = P["raster_vars"], P["poly_vars"]
    if not p_raster.values:
        p_raster.setErrorMessage(
            "Add at least one raster. The landscape variables are always required -- they "
            "supply each location's value vector. The mean vector and covariance (whether "
            "computed here or read from existing tables) describe these same variables, in "
            "this order.")
    flagged = _rat_flagged(p_raster)
    if flagged:
        p_raster.setWarningMessage(
            "These rasters have attribute tables, which will be IGNORED -- the tool uses "
            "their cell values: {}.".format(", ".join(flagged)))
    if p_poly.value:
        bad = [str(r[0]) for r in p_poly.value if not (len(r) > 1 and r[1])]
        if bad:
            p_poly.setErrorMessage(
                "Each polygon variable needs a numeric field selected in the Numeric "
                "field column.")
    source = P["stats_source"].valueAsText
    if "points" in P and source == mahalanobis.SRC_POINTS and not P["points"].value:
        P["points"].setErrorMessage("Select the sample point features.")
    if source == mahalanobis.SRC_CATEGORICAL and "cat_raster" in P:
        if not P["cat_raster"].value:
            P["cat_raster"].setErrorMessage("Select the categorical raster.")
        if P["cat_value"].value in (None, ""):
            P["cat_value"].setErrorMessage("Enter the category cell value to use.")
    if source == mahalanobis.SRC_TABLES:
        if not P["mean_table"].value:
            P["mean_table"].setErrorMessage("Select the mean vector table.")
        if not P["cov_table"].value:
            P["cov_table"].setErrorMessage("Select the covariance matrix table.")
        if (P["raster_vars"].values or p_poly.value) and (P["mean_table"].value or P["cov_table"].value):
            try:
                meta = mahalanobis.decode_variable_metadata(
                    P["mean_table"].valueAsText or P["cov_table"].valueAsText)
            except Exception:
                meta = None
            if meta and meta.get("variables"):
                want = [v["name"] for v in meta["variables"]]
                have = _expanded_var_names(p_raster, p_poly, _build_exclude(P["exclude_bands"]))
                if have and have != want:
                    p_raster.setWarningMessage(
                        "Variable order does not match the tables' stored order [{}]. A "
                        "wrong order produces incorrect distances.".format(", ".join(want)))


def _mahal_save_opts(P):
    opts = {}
    for gen, tbl, _suffix, key in _MAHAL_TABLE_SPECS:
        if P[gen].value and P[tbl].valueAsText:
            opts[key] = P[tbl].valueAsText
    return opts


def _mahal_core_params():
    """Parameters common (same name/definition) to both Mahalanobis tools, in order:
    sample_method, stats_source, cat_raster/field/value, mean/cov tables, inversion."""
    sample_method = arcpy.Parameter(
        displayName="Cell value sampling", name="sample_method", datatype="GPString",
        parameterType="Optional", direction="Input")
    sample_method.filter.type = "ValueList"
    sample_method.filter.list = [mahalanobis.SAMPLE_EXACT, mahalanobis.SAMPLE_INTERP]
    sample_method.value = mahalanobis.SAMPLE_EXACT

    stats_source = arcpy.Parameter(
        displayName="Source of mean vector and covariance matrix", name="stats_source",
        datatype="GPString", parameterType="Required", direction="Input")
    stats_source.filter.type = "ValueList"
    stats_source.filter.list = [mahalanobis.SRC_POINTS, mahalanobis.SRC_CATEGORICAL,
                                mahalanobis.SRC_TABLES]
    stats_source.value = mahalanobis.SRC_POINTS

    cat_raster = arcpy.Parameter(
        displayName="Categorical raster", name="cat_raster", datatype="GPRasterLayer",
        parameterType="Optional", direction="Input")
    cat_field = arcpy.Parameter(
        displayName="Category field (informational; cell values are used)", name="cat_field",
        datatype="Field", parameterType="Optional", direction="Input")
    cat_field.parameterDependencies = [cat_raster.name]
    cat_value = arcpy.Parameter(
        displayName="Category cell value", name="cat_value", datatype="GPString",
        parameterType="Optional", direction="Input")

    mean_table = arcpy.Parameter(
        displayName="Mean vector table", name="mean_table", datatype="GPTableView",
        parameterType="Optional", direction="Input")
    cov_table = arcpy.Parameter(
        displayName="Covariance matrix table", name="cov_table", datatype="GPTableView",
        parameterType="Optional", direction="Input")

    inversion = arcpy.Parameter(
        displayName="Covariance inversion method", name="inversion", datatype="GPString",
        parameterType="Required", direction="Input")
    inversion.filter.type = "ValueList"
    inversion.filter.list = [mahalanobis.INV_STANDARD, mahalanobis.INV_PSEUDO]
    inversion.value = mahalanobis.INV_STANDARD
    return [sample_method, stats_source, cat_raster, cat_field, cat_value,
            mean_table, cov_table, inversion]


def _out_type_param():
    p = arcpy.Parameter(displayName="Output value", name="out_type", datatype="GPString",
                        parameterType="Required", direction="Input")
    p.filter.type = "ValueList"
    p.filter.list = [mahalanobis.OUT_D2, mahalanobis.OUT_D, mahalanobis.OUT_PVALUE]
    p.value = mahalanobis.OUT_D2
    return p


# Points tool: the three output values as independent checkboxes (any or all).
_OUT_TYPE_CHECKS = [
    ("out_d2", "Squared distance (D2) -- traditional for ecological modeling", True),
    ("out_d", "Distance (D)", False),
    ("out_pval", "Chi-square p-value", False),
]


def _out_type_checks():
    params = []
    for name, label, default in _OUT_TYPE_CHECKS:
        p = arcpy.Parameter(displayName=label, name=name, datatype="GPBoolean",
                            parameterType="Optional", direction="Input")
        p.value = default
        params.append(p)
    return params


def _out_types_selected(P):
    """The selected OUT_* constants, in canonical order, from the checkbox params."""
    mapping = [("out_d2", mahalanobis.OUT_D2), ("out_d", mahalanobis.OUT_D),
               ("out_pval", mahalanobis.OUT_PVALUE)]
    return [const for name, const in mapping if name in P and P[name].value]


class MahalanobisRaster(object):
    """Mahalanobis Distance Raster -- the primary tool of the Mahalanobis suite.

    Parameters are accessed by name (not index). A multiband raster in the Raster
    Landscape Variables list expands to one variable per band at run time; use the
    Bands to exclude table to drop specific bands.
    """

    CAT = "Optional output statistics tables"

    def __init__(self):
        self.label = "Mahalanobis Distance Raster"
        self.description = (
            "Creates a raster of Mahalanobis distances: for every cell, the squared "
            "distance (x - mu)' * Sigma^-1 * (x - mu) between the cell's vector of "
            "landscape-variable values and the multivariate mean of a reference sample, "
            "scaled by the inverse covariance matrix. The mean vector and covariance can "
            "be derived from sample points, from cells of a categorical raster, or read "
            "from existing tables. Output can be squared distance, distance, or a "
            "chi-square p-value. Computed cell-by-cell (block vectorized) for speed and "
            "safety on large, many-variable rasters.")
        self.category = "Mahalanobis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_tables = {}

    def getParameterInfo(self):
        points = arcpy.Parameter(
            displayName="Sample points", name="points", datatype="GPFeatureLayer",
            parameterType="Optional", direction="Input")
        points.filter.list = ["Point", "Multipoint"]
        use_selected = arcpy.Parameter(
            displayName="Use only the selected points", name="use_selected",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        use_selected.value = False
        out_raster = arcpy.Parameter(
            displayName="Output Mahalanobis raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")

        sm, ss, cr, cf, cv, mt, ct, inv = _mahal_core_params()
        # Sample points (and its selection toggle) sit with the other input datasets,
        # above the Cell value sampling option.
        return [_make_raster_vars_param(), _make_poly_vars_param(), _make_exclude_bands_param(),
                points, use_selected, sm, ss, cr, cf, cv, mt, ct, inv,
                out_raster, _out_type_param()] + _make_mahal_table_params(self.CAT)

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        _mahal_update(self, {p.name: p for p in parameters}, "out_raster",
                      "Mahalanobis", r"_MahalanobisRaster\d*$")
        return

    def updateMessages(self, parameters):
        _mahal_messages(self, {p.name: p for p in parameters})
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(mahalanobis)
            P = {p.name: p for p in parameters}
            source = P["stats_source"].valueAsText
            points = P["points"].valueAsText if source == mahalanobis.SRC_POINTS else None
            use_selected = bool(P["use_selected"].value)
            if points and not use_selected:
                try:
                    points = arcpy.Describe(points).catalogPath
                except Exception:
                    pass

            out_lyrx = mahalanobis.run_mahalanobis_raster(
                var_rows=_combined_var_rows(P["raster_vars"], P["poly_vars"]),
                sample_method=(P["sample_method"].valueAsText or mahalanobis.SAMPLE_EXACT),
                stats_source=source,
                out_type=P["out_type"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                inversion=P["inversion"].valueAsText,
                points=points,
                use_selected=use_selected,
                cat_raster=P["cat_raster"].valueAsText,
                cat_field=P["cat_field"].valueAsText,
                cat_value=P["cat_value"].valueAsText,
                mean_table=P["mean_table"].valueAsText,
                cov_table=P["cov_table"].valueAsText,
                save_opts=(None if source == mahalanobis.SRC_TABLES
                           else (_mahal_save_opts(P) or None)),
                exclude_bands=_build_exclude(P["exclude_bands"]),
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class MahalanobisPoints(object):
    """Mahalanobis Distance at Points -- a Mahalanobis value at each input point.

    Like the raster tool but with target_points first, a stats_points sample (defaults
    to the target points), and a point feature-class output.
    """

    CAT = "Optional output statistics tables"

    def __init__(self):
        self.label = "Mahalanobis Distance at Points"
        self.description = (
            "Calculates a Mahalanobis value at each input point: the squared distance "
            "(x - mu)' * Sigma^-1 * (x - mu) between the point's vector of landscape-variable "
            "values and the multivariate mean of a reference sample. The output is a copy of "
            "the input points with a Mahalanobis value field; points outside the landscape "
            "data are null. Mean and covariance can come from sample points, a categorical "
            "raster, or existing tables; output value can be squared distance, distance, or a "
            "chi-square p-value.")
        self.category = "Mahalanobis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_tables = {}

    def getParameterInfo(self):
        target_points = arcpy.Parameter(
            displayName="Input points to evaluate", name="target_points",
            datatype="GPFeatureLayer", parameterType="Required", direction="Input")
        target_points.filter.list = ["Point", "Multipoint"]
        stats_points = arcpy.Parameter(
            displayName="Sample points for statistics (defaults to the input points)",
            name="stats_points", datatype="GPFeatureLayer",
            parameterType="Optional", direction="Input")
        stats_points.filter.list = ["Point", "Multipoint"]
        use_selected = arcpy.Parameter(
            displayName="Use only the selected statistics points", name="use_selected",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        use_selected.value = False
        out_points = arcpy.Parameter(
            displayName="Output points", name="out_points", datatype="DEFeatureClass",
            parameterType="Required", direction="Output")

        sm, ss, cr, cf, cv, mt, ct, inv = _mahal_core_params()
        # Categorical-raster statistics are not offered at points; only on-the-fly
        # from the point sample or precomputed from existing tables.
        ss.filter.list = [mahalanobis.SRC_POINTS, mahalanobis.SRC_TABLES]
        return [target_points, _make_raster_vars_param(), _make_poly_vars_param(),
                _make_exclude_bands_param(), sm, ss, stats_points, use_selected,
                mt, ct, inv, out_points] + _out_type_checks() \
            + _make_mahal_table_params(self.CAT)

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        _mahal_update(self, {p.name: p for p in parameters}, "out_points",
                      "Mahalanobis_Points", r"_MahalanobisPoints\d*$")
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        _mahal_messages(self, P)
        if not _out_types_selected(P):
            P["out_d2"].setErrorMessage(
                "Select at least one output value (D2, D, or p-value).")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(mahalanobis)
            P = {p.name: p for p in parameters}
            source = P["stats_source"].valueAsText
            stats_points = (P["stats_points"].valueAsText
                            if source == mahalanobis.SRC_POINTS else None)
            use_selected = bool(P["use_selected"].value)
            if stats_points and not use_selected:
                try:
                    stats_points = arcpy.Describe(stats_points).catalogPath
                except Exception:
                    pass

            mahalanobis.run_mahalanobis_points(
                target_points=P["target_points"].valueAsText,
                var_rows=_combined_var_rows(P["raster_vars"], P["poly_vars"]),
                sample_method=(P["sample_method"].valueAsText or mahalanobis.SAMPLE_EXACT),
                stats_source=source,
                out_types=_out_types_selected(P),
                out_points=P["out_points"].valueAsText,
                inversion=P["inversion"].valueAsText,
                stats_points=stats_points,
                use_selected=use_selected,
                cat_raster=None,
                cat_field=None,
                cat_value=None,
                mean_table=P["mean_table"].valueAsText,
                cov_table=P["cov_table"].valueAsText,
                save_opts=(None if source == mahalanobis.SRC_TABLES
                           else (_mahal_save_opts(P) or None)),
                exclude_bands=_build_exclude(P["exclude_bands"]),
                messages=messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class MahalanobisCovariance(object):
    """Mahalanobis statistics tables from datasets -- the producer for the suite.

    Computes the mean vector, covariance matrix, inverse covariance, and/or correlation
    matrix for a set of landscape variables and writes them as tables (with embedded
    variable-order metadata) that the Raster / at Points tools consume via their
    "Existing tables" source. Statistics come from sample points or a categorical raster.
    """

    # The four output tables, in order: (engine key, parameter name, default base name).
    _OUT_SPECS = [("mean", "out_mean", "Mahalanobis_Means"),
                  ("cov", "out_cov", "Mahalanobis_Covariance"),
                  ("invcov", "out_invcov", "Mahalanobis_Inv_Covariance"),
                  ("corr", "out_corr", "Mahalanobis_Correlation")]
    # Tables defaulted on first open (the mean + covariance pair the other tools need).
    _DEFAULT_KEYS = ("mean", "cov")

    def __init__(self):
        self.label = "Calculate Statistical Matrices"
        self.description = (
            "Computes the mean vector, covariance matrix, inverse covariance matrix, "
            "and/or correlation matrix for a set of landscape variables, and writes them "
            "as tables. The mean and covariance tables can then be supplied to the "
            "Mahalanobis Raster or at Points tools through their 'Existing tables' source, "
            "so the statistics are computed once and reused. The statistics are derived "
            "from sample points or from cells of a categorical raster. Each table embeds "
            "the ordered variable list so the consuming tools can validate that the same "
            "variables are used, in the same order.")
        self.category = "Mahalanobis Tools"
        self.canRunInBackground = False
        self._auto_tables = {}

    def getParameterInfo(self):
        points = arcpy.Parameter(
            displayName="Sample points", name="points", datatype="GPFeatureLayer",
            parameterType="Optional", direction="Input")
        points.filter.list = ["Point", "Multipoint"]
        use_selected = arcpy.Parameter(
            displayName="Use only the selected points", name="use_selected",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        use_selected.value = False

        out_params = []
        for _key, name, _base in self._OUT_SPECS:
            label = {"out_mean": "Output mean vector table",
                     "out_cov": "Output covariance matrix table",
                     "out_invcov": "Output inverse covariance matrix table",
                     "out_corr": "Output correlation matrix table"}[name]
            out_params.append(arcpy.Parameter(
                displayName=label, name=name, datatype="DETable",
                parameterType="Optional", direction="Output"))

        sm, ss, cr, cf, cv, mt, ct, inv = _mahal_core_params()
        # Existing tables is not a valid source here -- this tool *produces* those tables.
        ss.filter.list = [mahalanobis.SRC_POINTS, mahalanobis.SRC_CATEGORICAL]
        return [_make_raster_vars_param(), _make_poly_vars_param(),
                _make_exclude_bands_param(), sm, ss, points, use_selected,
                cr, cf, cv, inv] + out_params

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        P["sample_method"].enabled = True
        # The Bands-to-exclude control is only meaningful when a multiband raster is present.
        P["exclude_bands"].enabled = _any_multiband(P["raster_vars"])
        source = P["stats_source"].valueAsText
        is_pts = (source == mahalanobis.SRC_POINTS)
        is_cat = (source == mahalanobis.SRC_CATEGORICAL)
        P["points"].enabled = is_pts
        P["use_selected"].enabled = is_pts
        P["cat_raster"].enabled = is_cat
        P["cat_field"].enabled = is_cat
        P["cat_value"].enabled = is_cat
        # The inversion method only governs the inverse-covariance table.
        P["inversion"].enabled = bool(P["out_invcov"].value)

        # Default the mean + covariance table names on first open, and keep re-defaulting
        # only while they still hold our auto value (so a name the user typed -- or cleared
        # to opt out -- is left alone).
        ws = arcpy.env.workspace or arcpy.env.scratchGDB
        for key, name, base in self._OUT_SPECS:
            if key not in self._DEFAULT_KEYS:
                continue
            p_tbl = P[name]
            cur = p_tbl.valueAsText or ""
            cur_name = os.path.splitext(os.path.basename(cur))[0]
            prev = self._auto_tables.get(key)
            if (cur == "" and prev is None) or (prev is not None and cur_name == prev):
                full = _unique_name(base, ws)
                p_tbl.value = full
                self._auto_tables[key] = os.path.splitext(os.path.basename(full))[0]
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        _mahal_messages(self, P)
        if not any(P[name].value for _key, name, _base in self._OUT_SPECS):
            P["out_mean"].setErrorMessage(
                "Specify at least one output table (mean vector, covariance matrix, "
                "inverse covariance matrix, or correlation matrix).")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(mahalanobis)
            P = {p.name: p for p in parameters}
            source = P["stats_source"].valueAsText
            points = P["points"].valueAsText if source == mahalanobis.SRC_POINTS else None
            use_selected = bool(P["use_selected"].value)
            if points and not use_selected:
                try:
                    points = arcpy.Describe(points).catalogPath
                except Exception:
                    pass

            out_tables = {}
            for key, name, _base in self._OUT_SPECS:
                if P[name].valueAsText:
                    out_tables[key] = P[name].valueAsText

            mahalanobis.run_covariance_from_datasets(
                var_rows=_combined_var_rows(P["raster_vars"], P["poly_vars"]),
                sample_method=(P["sample_method"].valueAsText or mahalanobis.SAMPLE_EXACT),
                stats_source=source,
                out_tables=out_tables,
                inversion=P["inversion"].valueAsText,
                points=points,
                use_selected=use_selected,
                cat_raster=P["cat_raster"].valueAsText,
                cat_field=P["cat_field"].valueAsText,
                cat_value=P["cat_value"].valueAsText,
                exclude_bands=_build_exclude(P["exclude_bands"]),
                messages=messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class MahalanobisChiSquare(object):
    """Chi-square / p-value transform of a Mahalanobis squared-distance (D2) raster.

    Rescales a D2 raster to a 0-1 chi-square typicality p-value. Degrees of freedom =
    number of variables; pre-filled from the input raster's metadata when our Mahalanobis
    Raster tool created it.
    """

    def __init__(self):
        self.label = "Mahalanobis Distance - Chi-Square Transform"
        self.description = (
            "Transforms a Mahalanobis squared-distance (D2) raster into a chi-square "
            "typicality p-value raster. For every cell, p = P(chi-square > D2) with degrees "
            "of freedom equal to the number of variables, producing a 0 to 1 surface where "
            "values near 1 indicate a close match to the reference conditions. When the "
            "input D2 raster was created by the Mahalanobis Raster tool, the degrees of "
            "freedom are read from its metadata and pre-filled.")
        self.category = "Mahalanobis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._df_src = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input squared-distance (D2) raster", name="in_raster",
            datatype="GPRasterLayer", parameterType="Required", direction="Input")
        df = arcpy.Parameter(
            displayName="Degrees of freedom (number of variables)", name="df",
            datatype="GPLong", parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output chi-square p-value raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        return [in_raster, df, out_raster]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        p_in, p_df, p_out = P["in_raster"], P["df"], P["out_raster"]

        # Pre-fill df from the input raster's recorded variable count (our D2 rasters carry
        # it). Don't clobber a value the user typed -- only fill when empty or still equal
        # to the last value we auto-filled.
        if p_in.value:
            try:
                n, _ot = mahalanobis.read_recorded_df(p_in.valueAsText)
            except Exception:
                n = None
            if n and (p_df.value in (None, "") or p_df.value == self._df_src):
                p_df.value = n
                self._df_src = n

        # Default output name "Mahalanobis_Pvalue", auto-incremented, re-defaulting while
        # the user has not renamed it.
        cur = p_out.valueAsText or ""
        cur_name = os.path.basename(cur)
        if cur_name.lower().endswith(".tif"):
            cur_name = cur_name[:-4]
        if cur == "" or cur_name == self._auto_out:
            ws = arcpy.env.workspace or arcpy.env.scratchGDB
            unique = _unique_name("Mahalanobis_Pvalue", ws)
            p_out.value = unique
            self._auto_out = os.path.basename(unique)
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        p_in, p_df = P["in_raster"], P["df"]
        if p_df.value is not None and p_df.value < 1:
            p_df.setErrorMessage(
                "Degrees of freedom must be a positive integer (the number of variables).")
        if p_in.value:
            try:
                _n, ot = mahalanobis.read_recorded_df(p_in.valueAsText)
            except Exception:
                ot = None
            if ot and ot != mahalanobis.OUT_D2:
                p_in.setWarningMessage(
                    "This raster's metadata indicates it holds '{}', not squared distance "
                    "(D2). The chi-square transform expects a D2 raster.".format(ot))
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(mahalanobis)
            P = {p.name: p for p in parameters}
            out_lyrx = mahalanobis.run_chi_square_transform(
                in_raster=P["in_raster"].valueAsText,
                df=int(P["df"].value),
                out_raster=P["out_raster"].valueAsText,
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


# --------------------------------------------------------------------------- #
# Kernel Density Tools
# --------------------------------------------------------------------------- #
def _kd_default_ws(in_raster):
    """A writable geodatabase for suggested outputs: the input raster's geodatabase
    if it lives in one, otherwise the scratch geodatabase."""
    try:
        ws = os.path.dirname(arcpy.Describe(in_raster).catalogPath)
        d = arcpy.Describe(ws)
        if d.dataType in ("Workspace", "FeatureDataset") and \
                getattr(d, "workspaceType", "") in ("LocalDatabase", "RemoteDatabase"):
            return ws
    except Exception:
        pass
    return arcpy.env.scratchGDB


def _kd_basename(in_raster):
    try:
        return arcpy.Describe(in_raster).baseName
    except Exception:
        return "KernelDensity"


class KernelDensityGenerate(object):
    """Kernel Density -- a custom kernel-density surface from point features.

    Replicates Esri's Kernel Density for the quartic (default) planar case and adds
    other finite-support kernel shapes, an explicit planar/geodesic distance choice
    (forced geodesic for geographic data), and three output value types.
    """

    def __init__(self):
        self.label = "Kernel Density Enhanced"
        self.description = (
            "Creates a kernel-density surface from point features. The quartic kernel "
            "(default) reproduces the Esri Kernel Density tool; other finite-support "
            "kernel shapes (uniform, triangular, Epanechnikov, triweight, tricube, "
            "cosine -- all reaching zero at the search radius) are also offered. "
            "Distances may be planar or geodesic (geodesic is required, and forced, for "
            "geographic data). Output cell values can be densities (per a chosen area "
            "unit), expected counts, or a proportion-under-the-curve (volume) surface. "
            "If no search radius is given, a default is computed by Silverman's rule.")
        self.category = "Kernel Density Tools"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_features = arcpy.Parameter(
            displayName="Input point features", name="in_features",
            datatype="GPFeatureLayer", parameterType="Required", direction="Input")
        in_features.filter.list = ["Point", "Multipoint"]
        pop_field = arcpy.Parameter(
            displayName="Population field", name="population_field",
            datatype="Field", parameterType="Optional", direction="Input")
        pop_field.filter.list = ["Short", "Long", "Float", "Double"]
        pop_field.parameterDependencies = [in_features.name]
        out_raster = arcpy.Parameter(
            displayName="Output raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        cell_size = arcpy.Parameter(
            displayName="Output cell size", name="cell_size",
            datatype="analysis_cell_size", parameterType="Optional", direction="Input")
        search_radius = arcpy.Parameter(
            displayName="Search radius (bandwidth)", name="search_radius",
            datatype="GPLinearUnit", parameterType="Optional", direction="Input")
        kernel = arcpy.Parameter(
            displayName="Kernel shape", name="kernel",
            datatype="GPString", parameterType="Required", direction="Input")
        kernel.filter.type = "ValueList"
        kernel.filter.list = [lab for lab, _ in kernel_density.KERNEL_CHOICES]
        kernel.value = kernel.filter.list[0]
        distance_method = arcpy.Parameter(
            displayName="Distance method", name="distance_method",
            datatype="GPString", parameterType="Required", direction="Input")
        distance_method.filter.type = "ValueList"
        distance_method.filter.list = [kernel_density.DIST_PLANAR, kernel_density.DIST_GEODESIC]
        distance_method.value = kernel_density.DIST_PLANAR
        out_value = arcpy.Parameter(
            displayName="Output cell values", name="out_value",
            datatype="GPString", parameterType="Required", direction="Input")
        out_value.filter.type = "ValueList"
        out_value.filter.list = list(kernel_density.OUTPUT_VALUES)
        out_value.value = kernel_density.OUTV_DENSITIES
        area_unit = arcpy.Parameter(
            displayName="Area units", name="area_unit",
            datatype="GPString", parameterType="Optional", direction="Input")
        area_unit.filter.type = "ValueList"
        area_unit.filter.list = [lab for lab, _ in kernel_density.AREA_UNITS]
        area_unit.value = "Square map units"
        return [in_features, pop_field, out_raster, cell_size, search_radius,
                kernel, distance_method, out_value, area_unit]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inf, outr = P["in_features"], P["out_raster"]
        if inf.valueAsText and (not outr.altered or outr.valueAsText == self._auto_out):
            try:
                base = _legalize(_kd_basename(inf.valueAsText) + "_KDens")
                sug = _unique_name(base, _kd_default_ws(inf.valueAsText))
                outr.value = sug
                self._auto_out = sug
            except Exception:
                pass
        # Auto-fill Output cell size with the Esri default logic, until the user
        # types their own value (cell_size.altered).
        if inf.valueAsText and not P["cell_size"].altered:
            try:
                v = kernel_density.default_cell_size(inf.valueAsText)
                if v and v > 0:
                    P["cell_size"].value = v
            except Exception:
                pass
        # Geographic data must use geodesic distances; lock the control.
        is_geographic = False
        if inf.valueAsText:
            try:
                sr = arcpy.Describe(inf.valueAsText).spatialReference
                is_geographic = (sr is not None and sr.type == "Geographic")
            except Exception:
                is_geographic = False
        if is_geographic:
            P["distance_method"].value = kernel_density.DIST_GEODESIC
            P["distance_method"].enabled = False
        else:
            P["distance_method"].enabled = True
        # Area units apply only to the Densities output.
        P["area_unit"].enabled = (P["out_value"].value == kernel_density.OUTV_DENSITIES)
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(kernel_density)
            P = {p.name: p for p in parameters}
            kernel_density.run_kernel_density(
                points=P["in_features"].valueAsText,
                pop_field=P["population_field"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                cell_size=P["cell_size"].valueAsText,
                search_radius=P["search_radius"].valueAsText,
                kernel=P["kernel"].valueAsText,
                distance_method=P["distance_method"].valueAsText,
                out_value=P["out_value"].valueAsText,
                area_unit=P["area_unit"].valueAsText,
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class KernelDensityProportion(object):
    """Kernel Density to Proportion Surface -- the "volume" raster.

    Each output cell holds the percent of the total volume (sum of densities)
    enclosed by the isopleth through it. Contour the result (Esri Contour) at any
    levels (e.g. 50, 95, 99) to get home-range isopleths directly.
    """

    def __init__(self):
        self.label = "Kernel Density to Proportion Surface"
        self.description = (
            "Converts a kernel-density raster into a proportion-under-the-surface "
            "(volume) raster: each cell holds the percent (0-100) of the total volume "
            "contained in all cells whose density is greater than or equal to that "
            "cell's density. Peak cells are near 0, the outer edge near 100, so "
            "contouring the output at 50/95/99 yields the corresponding home-range "
            "isopleths. Volume is the density sum (constant cell area cancels); zero "
            "and NoData cells are excluded.")
        self.category = "Kernel Density Tools"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input kernel density raster", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output proportion raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        return [in_raster, out_raster]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr = P["in_raster"], P["out_raster"]
        if inr.valueAsText and (not outr.altered or outr.valueAsText == self._auto_out):
            try:
                base = _legalize(_kd_basename(inr.valueAsText) + "_Proportion")
                sug = _unique_name(base, _kd_default_ws(inr.valueAsText))
                outr.value = sug
                self._auto_out = sug
            except Exception:
                pass
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(kernel_density)
            P = {p.name: p for p in parameters}
            kernel_density.run_proportion_surface(
                kd=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class KernelDensityContours(object):
    """Kernel Density Probability Contours -- isopleth polylines and/or polygon areas
    at the standard home-range probabilities (5%..100% in 5% steps)."""

    def __init__(self):
        self.label = "Kernel Density Probability Contours"
        self.description = (
            "Generates probability isopleths from a kernel-density raster at the "
            "standard home-range levels (5% to 100% in 5% steps), as contour polylines "
            "and/or polygon areas. Each level's density is the threshold enclosing that "
            "percent of the total volume. Choose either or both outputs (at least one is "
            "required); polygon boundaries coincide with the polylines. Reproduces the "
            "legacy 'Probabilities' output.")
        self.category = "Kernel Density Tools"
        self.canRunInBackground = False
        self._auto = {}

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input kernel density raster", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        make_lines = arcpy.Parameter(
            displayName="Create contour polylines", name="make_lines",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        make_lines.value = True
        out_lines = arcpy.Parameter(
            displayName="Output contour polylines", name="out_lines",
            datatype="DEFeatureClass", parameterType="Optional", direction="Output")
        make_polys = arcpy.Parameter(
            displayName="Create contour polygon areas", name="make_polys",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        make_polys.value = True
        out_polys = arcpy.Parameter(
            displayName="Output contour polygon areas", name="out_polys",
            datatype="DEFeatureClass", parameterType="Optional", direction="Output")
        return [in_raster, make_lines, out_lines, make_polys, out_polys]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr = P["in_raster"]
        ws = _kd_default_ws(inr.valueAsText) if inr.valueAsText else None
        base = _kd_basename(inr.valueAsText) if inr.valueAsText else None
        for box, out, suffix in [("make_lines", "out_lines", "_Prob_Lines"),
                                 ("make_polys", "out_polys", "_Prob_Areas")]:
            on = bool(P[box].value)
            P[out].enabled = on
            if on and base and (not P[out].altered or P[out].valueAsText == self._auto.get(out)):
                try:
                    sug = _unique_name(_legalize(base + suffix), ws)
                    P[out].value = sug
                    self._auto[out] = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        if not P["make_lines"].value and not P["make_polys"].value:
            P["make_lines"].setErrorMessage(
                "Choose at least one output: contour polylines and/or polygon areas.")
        if P["make_lines"].value and not P["out_lines"].valueAsText:
            P["out_lines"].setErrorMessage(
                "Specify an output polyline feature class (or uncheck this option).")
        if P["make_polys"].value and not P["out_polys"].valueAsText:
            P["out_polys"].setErrorMessage(
                "Specify an output polygon feature class (or uncheck this option).")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(kernel_density)
            P = {p.name: p for p in parameters}
            lines = P["out_lines"].valueAsText if P["make_lines"].value else None
            polys = P["out_polys"].valueAsText if P["make_polys"].value else None
            kernel_density.run_probability_contours(
                kd=P["in_raster"].valueAsText,
                out_lines=lines, out_polys=polys, messages=messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class ShadowPolygons(object):
    """Create Shadow Polygons -- fading ring buffers for a cartographic shadow.

    Dissolves the input's polygons (the selected ones if the layer has a
    selection, else all of them) into one source polygon, then builds N ring
    buffers around it. Each ring carries a Transparency value that increases
    outward, so symbolizing the rings in black with per-feature transparency
    (the delivered .lyrx does this automatically) renders a soft shadow that
    makes the source features stand out.
    """

    def __init__(self):
        self.label = "Create Shadow Polygons"
        self.description = (
            "Creates concentric ring buffers around the (dissolved) polygons of a "
            "feature class, with a Transparency attribute that fades outward -- a "
            "soft black cartographic shadow behind the source features. If the "
            "input layer has selected polygons, only those are used; otherwise all "
            "polygons are dissolved into the source. Geographic inputs are "
            "buffered geodesically. The dissolved source polygon is included in "
            "the output with Transparency = 100 (fully transparent). The rings "
            "are approximate (the source is generalized before buffering) and "
            "intended for cartographic effect, not analysis.")
        self.category = "Cartography Tools"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_features = arcpy.Parameter(
            displayName="Input polygon features", name="in_features",
            datatype="GPFeatureLayer", parameterType="Required", direction="Input")
        in_features.filter.list = ["Polygon"]
        out_fc = arcpy.Parameter(
            displayName="Output shadow feature class", name="out_fc",
            datatype="DEFeatureClass", parameterType="Required", direction="Output")
        num_rings = arcpy.Parameter(
            displayName="Number of buffers", name="num_rings",
            datatype="GPLong", parameterType="Required", direction="Input")
        num_rings.value = 10
        size_value = arcpy.Parameter(
            displayName="Size of outermost buffer", name="size_value",
            datatype="GPDouble", parameterType="Required", direction="Input")
        size_value.value = 0.25
        size_units = arcpy.Parameter(
            displayName="Size units", name="size_units",
            datatype="GPString", parameterType="Required", direction="Input")
        size_units.filter.type = "ValueList"
        size_units.filter.list = cartography_tools.SIZE_UNITS
        size_units.value = cartography_tools.SIZE_PROP_WIDTH
        min_transp = arcpy.Parameter(
            displayName="Minimum transparency (percent)", name="min_transparency",
            datatype="GPDouble", parameterType="Required", direction="Input")
        min_transp.value = 50.0
        return [in_features, out_fc, num_rings, size_value, size_units, min_transp]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inf, outf = P["in_features"], P["out_fc"]
        if inf.valueAsText and (not outf.altered or outf.valueAsText == self._auto_out):
            try:
                base = _legalize(os.path.splitext(
                    os.path.basename(inf.valueAsText))[0] + "_Shadow")
                ws = arcpy.env.workspace or os.path.dirname(
                    arcpy.Describe(inf.valueAsText).catalogPath)
                sug = _unique_name(base, ws)
                outf.value = sug
                self._auto_out = sug
            except Exception:
                pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        if P["num_rings"].value is not None and P["num_rings"].value < 1:
            P["num_rings"].setErrorMessage("The number of buffers must be at least 1.")
        if P["size_value"].value is not None and P["size_value"].value <= 0:
            P["size_value"].setErrorMessage("The buffer size must be positive.")
        mt = P["min_transparency"].value
        if mt is not None and not (0 <= mt < 100):
            P["min_transparency"].setErrorMessage(
                "The minimum transparency must be at least 0 and less than 100.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(cartography_tools)
            P = {p.name: p for p in parameters}
            lyrx = cartography_tools.run_shadow_polygons(
                in_features=P["in_features"].valueAsText,
                out_fc=P["out_fc"].valueAsText,
                num_rings=P["num_rings"].value,
                size_value=P["size_value"].value,
                size_units=P["size_units"].valueAsText,
                min_transparency=P["min_transparency"].value,
                messages=messages)
            if lyrx:
                try:
                    P["out_fc"].symbology = lyrx
                except Exception:
                    pass
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class EnhancedHillshade(object):
    """Enhanced Hillshade -- geodesic-correct hillshade with optional
    hypsometric shading and cast shadows.

    Unlike the standard Esri hillshade, this tool computes gradients on
    geographic (lat/long) DEMs from true spheroidal cell dimensions at every
    row's latitude, so the shading is correct at any latitude without
    projecting the DEM first. Projected DEMs use the faster planar formulas.
    """

    def __init__(self):
        self.label = "Enhanced Hillshade"
        self.description = (
            "Computes a hillshade (shaded relief) raster from a single-band DEM. "
            "Geographic (lat/long) DEMs are handled correctly: cell dimensions are "
            "recomputed for every row from the spheroid, so the shading is accurate "
            "at any latitude without projecting first. Options include the number of "
            "hillshade levels (255 matches the Esri look; fewer gives a posterized "
            "cartographic effect), hypsometric shading (darkens low elevations), "
            "vertical exaggeration, and modeled cast shadows (terrain hidden from "
            "the sun is set to the darkest value).")
        self.category = "Cartography Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output hillshade raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        azimuth = arcpy.Parameter(
            displayName="Azimuth to sun (0-360 degrees)", name="azimuth",
            datatype="GPDouble", parameterType="Required", direction="Input")
        azimuth.value = 315.0
        altitude = arcpy.Parameter(
            displayName="Inclination to sun (0-90 degrees)", name="altitude",
            datatype="GPDouble", parameterType="Required", direction="Input")
        altitude.value = 45.0
        levels = arcpy.Parameter(
            displayName="Hillshade levels (Esri hillshade = 255)", name="levels",
            datatype="GPLong", parameterType="Required", direction="Input")
        levels.value = 255
        use_hypso = arcpy.Parameter(
            displayName="Apply hypsometric shading", name="use_hypso",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        use_hypso.value = False
        hypso_strength = arcpy.Parameter(
            displayName="Hypsometric strength (0-100)", name="hypso_strength",
            datatype="GPLong", parameterType="Optional", direction="Input")
        hypso_strength.value = 50
        hypso_strength.enabled = False
        vert_exag = arcpy.Parameter(
            displayName="Vertical exaggeration factor", name="vert_exag",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        vert_exag.value = 1.0
        model_shadows = arcpy.Parameter(
            displayName="Model cast shadows", name="model_shadows",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        model_shadows.value = False
        shadow_dim = arcpy.Parameter(
            displayName="Shadow brightness (percent of unshadowed value)",
            name="shadow_dim",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        shadow_dim.value = 20.0
        shadow_dim.enabled = False
        params = [in_raster, out_raster, elev_units, azimuth, altitude, levels,
                  use_hypso, hypso_strength, vert_exag, model_shadows, shadow_dim]
        # Pre-select the choices from the last run saved with this project.
        saved = _load_tool_defaults("EnhancedHillshade")
        for p in params:
            if p.name in saved and p.name not in ("in_raster", "out_raster"):
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        hypso_strength.enabled = bool(use_hypso.value)
        shadow_dim.enabled = bool(model_shadows.value)
        return params

    def isLicensed(self):
        return True

    def _detect_units(self, in_value):
        """(units, locked): elevation units read from the DEM, and whether the
        choice is settled automatically. Geographic DEMs are meters (legacy
        convention); otherwise a defined vertical coordinate system decides.
        Returns (None, False) when the user must choose."""
        try:
            sr = arcpy.Describe(in_value).spatialReference
        except Exception:
            return None, False
        if sr is None:
            return None, False
        if sr.type == "Geographic":
            return "Meters", True
        try:
            vcs = sr.VCS
            unit = (getattr(vcs, "linearUnitName", "") or "") if vcs else ""
            if "foot" in unit.lower():
                return "Feet", True
            if "met" in unit.lower():
                return "Meters", True
        except Exception:
            pass
        return None, False

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr, units = P["in_raster"], P["out_raster"], P["elev_units"]
        if inr.valueAsText:
            detected, locked = self._detect_units(inr.valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(
                        os.path.basename(inr.valueAsText))[0] + "_Hillshade")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        P["hypso_strength"].enabled = bool(P["use_hypso"].value)
        P["shadow_dim"].enabled = bool(P["model_shadows"].value)
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr, units = P["in_raster"], P["elev_units"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage(
                        "The elevation raster must be single band.")
            except Exception:
                pass
            if units.enabled and not units.valueAsText:
                units.setErrorMessage(
                    "The DEM does not specify its elevation units; choose "
                    "Meters or Feet.")
        az = P["azimuth"].value
        if az is not None and not (0 <= az <= 360):
            P["azimuth"].setErrorMessage("The azimuth must be 0 to 360 degrees.")
        alt = P["altitude"].value
        if alt is not None and not (0 <= alt <= 90):
            P["altitude"].setErrorMessage(
                "The inclination must be 0 to 90 degrees.")
        if P["levels"].value is not None and P["levels"].value < 1:
            P["levels"].setErrorMessage("There must be at least 1 hillshade level.")
        st = P["hypso_strength"].value
        if P["use_hypso"].value and st is not None and not (0 <= st <= 100):
            P["hypso_strength"].setErrorMessage(
                "The hypsometric strength must be 0 to 100.")
        ex = P["vert_exag"].value
        if ex is not None and ex <= 0:
            P["vert_exag"].setErrorMessage(
                "The vertical exaggeration must be positive.")
        sd = P["shadow_dim"].value
        if P["model_shadows"].value and sd is not None and not (0 <= sd <= 100):
            P["shadow_dim"].setErrorMessage(
                "The shadow brightness must be 0 to 100 percent.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(hillshade)
            P = {p.name: p for p in parameters}
            out_lyrx = hillshade.run_enhanced_hillshade(
                in_raster=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                elev_units=P["elev_units"].valueAsText or "Meters",
                azimuth=P["azimuth"].value,
                altitude=P["altitude"].value,
                levels=P["levels"].value,
                use_hypso=bool(P["use_hypso"].value),
                hypso_strength=P["hypso_strength"].value or 50,
                vert_exag=P["vert_exag"].value or 1.0,
                model_shadows=bool(P["model_shadows"].value),
                shadow_dim=(P["shadow_dim"].value
                            if P["shadow_dim"].value is not None else 20.0),
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
            # Remember the choices (not the datasets) with the project.
            _save_tool_defaults("EnhancedHillshade", {
                "elev_units": P["elev_units"].valueAsText or "Meters",
                "azimuth": P["azimuth"].value,
                "altitude": P["altitude"].value,
                "levels": P["levels"].value,
                "use_hypso": bool(P["use_hypso"].value),
                "hypso_strength": P["hypso_strength"].value,
                "vert_exag": P["vert_exag"].value,
                "model_shadows": bool(P["model_shadows"].value),
                "shadow_dim": P["shadow_dim"].value,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class MDOWHillshade(object):
    """Multi-Directional Oblique-Weighted (MDOW) hillshade -- four blended
    sun directions bring out terrain features that a single light source
    washes out (Mark 1992, USGS Open-File Report 92-422). Geodesic-correct
    on geographic DEMs like Enhanced Hillshade.
    """

    def __init__(self):
        self.label = "MDOW Hillshade"
        self.description = (
            "Computes a Multi-Directional Oblique-Weighted (MDOW) hillshade from a "
            "single-band DEM (after Mark 1992, USGS Open-File Report 92-422). Four "
            "hillshades are computed with sun directions fanned -67.5, -22.5, +22.5 "
            "and +67.5 degrees around the primary illumination direction and blended "
            "per cell with sine-squared aspect weights, so every slope is lit most "
            "strongly from the directions oblique to it -- revealing terrain that a "
            "single-direction hillshade washes out. Geographic (lat/long) DEMs are "
            "handled correctly: cell dimensions are recomputed for every row from "
            "the spheroid, so the shading is accurate at any latitude without "
            "projecting first. Options include the number of hillshade levels, "
            "hypsometric shading (darkens low elevations), and vertical exaggeration.")
        self.category = "Cartography Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output hillshade raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        azimuth = arcpy.Parameter(
            displayName="Primary illumination direction (0-360 degrees)",
            name="azimuth",
            datatype="GPDouble", parameterType="Required", direction="Input")
        azimuth.value = 270.0
        altitude = arcpy.Parameter(
            displayName="Inclination to sun, all directions (0-90 degrees)",
            name="altitude",
            datatype="GPDouble", parameterType="Required", direction="Input")
        altitude.value = 30.0
        levels = arcpy.Parameter(
            displayName="Hillshade levels", name="levels",
            datatype="GPLong", parameterType="Required", direction="Input")
        levels.value = 256
        use_hypso = arcpy.Parameter(
            displayName="Apply hypsometric shading", name="use_hypso",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        use_hypso.value = False
        hypso_strength = arcpy.Parameter(
            displayName="Hypsometric strength (0-100)", name="hypso_strength",
            datatype="GPLong", parameterType="Optional", direction="Input")
        hypso_strength.value = 50
        hypso_strength.enabled = False
        vert_exag = arcpy.Parameter(
            displayName="Vertical exaggeration factor", name="vert_exag",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        vert_exag.value = 1.0
        params = [in_raster, out_raster, elev_units, azimuth, altitude, levels,
                  use_hypso, hypso_strength, vert_exag]
        # Pre-select the choices from the last run saved with this project.
        saved = _load_tool_defaults("MDOWHillshade")
        for p in params:
            if p.name in saved and p.name not in ("in_raster", "out_raster"):
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        hypso_strength.enabled = bool(use_hypso.value)
        return params

    def isLicensed(self):
        return True

    # Same auto-detection as Enhanced Hillshade (shared helper).
    _detect_units = EnhancedHillshade._detect_units

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr, units = P["in_raster"], P["out_raster"], P["elev_units"]
        if inr.valueAsText:
            detected, locked = self._detect_units(inr.valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(
                        os.path.basename(inr.valueAsText))[0] + "_MDOW")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        P["hypso_strength"].enabled = bool(P["use_hypso"].value)
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr, units = P["in_raster"], P["elev_units"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage(
                        "The elevation raster must be single band.")
            except Exception:
                pass
            if units.enabled and not units.valueAsText:
                units.setErrorMessage(
                    "The DEM does not specify its elevation units; choose "
                    "Meters or Feet.")
        az = P["azimuth"].value
        if az is not None and not (0 <= az <= 360):
            P["azimuth"].setErrorMessage(
                "The primary illumination direction must be 0 to 360 degrees.")
        alt = P["altitude"].value
        if alt is not None and not (0 <= alt <= 90):
            P["altitude"].setErrorMessage(
                "The inclination must be 0 to 90 degrees.")
        if P["levels"].value is not None and P["levels"].value < 1:
            P["levels"].setErrorMessage("There must be at least 1 hillshade level.")
        st = P["hypso_strength"].value
        if P["use_hypso"].value and st is not None and not (0 <= st <= 100):
            P["hypso_strength"].setErrorMessage(
                "The hypsometric strength must be 0 to 100.")
        ex = P["vert_exag"].value
        if ex is not None and ex <= 0:
            P["vert_exag"].setErrorMessage(
                "The vertical exaggeration must be positive.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(hillshade)
            P = {p.name: p for p in parameters}
            out_lyrx = hillshade.run_mdow_hillshade(
                in_raster=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                elev_units=P["elev_units"].valueAsText or "Meters",
                azimuth=P["azimuth"].value,
                altitude=P["altitude"].value,
                levels=P["levels"].value,
                use_hypso=bool(P["use_hypso"].value),
                hypso_strength=P["hypso_strength"].value or 50,
                vert_exag=P["vert_exag"].value or 1.0,
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
            # Remember the choices (not the datasets) with the project.
            _save_tool_defaults("MDOWHillshade", {
                "elev_units": P["elev_units"].valueAsText or "Meters",
                "azimuth": P["azimuth"].value,
                "altitude": P["altitude"].value,
                "levels": P["levels"].value,
                "use_hypso": bool(P["use_hypso"].value),
                "hypso_strength": P["hypso_strength"].value,
                "vert_exag": P["vert_exag"].value,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


# Static defaults for the eight Multi-Sun Hillshade sun positions: suns 1-2
# from Jeff's lab exercise (280/35 and 350/45); each later sun steps 45
# degrees clockwise from sun 2 at a 45-degree inclination.
_MULTISUN_MAX = 8
_MULTISUN_AZ_DEFAULTS = [280.0, 350.0] + [
    (350.0 + 45.0 * k) % 360.0 for k in range(1, _MULTISUN_MAX - 1)]
_MULTISUN_ALT_DEFAULTS = [35.0, 45.0] + [45.0] * (_MULTISUN_MAX - 2)


def _multisun_balanced_weights(n):
    """Balanced weights per Jeff's rule: the first n-1 weights are 100/n
    rounded to the nearest integer (half rounds up); the last is 100 minus
    the others (e.g. 3 -> [33, 33, 34]; 6 -> [17, 17, 17, 17, 17, 15])."""
    w = int(100.0 / n + 0.5)
    return [w] * (n - 1) + [100 - w * (n - 1)]


class MultiSunHillshade(object):
    """Weighted blend of standard hillshades from multiple sun positions --
    the classic Swiss multiple-light-source relief-shading strategy.
    Geodesic-correct on geographic DEMs like the other hillshade tools.
    """

    def __init__(self):
        self.label = "Multi-Sun Hillshade"
        self.description = (
            "Computes a hillshade as the weighted average of standard hillshades "
            "from two to eight sun positions, each with its own azimuth, "
            "inclination, and weight. Blending several light sources brings out "
            "terrain features that a single sun washes out (the classic Swiss "
            "multiple-light-source strategy). Optional modeled cast shadows are "
            "computed separately for each sun and dimmed before the averaging; "
            "optional hypsometric shading is applied once to the averaged result. "
            "Geographic (lat/long) DEMs are handled correctly via per-row "
            "spheroidal cell dimensions.")
        self.category = "Cartography Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None
        self._last_n = None
        self._seen_update = False

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output hillshade raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        n_suns = arcpy.Parameter(
            displayName="Number of sun positions (2-8)", name="n_suns",
            datatype="GPLong", parameterType="Required", direction="Input")
        n_suns.filter.type = "Range"
        n_suns.filter.list = [2, _MULTISUN_MAX]
        n_suns.value = 2

        sun_params = []
        for i in range(1, _MULTISUN_MAX + 1):
            az = arcpy.Parameter(
                displayName="Sun {}: azimuth (0-360 degrees)".format(i),
                name="azimuth_{}".format(i),
                datatype="GPDouble", parameterType="Optional", direction="Input")
            az.value = _MULTISUN_AZ_DEFAULTS[i - 1]
            alt = arcpy.Parameter(
                displayName="Sun {}: inclination (0-90 degrees)".format(i),
                name="altitude_{}".format(i),
                datatype="GPDouble", parameterType="Optional", direction="Input")
            alt.value = _MULTISUN_ALT_DEFAULTS[i - 1]
            wt = arcpy.Parameter(
                displayName="Sun {}: weight (percent)".format(i),
                name="weight_{}".format(i),
                datatype="GPDouble", parameterType="Optional", direction="Input")
            sun_params.extend([az, alt, wt])

        # Pro hides disabled parameters, so the auto-calculated last weight
        # would be invisible; this always-visible text line shows it instead
        # (rewritten on every validation pass, so edits never stick).
        last_weight_info = arcpy.Parameter(
            displayName="Automatically calculated weight",
            name="last_weight_info",
            datatype="GPString", parameterType="Optional", direction="Input")

        levels = arcpy.Parameter(
            displayName="Hillshade levels (Esri hillshade = 255)", name="levels",
            datatype="GPLong", parameterType="Required", direction="Input")
        levels.value = 255
        use_hypso = arcpy.Parameter(
            displayName="Apply hypsometric shading", name="use_hypso",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        use_hypso.value = False
        hypso_strength = arcpy.Parameter(
            displayName="Hypsometric strength (0-100)", name="hypso_strength",
            datatype="GPLong", parameterType="Optional", direction="Input")
        hypso_strength.value = 50
        hypso_strength.enabled = False
        vert_exag = arcpy.Parameter(
            displayName="Vertical exaggeration factor", name="vert_exag",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        vert_exag.value = 1.0
        model_shadows = arcpy.Parameter(
            displayName="Model cast shadows (per sun, before averaging)",
            name="model_shadows",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        model_shadows.value = False
        shadow_dim = arcpy.Parameter(
            displayName="Shadow brightness (percent of unshadowed value)",
            name="shadow_dim",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        shadow_dim.value = 20.0
        shadow_dim.enabled = False

        params = ([in_raster, out_raster, elev_units, n_suns] + sun_params +
                  [last_weight_info, levels, use_hypso, hypso_strength,
                   vert_exag, model_shadows, shadow_dim])
        # Pre-select the choices from the last run saved with this project.
        saved = _load_tool_defaults("MultiSunHillshade")
        for p in params:
            if p.name in saved and p.name not in ("in_raster", "out_raster"):
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        hypso_strength.enabled = bool(use_hypso.value)
        shadow_dim.enabled = bool(model_shadows.value)
        # Show the right sun rows for the (possibly restored) count, with
        # balanced weights where none were saved.
        n = self._sun_count(n_suns.value)
        self._last_n = n
        by_name = {p.name: p for p in params}
        weights = _multisun_balanced_weights(n)
        for i in range(1, _MULTISUN_MAX + 1):
            on = i <= n
            by_name["azimuth_{}".format(i)].enabled = on
            by_name["altitude_{}".format(i)].enabled = on
            wt = by_name["weight_{}".format(i)]
            wt.enabled = on and i < n
            if on and wt.value is None:
                wt.value = weights[i - 1]
        self._sync_last_weight(by_name, n)
        return params

    @staticmethod
    def _sun_count(value):
        try:
            return max(2, min(_MULTISUN_MAX, int(value)))
        except Exception:
            return 2

    @staticmethod
    def _sync_last_weight(P, n):
        """Last enabled weight = 100 - sum of the editable ones (never below
        0), mirrored into the always-visible info line (Pro hides the disabled
        weight parameter itself)."""
        others = 0.0
        for i in range(1, n):
            v = P["weight_{}".format(i)].value
            others += float(v) if v is not None else 0.0
        last = P["weight_{}".format(n)]
        last.value = max(0.0, round(100.0 - others, 6))
        last.enabled = False
        info = P.get("last_weight_info")
        if info is not None:
            info.value = ("Sun {}: weight (percent) automatically calculated "
                          "as {:g}".format(n, last.value))

    def isLicensed(self):
        return True

    # Same auto-detection as Enhanced Hillshade (shared helper).
    _detect_units = EnhancedHillshade._detect_units

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr, units = P["in_raster"], P["out_raster"], P["elev_units"]
        if inr.valueAsText:
            detected, locked = self._detect_units(inr.valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(
                        os.path.basename(inr.valueAsText))[0] + "_MultiSun")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass

        n = self._sun_count(P["n_suns"].value)
        if n != self._last_n:
            # The count changed: show that many sun rows. Re-balance the
            # weights only on a live dialog interaction -- the FIRST
            # validation pass on a fresh instance is a scripted call (or
            # ModelBuilder) arriving with every value already set, and its
            # weights must be preserved.
            rebalance = self._seen_update
            self._last_n = n
            weights = _multisun_balanced_weights(n)
            for i in range(1, _MULTISUN_MAX + 1):
                on = i <= n
                P["azimuth_{}".format(i)].enabled = on
                P["altitude_{}".format(i)].enabled = on
                wt = P["weight_{}".format(i)]
                wt.enabled = on and i < n
                if on and (rebalance or wt.value is None):
                    wt.value = weights[i - 1]
        self._sync_last_weight(P, n)
        self._seen_update = True

        P["hypso_strength"].enabled = bool(P["use_hypso"].value)
        P["shadow_dim"].enabled = bool(P["model_shadows"].value)
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr, units = P["in_raster"], P["elev_units"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage(
                        "The elevation raster must be single band.")
            except Exception:
                pass
            if units.enabled and not units.valueAsText:
                units.setErrorMessage(
                    "The DEM does not specify its elevation units; choose "
                    "Meters or Feet.")
        n = self._sun_count(P["n_suns"].value)
        others = 0.0
        for i in range(1, n + 1):
            az = P["azimuth_{}".format(i)]
            if az.value is None:
                az.setErrorMessage("Enter an azimuth for sun {}.".format(i))
            elif not (0 <= az.value <= 360):
                az.setErrorMessage("The azimuth must be 0 to 360 degrees.")
            alt = P["altitude_{}".format(i)]
            if alt.value is None:
                alt.setErrorMessage("Enter an inclination for sun {}.".format(i))
            elif not (0 <= alt.value <= 90):
                alt.setErrorMessage("The inclination must be 0 to 90 degrees.")
            if i < n:
                wt = P["weight_{}".format(i)]
                if wt.value is None:
                    wt.setErrorMessage("Enter a weight for sun {}.".format(i))
                elif not (0 <= wt.value <= 100):
                    wt.setErrorMessage("Each weight must be 0 to 100 percent.")
                else:
                    others += float(wt.value)
        if others > 100.0 + 1e-9:
            P["weight_{}".format(n - 1)].setErrorMessage(
                "The weights of suns 1 to {} total {:g} percent, leaving "
                "nothing for sun {}. Weights must total 100.".format(
                    n - 1, others, n))
        if P["levels"].value is not None and P["levels"].value < 1:
            P["levels"].setErrorMessage("There must be at least 1 hillshade level.")
        st = P["hypso_strength"].value
        if P["use_hypso"].value and st is not None and not (0 <= st <= 100):
            P["hypso_strength"].setErrorMessage(
                "The hypsometric strength must be 0 to 100.")
        ex = P["vert_exag"].value
        if ex is not None and ex <= 0:
            P["vert_exag"].setErrorMessage(
                "The vertical exaggeration must be positive.")
        sd = P["shadow_dim"].value
        if P["model_shadows"].value and sd is not None and not (0 <= sd <= 100):
            P["shadow_dim"].setErrorMessage(
                "The shadow brightness must be 0 to 100 percent.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(hillshade)
            P = {p.name: p for p in parameters}
            n = self._sun_count(P["n_suns"].value)
            suns = []
            for i in range(1, n + 1):
                suns.append((P["azimuth_{}".format(i)].value,
                             P["altitude_{}".format(i)].value,
                             P["weight_{}".format(i)].value or 0.0))
            out_lyrx = hillshade.run_multisun_hillshade(
                in_raster=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                elev_units=P["elev_units"].valueAsText or "Meters",
                suns=suns,
                levels=P["levels"].value,
                use_hypso=bool(P["use_hypso"].value),
                hypso_strength=P["hypso_strength"].value or 50,
                vert_exag=P["vert_exag"].value or 1.0,
                model_shadows=bool(P["model_shadows"].value),
                shadow_dim=(P["shadow_dim"].value
                            if P["shadow_dim"].value is not None else 20.0),
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
            # Remember the choices (not the datasets) with the project.
            saved = {"elev_units": P["elev_units"].valueAsText or "Meters",
                     "n_suns": n,
                     "levels": P["levels"].value,
                     "use_hypso": bool(P["use_hypso"].value),
                     "hypso_strength": P["hypso_strength"].value,
                     "vert_exag": P["vert_exag"].value,
                     "model_shadows": bool(P["model_shadows"].value),
                     "shadow_dim": P["shadow_dim"].value}
            for i in range(1, _MULTISUN_MAX + 1):
                for stem in ("azimuth", "altitude", "weight"):
                    name = "{}_{}".format(stem, i)
                    saved[name] = P[name].value
            _save_tool_defaults("MultiSunHillshade", saved)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class BlurredHillshade(object):
    """Blurred (smoothed) hillshade -- the softening component of the Swiss
    method: either the DEM is blurred before shading (default; generalizes
    the terrain) or the finished hillshade image is blurred after shading.
    Geodesic-correct on geographic DEMs like the other hillshade tools.
    """

    def __init__(self):
        self.label = "Blurred Hillshade"
        self.description = (
            "Computes a deliberately softened (blurred) hillshade from a "
            "single-band DEM -- the blurring component of the classic Swiss "
            "method of relief presentation. A circular blur of a user-chosen "
            "radius (cells, meters, kilometers, feet, or miles; linear radii "
            "are sized per row on the spheroid for geographic DEMs) is applied "
            "either to the DEM before shading (default; de-emphasizes small "
            "terrain features) or to the finished hillshade image. All the "
            "Enhanced Hillshade options are available: hillshade levels, "
            "hypsometric shading, vertical exaggeration, and modeled cast "
            "shadows.")
        self.category = "Cartography Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output hillshade raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        blur_radius = arcpy.Parameter(
            displayName="Blur radius", name="blur_radius",
            datatype="GPDouble", parameterType="Required", direction="Input")
        blur_radius.value = 10.0
        radius_units = arcpy.Parameter(
            displayName="Blur radius units", name="radius_units",
            datatype="GPString", parameterType="Required", direction="Input")
        radius_units.filter.type = "ValueList"
        radius_units.filter.list = hillshade.RADIUS_UNITS
        radius_units.value = "Cells"
        blur_target = arcpy.Parameter(
            displayName="What to blur", name="blur_target",
            datatype="GPString", parameterType="Required", direction="Input")
        blur_target.filter.type = "ValueList"
        blur_target.filter.list = hillshade.BLUR_TARGETS
        blur_target.value = hillshade.BLUR_TARGETS[0]
        kernel = arcpy.Parameter(
            displayName="Kernel weighting", name="kernel",
            datatype="GPString", parameterType="Required", direction="Input")
        kernel.filter.type = "ValueList"
        kernel.filter.list = hillshade.BLUR_KERNELS
        kernel.value = "Average"
        azimuth = arcpy.Parameter(
            displayName="Azimuth to sun (0-360 degrees)", name="azimuth",
            datatype="GPDouble", parameterType="Required", direction="Input")
        azimuth.value = 315.0
        altitude = arcpy.Parameter(
            displayName="Inclination to sun (0-90 degrees)", name="altitude",
            datatype="GPDouble", parameterType="Required", direction="Input")
        altitude.value = 45.0
        levels = arcpy.Parameter(
            displayName="Hillshade levels (Esri hillshade = 255)", name="levels",
            datatype="GPLong", parameterType="Required", direction="Input")
        levels.value = 255
        use_hypso = arcpy.Parameter(
            displayName="Apply hypsometric shading", name="use_hypso",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        use_hypso.value = False
        hypso_strength = arcpy.Parameter(
            displayName="Hypsometric strength (0-100)", name="hypso_strength",
            datatype="GPLong", parameterType="Optional", direction="Input")
        hypso_strength.value = 50
        hypso_strength.enabled = False
        vert_exag = arcpy.Parameter(
            displayName="Vertical exaggeration factor", name="vert_exag",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        vert_exag.value = 1.0
        model_shadows = arcpy.Parameter(
            displayName="Model cast shadows", name="model_shadows",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        model_shadows.value = False
        shadow_dim = arcpy.Parameter(
            displayName="Shadow brightness (percent of unshadowed value)",
            name="shadow_dim",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        shadow_dim.value = 20.0
        shadow_dim.enabled = False
        params = [in_raster, out_raster, elev_units, blur_radius, radius_units,
                  blur_target, kernel, azimuth, altitude, levels,
                  use_hypso, hypso_strength, vert_exag, model_shadows,
                  shadow_dim]
        # Pre-select the choices from the last run saved with this project.
        saved = _load_tool_defaults("BlurredHillshade")
        for p in params:
            if p.name in saved and p.name not in ("in_raster", "out_raster"):
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        hypso_strength.enabled = bool(use_hypso.value)
        shadow_dim.enabled = bool(model_shadows.value)
        return params

    def isLicensed(self):
        return True

    # Same auto-detection as Enhanced Hillshade (shared helper).
    _detect_units = EnhancedHillshade._detect_units

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr, units = P["in_raster"], P["out_raster"], P["elev_units"]
        if inr.valueAsText:
            detected, locked = self._detect_units(inr.valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(
                        os.path.basename(inr.valueAsText))[0] + "_Blurred")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        P["hypso_strength"].enabled = bool(P["use_hypso"].value)
        P["shadow_dim"].enabled = bool(P["model_shadows"].value)
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr, units = P["in_raster"], P["elev_units"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage(
                        "The elevation raster must be single band.")
            except Exception:
                pass
            if units.enabled and not units.valueAsText:
                units.setErrorMessage(
                    "The DEM does not specify its elevation units; choose "
                    "Meters or Feet.")
        br = P["blur_radius"].value
        if br is not None and br <= 0:
            P["blur_radius"].setErrorMessage("The blur radius must be positive.")
        az = P["azimuth"].value
        if az is not None and not (0 <= az <= 360):
            P["azimuth"].setErrorMessage("The azimuth must be 0 to 360 degrees.")
        alt = P["altitude"].value
        if alt is not None and not (0 <= alt <= 90):
            P["altitude"].setErrorMessage(
                "The inclination must be 0 to 90 degrees.")
        if P["levels"].value is not None and P["levels"].value < 1:
            P["levels"].setErrorMessage("There must be at least 1 hillshade level.")
        st = P["hypso_strength"].value
        if P["use_hypso"].value and st is not None and not (0 <= st <= 100):
            P["hypso_strength"].setErrorMessage(
                "The hypsometric strength must be 0 to 100.")
        ex = P["vert_exag"].value
        if ex is not None and ex <= 0:
            P["vert_exag"].setErrorMessage(
                "The vertical exaggeration must be positive.")
        sd = P["shadow_dim"].value
        if P["model_shadows"].value and sd is not None and not (0 <= sd <= 100):
            P["shadow_dim"].setErrorMessage(
                "The shadow brightness must be 0 to 100 percent.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(hillshade)
            P = {p.name: p for p in parameters}
            out_lyrx = hillshade.run_blurred_hillshade(
                in_raster=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                elev_units=P["elev_units"].valueAsText or "Meters",
                azimuth=P["azimuth"].value,
                altitude=P["altitude"].value,
                levels=P["levels"].value,
                use_hypso=bool(P["use_hypso"].value),
                hypso_strength=P["hypso_strength"].value or 50,
                vert_exag=P["vert_exag"].value or 1.0,
                model_shadows=bool(P["model_shadows"].value),
                shadow_dim=(P["shadow_dim"].value
                            if P["shadow_dim"].value is not None else 20.0),
                blur_radius=P["blur_radius"].value,
                radius_units=P["radius_units"].valueAsText or "Cells",
                blur_target=P["blur_target"].valueAsText or None,
                kernel=P["kernel"].valueAsText or "Average",
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
            # Remember the choices (not the datasets) with the project.
            _save_tool_defaults("BlurredHillshade", {
                "elev_units": P["elev_units"].valueAsText or "Meters",
                "blur_radius": P["blur_radius"].value,
                "radius_units": P["radius_units"].valueAsText,
                "blur_target": P["blur_target"].valueAsText,
                "kernel": P["kernel"].valueAsText,
                "azimuth": P["azimuth"].value,
                "altitude": P["altitude"].value,
                "levels": P["levels"].value,
                "use_hypso": bool(P["use_hypso"].value),
                "hypso_strength": P["hypso_strength"].value,
                "vert_exag": P["vert_exag"].value,
                "model_shadows": bool(P["model_shadows"].value),
                "shadow_dim": P["shadow_dim"].value,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class SimpleHypsometricShading(object):
    """Composes a hypsometric-shading GROUP LAYER from a DEM and an existing
    hillshade -- no new dataset is created. The DEM sits on top, stretched
    black (lowest elevation) to white at a user-set transparency, over the
    hillshade. The group layer is plain layers the user can freely modify.
    """

    def __init__(self):
        self.label = "Simple Hypsometric Shading"
        self.description = (
            "Creates a group layer that drapes a DEM over an existing hillshade "
            "for a classic hypsometric-shading (elevation-tinted relief) effect: "
            "the DEM is symbolized with a black-to-white stretch (black at the "
            "lowest elevation) and set to a chosen transparency, on top of the "
            "hillshade. No new dataset is created -- the group layer simply "
            "references the two existing rasters, and every part of it (ramp, "
            "transparency, layer order) remains freely editable afterward.")
        self.category = "Cartography Tools"
        self.canRunInBackground = False
        self._auto_name = None

    def getParameterInfo(self):
        in_dem = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_dem",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        in_hillshade = arcpy.Parameter(
            displayName="Input hillshade raster", name="in_hillshade",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        transparency = arcpy.Parameter(
            displayName="DEM layer transparency (0-100 percent)",
            name="transparency",
            datatype="GPLong", parameterType="Required", direction="Input")
        transparency.value = 50
        blend_mode = arcpy.Parameter(
            displayName="Layer blend mode (DEM layer)", name="blend_mode",
            datatype="GPString", parameterType="Optional", direction="Input")
        blend_mode.filter.type = "ValueList"
        blend_mode.filter.list = cartography_tools.BLEND_MODE_NAMES
        blend_mode.value = "Normal"
        group_name = arcpy.Parameter(
            displayName="Group layer name", name="group_name",
            datatype="GPString", parameterType="Optional", direction="Input")
        out_group = arcpy.Parameter(
            displayName="Output group layer", name="out_group",
            datatype="GPGroupLayer", parameterType="Derived", direction="Output")
        params = [in_dem, in_hillshade, transparency, blend_mode, group_name,
                  out_group]
        # Pre-select the choices from the last run saved with this project.
        saved = _load_tool_defaults("SimpleHypsometricShading")
        for p, key in ((transparency, "transparency"), (blend_mode, "blend_mode")):
            if key in saved:
                try:
                    p.value = saved[key]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        dem, name = P["in_dem"], P["group_name"]
        if dem.valueAsText and (not name.altered or
                                name.valueAsText == self._auto_name):
            try:
                base = os.path.splitext(
                    os.path.basename(arcpy.Describe(
                        dem.valueAsText).catalogPath))[0]
                sug = "{} Hypsometric Shading".format(base)
                name.value = sug
                self._auto_name = sug
            except Exception:
                pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        dem = P["in_dem"]
        if dem.valueAsText:
            try:
                if arcpy.Describe(dem.valueAsText).bandCount != 1:
                    dem.setErrorMessage(
                        "The elevation raster must be single band.")
            except Exception:
                pass
        tr = P["transparency"].value
        if tr is not None and not (0 <= tr <= 100):
            P["transparency"].setErrorMessage(
                "The transparency must be 0 to 100 percent.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(cartography_tools)
            P = {p.name: p for p in parameters}
            lyrx = cartography_tools.run_simple_hypso_shading(
                dem=P["in_dem"].valueAsText,
                hillshade=P["in_hillshade"].valueAsText,
                transparency=P["transparency"].value,
                blend_mode=P["blend_mode"].valueAsText or "Normal",
                group_name=P["group_name"].valueAsText or None,
                messages=messages)
            P["out_group"].value = lyrx
            # Remember the choices (not the datasets) with the project.
            _save_tool_defaults("SimpleHypsometricShading", {
                "transparency": P["transparency"].value,
                "blend_mode": P["blend_mode"].valueAsText or "Normal",
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class SurfaceAreaRatio(object):
    """Surface Area and Ratio -- the 8-triangle surface-area method of
    Jenness (2004, Wildlife Society Bulletin 32:829-839), with three optional
    outputs (surface area, surface ratio, flat area; at least one required)
    and a choice of corner-interpolation method.
    """

    def __init__(self):
        self.label = "Surface Area and Ratio"
        self.description = (
            "Calculates the three-dimensional surface area of the terrain within "
            "each cell of a DEM using the 8-triangle method of Jenness (2004, "
            "Wildlife Society Bulletin 32:829-839), plus the surface ratio "
            "(surface area / flat area, a roughness index) and the planimetric "
            "(flat) cell area. Choose any combination of the three outputs. "
            "Geographic (lat/long) DEMs are handled correctly: all distances and "
            "cell areas are computed per row on the spheroid. Corner elevations "
            "may be interpolated with the original diagonal-midpoint method or "
            "with the more accurate 4-cell corner average.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_units = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_area = arcpy.Parameter(
            displayName="Output surface area raster (optional)", name="out_area",
            datatype="DERasterDataset", parameterType="Optional",
            direction="Output")
        area_units = arcpy.Parameter(
            displayName="Surface area units", name="area_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        area_units.filter.type = "ValueList"
        area_units.filter.list = topographic_tools.AREA_UNITS
        area_units.value = "Sq. Meters"
        out_ratio = arcpy.Parameter(
            displayName="Output surface ratio raster (optional)", name="out_ratio",
            datatype="DERasterDataset", parameterType="Optional",
            direction="Output")
        out_flat = arcpy.Parameter(
            displayName="Output flat area raster (optional)", name="out_flat",
            datatype="DERasterDataset", parameterType="Optional",
            direction="Output")
        flat_units = arcpy.Parameter(
            displayName="Flat area units", name="flat_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        flat_units.filter.type = "ValueList"
        flat_units.filter.list = topographic_tools.AREA_UNITS
        flat_units.value = "Sq. Meters"
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        corner_method = arcpy.Parameter(
            displayName="Corner elevation interpolation", name="corner_method",
            datatype="GPString", parameterType="Required", direction="Input")
        corner_method.filter.type = "ValueList"
        corner_method.filter.list = topographic_tools.CORNER_METHODS
        corner_method.value = topographic_tools.CORNER_METHODS[0]
        detrend, detrend_radius, detrend_units = _detrend_params()
        params = [in_raster, out_area, area_units, out_ratio, out_flat,
                  flat_units, elev_units, corner_method,
                  detrend, detrend_radius, detrend_units]
        # Pre-select the choices from the last run saved with this project.
        saved = _load_tool_defaults("SurfaceAreaRatio")
        for p in (area_units, flat_units, elev_units, corner_method,
                  detrend, detrend_radius, detrend_units):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    # Same auto-detection as Enhanced Hillshade (shared helper).
    _detect_units = EnhancedHillshade._detect_units

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, units = P["in_raster"], P["elev_units"]
        if inr.valueAsText:
            detected, locked = self._detect_units(inr.valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
        P["area_units"].enabled = bool(P["out_area"].valueAsText)
        P["flat_units"].enabled = bool(P["out_flat"].valueAsText)
        # Detrending changes the elevation surface, so it affects the surface
        # AREA and (the meaningful one) the surface RATIO = Dilts's SAPR-L, but
        # NOT the flat area (planimetric, elevation-independent). Offer it only
        # when a surface-area or surface-ratio output is requested.
        has_metric = bool(P["out_area"].valueAsText or P["out_ratio"].valueAsText)
        P["detrend"].enabled = has_metric
        on = has_metric and bool(P["detrend"].value)
        P["detrend_radius"].enabled = on
        P["detrend_units"].enabled = on
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr, units = P["in_raster"], P["elev_units"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage(
                        "The elevation raster must be single band.")
            except Exception:
                pass
            if units.enabled and not units.valueAsText:
                units.setErrorMessage(
                    "The DEM does not specify its elevation units; choose "
                    "Meters or Feet.")
        if not (P["out_area"].valueAsText or P["out_ratio"].valueAsText or
                P["out_flat"].valueAsText):
            P["out_area"].setErrorMessage(
                "Specify at least one output raster (surface area, surface "
                "ratio, or flat area).")
        _detrend_check(P)
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(topographic_tools)
            P = {p.name: p for p in parameters}
            delivered, lyrx = topographic_tools.run_surface_area(
                in_raster=P["in_raster"].valueAsText,
                out_area=P["out_area"].valueAsText or None,
                out_ratio=P["out_ratio"].valueAsText or None,
                out_flat=P["out_flat"].valueAsText or None,
                elev_units=P["elev_units"].valueAsText or "Meters",
                area_units=P["area_units"].valueAsText or "Sq. Meters",
                flat_units=P["flat_units"].valueAsText or "Sq. Meters",
                corner_method=P["corner_method"].valueAsText or None,
                detrend=bool(P["detrend"].value),
                detrend_radius=P["detrend_radius"].value or 1.5,
                detrend_units=P["detrend_units"].valueAsText or "Cells",
                messages=messages)
            delivered = delivered or {}
            lyrx = lyrx or {}
            # A folder output without an extension is delivered as .tif; point
            # the output parameters at the real datasets so Pro adds those.
            for key, pname in (("area", "out_area"), ("ratio", "out_ratio"),
                               ("flat", "out_flat")):
                actual = delivered.get(key)
                if actual and P[pname].valueAsText and \
                        actual != P[pname].valueAsText:
                    try:
                        P[pname].value = actual
                    except Exception:
                        pass
                if lyrx.get(key):
                    try:
                        P[pname].symbology = lyrx[key]
                    except Exception as e:
                        messages.addWarningMessage(
                            "Could not set output symbology: {}".format(e))
            # Remember the choices (not the datasets) with the project.
            _save_tool_defaults("SurfaceAreaRatio", {
                "area_units": P["area_units"].valueAsText,
                "flat_units": P["flat_units"].valueAsText,
                "elev_units": P["elev_units"].valueAsText,
                "corner_method": P["corner_method"].valueAsText,
                "detrend": bool(P["detrend"].value),
                "detrend_radius": P["detrend_radius"].value,
                "detrend_units": P["detrend_units"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class VRM(object):
    """Vector Ruggedness Measure -- terrain roughness from the 3-D dispersion
    of unit vectors orthogonal to the surface (Sappington et al. 2007),
    the spherical analog of Fisher's (1993) circular variance.
    """

    def __init__(self):
        self.label = "Vector Ruggedness Measure (VRM)"
        self.description = (
            "Computes the Vector Ruggedness Measure (VRM), a topographic "
            "roughness index (Sappington et al. 2007) that quantifies the "
            "three-dimensional dispersion of the vectors orthogonal to the "
            "terrain surface within a circular neighborhood of each cell -- "
            "the spherical analog of circular variance (Fisher 1993). Unlike "
            "slope alone, VRM distinguishes steep-but-even terrain from "
            "steep, broken terrain. Values range from 0 (flat and/or "
            "uniformly sloped) to 1 (maximally rugged).")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output VRM raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        radius = arcpy.Parameter(
            displayName="Neighborhood radius", name="radius",
            datatype="GPDouble", parameterType="Required", direction="Input")
        radius.value = 1.5
        radius_units = arcpy.Parameter(
            displayName="Neighborhood radius units", name="radius_units",
            datatype="GPString", parameterType="Required", direction="Input")
        radius_units.filter.type = "ValueList"
        radius_units.filter.list = topographic_tools.RADIUS_UNITS
        radius_units.value = "Cells"
        detrend, detrend_radius, detrend_units = _detrend_params()
        params = [in_raster, out_raster, elev_units, radius, radius_units,
                  detrend, detrend_radius, detrend_units]
        # Pre-select the choices from the last run saved with this project.
        saved = _load_tool_defaults("VRM")
        for p in (radius, radius_units, elev_units,
                  detrend, detrend_radius, detrend_units):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    # Same auto-detection as Enhanced Hillshade (shared helper).
    _detect_units = EnhancedHillshade._detect_units

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr, units = P["in_raster"], P["out_raster"], P["elev_units"]
        if inr.valueAsText:
            detected, locked = self._detect_units(inr.valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(
                        os.path.basename(inr.valueAsText))[0] + "_VRM")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        _detrend_enable(P)
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr, units = P["in_raster"], P["elev_units"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage(
                        "The elevation raster must be single band.")
            except Exception:
                pass
            if units.enabled and not units.valueAsText:
                units.setErrorMessage(
                    "The DEM does not specify its elevation units; choose "
                    "Meters or Feet.")
        r = P["radius"].value
        if r is not None and r <= 0:
            P["radius"].setErrorMessage("The radius must be positive.")
        _detrend_check(P)
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(topographic_tools)
            P = {p.name: p for p in parameters}
            out_lyrx = topographic_tools.run_vrm(
                in_raster=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                elev_units=P["elev_units"].valueAsText or "Meters",
                radius=P["radius"].value,
                radius_units=P["radius_units"].valueAsText or "Cells",
                detrend=bool(P["detrend"].value),
                detrend_radius=P["detrend_radius"].value or 1.5,
                detrend_units=P["detrend_units"].valueAsText or "Cells",
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
            # Remember the choices (not the datasets) with the project.
            _save_tool_defaults("VRM", {
                "radius": P["radius"].value,
                "radius_units": P["radius_units"].valueAsText,
                "elev_units": P["elev_units"].valueAsText,
                "detrend": bool(P["detrend"].value),
                "detrend_radius": P["detrend_radius"].value,
                "detrend_units": P["detrend_units"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class ArcChordRatio(object):
    """Arc-Chord Ratio (ACR) rugosity -- surface roughness decoupled from
    slope (Du Preez 2015): each cell's 3-D surface area divided by the area of
    its plane of best fit, equivalently the Jenness (2004) surface ratio times
    the cosine of the 3x3 best-fit-plane slope. A smooth tilted plane has
    ACR = 1; larger values indicate genuine surface roughness."""

    def __init__(self):
        self.label = "Arc-Chord Ratio (ACR)"
        self.description = (
            "Computes the Arc-Chord Ratio (ACR) rugosity index (Du Preez "
            "2015): each cell's three-dimensional surface area divided by the "
            "area of its plane of best fit. Unlike the plain surface ratio, "
            "ACR is decoupled from slope -- a smooth but steeply tilted "
            "surface has ACR = 1 (the minimum), so larger values reflect "
            "genuine surface roughness rather than steepness. Equivalently, "
            "ACR is the Jenness (2004) surface ratio times the cosine of the "
            "3x3 best-fit-plane slope. Optionally remove the smooth underlying "
            "topography first (Dilts et al. 2023) to map fine-scale roughness "
            "(ACR_L).")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output ACR raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        corner_method = arcpy.Parameter(
            displayName="Corner elevation interpolation", name="corner_method",
            datatype="GPString", parameterType="Required", direction="Input")
        corner_method.filter.type = "ValueList"
        corner_method.filter.list = topographic_tools.CORNER_METHODS
        corner_method.value = topographic_tools.CORNER_METHODS[0]
        detrend, detrend_radius, detrend_units = _detrend_params()
        params = [in_raster, out_raster, elev_units, corner_method,
                  detrend, detrend_radius, detrend_units]
        saved = _load_tool_defaults("ArcChordRatio")
        for p in (elev_units, corner_method,
                  detrend, detrend_radius, detrend_units):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    _detect_units = EnhancedHillshade._detect_units

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr, units = P["in_raster"], P["out_raster"], P["elev_units"]
        if inr.valueAsText:
            detected, locked = self._detect_units(inr.valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(
                        os.path.basename(inr.valueAsText))[0] + "_ACR")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        _detrend_enable(P)
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr, units = P["in_raster"], P["elev_units"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage(
                        "The elevation raster must be single band.")
            except Exception:
                pass
            if units.enabled and not units.valueAsText:
                units.setErrorMessage(
                    "The DEM does not specify its elevation units; choose "
                    "Meters or Feet.")
        _detrend_check(P)
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(topographic_tools)
            P = {p.name: p for p in parameters}
            out_lyrx = topographic_tools.run_acr(
                in_raster=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                elev_units=P["elev_units"].valueAsText or "Meters",
                corner_method=P["corner_method"].valueAsText or None,
                detrend=bool(P["detrend"].value),
                detrend_radius=P["detrend_radius"].value or 1.5,
                detrend_units=P["detrend_units"].valueAsText or "Cells",
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
            _save_tool_defaults("ArcChordRatio", {
                "elev_units": P["elev_units"].valueAsText,
                "corner_method": P["corner_method"].valueAsText,
                "detrend": bool(P["detrend"].value),
                "detrend_radius": P["detrend_radius"].value,
                "detrend_units": P["detrend_units"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class TRI(object):
    """Terrain Ruggedness Index -- one of four selectable neighborhood
    measures of how much a cell's surroundings depart from flat (Riley et al.
    1999; Wilson et al. 2007; a slope/distance-weighted variant; or the
    standard deviation of neighborhood elevations)."""

    def __init__(self):
        self.label = "Terrain Ruggedness Index (TRI)"
        self.description = (
            "Computes a Terrain Ruggedness Index (TRI): how much each cell's "
            "neighborhood departs from flat, by one of four methods -- Riley "
            "et al. (1999) root of summed squared differences, Wilson et al. "
            "(2007) mean absolute difference, a slope (distance-weighted) "
            "variant that corrects for diagonal neighbors being farther away, "
            "or the standard deviation of the neighborhood elevations. Higher "
            "values are more rugged. Different methods capture different "
            "notions of ruggedness; see the tool help.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output TRI raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        method = arcpy.Parameter(
            displayName="Method", name="method",
            datatype="GPString", parameterType="Required", direction="Input")
        method.filter.type = "ValueList"
        method.filter.list = topographic_tools.TRI_METHODS
        method.value = topographic_tools.TRI_METHODS[0]
        radius = arcpy.Parameter(
            displayName="Neighborhood radius", name="radius",
            datatype="GPDouble", parameterType="Required", direction="Input")
        radius.value = 1.5
        radius_units = arcpy.Parameter(
            displayName="Neighborhood radius units", name="radius_units",
            datatype="GPString", parameterType="Required", direction="Input")
        radius_units.filter.type = "ValueList"
        radius_units.filter.list = topographic_tools.RADIUS_UNITS
        radius_units.value = "Cells"
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        params = [in_raster, out_raster, method, radius, radius_units, elev_units]
        saved = _load_tool_defaults("TRI")
        for p in (method, radius, radius_units, elev_units):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    _detect_units = EnhancedHillshade._detect_units

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr, units = P["in_raster"], P["out_raster"], P["elev_units"]
        if inr.valueAsText:
            detected, locked = self._detect_units(inr.valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(
                        os.path.basename(inr.valueAsText))[0] + "_TRI")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        # The Slope method's output is a dimensionless rise/run, so elevation
        # units don't affect it (they only rescale the elevation-unit methods).
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr, units = P["in_raster"], P["elev_units"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage(
                        "The elevation raster must be single band.")
            except Exception:
                pass
            if units.enabled and not units.valueAsText:
                units.setErrorMessage(
                    "The DEM does not specify its elevation units; choose "
                    "Meters or Feet.")
        r = P["radius"].value
        if r is not None and r <= 0:
            P["radius"].setErrorMessage(
                "The neighborhood radius must be positive.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(topographic_tools)
            P = {p.name: p for p in parameters}
            out_lyrx = topographic_tools.run_tri(
                in_raster=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                method=P["method"].valueAsText or topographic_tools.TRI_METHODS[0],
                radius=P["radius"].value,
                radius_units=P["radius_units"].valueAsText or "Cells",
                elev_units=P["elev_units"].valueAsText or "Meters",
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
            _save_tool_defaults("TRI", {
                "method": P["method"].valueAsText,
                "radius": P["radius"].value,
                "radius_units": P["radius_units"].valueAsText,
                "elev_units": P["elev_units"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class TopographyAlongLines(object):
    """Topography along Lines -- adds topographic-roughness attribute fields to
    an existing polyline feature class, computed by sampling elevation along
    each line and reducing it to straight 3-D segments (see
    ``line_surface_tools``). Elevation comes from the polylines' own Z values or
    from a DEM sampled by bilinear interpolation."""

    _Z_SOURCE = "Z values from the polylines"
    _DEM_SOURCE = "DEM raster"
    _SLOPE_STATS = {"avg_slope", "min_slope", "max_slope"}

    def __init__(self):
        self.label = "Topography along Lines"
        self.description = (
            "Adds attribute fields describing the topography along each line of "
            "a polyline feature class -- length, surface ratio, uphill/downhill "
            "and slope statistics, direction statistics, sinuosity, fractal "
            "dimension, and more. Elevation is taken from the polylines' own Z "
            "values or sampled from a DEM by bilinear interpolation at a regular "
            "ground interval; each line is reduced to straight 3-D segments and "
            "every statistic is computed from those segments. Direction-"
            "dependent statistics describe the line travelling in the direction "
            "of its geometry, from the first vertex to the last. The fields are "
            "written into the input feature class.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_units = None

    def getParameterInfo(self):
        in_lines = arcpy.Parameter(
            displayName="Input polyline features", name="in_lines",
            datatype="GPFeatureLayer", parameterType="Required", direction="Input")
        in_lines.filter.list = ["Polyline"]

        elev_source = arcpy.Parameter(
            displayName="Elevation source", name="elev_source",
            datatype="GPString", parameterType="Required", direction="Input")
        elev_source.filter.type = "ValueList"
        elev_source.filter.list = [self._Z_SOURCE, self._DEM_SOURCE]
        elev_source.value = self._DEM_SOURCE

        in_dem = arcpy.Parameter(
            displayName="Input elevation raster (DEM)", name="in_dem",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input")

        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = ["Meters", "Feet"]

        attrs = arcpy.Parameter(
            displayName="Surface Attributes", name="surface_attributes",
            datatype="GPValueTable", parameterType="Required", direction="Input")
        # Property is the FIRST column so Pro's value-table "add multiple"
        # checklist (the down-arrow) attaches to it -- the user can check off
        # several properties at once and each new row's field name auto-fills.
        attrs.columns = [["GPString", "Property"],
                         ["GPString", "Field (Existing or New)"]]
        attrs.filters[0].type = "ValueList"
        attrs.filters[0].list = [label for _, label in line_surface_tools.STATS]
        attrs.filters[1].type = "ValueList"
        attrs.filters[1].list = []          # existing field names (populated live)

        use_aliases = arcpy.Parameter(
            displayName="List existing fields by alias (instead of name)",
            name="use_aliases", datatype="GPBoolean",
            parameterType="Optional", direction="Input")
        use_aliases.value = False

        length_unit = arcpy.Parameter(
            displayName="Length Unit", name="length_unit",
            datatype="GPString", parameterType="Required", direction="Input")
        length_unit.filter.type = "ValueList"
        length_unit.filter.list = line_surface_tools.LENGTH_UNITS
        length_unit.value = "Meters"

        slope_unit = arcpy.Parameter(
            displayName="Slope Unit", name="slope_unit",
            datatype="GPString", parameterType="Optional", direction="Input")
        slope_unit.filter.type = "ValueList"
        slope_unit.filter.list = line_surface_tools.SLOPE_UNITS
        slope_unit.value = "Degrees"

        params = [in_lines, elev_source, in_dem, elev_units, attrs,
                  use_aliases, length_unit, slope_unit]
        saved = _load_tool_defaults("TopographyAlongLines")
        for p in (length_unit, slope_unit):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    _detect_units = EnhancedHillshade._detect_units

    _NUMERIC_TYPES = ("SmallInteger", "Integer", "BigInteger", "Single", "Double")

    def _selected_keys(self, attrs_param):
        """The set of statistic keys currently chosen in the value table."""
        keys = set()
        rows = attrs_param.value
        if rows:
            for row in rows:
                try:
                    lbl = str(row[0]).strip()
                except Exception:
                    continue
                k = line_surface_tools.LABEL_STAT.get(lbl)
                if k:
                    keys.add(k)
        return keys

    def _numeric_fields(self, lines_value):
        """Editable numeric fields of the input (candidates for output)."""
        try:
            return [f for f in arcpy.ListFields(lines_value)
                    if f.editable and f.type in self._NUMERIC_TYPES]
        except Exception:
            return []

    @staticmethod
    def _resolve_field_cell(celltext, use_aliases, fields):
        """Map a value-table field cell to a real field. Returns (name, is_new,
        alias, warning). An exact field-NAME match always wins; in alias mode an
        alias match is tried next (first match if the alias is duplicated, with a
        warning); otherwise it is a new field (its typed text becomes the alias
        in alias mode)."""
        text = (celltext or "").strip()
        if not text:
            return None, False, None, None
        by_name = {f.name.lower(): f.name for f in fields}
        if text.lower() in by_name:
            return by_name[text.lower()], False, None, None
        if use_aliases:
            matches = [f for f in fields if (f.aliasName or f.name) == text]
            if matches:
                warn = None
                if len(matches) > 1:
                    warn = ("More than one field has the alias '{}'; the first "
                            "('{}') will be used.".format(text, matches[0].name))
                return matches[0].name, False, None, warn
        return None, True, (text if use_aliases else None), None

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        lines, src, dem, units = (P["in_lines"], P["elev_source"],
                                  P["in_dem"], P["elev_units"])

        # If the input is not Z-aware, elevation must come from a DEM.
        z_aware = False
        if lines.valueAsText:
            try:
                z_aware = bool(arcpy.Describe(lines.valueAsText).hasZ)
            except Exception:
                z_aware = False
        if not z_aware and lines.valueAsText:
            src.value = self._DEM_SOURCE
            src.enabled = False
        else:
            src.enabled = True

        use_dem = (src.valueAsText == self._DEM_SOURCE)
        dem.enabled = use_dem

        # Elevation units: detected from the DEM (DEM source) or from the
        # feature class's vertical coordinate system (Z source). Ask only when
        # undefined.
        detected, locked = None, False
        if use_dem and dem.valueAsText:
            detected, locked = self._detect_units(dem.valueAsText)
        elif not use_dem and lines.valueAsText:
            detected, locked = self._detect_units(lines.valueAsText)
        if locked:
            if not units.altered or units.valueAsText == self._auto_units:
                units.value = detected
                self._auto_units = detected
            units.enabled = False
        else:
            units.enabled = True

        # Offer the input's existing fields as a suggestion dropdown on the
        # field-name column, in the order they appear in the feature class. Only
        # EDITABLE NUMERIC fields are offered (system fields like OBJECTID /
        # Shape_Length are not editable and are excluded; text/date fields can't
        # hold numeric results). Names, or aliases when "List by alias" is on.
        # IMPORTANT: a non-empty ValueList on a GPString column is RESTRICTIVE
        # (rejects non-members with ERROR 000800), which would block typing a NEW
        # field name -- so we append whatever the user has already typed to the
        # list, making new names valid members while still showing existing
        # fields as suggestions.
        try:
            attrs = P["surface_attributes"]
            fields = self._numeric_fields(lines.valueAsText) if lines.valueAsText else []
            use_al = bool(P["use_aliases"].value)
            # Auto-fill a reasonable field name from the chosen property when the
            # field cell is left blank (e.g. Surface Length -> Surface_Length).
            rows = attrs.value or []
            new_rows, changed = [], False
            for row in rows:
                lbl = str(row[0]).strip() if row[0] is not None else ""
                fld = str(row[1]).strip() if row[1] is not None else ""
                key = line_surface_tools.LABEL_STAT.get(lbl)
                if not fld and key:
                    fld = line_surface_tools.default_field_name(key)
                    changed = True
                new_rows.append([lbl, fld])
            if changed:
                attrs.value = new_rows
                rows = new_rows
            # Suggestion dropdown (Field column): existing editable-numeric
            # fields (in dataset order) plus whatever now sits in the table, so
            # typed and auto-filled names validate against the otherwise-
            # restrictive ValueList.
            items = [(f.aliasName or f.name) if use_al else f.name for f in fields]
            seen = {v.lower() for v in items}
            for row in rows:
                v = str(row[1]).strip() if row[1] is not None else ""
                if v and v.lower() not in seen:
                    items.append(v)
                    seen.add(v.lower())
            attrs.filters[1].list = items
        except Exception:
            pass

        # The slope-unit choice matters only for the mean/min/max slope stats
        # (standard deviation and RMS are always reported in degrees).
        P["slope_unit"].enabled = bool(
            self._selected_keys(P["surface_attributes"]) & self._SLOPE_STATS)
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        lines, src, dem, units = (P["in_lines"], P["elev_source"],
                                  P["in_dem"], P["elev_units"])
        if lines.valueAsText:
            try:
                if arcpy.Describe(lines.valueAsText).shapeType != "Polyline":
                    lines.setErrorMessage("The input must be a polyline feature class.")
            except Exception:
                pass
        use_dem = (src.valueAsText == self._DEM_SOURCE)
        if use_dem and not dem.valueAsText:
            dem.setErrorMessage("Specify a DEM, or choose the polylines' own Z values.")
        if use_dem and dem.valueAsText:
            try:
                if arcpy.Describe(dem.valueAsText).bandCount != 1:
                    dem.setErrorMessage("The elevation raster must be single band.")
            except Exception:
                pass
        if (not use_dem) and lines.valueAsText:
            try:
                if not arcpy.Describe(lines.valueAsText).hasZ:
                    src.setErrorMessage(
                        "The polylines are not Z-enabled; choose a DEM instead.")
            except Exception:
                pass
        if units.enabled and not units.valueAsText:
            units.setErrorMessage(
                "The elevation units are not defined; choose Meters or Feet.")
        attrs = P["surface_attributes"]
        if attrs.value:
            int_types = ("SmallInteger", "Integer", "BigInteger")
            use_aliases = bool(P["use_aliases"].value)
            all_fields, types_by_name = [], {}
            if lines.valueAsText:
                try:
                    all_fields = arcpy.ListFields(lines.valueAsText)
                    types_by_name = {f.name.lower(): f.type for f in all_fields}
                except Exception:
                    all_fields, types_by_name = [], {}
            numeric = [f for f in all_fields
                       if f.editable and f.type in self._NUMERIC_TYPES]
            # Workspace of the input, so new-field names can be validated the way
            # the dataset actually creates them (shapefiles truncate to 10 chars
            # and apply dBASE rules); the duplicate check must use those ACTUAL
            # names, or two long names that truncate alike would slip through.
            ws = None
            if lines.valueAsText:
                try:
                    ws = arcpy.Describe(lines.valueAsText).path
                except Exception:
                    ws = None
            targets = {}                 # actual field -> count (duplicate check)
            for row in attrs.value:
                lbl = str(row[0]).strip() if row[0] is not None else ""
                fld = str(row[1]).strip() if row[1] is not None else ""
                if lbl and not fld:
                    attrs.setErrorMessage("Every property needs an output field name.")
                    break
                key = line_surface_tools.LABEL_STAT.get(lbl)
                name, is_new, _alias, _w = self._resolve_field_cell(
                    fld, use_aliases, numeric)
                actual = name
                if is_new and fld:
                    try:
                        actual = arcpy.ValidateFieldName(fld, ws) if ws else fld
                    except Exception:
                        actual = fld
                    if actual and actual.lower() != fld.lower():
                        attrs.setWarningMessage(
                            "Field name '{}' will be created as '{}' to satisfy "
                            "the dataset's naming rules (for example, shapefiles "
                            "limit field names to 10 characters).".format(fld, actual))
                if key and fld:
                    tkey = (actual or fld).lower()
                    targets[tkey] = targets.get(tkey, 0) + 1
                ftype = types_by_name.get(name.lower()) if (name and not is_new) else None
                if (key and ftype in int_types
                        and key not in line_surface_tools.INTEGER_STATS):
                    attrs.setWarningMessage(
                        "Field '{}' is an integer field, but '{}' can have "
                        "fractional values that would be rounded; choose or "
                        "create a floating-point (Double) field instead.".format(
                            name, lbl))
            if any(c > 1 for c in targets.values()):
                attrs.setErrorMessage(
                    "The same output field is used for more than one property "
                    "(after the dataset's field-naming rules are applied). Give "
                    "each property its own field.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(line_surface_tools)
            importlib.reload(tool_metadata)
            lst = line_surface_tools
            P = {p.name: p for p in parameters}

            in_lines = P["in_lines"].valueAsText
            use_dem = (P["elev_source"].valueAsText == self._DEM_SOURCE)
            source = "DEM" if use_dem else "Z"
            elev_units = P["elev_units"].valueAsText or "Meters"
            elev_mpu = lst.ELEV_METERS.get(elev_units, 1.0)
            length_unit = P["length_unit"].valueAsText or "Meters"
            slope_unit = P["slope_unit"].valueAsText or "Degrees"

            try:
                n_total = int(arcpy.management.GetCount(in_lines)[0])
            except Exception:
                n_total = 0
            phases = [("Preparing output fields...", 1)]
            if use_dem:
                phases.append(("Reading the DEM...", 2))
            phases.append(
                ("Computing attributes for {:,} line(s)...".format(n_total)
                 if n_total else "Computing line attributes...", 12))
            mahalanobis.begin_phases(phases)

            # Resolve each value-table row to a real output field (existing, or
            # a new one). In alias mode the cell may hold an alias.
            use_aliases = bool(P["use_aliases"].value)
            numeric = self._numeric_fields(in_lines)
            try:
                ws = arcpy.Describe(in_lines).path
            except Exception:
                ws = None
            existing = {f.name.lower() for f in arcpy.ListFields(in_lines)}
            pairs = []                       # (real field name, stat key)
            warned = set()
            for row in (P["surface_attributes"].value or []):
                lbl = str(row[0]).strip() if row[0] is not None else ""
                cell = str(row[1]).strip() if row[1] is not None else ""
                key = lst.LABEL_STAT.get(lbl)
                if not (cell and key):
                    continue
                name, is_new, alias, warn = self._resolve_field_cell(
                    cell, use_aliases, numeric)
                if warn and warn not in warned:
                    messages.addWarningMessage(warn)
                    warned.add(warn)
                if is_new:
                    valid = arcpy.ValidateFieldName(cell, ws) if ws else _legalize(cell)
                    n = 2
                    while valid.lower() in existing:
                        valid = "{}_{}".format(
                            arcpy.ValidateFieldName(cell, ws) if ws else _legalize(cell), n)
                        n += 1
                    ftype = "LONG" if key == "reversals" else "DOUBLE"
                    arcpy.management.AddField(in_lines, valid, ftype,
                                              field_alias=alias)
                    existing.add(valid.lower())
                    name = valid
                pairs.append((name, key))
            if not pairs:
                raise arcpy.ExecuteError(
                    "Choose at least one Surface Attribute with an output field.")
            wanted = set(k for _, k in pairs)

            dem = None
            if use_dem:
                mahalanobis.phase_advance()          # -> "Reading the DEM..."
                dem_path = _resolve_raster_path(P["in_dem"])
                dem_sr = arcpy.Describe(dem_path).spatialReference
                window = lst.lines_window_in_dem_crs(in_lines, dem_sr)
                dem = lst.load_dem(dem_path, window=window)
                messages.addMessage(
                    "DEM read over the lines' extent ({:,} x {:,} cells); "
                    "sampling interval {:.3f} m (min cell size); sample points "
                    "are projected into the DEM's coordinate system.".format(
                        dem["arr"].shape[1], dem["arr"].shape[0], dem["interval_m"]))

            field_names = [fld for fld, _ in pairs]
            field_keys = [key for _, key in pairs]
            want_fractal = "fractal_d" in wanted

            mahalanobis.phase_advance()              # -> "Computing attributes..."
            label_every = max(1, (n_total // 200) or 1)
            n_feat = n_written = short_fractal = nodata_segs = empty = 0
            with arcpy.da.UpdateCursor(in_lines, ["SHAPE@"] + field_names) as cur:
                for r in cur:
                    n_feat += 1
                    if n_total and (n_feat == 1 or n_feat == n_total
                                    or n_feat % label_every == 0):
                        arcpy.SetProgressorLabel(
                            "Computing line {:,} of {:,}...".format(
                                n_feat, n_total))
                    if n_total:
                        mahalanobis.phase_step(n_feat, n_total)
                    shp = r[0]
                    if shp is None:
                        continue
                    parts, diag = lst.build_parts(shp, source, dem, elev_mpu)
                    nodata_segs += diag.get("nodata_segments", 0)
                    if not parts:
                        empty += 1
                        continue
                    stats = lst.compute_line_statistics(
                        parts, length_unit, elev_units, slope_unit, wanted)
                    out = list(r)
                    for i, key in enumerate(field_keys):
                        v = stats.get(key)
                        if v is None or (isinstance(v, float) and math.isnan(v)):
                            out[i + 1] = None
                            if key == "fractal_d":
                                short_fractal += 1
                        else:
                            out[i + 1] = v
                    cur.updateRow(out)
                    n_written += 1

            messages.addMessage(
                "Computed {} attribute(s) for {} of {} line feature(s)."
                .format(len(pairs), n_written, n_feat))
            if want_fractal and short_fractal:
                messages.addWarningMessage(
                    "Fractal Dimension could not be computed for {} line(s) "
                    "because they were too short to span a range of scales (a "
                    "line must be longer than roughly six times the sampling "
                    "interval); those values were left null.".format(short_fractal))
            if nodata_segs:
                messages.addWarningMessage(
                    "{} segment(s) fell on DEM NoData and were skipped; the "
                    "affected lines describe only their valid portions."
                    .format(nodata_segs))
            if empty:
                messages.addWarningMessage(
                    "{} line(s) had no usable segments (too short, or entirely "
                    "on NoData) and were left null.".format(empty))

            # Per-field metadata stating the units (and the direction convention
            # for direction-dependent statistics).
            try:
                target = arcpy.Describe(in_lines).catalogPath
            except Exception:
                target = in_lines
            fields_meta = [{"name": fld,
                            "description": lst.field_description(
                                key, length_unit, elev_units, slope_unit)}
                           for fld, key in pairs]
            tool_metadata.set_entity_and_fields(
                target, fields=fields_meta, messages=messages)

            mahalanobis.end_phases()
            _save_tool_defaults("TopographyAlongLines", {
                "length_unit": length_unit, "slope_unit": slope_unit})
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class SplitLinesIntoSegments(object):
    """Split Lines into Topography Segments -- converts each polyline into a new
    PolylineZ feature class with one true 3-D feature per straight segment,
    carrying optional per-segment topography attributes and any chosen source
    fields. Shares the segment engine with Topography along Lines."""

    _Z_SOURCE = "Z values from the polylines"
    _DEM_SOURCE = "DEM raster"
    _SLOPE_STATS = {"rel_slope", "abs_slope"}
    _SYM_PRIORITY = ["mean_elev", "begin_x", "end_x", "abs_slope", "rel_slope"]
    _RAMP = [(95, 135, 193), (249, 254, 204), (224, 70, 53)]
    _RESERVED = {"objectid", "fid", "shape", "shape_length", "shape_area",
                 "oid", "globalid", "shape_leng"}
    _FTYPE_MAP = {"String": "TEXT", "Integer": "LONG", "SmallInteger": "SHORT",
                  "Double": "DOUBLE", "Single": "FLOAT", "Date": "DATE",
                  "BigInteger": "BIGINTEGER", "Guid": "GUID"}

    def __init__(self):
        self.label = "Split Lines into Topography Segments"
        self.description = (
            "Converts each polyline into a new PolylineZ feature class with one "
            "true three-dimensional feature per straight segment (sampled along "
            "the line from the polylines' own Z values or from a DEM, keeping "
            "the original vertices). Each segment can carry optional attributes "
            "-- start/end coordinates, planimetric and surface length, slope, "
            "bearing, deflection/inclination/3D-deflection angles, and more -- "
            "and you may copy any fields from the source table. The output "
            "displays in 3-D in a Scene.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_units = None
        self._auto_out = None

    def getParameterInfo(self):
        in_lines = arcpy.Parameter(
            displayName="Input polyline features", name="in_lines",
            datatype="GPFeatureLayer", parameterType="Required", direction="Input")
        in_lines.filter.list = ["Polyline"]
        out_fc = arcpy.Parameter(
            displayName="Output segment features (PolylineZ)", name="out_fc",
            datatype="DEFeatureClass", parameterType="Required", direction="Output")
        elev_source = arcpy.Parameter(
            displayName="Elevation source", name="elev_source",
            datatype="GPString", parameterType="Required", direction="Input")
        elev_source.filter.type = "ValueList"
        elev_source.filter.list = [self._Z_SOURCE, self._DEM_SOURCE]
        elev_source.value = self._DEM_SOURCE
        in_dem = arcpy.Parameter(
            displayName="Input elevation raster (DEM)", name="in_dem",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input")
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = ["Meters", "Feet"]

        attrs = arcpy.Parameter(
            displayName="Segment Attributes", name="surface_attributes",
            datatype="GPValueTable", parameterType="Optional", direction="Input")
        attrs.columns = [["GPString", "Property"],
                         ["GPString", "Field (New)"]]
        attrs.filters[0].type = "ValueList"
        attrs.filters[0].list = [lb for _, lb, _, _ in line_surface_tools.SEG_STATS]

        copy_fields = arcpy.Parameter(
            displayName="Source fields to copy", name="copy_fields",
            datatype="GPValueTable", parameterType="Optional", direction="Input")
        copy_fields.columns = [["GPString", "Source field to copy"]]
        copy_fields.filters[0].type = "ValueList"
        copy_fields.filters[0].list = []

        length_unit = arcpy.Parameter(
            displayName="Length Unit", name="length_unit",
            datatype="GPString", parameterType="Required", direction="Input")
        length_unit.filter.type = "ValueList"
        length_unit.filter.list = line_surface_tools.LENGTH_UNITS
        length_unit.value = "Meters"
        slope_unit = arcpy.Parameter(
            displayName="Slope Unit", name="slope_unit",
            datatype="GPString", parameterType="Optional", direction="Input")
        slope_unit.filter.type = "ValueList"
        slope_unit.filter.list = line_surface_tools.SLOPE_UNITS
        slope_unit.value = "Degrees"

        params = [in_lines, out_fc, elev_source, in_dem, elev_units,
                  attrs, copy_fields, length_unit, slope_unit]
        saved = _load_tool_defaults("SplitLinesIntoSegments")
        for p in (length_unit, slope_unit):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    _detect_units = EnhancedHillshade._detect_units

    def _seg_pairs(self, attrs):
        """[(stat key, output field name)] from the Segment Attributes table."""
        pairs = []
        for row in (attrs.value or []):
            lbl = str(row[0]).strip() if row[0] is not None else ""
            fld = str(row[1]).strip() if row[1] is not None else ""
            key = line_surface_tools.SEG_LABEL_KEY.get(lbl)
            if key and fld:
                pairs.append((key, fld))
        return pairs

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        lines, src, dem, units = (P["in_lines"], P["elev_source"],
                                  P["in_dem"], P["elev_units"])
        z_aware = False
        if lines.valueAsText:
            try:
                z_aware = bool(arcpy.Describe(lines.valueAsText).hasZ)
            except Exception:
                z_aware = False
        if not z_aware and lines.valueAsText:
            src.value = self._DEM_SOURCE
            src.enabled = False
        else:
            src.enabled = True
        use_dem = (src.valueAsText == self._DEM_SOURCE)
        dem.enabled = use_dem

        detected, locked = None, False
        if use_dem and dem.valueAsText:
            detected, locked = self._detect_units(dem.valueAsText)
        elif not use_dem and lines.valueAsText:
            detected, locked = self._detect_units(lines.valueAsText)
        if locked:
            if not units.altered or units.valueAsText == self._auto_units:
                units.value = detected
                self._auto_units = detected
            units.enabled = False
        else:
            units.enabled = True

        # Auto-suggest an output name from the input.
        outp = P["out_fc"]
        if lines.valueAsText and (not outp.altered or outp.valueAsText == self._auto_out):
            try:
                base = _legalize(os.path.splitext(
                    os.path.basename(lines.valueAsText))[0] + "_Segments")
                ws = arcpy.env.workspace or os.path.dirname(
                    arcpy.Describe(lines.valueAsText).catalogPath)
                sug = _unique_name(base, ws)
                outp.value = sug
                self._auto_out = sug
            except Exception:
                pass

        # Auto-fill blank field names (Seg_ prefixed) from the chosen property.
        attrs = P["surface_attributes"]
        rows = attrs.value or []
        new_rows, changed = [], False
        for row in rows:
            lbl = str(row[0]).strip() if row[0] is not None else ""
            fld = str(row[1]).strip() if row[1] is not None else ""
            key = line_surface_tools.SEG_LABEL_KEY.get(lbl)
            if not fld and key:
                fld = line_surface_tools.seg_default_field_name(key)
                changed = True
            new_rows.append([lbl, fld])
        if changed:
            attrs.value = new_rows

        # Populate the copy-fields checklist with the input's copyable fields.
        try:
            if lines.valueAsText:
                names = [f.name for f in arcpy.ListFields(lines.valueAsText)
                         if f.type not in ("OID", "Geometry", "Blob", "Raster")
                         and f.name.lower() not in self._RESERVED]
                P["copy_fields"].filters[0].list = names
        except Exception:
            pass

        P["slope_unit"].enabled = bool(
            {k for k, _ in self._seg_pairs(attrs)} & self._SLOPE_STATS)
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        lines, src, dem, units = (P["in_lines"], P["elev_source"],
                                  P["in_dem"], P["elev_units"])
        if lines.valueAsText:
            try:
                if arcpy.Describe(lines.valueAsText).shapeType != "Polyline":
                    lines.setErrorMessage("The input must be a polyline feature class.")
            except Exception:
                pass
        use_dem = (src.valueAsText == self._DEM_SOURCE)
        if use_dem and not dem.valueAsText:
            dem.setErrorMessage("Specify a DEM, or choose the polylines' own Z values.")
        if use_dem and dem.valueAsText:
            try:
                if arcpy.Describe(dem.valueAsText).bandCount != 1:
                    dem.setErrorMessage("The elevation raster must be single band.")
            except Exception:
                pass
        if (not use_dem) and lines.valueAsText:
            try:
                if not arcpy.Describe(lines.valueAsText).hasZ:
                    src.setErrorMessage(
                        "The polylines are not Z-enabled; choose a DEM instead.")
            except Exception:
                pass
        if units.enabled and not units.valueAsText:
            units.setErrorMessage(
                "The elevation units are not defined; choose Meters or Feet.")
        # Duplicate output field names among the segment attributes (copied
        # source fields are auto-renamed at run time if they would collide).
        attrs = P["surface_attributes"]
        seen = set()
        for _, fld in self._seg_pairs(attrs):
            n = fld.lower()
            if n in seen:
                attrs.setErrorMessage(
                    "The same output field is used for more than one property.")
                break
            seen.add(n)
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(line_surface_tools)
            importlib.reload(mahalanobis)
            lst = line_surface_tools
            P = {p.name: p for p in parameters}

            in_lines = P["in_lines"].valueAsText
            out_fc = P["out_fc"].valueAsText
            use_dem = (P["elev_source"].valueAsText == self._DEM_SOURCE)
            source = "DEM" if use_dem else "Z"
            elev_units = P["elev_units"].valueAsText or "Meters"
            elev_mpu = lst.ELEV_METERS.get(elev_units, 1.0)
            length_unit = P["length_unit"].valueAsText or "Meters"
            slope_unit = P["slope_unit"].valueAsText or "Degrees"
            source_sr = arcpy.Describe(in_lines).spatialReference

            pairs = self._seg_pairs(P["surface_attributes"])       # (key, field)
            copy_names = [str(r[0]).strip() for r in (P["copy_fields"].value or [])
                          if r[0] is not None and str(r[0]).strip()]

            try:
                n_total = int(arcpy.management.GetCount(in_lines)[0])
            except Exception:
                n_total = 0
            phases = [("Preparing the output feature class...", 1)]
            if use_dem:
                phases.append(("Reading the DEM...", 2))
            phases.append(
                ("Splitting {:,} line(s) into 3-D segments...".format(n_total)
                 if n_total else "Splitting lines into 3-D segments...", 12))
            phases.append(("Applying symbology...", 1))
            mahalanobis.begin_phases(phases)

            # Resolve the DEM's coordinate system up front so the output can
            # inherit a real vertical CS (the Z comes from the DEM). Without a
            # vertical CS the PolylineZ warns and mis-draws in a 3-D Scene.
            dem_path = dem_sr = None
            if use_dem:
                dem_path = _resolve_raster_path(P["in_dem"])
                dem_sr = arcpy.Describe(dem_path).spatialReference
            out_sr = lst.output_spatial_reference(
                source_sr, dem_sr if use_dem else source_sr, elev_units)

            # Create the output PolylineZ feature class (source horizontal CS +
            # an elevation vertical CS).
            out_ws, out_name = os.path.split(out_fc)
            arcpy.management.CreateFeatureclass(
                out_ws, out_name, "POLYLINE", spatial_reference=out_sr,
                has_z="ENABLED")
            null_ok = (".gdb" in out_fc.lower() or ".sde" in out_fc.lower())

            taken = {f.name.lower() for f in arcpy.ListFields(out_fc)}

            def make_field(desired, ftype, length=None, alias=None):
                try:
                    nm = arcpy.ValidateFieldName(desired, out_ws)
                except Exception:
                    nm = desired
                base, n = nm, 2
                while nm.lower() in taken or nm.lower() in self._RESERVED:
                    nm = arcpy.ValidateFieldName("{}_{}".format(base, n), out_ws)
                    n += 1
                kw = {}
                if length:
                    kw["field_length"] = length
                if alias:
                    kw["field_alias"] = alias
                arcpy.management.AddField(out_fc, nm, ftype, **kw)
                taken.add(nm.lower())
                return nm

            # Segment-attribute fields.
            key_field = []                       # (key, actual field name)
            for key, fld in pairs:
                ftype = lst.SEG_FTYPE[key]
                length = 16 if ftype == "TEXT" else None
                key_field.append((key, make_field(fld, ftype, length)))

            # Copied source fields (type from source; sanitized name).
            src_fields = {f.name: f for f in arcpy.ListFields(in_lines)}
            copy_map = []                        # (source name, target name)
            for cn in copy_names:
                f = src_fields.get(cn)
                if f is None:
                    continue
                ftype = self._FTYPE_MAP.get(f.type)
                if ftype is None:
                    messages.addWarningMessage(
                        "Field '{}' has a type that cannot be copied; skipped.".format(cn))
                    continue
                length = f.length if ftype == "TEXT" else None
                tgt = make_field(cn, ftype, length, alias=f.aliasName)
                copy_map.append((cn, tgt))

            dem = None
            if use_dem:
                mahalanobis.phase_advance()          # -> "Reading the DEM..."
                window = lst.lines_window_in_dem_crs(in_lines, dem_sr)
                dem = lst.load_dem(dem_path, window=window)
                messages.addMessage(
                    "DEM read over the lines' extent ({:,} x {:,} cells); "
                    "sampling interval {:.3f} m.".format(
                        dem["arr"].shape[1], dem["arr"].shape[0], dem["interval_m"]))

            null_val = None if null_ok else -999
            out_flds = ["SHAPE@"] + [fn for _, fn in key_field] + \
                       [tgt for _, tgt in copy_map]
            in_flds = ["SHAPE@", "OID@"] + [src for src, _ in copy_map]

            mahalanobis.phase_advance()              # -> "Splitting ... segments..."
            label_every = max(1, (n_total // 200) or 1)
            n_lines = n_segs = nodata_segs = 0
            icur = arcpy.da.InsertCursor(out_fc, out_flds)
            try:
                with arcpy.da.SearchCursor(in_lines, in_flds) as scur:
                    for i, row in enumerate(scur, 1):
                        if n_total and (i == 1 or i == n_total
                                        or i % label_every == 0):
                            arcpy.SetProgressorLabel(
                                "Splitting line {:,} of {:,} "
                                "({:,} segments so far)...".format(
                                    i, n_total, n_segs))
                        if n_total:
                            mahalanobis.phase_step(i, n_total)
                        shp, oid = row[0], row[1]
                        copied_vals = list(row[2:])
                        if shp is None:
                            continue
                        n_lines += 1
                        recs = lst.build_segments(
                            shp, source, dem, elev_mpu, source_sr, oid)
                        for rec in recs:
                            pts = arcpy.Array([arcpy.Point(x, y, z)
                                               for (x, y, z) in rec["geom"]])
                            geom = arcpy.Polyline(pts, source_sr, True)
                            vals = [geom]
                            for key, _ in key_field:
                                v = lst.seg_value(rec, key, length_unit,
                                                  elev_units, slope_unit)
                                if v is None:
                                    v = null_val
                                vals.append(v)
                            vals.extend(copied_vals)
                            icur.insertRow(vals)
                            n_segs += 1
            finally:
                del icur

            messages.addMessage(
                "Created {:,} segment feature(s) from {:,} line(s).".format(
                    n_segs, n_lines))

            # Output symbology: Unclassed Colors on the highest-priority field the
            # user calculated (else the first one), blue -> yellow -> red.
            mahalanobis.phase_advance()              # -> "Applying symbology..."
            sym_field = None
            by_key = {k: fn for k, fn in key_field}
            for pk in self._SYM_PRIORITY:
                if pk in by_key:
                    sym_field = by_key[pk]
                    break
            if sym_field is None and key_field:
                sym_field = key_field[0][1]
            if sym_field:
                try:
                    lyrx = mahalanobis.default_unclassed_lyrx(
                        out_fc, sym_field, self._RAMP, messages=messages)
                    if lyrx:
                        P["out_fc"].symbology = lyrx
                except Exception as e:
                    messages.addWarningMessage(
                        "Could not set output symbology: {}".format(e))

            mahalanobis.end_phases()
            _save_tool_defaults("SplitLinesIntoSegments", {
                "length_unit": length_unit, "slope_unit": slope_unit})
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class AspectFocalStatistics(object):
    """Aspect Focal Statistics -- neighborhood (focal) statistics for an aspect
    raster, computed with CIRCULAR statistics (aspect is a circular variable, so
    ordinary Focal Statistics is invalid). All neighborhood shapes; projected or
    geographic; no Spatial Analyst. See ``aspect_tools``."""

    # Short codes for auto-named outputs.
    _ABBR = {"MEAN": "MeanDir", "MEDIAN": "MedDir", "RESULTANT": "R",
             "CIRCVAR": "CVar", "ANGVAR": "AVar", "CIRCSD": "CSD",
             "ANGDEV": "ADev", "RANGE": "Range", "RAYLEIGH": "RayP",
             "KAPPA": "Kappa", "PROPDEF": "PropDef"}
    _RECT = {"width", "height"}
    _CIRC = {"radius"}
    _ANN = {"inner_radius", "outer_radius"}
    _WEDGE = {"radius", "start_angle", "end_angle"}
    _FILE = {"kernel_file"}

    def __init__(self):
        self.label = "Aspect Focal Statistics"
        self.description = (
            "Neighborhood (focal) statistics for an aspect raster, using "
            "circular statistics. Aspect is a circular variable (0 and 360 "
            "degrees are the same direction), so ordinary Focal Statistics -- "
            "which treats it as a linear number -- is wrong; this tool offers "
            "circular measures of central tendency and dispersion (mean "
            "direction, circular variance, and more) over any neighborhood "
            "shape, on projected or geographic aspect rasters, with no Spatial "
            "Analyst required. Flat cells (aspect -1) carry no direction and are "
            "ignored. D-infinity flow-direction rasters (which report aspect in "
            "the mathematical convention) are supported via a convention "
            "toggle.")
        self.category = "Aspect Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_conv = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input aspect raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        statistic = arcpy.Parameter(
            displayName="Statistic", name="statistic",
            datatype="GPString", parameterType="Required", direction="Input")
        statistic.filter.type = "ValueList"
        statistic.filter.list = aspect_tools.STAT_LABELS
        statistic.value = aspect_tools.STAT_LABELS[0]
        neighborhood = arcpy.Parameter(
            displayName="Neighborhood", name="neighborhood",
            datatype="GPString", parameterType="Required", direction="Input")
        neighborhood.filter.type = "ValueList"
        neighborhood.filter.list = aspect_tools.NEIGHBORHOODS
        neighborhood.value = "Circle"
        nbr_units = arcpy.Parameter(
            displayName="Neighborhood units", name="nbr_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        nbr_units.filter.type = "ValueList"
        nbr_units.filter.list = aspect_tools.NBR_UNITS
        nbr_units.value = "Cells"
        radius = arcpy.Parameter(
            displayName="Radius", name="radius",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        radius.value = 3
        width = arcpy.Parameter(
            displayName="Width", name="width",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        width.value = 3
        height = arcpy.Parameter(
            displayName="Height", name="height",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        height.value = 3
        inner_radius = arcpy.Parameter(
            displayName="Inner radius", name="inner_radius",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        inner_radius.value = 1
        outer_radius = arcpy.Parameter(
            displayName="Outer radius", name="outer_radius",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        outer_radius.value = 3
        start_angle = arcpy.Parameter(
            displayName="Start angle (deg, CCW from east)", name="start_angle",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        start_angle.value = 0
        end_angle = arcpy.Parameter(
            displayName="End angle (deg, CCW from east)", name="end_angle",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        end_angle.value = 90
        kernel_file = arcpy.Parameter(
            displayName="Kernel file", name="kernel_file",
            datatype="DEFile", parameterType="Optional", direction="Input")
        kernel_file.filter.list = ["txt"]
        input_convention = arcpy.Parameter(
            displayName="Input aspect convention", name="input_convention",
            datatype="GPString", parameterType="Required", direction="Input")
        input_convention.filter.type = "ValueList"
        input_convention.filter.list = aspect_tools.INPUT_CONVENTIONS
        input_convention.value = aspect_tools.INPUT_CONVENTIONS[0]
        flat_handling = arcpy.Parameter(
            displayName="All-flat neighborhoods", name="flat_handling",
            datatype="GPString", parameterType="Required", direction="Input")
        flat_handling.filter.type = "ValueList"
        flat_handling.filter.list = aspect_tools.FLAT_HANDLING
        flat_handling.value = aspect_tools.FLAT_HANDLING[0]
        params = [in_raster, out_raster, statistic, neighborhood, nbr_units,
                  radius, width, height, inner_radius, outer_radius,
                  start_angle, end_angle, kernel_file, input_convention,
                  flat_handling]
        saved = _load_tool_defaults("AspectFocalStatistics")
        for p in (statistic, neighborhood, nbr_units, flat_handling):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    def _relevant(self, nbr):
        if nbr == "Rectangle":
            return self._RECT
        if nbr == "Circle":
            return self._CIRC
        if nbr == "Annulus":
            return self._ANN
        if nbr == "Wedge":
            return self._WEDGE
        if nbr in ("Irregular", "Weight"):
            return self._FILE
        return set()

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr = P["in_raster"], P["out_raster"]
        nbr = P["neighborhood"].valueAsText or "Circle"
        rel = self._relevant(nbr)
        for nm in ("radius", "width", "height", "inner_radius", "outer_radius",
                   "start_angle", "end_angle", "kernel_file"):
            P[nm].enabled = nm in rel
        # Units apply to every shape except the (cell-based) kernel files.
        P["nbr_units"].enabled = nbr not in ("Irregular", "Weight")

        if inr.valueAsText:
            # Auto-suggest the output name from the input + statistic.
            stat_key = aspect_tools.STAT_KEY.get(
                P["statistic"].valueAsText, "MEAN")
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(os.path.basename(
                        inr.valueAsText))[0] + "_" + self._ABBR.get(stat_key, "Asp"))
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
            # Suggest the Mathematical convention once, only on the cycle where
            # the input just changed, if it looks like a D-infinity raster.
            if not inr.hasBeenValidated:
                try:
                    kind, _why = aspect_tools._detect_source_kind(inr.valueAsText)
                except Exception:
                    kind = "unknown"
                conv = P["input_convention"]
                if kind == "dinf" and (not conv.altered
                                       or conv.valueAsText == self._auto_conv):
                    conv.value = aspect_tools.INPUT_CONVENTIONS[1]
                    self._auto_conv = aspect_tools.INPUT_CONVENTIONS[1]
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr = P["in_raster"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage("The aspect raster must be single band.")
            except Exception:
                pass
        nbr = P["neighborhood"].valueAsText or "Circle"
        rel = self._relevant(nbr)
        for nm in ("radius", "width", "height", "inner_radius", "outer_radius"):
            if nm in rel:
                v = P[nm].value
                if v is not None and v <= 0:
                    P[nm].setErrorMessage("Must be a positive value.")
        if nbr == "Annulus":
            ri, ro = P["inner_radius"].value, P["outer_radius"].value
            if ri is not None and ro is not None and ri >= ro:
                P["outer_radius"].setErrorMessage(
                    "The outer radius must be greater than the inner radius.")
        if nbr == "Wedge":
            for nm in ("radius",):
                if P[nm].value is None:
                    P[nm].setErrorMessage("Required for a wedge neighborhood.")
        if nbr in ("Irregular", "Weight"):
            kf = P["kernel_file"].valueAsText
            if not kf:
                P["kernel_file"].setErrorMessage(
                    "The {} neighborhood needs a kernel file.".format(nbr))
            elif not os.path.exists(kf):
                P["kernel_file"].setErrorMessage("Kernel file not found.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(aspect_tools)
            P = {p.name: p for p in parameters}
            in_path = _resolve_raster_path(P["in_raster"]) or P["in_raster"].valueAsText
            stat_key = aspect_tools.STAT_KEY.get(
                P["statistic"].valueAsText, P["statistic"].valueAsText)
            out_lyrx = aspect_tools.run_aspect_focal(
                in_raster=in_path,
                out_raster=P["out_raster"].valueAsText,
                statistic=stat_key,
                neighborhood=P["neighborhood"].valueAsText or "Circle",
                radius=P["radius"].value if P["radius"].value is not None else 3.0,
                width=P["width"].value if P["width"].value is not None else 3.0,
                height=P["height"].value if P["height"].value is not None else 3.0,
                inner_radius=(P["inner_radius"].value
                              if P["inner_radius"].value is not None else 1.0),
                outer_radius=(P["outer_radius"].value
                              if P["outer_radius"].value is not None else 3.0),
                start_angle=(P["start_angle"].value
                             if P["start_angle"].value is not None else 0.0),
                end_angle=(P["end_angle"].value
                           if P["end_angle"].value is not None else 90.0),
                nbr_units=P["nbr_units"].valueAsText or "Cells",
                kernel_file=P["kernel_file"].valueAsText,
                input_convention=(P["input_convention"].valueAsText
                                  or aspect_tools.INPUT_CONVENTIONS[0]),
                flat_handling=(P["flat_handling"].valueAsText
                               or aspect_tools.FLAT_HANDLING[0]),
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage(
                        "Could not set output symbology: {}".format(e))
            _save_tool_defaults("AspectFocalStatistics", {
                "statistic": P["statistic"].valueAsText,
                "neighborhood": P["neighborhood"].valueAsText,
                "nbr_units": P["nbr_units"].valueAsText,
                "flat_handling": P["flat_handling"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class AspectTransformation(object):
    """Aspect Transformation -- generate one or more transformations of an
    aspect raster at once: directional reclassifications (with a Class attribute
    table) and trigonometric transforms (northness, eastness, deviation from a
    bearing). No Spatial Analyst. See ``aspect_tools.run_aspect_transform``."""

    def __init__(self):
        self.label = "Aspect Transformation"
        self.description = (
            "Transforms an aspect raster in one or more ways at once. "
            "Reclassifications assign each cell a named direction class "
            "(North/South, East/West, the four or eight cardinal/intercardinal "
            "directions) written to a Class attribute table. Trigonometric "
            "transforms produce continuous rasters: northness (cosine), "
            "eastness (sine), a trigonometric deviation from a chosen bearing "
            "(1 toward, -1 away), or a numeric deviation in degrees (0-180). "
            "D-infinity flow-direction rasters are supported via a convention "
            "toggle. No Spatial Analyst required.")
        self.category = "Aspect Tools"
        self.canRunInBackground = False
        self._auto_conv = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input aspect raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        transformations = arcpy.Parameter(
            displayName="Transformations", name="transformations",
            datatype="GPString", parameterType="Required", direction="Input",
            multiValue=True)
        transformations.filter.type = "ValueList"
        transformations.filter.list = aspect_tools.TRANSFORM_LABELS
        out_workspace = arcpy.Parameter(
            displayName="Output location (folder or geodatabase)",
            name="out_workspace", datatype="DEWorkspace",
            parameterType="Required", direction="Input")
        base_name = arcpy.Parameter(
            displayName="Output base name", name="base_name",
            datatype="GPString", parameterType="Optional", direction="Input")
        target_bearing = arcpy.Parameter(
            displayName="Target bearing (deg, for the deviation transforms)",
            name="target_bearing", datatype="GPDouble",
            parameterType="Optional", direction="Input")
        target_bearing.value = 45
        input_convention = arcpy.Parameter(
            displayName="Input aspect convention", name="input_convention",
            datatype="GPString", parameterType="Required", direction="Input")
        input_convention.filter.type = "ValueList"
        input_convention.filter.list = aspect_tools.INPUT_CONVENTIONS
        input_convention.value = aspect_tools.INPUT_CONVENTIONS[0]
        flat_handling = arcpy.Parameter(
            displayName="Flat cells", name="flat_handling",
            datatype="GPString", parameterType="Required", direction="Input")
        flat_handling.filter.type = "ValueList"
        flat_handling.filter.list = aspect_tools.FLAT_TRANSFORM
        flat_handling.value = aspect_tools.FLAT_TRANSFORM[1]
        out_rasters = arcpy.Parameter(
            displayName="Output rasters", name="out_rasters",
            datatype="DERasterDataset", parameterType="Derived",
            direction="Output", multiValue=True)
        params = [in_raster, transformations, out_workspace, base_name,
                  target_bearing, input_convention, flat_handling, out_rasters]
        saved = _load_tool_defaults("AspectTransformation")
        for p in (transformations, target_bearing, flat_handling):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    def _selected_keys(self, param):
        vals = param.values if param.values else []
        return [aspect_tools.TRANSFORM_KEY.get(str(v), str(v)) for v in vals]

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outws = P["in_raster"], P["out_workspace"]
        # Default the output location to the input's workspace.
        if inr.valueAsText and not outws.altered:
            try:
                d = arcpy.Describe(inr.valueAsText)
                cp = d.catalogPath
                ws = os.path.dirname(cp)
                # step up out of a .gdb raster's internal path if needed
                outws.value = ws
            except Exception:
                pass
        # Enable the target bearing only when a deviation transform is picked.
        keys = set(self._selected_keys(P["transformations"]))
        P["target_bearing"].enabled = bool(keys & aspect_tools._BEARING_KEYS)
        # Suggest the Mathematical convention once for a DINF raster.
        if inr.valueAsText and not inr.hasBeenValidated:
            try:
                kind, _why = aspect_tools._detect_source_kind(inr.valueAsText)
            except Exception:
                kind = "unknown"
            conv = P["input_convention"]
            if kind == "dinf" and (not conv.altered
                                   or conv.valueAsText == self._auto_conv):
                conv.value = aspect_tools.INPUT_CONVENTIONS[1]
                self._auto_conv = aspect_tools.INPUT_CONVENTIONS[1]
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr = P["in_raster"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage("The aspect raster must be single band.")
            except Exception:
                pass
        tb = P["target_bearing"]
        if P["target_bearing"].enabled and tb.value is not None:
            if tb.value < 0 or tb.value > 360:
                tb.setErrorMessage("The target bearing must be 0 - 360 degrees.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(aspect_tools)
            P = {p.name: p for p in parameters}
            in_raster = _resolve_raster_path(P["in_raster"]) or \
                P["in_raster"].valueAsText
            keys = self._selected_keys(P["transformations"])
            outputs = aspect_tools.run_aspect_transform(
                in_raster=in_raster,
                out_workspace=P["out_workspace"].valueAsText,
                transformations=keys,
                base_name=P["base_name"].valueAsText,
                target_bearing=(P["target_bearing"].value
                                if P["target_bearing"].value is not None else 45.0),
                input_convention=(P["input_convention"].valueAsText
                                  or aspect_tools.INPUT_CONVENTIONS[0]),
                flat_handling=(P["flat_handling"].valueAsText
                               or aspect_tools.FLAT_TRANSFORM[1]),
                messages=messages)
            if outputs:
                try:
                    P["out_rasters"].value = ";".join(outputs)
                except Exception:
                    pass
            _save_tool_defaults("AspectTransformation", {
                "transformations": P["transformations"].valueAsText,
                "target_bearing": P["target_bearing"].value,
                "flat_handling": P["flat_handling"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class ExtractAspectValuesToPoints(object):
    """Extract Aspect Values to Points -- the circular analog of Esri's Extract
    Values to Points: copies the input points and adds a field holding the
    aspect at each point, either the containing cell's value or a bilinear
    WEIGHTED MEAN DIRECTION (circular interpolation). No Spatial Analyst. See
    ``aspect_tools.run_extract_aspect_points``."""

    def __init__(self):
        self.label = "Extract Aspect Values to Points"
        self.description = (
            "Copies the input point features and adds a field holding the "
            "aspect value at each point, extracted from an aspect raster -- "
            "like Esri's Extract Values to Points, but built for aspect: the "
            "optional interpolation is a bilinear WEIGHTED MEAN DIRECTION "
            "(circular), never a linear average (which would put ~180 between "
            "359 and 1). Flat cells (aspect -1) carry no direction and are "
            "excluded from interpolation; an all-flat set of neighbors gives "
            "-1. D-infinity flow-direction rasters are supported via a "
            "convention toggle. No Spatial Analyst required.")
        self.category = "Aspect Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_conv = None

    def getParameterInfo(self):
        in_points = arcpy.Parameter(
            displayName="Input point features", name="in_points",
            datatype="GPFeatureLayer", parameterType="Required", direction="Input")
        in_points.filter.list = ["Point"]
        in_raster = arcpy.Parameter(
            displayName="Input aspect raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_points = arcpy.Parameter(
            displayName="Output point features", name="out_points",
            datatype="DEFeatureClass", parameterType="Required",
            direction="Output")
        interpolate = arcpy.Parameter(
            displayName="Interpolate values at the point locations "
                        "(weighted mean direction)", name="interpolate",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        interpolate.value = False
        field_name = arcpy.Parameter(
            displayName="Output field name", name="field_name",
            datatype="GPString", parameterType="Optional", direction="Input")
        field_name.value = "Aspect"
        input_convention = arcpy.Parameter(
            displayName="Input aspect convention", name="input_convention",
            datatype="GPString", parameterType="Required", direction="Input")
        input_convention.filter.type = "ValueList"
        input_convention.filter.list = aspect_tools.INPUT_CONVENTIONS
        input_convention.value = aspect_tools.INPUT_CONVENTIONS[0]
        params = [in_points, in_raster, out_points, interpolate, field_name,
                  input_convention]
        saved = _load_tool_defaults("ExtractAspectValuesToPoints")
        for p in (interpolate, field_name):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inp, inr, outp = P["in_points"], P["in_raster"], P["out_points"]
        if inp.valueAsText:
            if not outp.altered or outp.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(os.path.basename(
                        inp.valueAsText))[0] + "_Aspect")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inp.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outp.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        if inr.valueAsText and not inr.hasBeenValidated:
            # Suggest the Mathematical convention when the raster looks like
            # a D-infinity flow-direction raster. Unconditional on each
            # raster change; module reloaded because validation runs Pro's
            # session-cached copy otherwise.
            try:
                importlib.reload(aspect_tools)
                kind, _why = aspect_tools._detect_source_kind(inr.valueAsText)
            except Exception:
                kind = "unknown"
            if kind == "dinf":
                P["input_convention"].value = aspect_tools.INPUT_CONVENTIONS[1]
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inp, inr = P["in_points"], P["in_raster"]
        if inp.valueAsText:
            try:
                if arcpy.Describe(inp.valueAsText).shapeType != "Point":
                    inp.setErrorMessage(
                        "The input must be a point feature class (multipoint "
                        "is not supported).")
            except Exception:
                pass
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage("The aspect raster must be single band.")
            except Exception:
                pass
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(aspect_tools)
            P = {p.name: p for p in parameters}
            in_raster = _resolve_raster_path(P["in_raster"]) or \
                P["in_raster"].valueAsText
            aspect_tools.run_extract_aspect_points(
                in_points=P["in_points"].valueAsText,
                in_raster=in_raster,
                out_points=P["out_points"].valueAsText,
                interpolate=bool(P["interpolate"].value),
                field_name=P["field_name"].valueAsText or "Aspect",
                input_convention=(P["input_convention"].valueAsText
                                  or aspect_tools.INPUT_CONVENTIONS[0]),
                messages=messages)
            _save_tool_defaults("ExtractAspectValuesToPoints", {
                "interpolate": bool(P["interpolate"].value),
                "field_name": P["field_name"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class AspectZonalStatisticsAsTable(object):
    """Aspect Zonal Statistics as Table -- the circular analog of Esri's
    Zonal Statistics as Table, built for aspect rasters: all statistics are
    CIRCULAR, flat cells are excluded (and counted), and no Spatial Analyst
    or Image Analyst extension is needed. See
    ``aspect_tools.run_aspect_zonal``."""

    def __init__(self):
        self.label = "Aspect Zonal Statistics as Table"
        self.description = (
            "Summarizes an aspect raster per zone into a table of CIRCULAR "
            "statistics (mean direction, mean resultant length, circular "
            "variance/SD, median direction, circular range, von Mises "
            "concentration, Rayleigh test, and the 95% confidence interval "
            "of the mean direction). Very similar to Esri's Zonal Statistics "
            "as Table, but targeted at aspect: a circular variable where 0 "
            "and 360 degrees are the same direction, so the ordinary zonal "
            "MEAN/STD are wrong. Requires no Spatial Analyst or Image "
            "Analyst extension. Zones come from an INTEGER raster or from "
            "point / polyline / polygon features; overlapping features in "
            "different zones are each analyzed in full (each zone is "
            "processed independently -- the historic rasterize-first overlap "
            "problem cannot occur). Point zones use a bilinear WEIGHTED MEAN "
            "DIRECTION interpolated at each point; polygons use every cell "
            "whose center falls inside; polylines use every cell the line "
            "passes through. Flat cells (aspect = -1) carry no direction and "
            "are excluded from the statistics but counted per zone "
            "(FLAT_COUNT, FLAT_PCT). D-infinity flow-direction rasters are "
            "supported via the input-convention setting, which defaults from "
            "the raster's geoprocessing history.")
        self.category = "Aspect Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_conv = None

    def getParameterInfo(self):
        in_zone = arcpy.Parameter(
            displayName="Input raster or feature zone data", name="in_zone_data",
            datatype=["GPFeatureLayer", "DEFeatureClass", "GPRasterLayer",
                      "DERasterDataset"],
            parameterType="Required", direction="Input")
        # A GPString value-list rather than a Field parameter: Field-type
        # filters fail to offer ObjectID fields in .pyt tools (a known Pro
        # quirk), and ObjectID is a perfectly good zone field. The list is
        # filled from the zone dataset in updateParameters.
        zone_field = arcpy.Parameter(
            displayName="Zone field", name="zone_field",
            datatype="GPString", parameterType="Optional", direction="Input")
        zone_field.filter.type = "ValueList"
        zone_field.filter.list = []
        in_raster = arcpy.Parameter(
            displayName="Input aspect raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_table = arcpy.Parameter(
            displayName="Output table", name="out_table",
            datatype="DETable", parameterType="Required", direction="Output")
        ignore_nodata = arcpy.Parameter(
            displayName="Ignore NoData in calculations", name="ignore_nodata",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        ignore_nodata.value = True
        statistics_type = arcpy.Parameter(
            displayName="Statistics type", name="statistics_type",
            datatype="GPString", parameterType="Optional", direction="Input")
        statistics_type.filter.type = "ValueList"
        statistics_type.filter.list = aspect_tools.ZONAL_LABELS
        statistics_type.value = aspect_tools.ZONAL_LABELS[0]
        input_convention = arcpy.Parameter(
            displayName="Input aspect convention", name="input_convention",
            datatype="GPString", parameterType="Required", direction="Input")
        input_convention.filter.type = "ValueList"
        input_convention.filter.list = aspect_tools.INPUT_CONVENTIONS
        input_convention.value = aspect_tools.INPUT_CONVENTIONS[0]
        params = [in_zone, zone_field, in_raster, out_table, ignore_nodata,
                  statistics_type, input_convention]
        saved = _load_tool_defaults("AspectZonalStatisticsAsTable")
        for p in (ignore_nodata, statistics_type):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    def _zone_is_raster(self, param):
        try:
            dt = arcpy.Describe(param.valueAsText).dataType
            return dt in ("RasterDataset", "RasterLayer", "MosaicDataset",
                          "MosaicLayer")
        except Exception:
            return False

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inz, outt = P["in_zone_data"], P["out_table"]
        if inz.valueAsText:
            if not outt.altered or outt.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(os.path.basename(
                        inz.valueAsText))[0] + "_AspectZonal")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inz.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outt.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
            if not inz.hasBeenValidated:
                zf = P["zone_field"]
                if self._zone_is_raster(inz):
                    zf.filter.list = ["Value"]
                    if not zf.altered or zf.valueAsText not in ("Value",):
                        zf.value = "Value"
                else:
                    # Integer, text, GUID and ObjectID fields all make valid
                    # zones.
                    ok_types = ("OID", "SmallInteger", "Integer", "BigInteger",
                                "String", "GlobalID", "Guid")
                    try:
                        names = [fld.name for fld in
                                 arcpy.ListFields(inz.valueAsText)
                                 if fld.type in ok_types]
                    except Exception:
                        names = []
                    zf.filter.list = names
                    if names and (not zf.altered or
                                  zf.valueAsText not in names):
                        zf.value = names[0]
        inr = P["in_raster"]
        if inr.valueAsText and not inr.hasBeenValidated:
            # Default the convention from the raster's geoprocessing history
            # (Compass unless the lineage says D-infinity Flow Direction).
            # Unconditional on each raster change; module reloaded because
            # validation runs Pro's session-cached copy otherwise.
            try:
                importlib.reload(aspect_tools)
                kind, _why = aspect_tools._detect_source_kind(inr.valueAsText)
            except Exception:
                kind = "unknown"
            if kind == "dinf":
                P["input_convention"].value = aspect_tools.INPUT_CONVENTIONS[1]
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inz, zf, inr = P["in_zone_data"], P["zone_field"], P["in_raster"]
        if inz.valueAsText:
            if self._zone_is_raster(inz):
                try:
                    if not arcpy.Describe(inz.valueAsText).isInteger:
                        inz.setErrorMessage(
                            "The zone raster must be an INTEGER raster "
                            "(each distinct value is a zone).")
                except Exception:
                    pass
            else:
                try:
                    st = arcpy.Describe(inz.valueAsText).shapeType
                    if st == "Multipoint":
                        inz.setErrorMessage(
                            "Multipoint zone features are not supported -- "
                            "use single-part points.")
                except Exception:
                    pass
                if not zf.valueAsText:
                    zf.setErrorMessage(
                        "A zone field is required for feature zone data.")
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage("The aspect raster must be single band.")
            except Exception:
                pass
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(aspect_tools)
            P = {p.name: p for p in parameters}
            in_raster = _resolve_raster_path(P["in_raster"]) or \
                P["in_raster"].valueAsText
            if self._zone_is_raster(P["in_zone_data"]):
                in_zone = _resolve_raster_path(P["in_zone_data"]) or \
                    P["in_zone_data"].valueAsText
            else:
                in_zone = P["in_zone_data"].valueAsText
            aspect_tools.run_aspect_zonal(
                in_zone_data=in_zone,
                zone_field=P["zone_field"].valueAsText or "Value",
                in_raster=in_raster,
                out_table=P["out_table"].valueAsText,
                ignore_nodata=(P["ignore_nodata"].value is not False),
                statistics_type=(P["statistics_type"].valueAsText
                                 or aspect_tools.ZONAL_LABELS[0]),
                input_convention=(P["input_convention"].valueAsText
                                  or aspect_tools.INPUT_CONVENTIONS[0]),
                messages=messages)
            _save_tool_defaults("AspectZonalStatisticsAsTable", {
                "ignore_nodata": P["ignore_nodata"].value is not False,
                "statistics_type": P["statistics_type"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class SlopeZonalStatisticsAsTable(object):
    """Slope Zonal Statistics as Table -- the slope-targeted sibling of
    Esri's Zonal Statistics as Table and of this toolbox's Aspect Zonal
    Statistics as Table: linear statistics computed in ANGLE space (percent
    slopes pass through the arctangent before averaging), reported in the
    source unit, with no Spatial Analyst or Image Analyst required. See
    ``topographic_tools.run_slope_zonal``."""

    def __init__(self):
        self.label = "Slope Zonal Statistics as Table"
        self.description = (
            "Summarizes a slope raster per zone into a table: count, mean, "
            "median, standard deviation, minimum, maximum, range, 5th/95th "
            "percentiles, and the 95% confidence interval of the mean. Very "
            "similar to Esri's Zonal Statistics as Table, but targeted at "
            "slope: the mean, SD and CI are computed on the slope ANGLES "
            "(percent slope is the tangent of the angle -- a convex "
            "transform -- so averaging raw percent values biases steepness "
            "statistics high) and converted back to the raster's unit for "
            "display. Requires no Spatial Analyst or Image Analyst "
            "extension. Slope units (degrees, percent, rise:run ratio, or "
            "gradians) default automatically from the raster's "
            "geoprocessing history. Negative slopes can be treated as "
            "positive (absolute steepness, the default) or keep their sign "
            "so downhill balances uphill; the per-zone count of negative "
            "source values is always reported. Zones come from an INTEGER "
            "raster or point / polyline / polygon features; overlapping "
            "features in different zones are each analyzed in full. Point "
            "zones use bilinear interpolation computed on the slope angles. "
            "Values outside the valid range for the chosen unit (beyond "
            "+/-90 degrees or +/-100 gon) are excluded, counted, and "
            "reported.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_unit = None

    def getParameterInfo(self):
        in_zone = arcpy.Parameter(
            displayName="Input raster or feature zone data", name="in_zone_data",
            datatype=["GPFeatureLayer", "DEFeatureClass", "GPRasterLayer",
                      "DERasterDataset"],
            parameterType="Required", direction="Input")
        # GPString value-list (not a Field parameter): Field-type filters
        # fail to offer ObjectID fields in .pyt tools; the list is filled
        # from the zone dataset in updateParameters.
        zone_field = arcpy.Parameter(
            displayName="Zone field", name="zone_field",
            datatype="GPString", parameterType="Optional", direction="Input")
        zone_field.filter.type = "ValueList"
        zone_field.filter.list = []
        in_raster = arcpy.Parameter(
            displayName="Input slope raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_table = arcpy.Parameter(
            displayName="Output table", name="out_table",
            datatype="DETable", parameterType="Required", direction="Output")
        ignore_nodata = arcpy.Parameter(
            displayName="Ignore NoData in calculations", name="ignore_nodata",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        ignore_nodata.value = True
        statistics_type = arcpy.Parameter(
            displayName="Statistics type", name="statistics_type",
            datatype="GPString", parameterType="Optional", direction="Input")
        statistics_type.filter.type = "ValueList"
        statistics_type.filter.list = topographic_tools.SLOPE_ZONAL_LABELS
        statistics_type.value = topographic_tools.SLOPE_ZONAL_LABELS[0]
        slope_units = arcpy.Parameter(
            displayName="Slope units of the input raster", name="slope_units",
            datatype="GPString", parameterType="Required", direction="Input")
        slope_units.filter.type = "ValueList"
        slope_units.filter.list = topographic_tools.SLOPE_UNITS_Z
        slope_units.value = topographic_tools.SLOPE_UNITS_Z[0]
        use_absolute = arcpy.Parameter(
            displayName="Treat negative slopes as positive (absolute "
                        "steepness)", name="use_absolute",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        use_absolute.value = True
        params = [in_zone, zone_field, in_raster, out_table, ignore_nodata,
                  statistics_type, slope_units, use_absolute]
        saved = _load_tool_defaults("SlopeZonalStatisticsAsTable")
        for p in (ignore_nodata, statistics_type, use_absolute):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    def _zone_is_raster(self, param):
        try:
            dt = arcpy.Describe(param.valueAsText).dataType
            return dt in ("RasterDataset", "RasterLayer", "MosaicDataset",
                          "MosaicLayer")
        except Exception:
            return False

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inz, outt = P["in_zone_data"], P["out_table"]
        if inz.valueAsText:
            if not outt.altered or outt.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(os.path.basename(
                        inz.valueAsText))[0] + "_SlopeZonal")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inz.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outt.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
            if not inz.hasBeenValidated:
                zf = P["zone_field"]
                if self._zone_is_raster(inz):
                    zf.filter.list = ["Value"]
                    if not zf.altered or zf.valueAsText not in ("Value",):
                        zf.value = "Value"
                else:
                    ok_types = ("OID", "SmallInteger", "Integer", "BigInteger",
                                "String", "GlobalID", "Guid")
                    try:
                        names = [fld.name for fld in
                                 arcpy.ListFields(inz.valueAsText)
                                 if fld.type in ok_types]
                    except Exception:
                        names = []
                    zf.filter.list = names
                    if names and (not zf.altered or
                                  zf.valueAsText not in names):
                        zf.value = names[0]
        inr = P["in_raster"]
        if inr.valueAsText and not inr.hasBeenValidated:
            # Default the unit from the raster's geoprocessing history
            # (Esri's Slope / Surface Parameters record DEGREE or
            # PERCENT_RISE); keep the current choice when nothing can be
            # determined. Set UNCONDITIONALLY on each raster change:
            # instance state does not persist across validation cycles, so
            # an altered-guard can never work here. The module is reloaded
            # because VALIDATION (unlike execute) otherwise runs whatever
            # copy Pro cached when the session started.
            try:
                importlib.reload(topographic_tools)
                detected = topographic_tools._detect_slope_units(
                    inr.valueAsText)
            except Exception:
                detected = None
            if detected:
                P["slope_units"].value = (
                    topographic_tools.SLOPE_UNITS_Z[1]
                    if detected == "percent"
                    else topographic_tools.SLOPE_UNITS_Z[0])
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inz, zf, inr = P["in_zone_data"], P["zone_field"], P["in_raster"]
        if inz.valueAsText:
            if self._zone_is_raster(inz):
                try:
                    if not arcpy.Describe(inz.valueAsText).isInteger:
                        inz.setErrorMessage(
                            "The zone raster must be an INTEGER raster "
                            "(each distinct value is a zone).")
                except Exception:
                    pass
            else:
                try:
                    st = arcpy.Describe(inz.valueAsText).shapeType
                    if st == "Multipoint":
                        inz.setErrorMessage(
                            "Multipoint zone features are not supported -- "
                            "use single-part points.")
                except Exception:
                    pass
                if not zf.valueAsText:
                    zf.setErrorMessage(
                        "A zone field is required for feature zone data.")
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage("The slope raster must be single band.")
            except Exception:
                pass
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(topographic_tools)
            importlib.reload(aspect_tools)
            P = {p.name: p for p in parameters}
            in_raster = _resolve_raster_path(P["in_raster"]) or \
                P["in_raster"].valueAsText
            if self._zone_is_raster(P["in_zone_data"]):
                in_zone = _resolve_raster_path(P["in_zone_data"]) or \
                    P["in_zone_data"].valueAsText
            else:
                in_zone = P["in_zone_data"].valueAsText
            topographic_tools.run_slope_zonal(
                in_zone_data=in_zone,
                zone_field=P["zone_field"].valueAsText or "Value",
                in_raster=in_raster,
                out_table=P["out_table"].valueAsText,
                ignore_nodata=(P["ignore_nodata"].value is not False),
                statistics_type=(P["statistics_type"].valueAsText
                                 or topographic_tools.SLOPE_ZONAL_LABELS[0]),
                slope_units=(P["slope_units"].valueAsText
                             or topographic_tools.SLOPE_UNITS_Z[0]),
                use_absolute=(P["use_absolute"].value is not False),
                messages=messages)
            _save_tool_defaults("SlopeZonalStatisticsAsTable", {
                "ignore_nodata": P["ignore_nodata"].value is not False,
                "statistics_type": P["statistics_type"].valueAsText,
                "use_absolute": P["use_absolute"].value is not False,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class FractalDimension(object):
    """Local fractal dimension (surface roughness, D in [2, 3]) of the terrain
    over a moving window, by a selectable method: triangular prism (Clarke
    1986; Ju & Lam 2009), variogram or madogram (Gneiting, Sevcikova &
    Percival 2012 / fractaldim), or differential box counting (Sarkar &
    Chaudhuri 1994)."""

    def __init__(self):
        self.label = "Fractal Dimension"
        self.description = (
            "Computes the local fractal dimension (surface roughness) of a "
            "DEM: for each cell, the fractal dimension D of the terrain "
            "within a moving window, from 2 (smooth -- flat or a smoothly "
            "tilted plane) to 3 (space-filling, maximally rough). Like the "
            "Vector Ruggedness Measure, it is independent of overall slope; "
            "it measures how roughness varies across scales. Four methods are "
            "offered (triangular prism, variogram, madogram, differential box "
            "counting); all estimate the same D but can give somewhat "
            "different values on real terrain.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output fractal-dimension raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        method = arcpy.Parameter(
            displayName="Method", name="method",
            datatype="GPString", parameterType="Required", direction="Input")
        method.filter.type = "ValueList"
        method.filter.list = topographic_tools.FD_METHODS
        method.value = topographic_tools.FD_METHODS[1]   # variogram (recommended default)
        window_size = arcpy.Parameter(
            displayName="Window size (cells, odd)", name="window_size",
            datatype="GPLong", parameterType="Required", direction="Input")
        window_size.value = 25
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        params = [in_raster, out_raster, method, window_size, elev_units]
        saved = _load_tool_defaults("FractalDimension")
        for p in (method, window_size, elev_units):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    _detect_units = EnhancedHillshade._detect_units

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr, units = P["in_raster"], P["out_raster"], P["elev_units"]
        if inr.valueAsText:
            detected, locked = self._detect_units(inr.valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(
                        os.path.basename(inr.valueAsText))[0] + "_FD")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        # Elevation units don't affect the variogram/madogram (amplitude-
        # invariant); they only rescale the prism / box-counting methods.
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr, units = P["in_raster"], P["elev_units"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage("The elevation raster must be single band.")
            except Exception:
                pass
            if units.enabled and not units.valueAsText:
                units.setErrorMessage(
                    "The DEM does not specify its elevation units; choose "
                    "Meters or Feet.")
        w = P["window_size"].value
        if w is not None:
            if w < 9 or w % 2 == 0:
                P["window_size"].setErrorMessage(
                    "The window size must be an odd number of cells, at least "
                    "9 (e.g. 13, 17, 25, 33, 37, 49, or 61).")
            elif (P["method"].valueAsText == topographic_tools.FD_METHODS[0]
                  and len(topographic_tools._fd_divisor_steps(int(w))) < 3):
                P["window_size"].setErrorMessage(
                    "The triangular prism method needs several step sizes that "
                    "divide the window evenly, so (window size minus 1) must "
                    "have several whole-number factors; {} does not. You do "
                    "not enter the step sizes -- the tool derives them. Try "
                    "13, 17, 25, 33, 37, 49, or 61.".format(int(w)))
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(topographic_tools)
            P = {p.name: p for p in parameters}
            out_lyrx = topographic_tools.run_fractal_dimension(
                in_raster=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                method=P["method"].valueAsText or topographic_tools.FD_METHODS[1],
                window_size=P["window_size"].value,
                elev_units=P["elev_units"].valueAsText or "Meters",
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
            _save_tool_defaults("FractalDimension", {
                "method": P["method"].valueAsText,
                "window_size": P["window_size"].value,
                "elev_units": P["elev_units"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class TopographicWetnessIndex(object):
    """Topographic Wetness Index (TWI / CTI): ln(SCA / (tan(beta) + eps)),
    with specific catchment area SCA = (flow accumulation + 1) x cell size and
    slope from the original DEM. Fill (priority-flood), D8 flow direction, and
    flow accumulation are computed internally (no Spatial Analyst); a user may
    supply their own filled DEM and/or flow-accumulation raster to skip those
    steps. Beven & Kirkby 1979."""

    def __init__(self):
        self.label = "Topographic Wetness Index"
        self.description = (
            "Computes the Topographic Wetness Index (TWI), also called the "
            "Compound Topographic Index (CTI): ln( specific catchment area / "
            "(tan(slope) + epsilon) ). High values mark cells with large "
            "upslope area and/or gentle slope (valley bottoms, convergence "
            "zones -- prone to wetness); low values mark ridges and steep, "
            "diverging slopes. Sink filling (priority-flood), D8 flow "
            "direction, and flow accumulation are computed internally -- no "
            "Spatial Analyst required -- or you can supply a pre-filled DEM "
            "and/or flow-accumulation raster. Beven & Kirkby 1979.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output TWI raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        slope_method = arcpy.Parameter(
            displayName="Slope method (projected DEMs)", name="slope_method",
            datatype="GPString", parameterType="Optional", direction="Input")
        slope_method.filter.type = "ValueList"
        slope_method.filter.list = topographic_tools.SLOPE_METHODS
        slope_method.value = topographic_tools.SLOPE_METHODS[0]   # Planar
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        slope_epsilon = arcpy.Parameter(
            displayName="Value added to slope (avoids divide-by-zero)",
            name="slope_epsilon", datatype="GPDouble",
            parameterType="Required", direction="Input")
        slope_epsilon.value = 0.0001
        in_filled_dem = arcpy.Parameter(
            displayName="Existing filled DEM (optional)", name="in_filled_dem",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input",
            category="Use existing intermediate rasters (optional)")
        in_flow_accumulation = arcpy.Parameter(
            displayName="Existing flow-accumulation raster (optional)",
            name="in_flow_accumulation",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input",
            category="Use existing intermediate rasters (optional)")
        out_filled_dem = arcpy.Parameter(
            displayName="Output filled DEM", name="out_filled_dem",
            datatype="DERasterDataset", parameterType="Optional",
            direction="Output",
            category="Save computed intermediate rasters (optional)")
        out_flow_accumulation = arcpy.Parameter(
            displayName="Output flow-accumulation raster",
            name="out_flow_accumulation", datatype="DERasterDataset",
            parameterType="Optional", direction="Output",
            category="Save computed intermediate rasters (optional)")
        params = [in_raster, out_raster, slope_method, elev_units,
                  slope_epsilon, in_filled_dem, in_flow_accumulation,
                  out_filled_dem, out_flow_accumulation]
        saved = _load_tool_defaults("TopographicWetnessIndex")
        for p in (slope_method, elev_units, slope_epsilon):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    _detect_units = EnhancedHillshade._detect_units

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr, units = P["in_raster"], P["out_raster"], P["elev_units"]
        smeth = P["slope_method"]
        if inr.valueAsText:
            detected, locked = self._detect_units(inr.valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            # slope method only applies to projected DEMs (geographic is always
            # geodesic -- spheroidal per row)
            try:
                geo = arcpy.Describe(inr.valueAsText).spatialReference.type \
                    == "Geographic"
            except Exception:
                geo = False
            smeth.enabled = not geo
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(
                        os.path.basename(inr.valueAsText))[0] + "_TWI")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        # Do not offer to save an intermediate the user is supplying: a filled
        # DEM is only computed when neither a filled DEM nor a flow-accumulation
        # raster is supplied; flow accumulation is computed unless supplied.
        have_fac = bool(P["in_flow_accumulation"].valueAsText)
        have_fill = bool(P["in_filled_dem"].valueAsText)
        P["in_filled_dem"].enabled = not have_fac
        ofill, ofac = P["out_filled_dem"], P["out_flow_accumulation"]
        ofill.enabled = not (have_fill or have_fac)
        ofac.enabled = not have_fac
        # A disabled OUTPUT parameter must be EMPTIED, not just greyed out
        # (no .altered guard): any value it still holds makes Pro's GP
        # framework pre-delete that dataset on Run (overwrite-output), which
        # can destroy the very raster now being supplied as an input.
        if not ofill.enabled and ofill.valueAsText:
            ofill.value = None
        if not ofac.enabled and ofac.valueAsText:
            ofac.value = None
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr, units = P["in_raster"], P["elev_units"]
        w = h = None
        if inr.valueAsText:
            try:
                d = arcpy.Describe(inr.valueAsText)
                if d.bandCount != 1:
                    inr.setErrorMessage("The elevation raster must be single band.")
                w, h = d.width, d.height
            except Exception:
                pass
            if units.enabled and not units.valueAsText:
                units.setErrorMessage(
                    "The DEM does not specify its elevation units; choose "
                    "Meters or Feet.")
        eps = P["slope_epsilon"].value
        if eps is not None and eps <= 0:
            P["slope_epsilon"].setErrorMessage(
                "Enter a small positive number (the default 0.0001 is typical).")
        for nm, lbl in (("in_filled_dem", "filled DEM"),
                        ("in_flow_accumulation", "flow-accumulation raster")):
            v = P[nm].valueAsText
            if v and w is not None:
                try:
                    d2 = arcpy.Describe(v)
                    if d2.width != w or d2.height != h:
                        P[nm].setErrorMessage(
                            "This {l} ({a} x {b}) must share the DEM's grid "
                            "({c} x {d}: same extent, cell size, and "
                            "alignment).".format(l=lbl, a=d2.width, b=d2.height,
                                                  c=w, d=h))
                except Exception:
                    pass
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(topographic_tools)
            P = {p.name: p for p in parameters}
            geo = False
            try:
                geo = arcpy.Describe(P["in_raster"].valueAsText) \
                    .spatialReference.type == "Geographic"
            except Exception:
                pass
            out_lyrx = topographic_tools.run_twi(
                in_raster=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                elev_units=P["elev_units"].valueAsText or "Meters",
                slope_method=(P["slope_method"].valueAsText
                              or topographic_tools.SLOPE_METHODS[0]),
                slope_epsilon=P["slope_epsilon"].value,
                in_filled_dem=P["in_filled_dem"].valueAsText or None,
                in_flow_accumulation=P["in_flow_accumulation"].valueAsText or None,
                out_filled_dem=(P["out_filled_dem"].valueAsText
                                if P["out_filled_dem"].enabled else None),
                out_flow_accumulation=(P["out_flow_accumulation"].valueAsText
                                       if P["out_flow_accumulation"].enabled
                                       else None),
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            _writeback_resolved_raster(P, "out_filled_dem")
            _writeback_resolved_raster(P, "out_flow_accumulation")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage(
                        "Could not set output symbology: {}".format(e))
            _save_tool_defaults("TopographicWetnessIndex", {
                "slope_method": P["slope_method"].valueAsText,
                "elev_units": P["elev_units"].valueAsText,
                "slope_epsilon": P["slope_epsilon"].value,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class SimpleTPI(object):
    """Topographic Position Index (TPI): each cell's elevation minus the mean
    elevation of a neighborhood around it (Weiss 2001; Guisan et al. 1999).
    Writes up to three optional outputs: raw TPI (elevation units), and TPI
    standardized (in standard-deviation units) at DEM scale or at neighborhood
    scale. No Spatial Analyst."""

    def __init__(self):
        self.label = "Topographic Position Index (TPI)"
        self.description = (
            "Computes the Topographic Position Index (TPI) of a DEM over a "
            "neighborhood: each cell's elevation minus the mean elevation of "
            "the cells around it. Positive values sit above their surroundings "
            "(ridges, hills); negative values sit below (valleys, channels). "
            "Choose any of five outputs: raw TPI in elevation units, TPI "
            "standardized into standard-deviation units at neighborhood scale "
            "or at DEM scale, TPI in percentile units, or an experimental TPI "
            "standardized by the neighborhood standard deviation of TPI. "
            "Computed internally -- no Spatial Analyst.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None

    _detect_units = EnhancedHillshade._detect_units

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_tpi = arcpy.Parameter(
            displayName="Output TPI (elevation units)", name="out_tpi",
            datatype="DERasterDataset", parameterType="Optional", direction="Output")
        out_dev = arcpy.Parameter(
            displayName="Output TPI, standardized at neighborhood scale (std. deviations)",
            name="out_dev", datatype="DERasterDataset",
            parameterType="Optional", direction="Output")
        out_pctl = arcpy.Parameter(
            displayName="Output TPI (percentile units)", name="out_pctl",
            datatype="DERasterDataset", parameterType="Optional", direction="Output")
        out_demstd = arcpy.Parameter(
            displayName="Output TPI, standardized at DEM scale (std. deviations)",
            name="out_demstd", datatype="DERasterDataset",
            parameterType="Optional", direction="Output")
        out_devtpi = arcpy.Parameter(
            displayName=("Output TPI, standardized by neighborhood SD of TPI "
                         "(experimental)"),
            name="out_devtpi", datatype="DERasterDataset",
            parameterType="Optional", direction="Output")
        nb = _tpi_nbhd_params("nb_", "Neighborhood", 10, None)
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units", datatype="GPString",
            parameterType="Optional", direction="Input")
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        return ([in_raster, out_tpi, out_dev, out_pctl, out_demstd, out_devtpi]
                + nb + [elev_units])

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        P["nb_inner"].enabled = (P["nb_shape"].valueAsText == "Annulus")
        inr, outr, units = P["in_raster"], P["out_tpi"], P["elev_units"]
        dem = inr.valueAsText
        if dem:
            detected, locked = self._detect_units(dem)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            # Suggest an output name ONLY on the validation cycle where the DEM
            # itself just changed (in_raster.hasBeenValidated is False only for
            # the parameter the user just edited) -- never on cycles triggered by
            # editing other fields. This lets the user freely clear this optional
            # output; it will not be re-filled. (All five outputs are optional;
            # at least one is required.) hasBeenValidated is used instead of
            # instance state, which Pro does not guarantee across cycles.
            if not inr.hasBeenValidated:
                if not outr.valueAsText or outr.valueAsText == self._auto_out:
                    try:
                        base = _legalize(os.path.splitext(os.path.basename(
                            dem))[0] + "_TPI")
                        ws = arcpy.env.workspace or os.path.dirname(
                            arcpy.Describe(dem).catalogPath)
                        sug = _unique_name(base, ws)
                        outr.value = sug
                        self._auto_out = sug
                    except Exception:
                        pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        if not (P["out_tpi"].valueAsText or P["out_dev"].valueAsText
                or P["out_pctl"].valueAsText or P["out_demstd"].valueAsText
                or P["out_devtpi"].valueAsText):
            P["out_tpi"].setErrorMessage(
                "Choose at least one output raster (any of the five).")
        if P["nb_outer"].value is not None and P["nb_outer"].value <= 0:
            P["nb_outer"].setErrorMessage("The radius must be positive.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(tpi_tools)
            P = {p.name: p for p in parameters}
            lyrx = tpi_tools.run_tpi(
                in_raster=_resolve_raster_path(P["in_raster"]),
                out_raw=P["out_tpi"].valueAsText or None,
                out_dev=P["out_dev"].valueAsText or None,
                out_pctl=P["out_pctl"].valueAsText or None,
                out_demstd=P["out_demstd"].valueAsText or None,
                out_devtpi=P["out_devtpi"].valueAsText or None,
                shape=P["nb_shape"].valueAsText,
                outer=P["nb_outer"].value,
                inner=P["nb_inner"].value or 0.0,
                units=P["nb_units"].valueAsText,
                elev_units=P["elev_units"].valueAsText or "Meters",
                messages=messages)
            for pname, key in (("out_tpi", "raw"), ("out_dev", "dev"),
                               ("out_pctl", "pctl"), ("out_demstd", "demstd"),
                               ("out_devtpi", "dev_tpi")):
                _writeback_resolved_raster(P, pname)
                if lyrx and lyrx.get(key):
                    try:
                        P[pname].symbology = lyrx[key]
                    except Exception:
                        pass
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


# ----------------------------------------------------------------------------
#  TPI / Slope Position / Landform classification (shared helpers)
# ----------------------------------------------------------------------------
def _tpi_system_param(family, default_name):
    p = arcpy.Parameter(
        displayName="Classification system", name="system",
        datatype="GPString", parameterType="Required", direction="Input")
    p.filter.type = "ValueList"
    try:
        names = classification_store.system_names(family)
    except Exception:
        names = []
    p.filter.list = names
    if default_name in names:
        p.value = default_name
    elif names:
        p.value = names[0]
    return p


def _tpi_nbhd_params(prefix, disp, outer_default, category):
    shape = arcpy.Parameter(
        displayName=disp + " shape", name=prefix + "shape", datatype="GPString",
        parameterType="Required", direction="Input", category=category)
    shape.filter.type = "ValueList"
    shape.filter.list = tpi_tools.NBHD_SHAPES
    shape.value = "Circle"
    outer = arcpy.Parameter(
        displayName=disp + " radius", name=prefix + "outer", datatype="GPDouble",
        parameterType="Required", direction="Input", category=category)
    outer.value = outer_default
    inner = arcpy.Parameter(
        displayName=disp + " inner radius (annulus only)", name=prefix + "inner",
        datatype="GPDouble", parameterType="Optional", direction="Input",
        category=category)
    inner.value = 0
    units = arcpy.Parameter(
        displayName=disp + " radius units", name=prefix + "units",
        datatype="GPString", parameterType="Required", direction="Input",
        category=category)
    units.filter.type = "ValueList"
    units.filter.list = tpi_tools.RADIUS_UNITS
    units.value = "Cells"
    return [shape, outer, inner, units]


def _tpi_refresh_systems(P, family):
    try:
        P["system"].filter.list = classification_store.system_names(family)
    except Exception:
        pass


def _tpi_nbhd_spec(P, prefix, dem):
    return {"kind": "tpi", "dem": dem,
            "shape": P[prefix + "shape"].valueAsText,
            "outer": P[prefix + "outer"].value,
            "inner": P[prefix + "inner"].value or 0.0,
            "units": P[prefix + "units"].valueAsText}


# ---- source toggle + TPI-type / slope-units dropdown machinery --------------
_SRC_ONFLY = "Calculate TPI and Slope on-the-fly from DEM"
_SRC_EXISTING = "Use Existing TPI and Slope Rasters"
# (label shown in the dialog, code stored in the system/spec)
_TPI_TYPE_ONFLY = [("Raw (elevation units)", "raw"),
                   ("Neighborhood standard deviation", "dev"),
                   ("Percentile", "pctl")]
_TPI_TYPE_DECLARE = [("Raw (elevation units)", "raw"),
                     ("Neighborhood standard deviation", "dev"),
                     ("DEM-scale standard deviation", "demstd"),
                     ("Percentile", "pctl")]
_KIND_DEFAULT_LABEL = {"raw": "Raw (elevation units)",
                       "sd": "Neighborhood standard deviation",
                       "pctl": "Percentile"}


def _code_from_label(label, pairs):
    for lbl, code in pairs:
        if lbl == label:
            return code
    return None


def _label_from_code(code, pairs):
    for lbl, c in pairs:
        if c == code:
            return lbl
    return None


def _tpi_source_param():
    p = arcpy.Parameter(
        displayName="TPI and slope source", name="tpi_source",
        datatype="GPString", parameterType="Required", direction="Input")
    p.filter.type = "ValueList"
    p.filter.list = [_SRC_ONFLY, _SRC_EXISTING]
    p.value = _SRC_ONFLY
    return p


def _tpi_type_param(name, disp, pairs, category, optional=False):
    p = arcpy.Parameter(
        displayName=disp, name=name, datatype="GPString",
        parameterType=("Optional" if optional else "Required"),
        direction="Input", category=category)
    p.filter.type = "ValueList"
    p.filter.list = [lbl for (lbl, _c) in pairs]
    return p


def _slope_units_param(name, disp, category, optional=False):
    p = arcpy.Parameter(
        displayName=disp, name=name, datatype="GPString",
        parameterType=("Optional" if optional else "Required"),
        direction="Input", category=category)
    p.filter.type = "ValueList"
    p.filter.list = list(classification_store.SLOPE_UNITS)
    return p


def _save_output_param(name, disp, category):
    return arcpy.Parameter(
        displayName=disp, name=name, datatype="DERasterDataset",
        parameterType="Optional", direction="Output", category=category)


def _tpi_load_system_cached(tool, name, family):
    cache = getattr(tool, "_sys_cache", None)
    if cache is None:
        cache = tool._sys_cache = {}
    key = (family, name)
    if key not in cache:
        try:
            cache[key] = classification_store.load_system(name, family)
        except Exception:
            cache[key] = None
    return cache[key]


def _system_uses_role(system, role):
    """True if any class criterion in ``system`` references ``role``."""
    return any(cr.get("input") == role
               for c in system.get("classes", [])
               for cr in c.get("criteria", []))


def _system_tpi_kind(system, role):
    """The warning KIND (raw/sd/pctl) the system expects for a TPI role."""
    kinds = [tpi_tools._UNIT_KIND.get(cr.get("units", "raw"), "raw")
             for c in system.get("classes", [])
             for cr in c.get("criteria", []) if cr.get("input") == role]
    for want in ("sd", "pctl", "raw"):
        if want in kinds:
            return want
    return "raw"


def _tpi_apply_system_defaults(tool, P, family, tpi_type_roles, slope_units_name):
    """Default the on-the-fly TPI-type and slope-units dropdowns to what the
    selected system expects -- only while the user has not overridden them.
    ``tpi_type_roles`` is a list of (role, tpi_type_param_name)."""
    name = P["system"].valueAsText
    system = _tpi_load_system_cached(tool, name, family) if name else None
    if not system:
        return
    for role, pname in tpi_type_roles:
        par = P.get(pname)
        if par is None or par.altered:
            continue
        want = _KIND_DEFAULT_LABEL.get(_system_tpi_kind(system, role))
        if want:
            par.value = want
    if slope_units_name and P.get(slope_units_name) is not None:
        par = P[slope_units_name]
        su = system.get("slopeUnits")
        if su and not par.altered:
            par.value = su


def _tpi_toggle_enable(P, onfly_names, existing_names):
    onfly = P["tpi_source"].valueAsText != _SRC_EXISTING
    for n in onfly_names:
        if n in P:
            P[n].enabled = onfly
    for n in existing_names:
        if n in P:
            P[n].enabled = not onfly
    return onfly


def _tpi_clear_stale_saves(P, onfly, save_names):
    """In existing-rasters mode the optional save parameters MUST be EMPTIED,
    not merely disabled. They are OUTPUT parameters: any value left over from
    an earlier on-the-fly run makes Pro's geoprocessing framework PRE-DELETE
    that dataset (standard overwrite-output behavior, before execute() ever
    runs) the moment Run is clicked -- destroying the very raster the user is
    now supplying as an existing input. This was the vanishing-saved-raster
    bug: the raster saved fine, then the NEXT run silently deleted it."""
    if onfly:
        return
    for n in save_names:
        p = P.get(n)
        if p is not None and p.valueAsText:
            try:
                p.value = None
            except Exception:
                pass


def _cached_meta(tool, attr, path, reader):
    if not path:
        return None
    cache = getattr(tool, attr, None)
    if cache is None:
        cache = {}
        setattr(tool, attr, cache)
    if path not in cache:
        try:
            cache[path] = reader(path)
        except Exception:
            cache[path] = None
    return cache[path]


def _effective_tpi_type(tool, P, onfly, type_name, raster_name, declare_name):
    """Effective TPI-type code for one role given the current source mode."""
    if onfly:
        return _code_from_label(P[type_name].valueAsText, _TPI_TYPE_ONFLY)
    if P[declare_name].valueAsText:
        return _code_from_label(P[declare_name].valueAsText, _TPI_TYPE_DECLARE)
    return _cached_meta(tool, "_tpi_type_cache", _resolve_raster_path(P[raster_name]),
                        tpi_tools.read_tpi_type)


def _effective_slope_units(tool, P, onfly, units_name, raster_name, declare_name):
    if onfly:
        return P[units_name].valueAsText
    if P[declare_name].valueAsText:
        return P[declare_name].valueAsText
    return _cached_meta(tool, "_slope_units_cache",
                        _resolve_raster_path(P[raster_name]),
                        tpi_tools.read_slope_units)


def _tpi_autofill_declares(tool, P, onfly, tpi_pairs, slope_raster_name):
    """In existing-rasters mode, pre-select each 'Declare TPI type' / 'Declare
    slope units' dropdown from the stamped metadata of ITS OWN supplied raster --
    when our own tools generated it and recorded a type/units -- so the user
    sees the detected value. ``tpi_pairs`` is a list of (raster_param_name,
    declare_param_name), one pair per TPI raster (Landform declares its small-
    and large-neighborhood TPIs independently). Only while the user has not
    overridden the dropdown; a raster with no stamp leaves it blank."""
    if onfly:
        return
    for rn, dn in tpi_pairs:
        dt = P.get(dn)
        if dt is None or dt.altered:
            continue
        code = _cached_meta(tool, "_tpi_type_cache",
                            _resolve_raster_path(P[rn]), tpi_tools.read_tpi_type)
        dt.value = _label_from_code(code, _TPI_TYPE_DECLARE) if code else None
    ds = P.get("declare_slope_units")
    if ds is not None and not ds.altered:
        su = _cached_meta(tool, "_slope_units_cache",
                          _resolve_raster_path(P[slope_raster_name]),
                          tpi_tools.read_slope_units)
        ds.value = su.capitalize() if su else None


def _build_tpi_source(P, onfly, dem, prefix, raster_name, declare_name, save_name,
                      type_name="tpi_type"):
    """One TPI role's ``sources`` spec (on-the-fly from the DEM, or supplied).
    ``type_name`` is that role's own on-the-fly TPI-type dropdown (Landform has
    one per neighborhood scale; Slope Position the single default)."""
    if onfly:
        spec = _tpi_nbhd_spec(P, prefix, dem)
        spec["tpi_type"] = _code_from_label(
            P[type_name].valueAsText, _TPI_TYPE_ONFLY) or "dev"
        if save_name and P[save_name].valueAsText:
            spec["save"] = P[save_name].valueAsText
        return spec
    spec = {"kind": "tpi", "raster": _resolve_raster_path(P[raster_name])}
    dc = _code_from_label(P[declare_name].valueAsText, _TPI_TYPE_DECLARE)
    if dc:
        spec["declared_type"] = dc
    return spec


def _build_slope_source(P, onfly, dem, raster_name, declare_name, save_name):
    if onfly:
        spec = {"kind": "slope", "dem": dem,
                "method": P["slope_method"].valueAsText or "Planar",
                "elev_units": P["elev_units"].valueAsText or "Meters",
                "slope_units": P["slope_units"].valueAsText or "Degrees"}
        if save_name and P[save_name].valueAsText:
            spec["save"] = P[save_name].valueAsText
        return spec
    spec = {"kind": "slope", "raster": _resolve_raster_path(P[raster_name])}
    du = P[declare_name].valueAsText
    if du:
        spec["declared_units"] = du
    return spec


class SlopePositionClassification(object):
    """Slope Position classification: classify terrain into slope-position
    classes (valley, lower/middle/upper slope, flat, ridge) from a TPI (and
    slope), using a saved classification system. Weiss 2001; Jenness TPI."""

    _ONFLY_CAT = "Calculate on-the-fly (from DEM)"
    _EXIST_CAT = "Use existing rasters"
    _ONFLY = ("in_raster", "nb_shape", "nb_outer", "nb_inner", "nb_units",
              "tpi_type", "elev_units", "slope_method", "slope_units",
              "save_tpi", "save_slope")
    _EXIST = ("in_tpi", "in_slope", "declare_tpi_type", "declare_slope_units")

    def __init__(self):
        self.label = "Slope Position Classification"
        self.description = (
            "Classifies a DEM into slope-position classes using a saved "
            "classification system (e.g. Weiss 6-class or Dickson & Beier "
            "4-class). Choose whether the tool computes the TPI and slope "
            "on-the-fly from a DEM (no Spatial Analyst) or uses existing TPI "
            "and slope rasters you supply. Manage the class rules and colors "
            "with the Classification System Builder.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None

    _detect_units = EnhancedHillshade._detect_units

    def getParameterInfo(self):
        source = _tpi_source_param()
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (DEM)",
            name="in_raster", datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input", category=self._ONFLY_CAT)
        system = _tpi_system_param("slope_position", "Weiss 6-Class Slope Position")
        system.category = "Classification"
        out_raster = arcpy.Parameter(
            displayName="Output slope-position raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required",
            direction="Output", category="Output")
        oc = self._ONFLY_CAT
        nb = _tpi_nbhd_params("nb_", "Neighborhood", 10, oc)
        tpi_type = _tpi_type_param("tpi_type", "TPI type (on-the-fly)",
                                   _TPI_TYPE_ONFLY, oc)
        tpi_type.value = _TPI_TYPE_ONFLY[1][0]                 # dev by default
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units", datatype="GPString",
            parameterType="Optional", direction="Input", category=oc)
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        slope_method = arcpy.Parameter(
            displayName="Slope method (projected DEMs)", name="slope_method",
            datatype="GPString", parameterType="Optional", direction="Input",
            category=oc)
        slope_method.filter.type = "ValueList"
        slope_method.filter.list = topographic_tools.SLOPE_METHODS
        slope_method.value = topographic_tools.SLOPE_METHODS[0]
        slope_units = _slope_units_param("slope_units", "Slope output units", oc)
        slope_units.value = classification_store.SLOPE_UNITS[0]
        save_tpi = _save_output_param(
            "save_tpi", "Save generated TPI raster (optional)", oc)
        save_slope = _save_output_param(
            "save_slope", "Save generated slope raster (optional)", oc)
        ec = self._EXIST_CAT
        in_tpi = arcpy.Parameter(
            displayName="Existing TPI raster", name="in_tpi",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input", category=ec)
        in_slope = arcpy.Parameter(
            displayName="Existing slope raster", name="in_slope",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input", category=ec)
        declare_tpi = _tpi_type_param(
            "declare_tpi_type", "Declare TPI type (optional, for warnings)",
            _TPI_TYPE_DECLARE, ec, optional=True)
        declare_slope = _slope_units_param(
            "declare_slope_units", "Declare slope units (optional, for warnings)",
            ec, optional=True)
        return ([source, in_raster] + nb +
                [tpi_type, elev_units, slope_method, slope_units, save_tpi,
                 save_slope, in_tpi, in_slope, declare_tpi, declare_slope,
                 system, out_raster])

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        _tpi_refresh_systems(P, "slope_position")
        onfly = _tpi_toggle_enable(P, self._ONFLY, self._EXIST)
        _tpi_clear_stale_saves(P, onfly, ("save_tpi", "save_slope"))
        _tpi_apply_system_defaults(self, P, "slope_position",
                                   [("tpi", "tpi_type")], "slope_units")
        _tpi_autofill_declares(self, P, onfly,
                               [("in_tpi", "declare_tpi_type")], "in_slope")
        P["nb_inner"].enabled = (onfly and P["nb_shape"].valueAsText == "Annulus")
        units = P["elev_units"]
        primary = (P["in_raster"].valueAsText if onfly
                   else P["in_tpi"].valueAsText)
        if onfly and P["in_raster"].valueAsText:
            detected, locked = self._detect_units(P["in_raster"].valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            try:
                geo = arcpy.Describe(P["in_raster"].valueAsText
                                     ).spatialReference.type == "Geographic"
            except Exception:
                geo = False
            P["slope_method"].enabled = not geo
        outr = P["out_raster"]
        if primary and (not outr.altered or outr.valueAsText == self._auto_out):
            try:
                base = _legalize(os.path.splitext(os.path.basename(
                    primary))[0] + "_SlopePos")
                ws = arcpy.env.workspace or os.path.dirname(
                    arcpy.Describe(primary).catalogPath)
                sug = _unique_name(base, ws)
                outr.value = sug
                self._auto_out = sug
            except Exception:
                pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        onfly = P["tpi_source"].valueAsText != _SRC_EXISTING
        if not P["system"].filter.list:
            P["system"].setWarningMessage(
                "No slope-position classification systems found. Create one "
                "with the Classification System Builder, or reinstall to "
                "restore the bundled defaults.")
        if onfly:
            if not P["in_raster"].valueAsText:
                P["in_raster"].setErrorMessage(
                    "Provide a DEM, or switch to 'Use Existing TPI and Slope "
                    "Rasters'.")
            if (P["nb_outer"].value is not None and P["nb_outer"].value <= 0):
                P["nb_outer"].setErrorMessage("The radius must be positive.")
        else:
            if not P["in_tpi"].valueAsText:
                P["in_tpi"].setErrorMessage(
                    "Provide an existing TPI raster, or switch to on-the-fly.")
        system = _tpi_load_system_cached(self, P["system"].valueAsText,
                                         "slope_position")
        if system:
            if (not onfly and _system_uses_role(system, "slope")
                    and not P["in_slope"].valueAsText):
                P["in_slope"].setErrorMessage(
                    "This system uses slope; provide an existing slope raster.")
            rtype = _effective_tpi_type(self, P, onfly, "tpi_type", "in_tpi",
                                        "declare_tpi_type")
            w = tpi_tools.tpi_type_warning(system, "tpi", rtype)
            if w:
                (P["tpi_type"] if onfly else P["in_tpi"]).setWarningMessage(w)
            su = _effective_slope_units(self, P, onfly, "slope_units",
                                        "in_slope", "declare_slope_units")
            w = tpi_tools.slope_units_warning(system, su)
            if w:
                (P["slope_units"] if onfly else P["in_slope"]).setWarningMessage(w)
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(classification_store)
            importlib.reload(tpi_tools)
            P = {p.name: p for p in parameters}
            onfly = P["tpi_source"].valueAsText != _SRC_EXISTING
            dem = _resolve_raster_path(P["in_raster"])
            system = classification_store.load_system(
                P["system"].valueAsText, "slope_position")
            ref = dem if onfly else (_resolve_raster_path(P["in_tpi"])
                                     or _resolve_raster_path(P["in_slope"]))
            sources = {}
            for inp in system["inputs"]:
                role, kind = inp["role"], inp.get("kind", "external")
                if kind == "tpi":
                    sources[role] = _build_tpi_source(
                        P, onfly, dem, "nb_", "in_tpi", "declare_tpi_type",
                        "save_tpi")
                elif kind == "slope":
                    sources[role] = _build_slope_source(
                        P, onfly, dem, "in_slope", "declare_slope_units",
                        "save_slope")
            lyrx = tpi_tools.run_classification(
                "slope_position", ref, P["out_raster"].valueAsText, system,
                sources, elev_units=P["elev_units"].valueAsText or "Meters",
                slope_method=P["slope_method"].valueAsText or "Planar",
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if lyrx:
                try:
                    P["out_raster"].symbology = lyrx
                except Exception:
                    pass
            for sp in ("save_tpi", "save_slope"):
                if onfly and P[sp].valueAsText:
                    _writeback_saved_output(P, sp)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class LandformClassification(object):
    """Landform classification: classify terrain into landform categories
    (canyons, valleys, plains, open/upper slopes, ridges, peaks) from a small-
    and a large-neighborhood TPI plus slope, using a saved classification
    system (e.g. Weiss 10-class). Weiss 2001; Jenness TPI."""

    _ONFLY_CAT = "Calculate on-the-fly (from DEM)"
    _EXIST_CAT = "Use existing rasters"
    _ONFLY = ("in_raster", "sm_shape", "sm_outer", "sm_inner", "sm_units",
              "tpi_type_small", "lg_shape", "lg_outer", "lg_inner", "lg_units",
              "tpi_type_large", "elev_units", "slope_method", "slope_units",
              "save_tpi_small", "save_tpi_large", "save_slope")
    _EXIST = ("in_tpi_small", "in_tpi_large", "in_slope",
              "declare_tpi_type_small", "declare_tpi_type_large",
              "declare_slope_units")

    def __init__(self):
        self.label = "Landform Classification"
        self.description = (
            "Classifies a DEM into landform categories using a saved "
            "classification system (e.g. Weiss 10-class). Choose whether the "
            "tool computes two TPIs -- at a small and a large neighborhood -- "
            "and slope on-the-fly from a DEM (no Spatial Analyst), or uses "
            "existing TPI and slope rasters you supply. Manage the class rules "
            "and colors with the Classification System Builder.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_units = None

    _detect_units = EnhancedHillshade._detect_units

    def getParameterInfo(self):
        source = _tpi_source_param()
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (DEM)",
            name="in_raster", datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input", category=self._ONFLY_CAT)
        system = _tpi_system_param("landform", "Weiss 10-Class Landform")
        system.category = "Classification"
        out_raster = arcpy.Parameter(
            displayName="Output landform raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required",
            direction="Output", category="Output")
        oc = self._ONFLY_CAT
        sm = _tpi_nbhd_params("sm_", "Small neighborhood", 3, oc)
        lg = _tpi_nbhd_params("lg_", "Large neighborhood", 15, oc)
        tpi_type_s = _tpi_type_param(
            "tpi_type_small", "Small-neighborhood TPI type (on-the-fly)",
            _TPI_TYPE_ONFLY, oc)
        tpi_type_s.value = _TPI_TYPE_ONFLY[1][0]               # dev by default
        tpi_type_l = _tpi_type_param(
            "tpi_type_large", "Large-neighborhood TPI type (on-the-fly)",
            _TPI_TYPE_ONFLY, oc)
        tpi_type_l.value = _TPI_TYPE_ONFLY[1][0]
        elev_units = arcpy.Parameter(
            displayName="Elevation units", name="elev_units", datatype="GPString",
            parameterType="Optional", direction="Input", category=oc)
        elev_units.filter.type = "ValueList"
        elev_units.filter.list = hillshade.ELEV_UNITS
        slope_method = arcpy.Parameter(
            displayName="Slope method (projected DEMs)", name="slope_method",
            datatype="GPString", parameterType="Optional", direction="Input",
            category=oc)
        slope_method.filter.type = "ValueList"
        slope_method.filter.list = topographic_tools.SLOPE_METHODS
        slope_method.value = topographic_tools.SLOPE_METHODS[0]
        slope_units = _slope_units_param("slope_units", "Slope output units", oc)
        slope_units.value = classification_store.SLOPE_UNITS[0]
        save_ts = _save_output_param(
            "save_tpi_small", "Save generated small-neighborhood TPI (optional)", oc)
        save_tl = _save_output_param(
            "save_tpi_large", "Save generated large-neighborhood TPI (optional)", oc)
        save_slope = _save_output_param(
            "save_slope", "Save generated slope raster (optional)", oc)
        ec = self._EXIST_CAT
        in_tpi_s = arcpy.Parameter(
            displayName="Existing small-neighborhood TPI", name="in_tpi_small",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input", category=ec)
        in_tpi_l = arcpy.Parameter(
            displayName="Existing large-neighborhood TPI", name="in_tpi_large",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input", category=ec)
        in_slope = arcpy.Parameter(
            displayName="Existing slope raster", name="in_slope",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input", category=ec)
        declare_tpi_s = _tpi_type_param(
            "declare_tpi_type_small",
            "Declare small-neighborhood TPI type (optional, for warnings)",
            _TPI_TYPE_DECLARE, ec, optional=True)
        declare_tpi_l = _tpi_type_param(
            "declare_tpi_type_large",
            "Declare large-neighborhood TPI type (optional, for warnings)",
            _TPI_TYPE_DECLARE, ec, optional=True)
        declare_slope = _slope_units_param(
            "declare_slope_units", "Declare slope units (optional, for warnings)",
            ec, optional=True)
        # each neighborhood followed by its own TPI-type dropdown (on-the-fly),
        # and each raster followed by its own declare dropdown (existing)
        return ([source, in_raster] + sm + [tpi_type_s] + lg + [tpi_type_l] +
                [elev_units, slope_method, slope_units, save_ts,
                 save_tl, save_slope, in_tpi_s, declare_tpi_s, in_tpi_l,
                 declare_tpi_l, in_slope, declare_slope, system, out_raster])

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        _tpi_refresh_systems(P, "landform")
        onfly = _tpi_toggle_enable(P, self._ONFLY, self._EXIST)
        _tpi_clear_stale_saves(P, onfly, ("save_tpi_small", "save_tpi_large",
                                          "save_slope"))
        _tpi_apply_system_defaults(self, P, "landform",
                                   [("tpi_small", "tpi_type_small"),
                                    ("tpi_large", "tpi_type_large")],
                                   "slope_units")
        _tpi_autofill_declares(self, P, onfly,
                               [("in_tpi_small", "declare_tpi_type_small"),
                                ("in_tpi_large", "declare_tpi_type_large")],
                               "in_slope")
        P["sm_inner"].enabled = (onfly and P["sm_shape"].valueAsText == "Annulus")
        P["lg_inner"].enabled = (onfly and P["lg_shape"].valueAsText == "Annulus")
        units = P["elev_units"]
        primary = (P["in_raster"].valueAsText if onfly
                   else (P["in_tpi_small"].valueAsText
                         or P["in_tpi_large"].valueAsText))
        if onfly and P["in_raster"].valueAsText:
            detected, locked = self._detect_units(P["in_raster"].valueAsText)
            if locked:
                if not units.altered or units.valueAsText == self._auto_units:
                    units.value = detected
                    self._auto_units = detected
                units.enabled = False
            else:
                units.enabled = True
            try:
                geo = arcpy.Describe(P["in_raster"].valueAsText
                                     ).spatialReference.type == "Geographic"
            except Exception:
                geo = False
            P["slope_method"].enabled = not geo
        outr = P["out_raster"]
        if primary and (not outr.altered or outr.valueAsText == self._auto_out):
            try:
                base = _legalize(os.path.splitext(os.path.basename(
                    primary))[0] + "_Landform")
                ws = arcpy.env.workspace or os.path.dirname(
                    arcpy.Describe(primary).catalogPath)
                sug = _unique_name(base, ws)
                outr.value = sug
                self._auto_out = sug
            except Exception:
                pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        onfly = P["tpi_source"].valueAsText != _SRC_EXISTING
        if not P["system"].filter.list:
            P["system"].setWarningMessage(
                "No landform classification systems found. Create one with the "
                "Classification System Builder, or reinstall to restore the "
                "bundled defaults.")
        if onfly:
            if not P["in_raster"].valueAsText:
                P["in_raster"].setErrorMessage(
                    "Provide a DEM, or switch to 'Use Existing TPI and Slope "
                    "Rasters'.")
            for k in ("sm_outer", "lg_outer"):
                if P[k].value is not None and P[k].value <= 0:
                    P[k].setErrorMessage("The radius must be positive.")
        else:
            for pn, lbl in (("in_tpi_small", "small-neighborhood"),
                            ("in_tpi_large", "large-neighborhood")):
                if not P[pn].valueAsText:
                    P[pn].setErrorMessage(
                        "Provide the existing {} TPI raster, or switch to "
                        "on-the-fly.".format(lbl))
        system = _tpi_load_system_cached(self, P["system"].valueAsText, "landform")
        if system:
            if (not onfly and _system_uses_role(system, "slope")
                    and not P["in_slope"].valueAsText):
                P["in_slope"].setErrorMessage(
                    "This system uses slope; provide an existing slope raster.")
            for role, rp, dn, tn in (
                    ("tpi_small", "in_tpi_small", "declare_tpi_type_small",
                     "tpi_type_small"),
                    ("tpi_large", "in_tpi_large", "declare_tpi_type_large",
                     "tpi_type_large")):
                rtype = _effective_tpi_type(self, P, onfly, tn, rp, dn)
                w = tpi_tools.tpi_type_warning(system, role, rtype)
                if w:
                    (P[tn] if onfly else P[rp]).setWarningMessage(w)
            su = _effective_slope_units(self, P, onfly, "slope_units",
                                        "in_slope", "declare_slope_units")
            w = tpi_tools.slope_units_warning(system, su)
            if w:
                (P["slope_units"] if onfly else P["in_slope"]).setWarningMessage(w)
        # Scale sanity check: the small TPI's neighborhood should be SMALLER
        # than the large one's. On-the-fly compares the entered radii; existing
        # mode compares the radii stamped in the rasters' metadata by our TPI
        # tools (unstamped rasters -- e.g. made before v1.31.1, or by other
        # software -- cannot be checked).
        if onfly:
            w = tpi_tools.tpi_scale_warning(
                (P["sm_outer"].value, P["sm_units"].valueAsText or "Cells"),
                (P["lg_outer"].value, P["lg_units"].valueAsText or "Cells"))
            if w:
                P["sm_outer"].setWarningMessage(w)
                P["lg_outer"].setWarningMessage(w)
        else:
            nb_s = _cached_meta(self, "_tpi_nbhd_cache",
                                _resolve_raster_path(P["in_tpi_small"]),
                                tpi_tools.read_tpi_nbhd)
            nb_l = _cached_meta(self, "_tpi_nbhd_cache",
                                _resolve_raster_path(P["in_tpi_large"]),
                                tpi_tools.read_tpi_nbhd)
            w = tpi_tools.tpi_scale_warning(nb_s, nb_l)
            if w:
                P["in_tpi_small"].setWarningMessage(w)
                P["in_tpi_large"].setWarningMessage(w)
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(classification_store)
            importlib.reload(tpi_tools)
            P = {p.name: p for p in parameters}
            onfly = P["tpi_source"].valueAsText != _SRC_EXISTING
            dem = _resolve_raster_path(P["in_raster"])
            system = classification_store.load_system(
                P["system"].valueAsText, "landform")
            ref = dem if onfly else (_resolve_raster_path(P["in_tpi_small"])
                                     or _resolve_raster_path(P["in_tpi_large"])
                                     or _resolve_raster_path(P["in_slope"]))
            role_prefix = {
                "tpi_small": ("sm_", "in_tpi_small", "declare_tpi_type_small",
                              "save_tpi_small", "tpi_type_small"),
                "tpi_large": ("lg_", "in_tpi_large", "declare_tpi_type_large",
                              "save_tpi_large", "tpi_type_large")}
            sources = {}
            for inp in system["inputs"]:
                role, kind = inp["role"], inp.get("kind", "external")
                if kind == "tpi":
                    if role not in role_prefix:
                        raise arcpy.ExecuteError(
                            "Unsupported TPI input role '{}' in system '{}': "
                            "this tool binds only 'tpi_small' and 'tpi_large'. "
                            "Edit the system in the Classification System "
                            "Builder.".format(role, system["name"]))
                    pre, rp, dn, sv, tn = role_prefix[role]
                    sources[role] = _build_tpi_source(
                        P, onfly, dem, pre, rp, dn, sv, type_name=tn)
                elif kind == "slope":
                    sources[role] = _build_slope_source(
                        P, onfly, dem, "in_slope", "declare_slope_units",
                        "save_slope")
            lyrx = tpi_tools.run_classification(
                "landform", ref, P["out_raster"].valueAsText, system, sources,
                elev_units=P["elev_units"].valueAsText or "Meters",
                slope_method=P["slope_method"].valueAsText or "Planar",
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if lyrx:
                try:
                    P["out_raster"].symbology = lyrx
                except Exception:
                    pass
            for sp in ("save_tpi_small", "save_tpi_large", "save_slope"):
                if onfly and P[sp].valueAsText:
                    _writeback_saved_output(P, sp)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class GeneralRasterClassification(object):
    """General single-raster classification: classify any raster into
    categories using a saved 'general' classification system (threshold rules
    on the raster's raw or standardized values). Jenness TPI extension."""

    def __init__(self):
        self.label = "General Raster Classification"
        self.description = (
            "Classifies any single raster into categories using a saved "
            "'general' classification system (threshold rules on the raster's "
            "raw or whole-raster standardized values). Similar in purpose to "
            "Esri's standard Reclassify tool, except that the classification "
            "scheme is authored once, as a reusable named system, in the "
            "Classification System Builder; that each output class stores a "
            "name (and color) with the raster; and that no Spatial Analyst or "
            "3D Analyst extension is required.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output classified raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        system = _tpi_system_param("general", "Basic 4-Class (Standardized Bands)")
        return [in_raster, out_raster, system]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        _tpi_refresh_systems(P, "general")
        inr, outr = P["in_raster"], P["out_raster"]
        if inr.valueAsText and (not outr.altered or outr.valueAsText == self._auto_out):
            try:
                base = _legalize(os.path.splitext(os.path.basename(
                    inr.valueAsText))[0] + "_Classified")
                ws = arcpy.env.workspace or os.path.dirname(
                    arcpy.Describe(inr.valueAsText).catalogPath)
                sug = _unique_name(base, ws)
                outr.value = sug
                self._auto_out = sug
            except Exception:
                pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        if not P["system"].filter.list:
            P["system"].setWarningMessage(
                "No general classification systems found. Create one with the "
                "Classification System Builder, or reinstall to restore the "
                "bundled defaults.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(classification_store)
            importlib.reload(tpi_tools)
            P = {p.name: p for p in parameters}
            grid = _resolve_raster_path(P["in_raster"])
            system = classification_store.load_system(
                P["system"].valueAsText, "general")
            externals = [inp for inp in system["inputs"]
                         if inp.get("kind", "external") == "external"]
            if len(externals) != 1:
                raise arcpy.ExecuteError(
                    "This tool supports general systems with exactly one input "
                    "raster; '{}' declares {}.".format(system["name"],
                                                       len(externals)))
            role = externals[0]["role"]
            # whole-raster standardize the grid iff the system thresholds its
            # standardized (z-score) values; raw-unit systems classify as-is
            standardize = any(
                cr.get("units") == "standardized"
                for c in system["classes"] for cr in c["criteria"]
                if cr.get("input") == role)
            sources = {role: {"kind": "external", "raster": grid,
                              "standardize": standardize}}
            lyrx = tpi_tools.run_classification(
                "general", grid, P["out_raster"].valueAsText, system, sources,
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if lyrx:
                try:
                    P["out_raster"].symbology = lyrx
                except Exception:
                    pass
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class LSRIOnDEM(object):
    """Land Surface Ruggedness Index (LSRI), computed on the fly from a DEM:
    generates contours (matching Esri's own Contour tool's interval / base /
    z-factor logic, unsmoothed), then reports total contour length within a
    circular neighborhood of each cell (Beasom et al. 1983)."""

    def __init__(self):
        self.label = "LSRI on DEM"
        self.description = (
            "Computes the Land Surface Ruggedness Index (LSRI) directly from "
            "a DEM: generates contours on the fly (the same Contour interval "
            "/ Base contour / Z factor logic as Esri's own Contour tool, "
            "unsmoothed), then reports the total length of contour line "
            "within a circular neighborhood of each cell (Beasom, Wiggers, "
            "and Giardino 1983) -- higher values mean more, or more "
            "convoluted, contour line nearby, i.e. more rugged terrain. The "
            "derived contours may optionally be saved.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_contours = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input elevation raster (single band)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output LSRI raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        contour_interval = arcpy.Parameter(
            displayName="Contour interval", name="contour_interval",
            datatype="GPDouble", parameterType="Required", direction="Input")
        base_contour = arcpy.Parameter(
            displayName="Base contour", name="base_contour",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        base_contour.value = 0.0
        z_factor = arcpy.Parameter(
            displayName="Z factor", name="z_factor",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        z_factor.value = 1.0
        radius = arcpy.Parameter(
            displayName="Neighborhood radius", name="radius",
            datatype="GPDouble", parameterType="Required", direction="Input")
        radius_units = arcpy.Parameter(
            displayName="Neighborhood radius units", name="radius_units",
            datatype="GPString", parameterType="Required", direction="Input")
        radius_units.filter.type = "ValueList"
        radius_units.filter.list = lsri.RADIUS_UNITS
        radius_units.value = "Meters"
        length_units = arcpy.Parameter(
            displayName="Length units", name="length_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        length_units.filter.type = "ValueList"
        length_units.filter.list = lsri.LENGTH_UNITS
        length_units.value = "Meters"
        out_cell_size = arcpy.Parameter(
            displayName="Output cell size (default: same as input DEM)",
            name="out_cell_size", datatype="GPDouble", parameterType="Optional",
            direction="Input", category="Output options")
        save_contours = arcpy.Parameter(
            displayName="Save the contour feature class", name="save_contours",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        save_contours.value = False
        out_contours = arcpy.Parameter(
            displayName="Output contour feature class", name="out_contours",
            datatype="DEFeatureClass", parameterType="Optional", direction="Output")
        out_contours.enabled = False
        params = [in_raster, out_raster, contour_interval, base_contour,
                  z_factor, radius, radius_units, length_units, out_cell_size,
                  save_contours, out_contours]
        saved = _load_tool_defaults("LSRIOnDEM")
        for p in (contour_interval, base_contour, z_factor, radius,
                  radius_units, length_units):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr = P["in_raster"], P["out_raster"]
        if inr.valueAsText and (not outr.altered or outr.valueAsText == self._auto_out):
            try:
                base = _legalize(os.path.splitext(
                    os.path.basename(inr.valueAsText))[0] + "_LSRI")
                ws = arcpy.env.workspace or os.path.dirname(
                    arcpy.Describe(inr.valueAsText).catalogPath)
                sug = _unique_name(base, ws)
                outr.value = sug
                self._auto_out = sug
            except Exception:
                pass
        on = bool(P["save_contours"].value)
        P["out_contours"].enabled = on
        # disabled OUTPUT params must be emptied -- a leftover value would be
        # pre-deleted by the GP framework on the next Run (overwrite-output)
        if not on and P["out_contours"].valueAsText:
            P["out_contours"].value = None
        if on and inr.valueAsText and (not P["out_contours"].altered or
                                       P["out_contours"].valueAsText == self._auto_contours):
            try:
                base = _legalize(os.path.splitext(
                    os.path.basename(inr.valueAsText))[0] + "_Contours")
                ws = arcpy.env.workspace or os.path.dirname(
                    arcpy.Describe(inr.valueAsText).catalogPath)
                sug = _unique_name(base, ws)
                P["out_contours"].value = sug
                self._auto_contours = sug
            except Exception:
                pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        if P["in_raster"].valueAsText:
            try:
                if arcpy.Describe(P["in_raster"].valueAsText).bandCount != 1:
                    P["in_raster"].setErrorMessage(
                        "The elevation raster must be single band.")
            except Exception:
                pass
        ci = P["contour_interval"].value
        if ci is not None and ci <= 0:
            P["contour_interval"].setErrorMessage("The contour interval must be positive.")
        r = P["radius"].value
        if r is not None and r <= 0:
            P["radius"].setErrorMessage("The neighborhood radius must be positive.")
        ocs = P["out_cell_size"].value
        if ocs is not None and ocs <= 0:
            P["out_cell_size"].setErrorMessage("The output cell size must be positive.")
        if P["save_contours"].value and not P["out_contours"].valueAsText:
            P["out_contours"].setErrorMessage(
                "Specify an output contour feature class (or uncheck "
                "'Save the contour feature class').")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(lsri)
            P = {p.name: p for p in parameters}
            out_lyrx = lsri.run_lsri_on_dem(
                in_raster=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                contour_interval=P["contour_interval"].value,
                base_contour=P["base_contour"].value,
                z_factor=P["z_factor"].value,
                radius=P["radius"].value,
                radius_units=P["radius_units"].valueAsText or "Meters",
                length_units=P["length_units"].valueAsText or "Meters",
                out_contours=(P["out_contours"].valueAsText
                             if P["save_contours"].value else None),
                out_cell_size=P["out_cell_size"].value,
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
            _save_tool_defaults("LSRIOnDEM", {
                "contour_interval": P["contour_interval"].value,
                "base_contour": P["base_contour"].value,
                "z_factor": P["z_factor"].value,
                "radius": P["radius"].value,
                "radius_units": P["radius_units"].valueAsText,
                "length_units": P["length_units"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class LSRIOnContourPolylines(object):
    """Land Surface Ruggedness Index (LSRI), computed directly from an
    existing contour (or any polyline) feature class: total polyline length
    within a circular neighborhood of each cell (Beasom et al. 1983)."""

    def __init__(self):
        self.label = "LSRI on Contours"
        self.description = (
            "Computes the Land Surface Ruggedness Index (LSRI) directly from "
            "an existing contour (or any polyline) feature class: the total "
            "length of polyline within a circular neighborhood of each cell "
            "(Beasom, Wiggers, and Giardino 1983) -- higher values mean "
            "more, or more convoluted, line nearby. If the input has a "
            "selection, only the selected features are used.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None
        self._auto_cell_size = None

    def getParameterInfo(self):
        in_polylines = arcpy.Parameter(
            displayName="Input polyline features", name="in_polylines",
            datatype="GPFeatureLayer", parameterType="Required", direction="Input")
        in_polylines.filter.list = ["Polyline"]
        out_raster = arcpy.Parameter(
            displayName="Output LSRI raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required", direction="Output")
        cell_size = arcpy.Parameter(
            displayName="Cell size", name="cell_size",
            datatype="GPDouble", parameterType="Required", direction="Input")
        radius = arcpy.Parameter(
            displayName="Neighborhood radius", name="radius",
            datatype="GPDouble", parameterType="Required", direction="Input")
        radius_units = arcpy.Parameter(
            displayName="Neighborhood radius units", name="radius_units",
            datatype="GPString", parameterType="Required", direction="Input")
        radius_units.filter.type = "ValueList"
        radius_units.filter.list = lsri.RADIUS_UNITS
        radius_units.value = "Meters"
        length_units = arcpy.Parameter(
            displayName="Length units", name="length_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        length_units.filter.type = "ValueList"
        length_units.filter.list = lsri.LENGTH_UNITS
        length_units.value = "Meters"
        params = [in_polylines, out_raster, cell_size, radius, radius_units,
                  length_units]
        saved = _load_tool_defaults("LSRIOnContourPolylines")
        for p in (radius, radius_units, length_units):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inl, outr, cs = P["in_polylines"], P["out_raster"], P["cell_size"]
        if inl.valueAsText:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(
                        os.path.basename(inl.valueAsText))[0] + "_LSRI")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inl.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
            if not cs.altered or cs.value == self._auto_cell_size:
                try:
                    importlib.reload(lsri)
                    sug = lsri.default_cell_size_from_extent(inl.valueAsText)
                    if sug:
                        cs.value = sug
                        self._auto_cell_size = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        cs = P["cell_size"].value
        if cs is not None and cs <= 0:
            P["cell_size"].setErrorMessage("The cell size must be positive.")
        r = P["radius"].value
        if r is not None and r <= 0:
            P["radius"].setErrorMessage("The neighborhood radius must be positive.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(lsri)
            P = {p.name: p for p in parameters}
            out_lyrx = lsri.run_lsri_on_contours(
                in_polylines=P["in_polylines"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                cell_size=P["cell_size"].value,
                radius=P["radius"].value,
                radius_units=P["radius_units"].valueAsText or "Meters",
                length_units=P["length_units"].valueAsText or "Meters",
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage("Could not set output symbology: {}".format(e))
            _save_tool_defaults("LSRIOnContourPolylines", {
                "radius": P["radius"].value,
                "radius_units": P["radius_units"].valueAsText,
                "length_units": P["length_units"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")



class DiversityIndices(object):
    """Diversity Indices -- neighborhood diversity of a CATEGORICAL raster
    (each cell a class, typically a species or community). Effective number of
    species (Hill numbers), Shannon, and Simpson measures over any neighborhood
    shape/size, projected or geographic, with no Spatial Analyst. Part of the
    Land Facet Corridor Designer tool family. See ``diversity_tools``."""

    _RECT = {"width", "height"}
    _CIRC = {"radius"}
    _ANN = {"inner_radius", "outer_radius"}
    _WEDGE = {"radius", "start_angle", "end_angle"}
    _FILE = {"kernel_file"}

    def __init__(self):
        self.label = "Diversity Indices"
        self.description = (
            "Neighborhood (focal) diversity of a categorical raster -- each "
            "cell is a class, typically a species or vegetation/community type. "
            "Over a moving neighborhood of any shape and size it reports the "
            "effective number of species (Hill numbers of order q -- the "
            "default and most defensible modern measure), Shannon's index, the "
            "Gini-Simpson index, or Simpson's concentration. Works on projected "
            "or geographic rasters, with the same geodesic per-row adjustment "
            "the other neighborhood tools use, and needs no Spatial Analyst. A "
            "modernized port of the Land Facet Corridor Designer's ArcMap "
            "Shannon's Index tool.")
        self.category = "Corridor Designer Tools\\Land Facet Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input categorical raster (single band)",
            name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output diversity raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required",
            direction="Output")
        index = arcpy.Parameter(
            displayName="Diversity measure", name="index",
            datatype="GPString", parameterType="Required", direction="Input")
        index.filter.type = "ValueList"
        index.filter.list = diversity_tools.INDEX_LABELS
        index.value = diversity_tools.INDEX_LABELS[0]
        q = arcpy.Parameter(
            displayName="Order (q) for the effective number", name="q",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        q.value = 1
        neighborhood = arcpy.Parameter(
            displayName="Neighborhood", name="neighborhood",
            datatype="GPString", parameterType="Required", direction="Input")
        neighborhood.filter.type = "ValueList"
        neighborhood.filter.list = diversity_tools.NEIGHBORHOODS
        neighborhood.value = "Circle"
        nbr_units = arcpy.Parameter(
            displayName="Neighborhood units", name="nbr_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        nbr_units.filter.type = "ValueList"
        nbr_units.filter.list = diversity_tools.NBR_UNITS
        nbr_units.value = "Cells"
        radius = arcpy.Parameter(
            displayName="Radius", name="radius",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        radius.value = 3
        width = arcpy.Parameter(
            displayName="Width", name="width",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        width.value = 3
        height = arcpy.Parameter(
            displayName="Height", name="height",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        height.value = 3
        inner_radius = arcpy.Parameter(
            displayName="Inner radius", name="inner_radius",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        inner_radius.value = 1
        outer_radius = arcpy.Parameter(
            displayName="Outer radius", name="outer_radius",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        outer_radius.value = 3
        start_angle = arcpy.Parameter(
            displayName="Start angle (deg, CCW from east)", name="start_angle",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        start_angle.value = 0
        end_angle = arcpy.Parameter(
            displayName="End angle (deg, CCW from east)", name="end_angle",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        end_angle.value = 90
        kernel_file = arcpy.Parameter(
            displayName="Kernel file", name="kernel_file",
            datatype="DEFile", parameterType="Optional", direction="Input")
        kernel_file.filter.list = ["txt"]
        exclude_null = arcpy.Parameter(
            displayName="Return NoData if the neighborhood includes any NoData "
                        "cell", name="exclude_null",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        exclude_null.value = False
        params = [in_raster, out_raster, index, q, neighborhood, nbr_units,
                  radius, width, height, inner_radius, outer_radius,
                  start_angle, end_angle, kernel_file, exclude_null]
        saved = _load_tool_defaults("DiversityIndices")
        for p in (index, neighborhood, nbr_units):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    def _relevant(self, nbr):
        if nbr == "Rectangle":
            return self._RECT
        if nbr == "Circle":
            return self._CIRC
        if nbr == "Annulus":
            return self._ANN
        if nbr == "Wedge":
            return self._WEDGE
        if nbr in ("Irregular", "Weight"):
            return self._FILE
        return set()

    def _abbr(self, index_key, q):
        base = diversity_tools._ABBR.get(index_key, "Div")
        if index_key == "EFFECTIVE" and q is not None:
            try:
                base += "_q{:g}".format(float(q))
            except Exception:
                pass
        return base

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr = P["in_raster"], P["out_raster"]
        index_key = diversity_tools.INDEX_KEY.get(
            P["index"].valueAsText, "EFFECTIVE")
        # q applies only to the effective number.
        P["q"].enabled = (index_key == "EFFECTIVE")
        nbr = P["neighborhood"].valueAsText or "Circle"
        rel = self._relevant(nbr)
        for nm in ("radius", "width", "height", "inner_radius", "outer_radius",
                   "start_angle", "end_angle", "kernel_file"):
            P[nm].enabled = nm in rel
        P["nbr_units"].enabled = nbr not in ("Irregular", "Weight")

        if inr.valueAsText:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(os.path.basename(
                        inr.valueAsText))[0] + "_"
                        + self._abbr(index_key, P["q"].value))
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr = P["in_raster"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage(
                        "The categorical raster must be single band.")
            except Exception:
                pass
        index_key = diversity_tools.INDEX_KEY.get(
            P["index"].valueAsText, "EFFECTIVE")
        if index_key == "EFFECTIVE":
            qv = P["q"].value
            if qv is not None and qv < 0:
                P["q"].setErrorMessage(
                    "The order q must be zero or positive.")
        nbr = P["neighborhood"].valueAsText or "Circle"
        rel = self._relevant(nbr)
        for nm in ("radius", "width", "height", "inner_radius", "outer_radius"):
            if nm in rel:
                v = P[nm].value
                if v is not None and v <= 0:
                    P[nm].setErrorMessage("Must be a positive value.")
        if nbr == "Annulus":
            ri, ro = P["inner_radius"].value, P["outer_radius"].value
            if ri is not None and ro is not None and ri >= ro:
                P["outer_radius"].setErrorMessage(
                    "The outer radius must be greater than the inner radius.")
        if nbr in ("Irregular", "Weight"):
            kf = P["kernel_file"].valueAsText
            if not kf:
                P["kernel_file"].setErrorMessage(
                    "The {} neighborhood needs a kernel file.".format(nbr))
            elif not os.path.exists(kf):
                P["kernel_file"].setErrorMessage("Kernel file not found.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(diversity_tools)
            P = {p.name: p for p in parameters}
            in_path = (_resolve_raster_path(P["in_raster"])
                       or P["in_raster"].valueAsText)
            index_key = diversity_tools.INDEX_KEY.get(
                P["index"].valueAsText, "EFFECTIVE")
            out_lyrx = diversity_tools.run_diversity(
                in_raster=in_path,
                out_raster=P["out_raster"].valueAsText,
                index=index_key,
                q=P["q"].value if P["q"].value is not None else 1.0,
                neighborhood=P["neighborhood"].valueAsText or "Circle",
                radius=P["radius"].value if P["radius"].value is not None else 3.0,
                width=P["width"].value if P["width"].value is not None else 3.0,
                height=P["height"].value if P["height"].value is not None else 3.0,
                inner_radius=(P["inner_radius"].value
                              if P["inner_radius"].value is not None else 1.0),
                outer_radius=(P["outer_radius"].value
                              if P["outer_radius"].value is not None else 3.0),
                start_angle=(P["start_angle"].value
                             if P["start_angle"].value is not None else 0.0),
                end_angle=(P["end_angle"].value
                           if P["end_angle"].value is not None else 90.0),
                nbr_units=P["nbr_units"].valueAsText or "Cells",
                kernel_file=P["kernel_file"].valueAsText,
                exclude_if_any_null=bool(P["exclude_null"].value),
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            if out_lyrx:
                try:
                    P["out_raster"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage(
                        "Could not set output symbology: {}".format(e))
            _save_tool_defaults("DiversityIndices", {
                "index": P["index"].valueAsText,
                "neighborhood": P["neighborhood"].valueAsText,
                "nbr_units": P["nbr_units"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


# Dialog wording for the two denominator rules. The first reproduces the
# original ArcMap tool exactly; see land_facet_density's module docstring for
# why it is the default and why the second is not comparable across analyses.
_DENOM_FAITHFUL = "Every neighborhood cell inside the raster (original rule)"
_DENOM_NONNULL = "Only neighborhood cells that hold data (ignore NoData)"


class LandFacetDensity(object):
    """Land Facet Density -- the proportion of a neighborhood holding each land
    facet, as ONE multi-band raster (a band per facet) rather than the original
    ArcMap tool's one-raster-per-facet. See ``land_facet_density``."""

    _RECT = {"width", "height"}
    _CIRC = {"radius"}
    _ANN = {"inner_radius", "outer_radius"}
    _WEDGE = {"radius", "start_angle", "end_angle"}
    _FILE = {"kernel_file"}

    def __init__(self):
        self.label = "Land Facet Density"
        self.description = (
            "For every land facet in a land facet raster, the proportion of the "
            "surrounding neighborhood composed of that facet (0 to 1) -- "
            "written as ONE multi-band raster with a band per facet, computed "
            "in a single pass. The original ArcMap Calculate Density Surface "
            "tool produced one single-band raster per facet, one run at a time. "
            "The density surface places corridor termini inside the Wildland "
            "Blocks and supplies one of the variables for the Mahalanobis cost "
            "surface, where its ideal value is 100% rather than its mean. A "
            "linkage record naming which band holds which facet is written into "
            "the output's metadata, because raster band names do not survive "
            "being copied into a geodatabase. Needs no Spatial Analyst.")
        self.category = "Corridor Designer Tools\\Land Facet Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input land facet raster (single band)",
            name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output multi-band density raster", name="out_raster",
            datatype="DERasterDataset", parameterType="Required",
            direction="Output")
        facets = arcpy.Parameter(
            displayName="Land facets to include (default: all)", name="facets",
            datatype="GPString", parameterType="Optional", direction="Input",
            multiValue=True)
        neighborhood = arcpy.Parameter(
            displayName="Neighborhood", name="neighborhood",
            datatype="GPString", parameterType="Required", direction="Input")
        neighborhood.filter.type = "ValueList"
        neighborhood.filter.list = land_facet_density.NEIGHBORHOODS
        neighborhood.value = "Circle"
        nbr_units = arcpy.Parameter(
            displayName="Neighborhood units", name="nbr_units",
            datatype="GPString", parameterType="Optional", direction="Input")
        nbr_units.filter.type = "ValueList"
        nbr_units.filter.list = land_facet_density.NBR_UNITS
        nbr_units.value = "Cells"
        radius = arcpy.Parameter(
            displayName="Radius", name="radius",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        radius.value = 3
        width = arcpy.Parameter(
            displayName="Width", name="width",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        width.value = 3
        height = arcpy.Parameter(
            displayName="Height", name="height",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        height.value = 3
        inner_radius = arcpy.Parameter(
            displayName="Inner radius", name="inner_radius",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        inner_radius.value = 1
        outer_radius = arcpy.Parameter(
            displayName="Outer radius", name="outer_radius",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        outer_radius.value = 3
        start_angle = arcpy.Parameter(
            displayName="Start angle (deg, CCW from east)", name="start_angle",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        start_angle.value = 0
        end_angle = arcpy.Parameter(
            displayName="End angle (deg, CCW from east)", name="end_angle",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        end_angle.value = 90
        kernel_file = arcpy.Parameter(
            displayName="Kernel file", name="kernel_file",
            datatype="DEFile", parameterType="Optional", direction="Input")
        kernel_file.filter.list = ["txt"]
        denominator = arcpy.Parameter(
            displayName="Denominator", name="denominator",
            datatype="GPString", parameterType="Optional", direction="Input",
            category="Advanced")
        denominator.filter.type = "ValueList"
        denominator.filter.list = [_DENOM_FAITHFUL, _DENOM_NONNULL]
        denominator.value = _DENOM_FAITHFUL
        params = [in_raster, out_raster, facets, neighborhood, nbr_units,
                  radius, width, height, inner_radius, outer_radius,
                  start_angle, end_angle, kernel_file, denominator]
        saved = _load_tool_defaults("LandFacetDensity")
        for p in (neighborhood, nbr_units):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    def _relevant(self, nbr):
        if nbr == "Rectangle":
            return self._RECT
        if nbr == "Circle":
            return self._CIRC
        if nbr == "Annulus":
            return self._ANN
        if nbr == "Wedge":
            return self._WEDGE
        if nbr in ("Irregular", "Weight"):
            return self._FILE
        return set()

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr = P["in_raster"], P["out_raster"]
        nbr = P["neighborhood"].valueAsText or "Circle"
        rel = self._relevant(nbr)
        for nm in ("radius", "width", "height", "inner_radius", "outer_radius",
                   "start_angle", "end_angle", "kernel_file"):
            P[nm].enabled = nm in rel
        P["nbr_units"].enabled = nbr not in ("Irregular", "Weight")

        # Offer the facet values as a pick list, labelled from the linkage
        # record when the input carries one. Gated on hasBeenValidated so it
        # refreshes when the raster changes but does not fight the user.
        if inr.valueAsText and not inr.hasBeenValidated:
            try:
                path = _resolve_raster_path(inr) or inr.valueAsText
                rec = land_facet_linkage.read(path, land_facet_linkage.FACETS)
                choices = []
                if rec:
                    for f in rec.get("facets") or []:
                        choices.append("{} ({})".format(
                            f.get("facet_id"), land_facet_linkage.facet_label(f)))
                if choices:
                    P["facets"].filter.list = choices
            except Exception:
                pass

        if inr.valueAsText:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(os.path.basename(
                        inr.valueAsText))[0] + "_Density")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr = P["in_raster"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage(
                        "The land facet raster must be single band.")
            except Exception:
                pass
        nbr = P["neighborhood"].valueAsText or "Circle"
        rel = self._relevant(nbr)
        for nm in ("radius", "width", "height", "inner_radius", "outer_radius"):
            if nm in rel:
                v = P[nm].value
                if v is not None and v <= 0:
                    P[nm].setErrorMessage("Must be a positive value.")
        if nbr == "Annulus":
            ri, ro = P["inner_radius"].value, P["outer_radius"].value
            if ri is not None and ro is not None and ri >= ro:
                P["outer_radius"].setErrorMessage(
                    "The outer radius must be greater than the inner radius.")
        if nbr in ("Irregular", "Weight"):
            kf = P["kernel_file"].valueAsText
            if not kf:
                P["kernel_file"].setErrorMessage(
                    "The {} neighborhood needs a kernel file.".format(nbr))
            elif not os.path.exists(kf):
                P["kernel_file"].setErrorMessage("Kernel file not found.")
        if P["denominator"].valueAsText == _DENOM_NONNULL:
            P["denominator"].setWarningMessage(
                "This is NOT the original tool's rule, and its values are not "
                "comparable across analyses. Ignoring NoData makes the "
                "proportion depend on how much of the raster is populated, so "
                "the same landscape gives different values on a single-facet "
                "raster than on a combined one. Use it only for a deliberate, "
                "documented reason.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(land_facet_density)
            P = {p.name: p for p in parameters}
            in_path = (_resolve_raster_path(P["in_raster"])
                       or P["in_raster"].valueAsText)
            picked = P["facets"].valueAsText
            facet_values = None
            if picked:
                facet_values = []
                for tok in picked.split(";"):
                    tok = tok.strip().strip("'").strip()
                    if tok:
                        # Entries look like "7 (Valley, Cluster 2)".
                        facet_values.append(tok.split("(", 1)[0].strip())
            denom = (land_facet_linkage.DENOM_NON_NODATA
                     if P["denominator"].valueAsText == _DENOM_NONNULL
                     else land_facet_linkage.DENOM_IN_RASTER)
            land_facet_density.run_density(
                in_raster=in_path,
                out_raster=P["out_raster"].valueAsText,
                facet_values=facet_values,
                neighborhood=P["neighborhood"].valueAsText or "Circle",
                radius=P["radius"].value if P["radius"].value is not None else 3.0,
                width=P["width"].value if P["width"].value is not None else 3.0,
                height=P["height"].value if P["height"].value is not None else 3.0,
                inner_radius=(P["inner_radius"].value
                              if P["inner_radius"].value is not None else 1.0),
                outer_radius=(P["outer_radius"].value
                              if P["outer_radius"].value is not None else 3.0),
                start_angle=(P["start_angle"].value
                             if P["start_angle"].value is not None else 0.0),
                end_angle=(P["end_angle"].value
                           if P["end_angle"].value is not None else 90.0),
                nbr_units=P["nbr_units"].valueAsText or "Cells",
                kernel_file=P["kernel_file"].valueAsText,
                denominator=denom,
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            _save_tool_defaults("LandFacetDensity", {
                "neighborhood": P["neighborhood"].valueAsText,
                "nbr_units": P["nbr_units"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class LandFacetMahalanobis(object):
    """Land Facet Mahalanobis -- one Mahalanobis distance cost surface per land
    facet, sampled inside the Wildland Blocks. See ``land_facet_mahalanobis``."""

    def __init__(self):
        self.label = "Land Facet Mahalanobis"
        self.description = (
            "One Mahalanobis distance cost (resistance) surface per land facet: "
            "how far every cell on the landscape departs, in multivariate "
            "standard deviations, from the conditions typical of that facet. "
            "The mean vector and covariance matrix are built only from the "
            "facet's cells INSIDE the Wildland Blocks -- the training data -- "
            "while the surface itself covers the whole analysis area. Each "
            "facet gets its own single-band raster, ready to use directly as "
            "the resistance surface in least-cost corridor modelling. If a Land "
            "Facet Density raster is supplied, density joins the analysis with a "
            "characteristic value of 100 percent rather than its mean, as the "
            "Land Facet method requires. Needs no Spatial Analyst.")
        self.category = "Corridor Designer Tools\\Land Facet Corridor Designer"
        self.canRunInBackground = False

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input land facet raster (single band)",
            name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        # Single datatype, not a list: a geometry-type filter cannot be set on a
        # parameter whose datatype is a list (the Filter has no 'list' attribute).
        blocks = arcpy.Parameter(
            displayName="Wildland Blocks (training polygons)", name="blocks",
            datatype="GPFeatureLayer",
            parameterType="Required", direction="Input")
        blocks.filter.list = ["Polygon"]
        variables = arcpy.Parameter(
            displayName="Variable rasters (same order used for clustering)",
            name="variables", datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input", multiValue=True)
        density = arcpy.Parameter(
            displayName="Land facet density raster (optional)",
            name="density_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Optional", direction="Input")
        facets = arcpy.Parameter(
            displayName="Land facets to process (default: all)", name="facets",
            datatype="GPString", parameterType="Optional", direction="Input",
            multiValue=True)
        out_ws = arcpy.Parameter(
            displayName="Output workspace (folder or geodatabase)",
            name="out_workspace", datatype="DEWorkspace",
            parameterType="Required", direction="Input")
        out_prefix = arcpy.Parameter(
            displayName="Output name prefix", name="out_prefix",
            datatype="GPString", parameterType="Optional", direction="Input")
        # A trailing underscore reads naturally in the dialog; safe_name() strips
        # it before the facet label is appended, so the result is Mahal_<facet>
        # rather than Mahal__<facet>.
        out_prefix.value = "Mahal_"
        out_type = arcpy.Parameter(
            displayName="Output values", name="out_type",
            datatype="GPString", parameterType="Required", direction="Input")
        out_type.filter.type = "ValueList"
        out_type.filter.list = land_facet_mahalanobis.OUT_TYPES
        out_type.value = mahalanobis.OUT_D2
        inversion = arcpy.Parameter(
            displayName="Covariance inversion", name="inversion",
            datatype="GPString", parameterType="Optional", direction="Input",
            category="Advanced")
        inversion.filter.type = "ValueList"
        inversion.filter.list = land_facet_mahalanobis.INVERSIONS
        inversion.value = mahalanobis.INV_STANDARD
        save_tables = arcpy.Parameter(
            displayName="Also save each facet's mean vector and covariance table",
            name="save_tables", datatype="GPBoolean",
            parameterType="Optional", direction="Input", category="Advanced")
        save_tables.value = False
        params = [in_raster, blocks, variables, density, facets, out_ws,
                  out_prefix, out_type, inversion, save_tables]
        saved = _load_tool_defaults("LandFacetMahalanobis")
        for p in (out_type, inversion):
            if p.name in saved:
                try:
                    p.value = saved[p.name]
                except Exception:
                    pass
        return params

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr = P["in_raster"]
        # When the facet raster changes, pull the clustering's own variable list
        # and facet list out of its linkage record. Matching on the recorded
        # catalog path first means a renamed layer still resolves.
        if inr.valueAsText and not inr.hasBeenValidated:
            try:
                path = _resolve_raster_path(inr) or inr.valueAsText
                rec = land_facet_linkage.read(path, land_facet_linkage.FACETS)
                if rec:
                    srcs = []
                    for v in rec.get("variables") or []:
                        s = (v.get("source") or "").strip()
                        if s and arcpy.Exists(s):
                            srcs.append(s)
                        elif v.get("name"):
                            srcs.append(v["name"])
                    if srcs and not P["variables"].valueAsText:
                        P["variables"].values = srcs
                    choices = ["{} ({})".format(
                        f.get("facet_id"), land_facet_linkage.facet_label(f))
                        for f in (rec.get("facets") or [])]
                    if choices:
                        P["facets"].filter.list = choices
            except Exception:
                pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr = P["in_raster"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage(
                        "The land facet raster must be single band.")
            except Exception:
                pass
        dens = P["density_raster"]
        if dens.valueAsText and inr.valueAsText:
            try:
                dpath = _resolve_raster_path(dens) or dens.valueAsText
                fpath = _resolve_raster_path(inr) or inr.valueAsText
                drec = land_facet_linkage.read(dpath, land_facet_linkage.DENSITY)
                frec = land_facet_linkage.read(fpath, land_facet_linkage.FACETS)
                if drec is None:
                    dens.setErrorMessage(
                        "This raster carries no land facet density record, so "
                        "there is no way to tell which band holds which facet. "
                        "Use a raster made by the Land Facet Density tool, or "
                        "leave this empty to run without density.")
                elif frec and drec.get("source_analysis_id") != frec.get("analysis_id"):
                    have = {b.get("facet_id") for b in (drec.get("bands") or [])}
                    want = {f.get("facet_id") for f in (frec.get("facets") or [])}
                    if want - have:
                        dens.setErrorMessage(
                            "This density raster has no band for land facet(s) "
                            + ", ".join(str(x) for x in sorted(want - have))
                            + ". It was built from a different set of facets, so "
                              "its bands cannot be matched to this land facet "
                              "raster. Rebuild it from this raster.")
                    else:
                        dens.setWarningMessage(
                            "This density raster does not record the same "
                            "analysis as the land facet raster, though it does "
                            "cover every facet. Check it was built from THIS "
                            "raster and not an earlier version.")
            except Exception:
                pass
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(land_facet_mahalanobis)
            P = {p.name: p for p in parameters}
            in_path = (_resolve_raster_path(P["in_raster"])
                       or P["in_raster"].valueAsText)
            var_txt = P["variables"].valueAsText or ""
            variables = [t.strip().strip("'") for t in var_txt.split(";")
                         if t.strip()]
            picked = P["facets"].valueAsText
            facet_values = None
            if picked:
                facet_values = [t.strip().strip("'").split("(", 1)[0].strip()
                                for t in picked.split(";") if t.strip()]
            dens = (_resolve_raster_path(P["density_raster"])
                    or P["density_raster"].valueAsText) or None
            land_facet_mahalanobis.run_facet_mahalanobis(
                in_raster=in_path,
                blocks=P["blocks"].valueAsText,
                variables=variables,
                out_workspace=P["out_workspace"].valueAsText,
                out_prefix=P["out_prefix"].valueAsText,
                density_raster=dens,
                facet_values=facet_values,
                out_type=P["out_type"].valueAsText,
                inversion=P["inversion"].valueAsText,
                save_tables=bool(P["save_tables"].value),
                messages=messages)
            _save_tool_defaults("LandFacetMahalanobis", {
                "out_type": P["out_type"].valueAsText,
                "inversion": P["inversion"].valueAsText,
            })
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class IdentifyTerminiPolygons(object):
    """Identify Termini Polygons -- the concentrations of a land facet (or of
    facet diversity) inside each Wildland Block, as the polygons the
    least-cost corridors will run between. See ``land_facet_termini``."""

    _MODE_FIXED_UI = "Fixed value (density surfaces: 0)"
    _MODE_MEDIAN_UI = ("Median within each Wildland Block (diversity and "
                       "habitat suitability surfaces)")
    _MODE_WHOLE_UI = "Treat entire block polygons as Termini polygons"

    def __init__(self):
        self.label = "Identify Termini Polygons"
        self.description = (
            "Finds the corridor termini polygons within each Wildland Block: "
            "the regions of an analysis surface (typically a Land Facet "
            "Density raster or a Diversity surface) whose cells exceed a "
            "threshold, aggregated so that cells touching along an edge or "
            "at a corner form one region, keeping regions at least a given "
            "percentage (default 50%) of the size of the largest region in "
            "their block. Fed this suite's multi-band Land Facet Density "
            "raster it processes every category in one run and writes ONE "
            "polygon feature class with a Category field; any number of "
            "Wildland Block features is allowed (each feature is one "
            "block). The threshold is a fixed value (use 0 for density: any "
            "density at all) or, for diversity surfaces, the median of each "
            "block's own cells -- the manual's 'top half of H-prime' rule "
            "computed exactly -- or, with no thresholding at all, the "
            "whole Wildland Block polygon used directly as its own "
            "terminus (a baseline corridor endpoint, or a way to use "
            "Least-Cost Corridor with no quality surface at all). Areas "
            "are geodesic. Every output records its "
            "place in the Land Facet analysis chain. A modernized port of "
            "the Land Facet Corridor Designer's Identify Termini Polygons "
            "tool. Needs no Spatial Analyst.")
        self.category = "Corridor Designer Tools\\Land Facet Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Analysis surface (density, diversity or HSI raster)",
            name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset", "DERasterBand"],
            parameterType="Required", direction="Input")
        blocks = arcpy.Parameter(
            displayName="Wildland Block polygons (each feature = one block)",
            name="blocks",
            datatype="GPFeatureLayer",
            parameterType="Required", direction="Input")
        # NOTE: filter.list with a LIST of datatypes silently breaks
        # getParameterInfo (the zero-params trap) -- single datatype only.
        blocks.filter.list = ["Polygon"]
        out_fc = arcpy.Parameter(
            displayName="Output termini polygon feature class", name="out_fc",
            datatype="DEFeatureClass", parameterType="Required",
            direction="Output")
        thr_mode = arcpy.Parameter(
            displayName="Threshold rule", name="threshold_mode",
            datatype="GPString", parameterType="Required", direction="Input")
        thr_mode.filter.type = "ValueList"
        thr_mode.filter.list = [self._MODE_FIXED_UI, self._MODE_MEDIAN_UI,
                                self._MODE_WHOLE_UI]
        thr_mode.value = self._MODE_FIXED_UI
        thr_value = arcpy.Parameter(
            displayName="Threshold value (keep cells above this)",
            name="threshold_value",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        thr_value.value = 0.0
        pct = arcpy.Parameter(
            displayName="Relative size threshold (% of the largest polygon "
                        "in each block)",
            name="relative_size_pct",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        pct.value = land_facet_termini.DEFAULT_PCT
        facets = arcpy.Parameter(
            displayName="Categories / bands to analyze (default: all)",
            name="facets", datatype="GPString", parameterType="Optional",
            direction="Input", multiValue=True)
        return [in_raster, blocks, out_fc, thr_mode, thr_value, pct, facets]

    def isLicensed(self):
        return True

    def _band_count(self, inr):
        try:
            return getattr(arcpy.Describe(inr.valueAsText), "bandCount", 1)
        except Exception:
            return 1

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr = P["in_raster"], P["out_fc"]
        multi = bool(inr.valueAsText) and self._band_count(inr) > 1
        P["facets"].enabled = multi

        if inr.valueAsText and not inr.hasBeenValidated:
            try:
                path = _resolve_raster_path(inr) or inr.valueAsText
                rec, _bn = invert_raster.source_density_record(path)
                choices = []
                if rec:
                    for b in rec.get("bands") or []:
                        choices.append("{} ({})".format(
                            b.get("facet_id"),
                            land_facet_linkage.facet_label(b)))
                if choices:
                    P["facets"].filter.list = choices
                # The manual's rules, applied automatically when the input's
                # own records identify it: a Land Facet Density raster ->
                # fixed threshold 0 (any density at all); a Diversity
                # Indices surface -> each block's own median. Suggested only
                # on the cycle where the INPUT changed, so a later manual
                # choice of rule is left alone.
                if rec:
                    P["threshold_mode"].value = self._MODE_FIXED_UI
                    P["threshold_value"].value = 0.0
                elif land_facet_linkage.read(
                        path, land_facet_linkage.DIVERSITY) is not None:
                    P["threshold_mode"].value = self._MODE_MEDIAN_UI
            except Exception:
                pass

        # After the auto-select, so the value field greys out on the same
        # validation cycle that switches the rule. Neither the threshold
        # value nor the relative-size filter mean anything once whole
        # blocks are used directly; only the threshold value is irrelevant
        # in median mode (the block computes its own).
        mode_now = P["threshold_mode"].valueAsText
        P["threshold_value"].enabled = mode_now not in (
            self._MODE_MEDIAN_UI, self._MODE_WHOLE_UI)
        P["relative_size_pct"].enabled = mode_now != self._MODE_WHOLE_UI

        if inr.valueAsText:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(os.path.basename(
                        inr.valueAsText))[0] + "_Termini")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        pct = P["relative_size_pct"].value
        if pct is not None and not (0 <= pct <= 100):
            P["relative_size_pct"].setErrorMessage(
                "The relative size threshold must be between 0 and 100 "
                "percent.")
        blocks = P["blocks"]
        if blocks.valueAsText:
            try:
                if arcpy.Describe(blocks.value or
                                  blocks.valueAsText).shapeType != "Polygon":
                    blocks.setErrorMessage(
                        "The Wildland Blocks must be a polygon layer.")
            except Exception:
                pass
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(land_facet_mahalanobis)
            importlib.reload(invert_raster)
            importlib.reload(land_facet_termini)
            P = {p.name: p for p in parameters}
            in_path = (_resolve_raster_path(P["in_raster"])
                       or P["in_raster"].valueAsText)
            picked = P["facets"].valueAsText
            facet_values = None
            if picked:
                facet_values = [t.strip().strip("'").split("(", 1)[0].strip()
                                for t in picked.split(";") if t.strip()]
            mode_ui = P["threshold_mode"].valueAsText
            if mode_ui == self._MODE_MEDIAN_UI:
                mode = land_facet_termini.MODE_MEDIAN
            elif mode_ui == self._MODE_WHOLE_UI:
                mode = land_facet_termini.MODE_WHOLE_BLOCK
            else:
                mode = land_facet_termini.MODE_FIXED
            out_lyrx = land_facet_termini.run_termini(
                in_raster=in_path,
                blocks=(P["blocks"].value or P["blocks"].valueAsText),
                out_fc=P["out_fc"].valueAsText,
                threshold_mode=mode,
                threshold_value=P["threshold_value"].value or 0.0,
                pct=(P["relative_size_pct"].value
                     if P["relative_size_pct"].value is not None
                     else land_facet_termini.DEFAULT_PCT),
                facet_values=facet_values,
                messages=messages)
            if out_lyrx:
                try:
                    P["out_fc"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage(
                        "Could not set output symbology: {}".format(e))
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class LeastCostCorridor(object):
    """Least-Cost Corridor -- combine cost-distance surfaces (from Invert
    Raster) and start/end points (from Identify Termini Polygons) into
    corridor polygons between every pair of Wildland Blocks. A modernized,
    generalized port of the CorridorDesigner CorridorModel tool -- its
    traditional and still most common use is a species habitat-suitability
    surface; a Land Facet surface is one specific kind of input. See
    ``least_cost_corridor``."""

    def __init__(self):
        self.label = "Least-Cost Corridor"
        self.description = (
            "Builds least-cost corridor polygons between every pair of "
            "Wildland Blocks represented in an Identify Termini Polygons "
            "output, for every category (facet) present. Combines the "
            "accumulated cost distance from each block -- computed "
            "internally with no Spatial Analyst -- into a corridor surface "
            "that is zero along the literal least-cost path and rises away "
            "from it, then extracts that surface at a series of "
            "percentile-width thresholds (default 0.1%, 1%, 2%, ..., 10%, "
            "matching the original CorridorDesigner CorridorModel tool) as "
            "polygons. Its traditional and still most common use is a "
            "species habitat-suitability surface inverted to a cost "
            "surface (Invert Raster); a Land Facet Mahalanobis distance "
            "raster is used directly with no inversion (low distance is "
            "already cheap), and a Land Facet Density or Diversity "
            "surface is one specific kind of input needing inversion "
            "first, used for corridors that do not depend on any "
            "particular future vegetation type. "
            "Geodesic distances are used automatically on a geographic "
            "coordinate system.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_termini = arcpy.Parameter(
            displayName="Termini feature class (Identify Termini Polygons "
                        "output)",
            name="in_termini", datatype=["GPFeatureLayer", "DEFeatureClass"],
            parameterType="Required", direction="Input")
        cost_raster = arcpy.Parameter(
            displayName="Cost raster (single category)", name="cost_raster",
            datatype=["GPRasterLayer", "DERasterDataset", "DERasterBand"],
            parameterType="Optional", direction="Input")
        cost_ws = arcpy.Parameter(
            displayName="Cost raster workspace (multiple categories, e.g. "
                        "Land Facet Mahalanobis's or Invert Raster's "
                        "output workspace)",
            name="cost_workspace", datatype="DEWorkspace",
            parameterType="Optional", direction="Input")
        out_ws = arcpy.Parameter(
            displayName="Output workspace (corridor cost rasters)",
            name="out_workspace", datatype="DEWorkspace",
            parameterType="Required", direction="Input")
        out_prefix = arcpy.Parameter(
            displayName="Output raster name prefix", name="out_prefix",
            datatype="GPString", parameterType="Optional", direction="Input")
        out_prefix.value = "Cor"
        out_fc = arcpy.Parameter(
            displayName="Output corridor polygon feature class",
            name="out_fc", datatype="DEFeatureClass",
            parameterType="Required", direction="Output")
        percentiles = arcpy.Parameter(
            displayName="Percentile-width thresholds (% of the analysis "
                        "area)",
            name="percentiles", datatype="GPDouble", parameterType="Optional",
            direction="Input", multiValue=True)
        percentiles.value = ";".join(
            "{:g}".format(p) for p in least_cost_corridor.DEFAULT_PERCENTILES)
        block_pairs = arcpy.Parameter(
            displayName="Wildland Block pairs to connect (default: all "
                        "pairs)",
            name="block_pairs", datatype="GPString", parameterType="Optional",
            direction="Input", multiValue=True)
        facets = arcpy.Parameter(
            displayName="Categories to process (default: all)",
            name="facets", datatype="GPString", parameterType="Optional",
            direction="Input", multiValue=True)
        return [in_termini, cost_raster, cost_ws, out_ws, out_prefix, out_fc,
                percentiles, block_pairs, facets]

    def isLicensed(self):
        return True

    def _termini_field_names(self, in_termini):
        try:
            path = in_termini.value or in_termini.valueAsText
            return {f.name for f in arcpy.ListFields(path)}
        except Exception:
            return set()

    def _distinct_facets_and_blocks(self, in_termini):
        facets, blocks = {}, set()
        try:
            with arcpy.da.SearchCursor(
                    in_termini.value or in_termini.valueAsText,
                    ["Category", "CategoryID", "Block"]) as cur:
                for category, cid, block in cur:
                    facets.setdefault(cid, category)
                    if block is not None:
                        blocks.add(block)
        except Exception:
            pass
        return facets, blocks

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        term, outr = P["in_termini"], P["out_fc"]

        # One cost source or the other, never both: picking a single cost
        # raster grays out the workspace box (and empties it, so a stale
        # value can't linger in a control the user can no longer reach).
        cw = P["cost_workspace"]
        cw.enabled = not P["cost_raster"].valueAsText
        if not cw.enabled and cw.valueAsText:
            cw.value = None

        if term.valueAsText and not term.hasBeenValidated:
            have = self._termini_field_names(term)
            if {"Category", "CategoryID", "Block"} <= have:
                facet_map, blocks = self._distinct_facets_and_blocks(term)
                multi = len(facet_map) > 1
                P["cost_raster"].enabled = not multi
                if multi and P["cost_raster"].valueAsText:
                    P["cost_raster"].value = None
                if facet_map:
                    P["facets"].filter.list = [
                        "{} ({})".format(fid, lab) for fid, lab in
                        sorted(facet_map.items(),
                              key=lambda kv: (kv[0] is None, kv[0]))]
                # The dropdown is built from the pairs that actually occur in
                # THIS termini feature class, so a user never has to know
                # what the Block field's numbers mean -- just pick a pair.
                pairs = list(itertools.combinations(sorted(blocks), 2))
                P["block_pairs"].filter.list = [
                    "{}-{}".format(a, b) for a, b in pairs]

        if term.valueAsText:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(os.path.basename(
                        term.valueAsText))[0] + "_Corridors")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(term.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        term = P["in_termini"]
        if term.valueAsText:
            have = self._termini_field_names(term)
            missing = [f for f in ("Category", "CategoryID", "Block")
                      if f not in have]
            if missing:
                term.setErrorMessage(
                    "This does not look like an Identify Termini Polygons "
                    "output -- missing field{} {}. Run Identify Termini "
                    "Polygons first (on your cost surface and Wildland "
                    "Blocks) and use ITS output here, not the Wildland "
                    "Blocks layer itself.".format(
                        "" if len(missing) == 1 else "s",
                        ", ".join(missing)))
        cr, cw = P["cost_raster"], P["cost_workspace"]
        if cr.valueAsText and cw.valueAsText:
            cw.setErrorMessage(
                "Supply either a single cost raster or a cost-raster "
                "workspace, not both.")
        elif not cr.valueAsText and not cw.valueAsText:
            cw.setErrorMessage(
                "Supply either a single cost raster or a cost-raster "
                "workspace.")
        pct = P["percentiles"]
        if pct.valueAsText:
            for tok in pct.valueAsText.split(";"):
                try:
                    v = float(tok.strip().strip("'"))
                except ValueError:
                    pct.setErrorMessage(
                        "Percentile thresholds must be numbers.")
                    break
                if not (0 < v <= 100):
                    pct.setErrorMessage(
                        "Percentile thresholds must be greater than 0 and "
                        "at most 100.")
                    break
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(land_facet_mahalanobis)
            importlib.reload(land_facet_termini)
            importlib.reload(least_cost_corridor)
            P = {p.name: p for p in parameters}

            def _split(p):
                text = p.valueAsText
                if not text:
                    return None
                return [t.strip().strip("'") for t in text.split(";")
                        if t.strip()]

            picked = _split(P["facets"])
            facet_values = ([t.split("(", 1)[0].strip() for t in picked]
                            if picked else None)

            least_cost_corridor.run_least_cost_corridor(
                in_termini=(P["in_termini"].value
                           or P["in_termini"].valueAsText),
                cost_raster=(_resolve_raster_path(P["cost_raster"])
                            if P["cost_raster"].valueAsText else None),
                cost_workspace=P["cost_workspace"].valueAsText or None,
                out_workspace=P["out_workspace"].valueAsText,
                out_prefix=P["out_prefix"].valueAsText or "Cor",
                out_fc=P["out_fc"].valueAsText,
                percentiles=[float(t) for t in _split(P["percentiles"]) or []]
                           or None,
                block_pairs=_split(P["block_pairs"]),
                facet_values=facet_values,
                messages=messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class FillCorridorHoles(object):
    """Fill Corridor Holes -- close small interior holes in polygon outputs
    (a corridor, a dissolved species HSM polygon, or any other polygon
    feature class), leaving genuinely large holes alone. Accepts multiple
    input feature classes in one run. See ``fill_corridor_holes``."""

    def __init__(self):
        self.label = "Fill Corridor Holes"
        self.description = (
            "Closes small interior holes in one or more polygon feature "
            "classes -- typically a Least-Cost Corridor output, but works "
            "on any polygon dataset. Every hole (interior ring) smaller "
            "than the given size threshold is filled; holes at or above "
            "the threshold are left as real, meaningful gaps (a lake, a "
            "genuinely excluded area). Hole size is measured as TRUE "
            "GEODESIC area, correct on any coordinate system including a "
            "geographic one -- Esri's own Eliminate Polygon Part tool was "
            "tested and rejected for this because its area threshold is "
            "planar only and silently misbehaves on unprojected data. "
            "Every attribute field is preserved unchanged; only the "
            "geometry is edited. Each output is named "
            "<input><name suffix> in the output workspace. When an input "
            "already carries a Land Facet linkage record (most commonly a "
            "Least-Cost Corridor output), the SAME record is kept on the "
            "output with this step noted, so the Corridor Analysis Data Report "
            "keeps describing it as the same analysis product.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False

    def getParameterInfo(self):
        in_features = arcpy.Parameter(
            displayName="Input polygon feature classes",
            name="in_features", datatype=["GPFeatureLayer", "DEFeatureClass"],
            parameterType="Required", direction="Input", multiValue=True)
        threshold_value = arcpy.Parameter(
            displayName="Hole size threshold (holes smaller than this are "
                        "filled)",
            name="threshold_value", datatype="GPDouble",
            parameterType="Required", direction="Input")
        threshold_units = arcpy.Parameter(
            displayName="Threshold units", name="threshold_units",
            datatype="GPString", parameterType="Required", direction="Input")
        threshold_units.filter.type = "ValueList"
        threshold_units.filter.list = fill_corridor_holes.UNIT_CHOICES
        threshold_units.value = fill_corridor_holes.DEFAULT_UNITS
        out_workspace = arcpy.Parameter(
            displayName="Output workspace", name="out_workspace",
            datatype="DEWorkspace", parameterType="Required", direction="Input")
        name_suffix = arcpy.Parameter(
            displayName="Output name suffix", name="name_suffix",
            datatype="GPString", parameterType="Optional", direction="Input")
        name_suffix.value = fill_corridor_holes.DEFAULT_SUFFIX
        out_features = arcpy.Parameter(
            displayName="Output feature classes", name="out_features",
            datatype="DEFeatureClass", parameterType="Derived",
            direction="Output", multiValue=True)
        return [in_features, threshold_value, threshold_units, out_workspace,
                name_suffix, out_features]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inf, outws = P["in_features"], P["out_workspace"]
        if inf.values and not outws.altered:
            try:
                first = str(inf.values[0])
                ws = os.path.dirname(arcpy.Describe(first).catalogPath)
                outws.value = ws
            except Exception:
                pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        tv = P["threshold_value"]
        if tv.value is not None and tv.value <= 0:
            tv.setErrorMessage("The hole-size threshold must be greater "
                               "than zero.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(land_facet_termini)
            importlib.reload(fill_corridor_holes)
            P = {p.name: p for p in parameters}
            outputs = fill_corridor_holes.run_fill_corridor_holes(
                in_features_list=list(P["in_features"].values or []),
                threshold_value=P["threshold_value"].value,
                threshold_units=(P["threshold_units"].valueAsText
                                or fill_corridor_holes.DEFAULT_UNITS),
                out_workspace=P["out_workspace"].valueAsText,
                name_suffix=(P["name_suffix"].valueAsText
                            or fill_corridor_holes.DEFAULT_SUFFIX),
                messages=messages)
            if outputs:
                try:
                    P["out_features"].value = ";".join(outputs)
                except Exception:
                    pass
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class UnionCorridors(object):
    """Union Corridors -- combine every input polygon feature class into
    ONE seamless dissolved shape, matching the original CorridorDesigner
    "Create union of all corridors" tool. See ``union_corridors``."""

    def __init__(self):
        self.label = "Union Corridors"
        self.description = (
            "Combines every input polygon feature class into ONE seamless "
            "combined shape: unions them together (arcpy Union, keeping "
            "every overlap distinct) then dissolves the result into a "
            "single multipart polygon with no dissolve field -- per-input "
            "attributes are deliberately NOT preserved, matching the "
            "original tool and its purpose: a single combined shape "
            "answering \"is this ground part of ANY corridor\", not which "
            "one. A modernized port of the CorridorDesigner \"Create union "
            "of all corridors\" tool. "
            "SELECT BEFORE YOU RUN. A corridor feature class typically "
            "holds many percentile-width bands as separate features (the "
            "Percentile field) -- to union just the 5% bands across "
            "several species, SELECT Percentile = 5 on each input's layer "
            "in the map before running this tool; an input with no active "
            "selection contributes all of its features, same as always. "
            "HONORING A SELECTION ONLY WORKS FOR INPUTS ALREADY IN THE "
            "MAP -- the tool itself happily accepts datasets added via "
            "the Browse button (all of their features are used), but a "
            "browsed dataset is a bare path with no concept of a "
            "selection at all; to use a subset of a feature class, load "
            "it into the map and select there first, then pick that "
            "layer here instead of browsing to the dataset. "
            "Warns when the inputs carry DIFFERENT Land Facet analysis "
            "ids -- combining genuinely unrelated analyses, not just "
            "different categories within one. Needs no Spatial Analyst.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_features = arcpy.Parameter(
            displayName="Input polygon feature classes",
            name="in_features", datatype=["GPFeatureLayer", "DEFeatureClass"],
            parameterType="Required", direction="Input", multiValue=True)
        out_features = arcpy.Parameter(
            displayName="Output feature class", name="out_features",
            datatype="DEFeatureClass", parameterType="Required",
            direction="Output")
        return [in_features, out_features]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inf, outr = P["in_features"], P["out_features"]
        if inf.valueAsText and not inf.hasBeenValidated:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    first = str(inf.values[0])
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(first).catalogPath)
                    sug = _unique_name("Corridor_Union", ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(union_corridors)
            P = {p.name: p for p in parameters}
            union_corridors.run_union_corridors(
                in_features_list=list(P["in_features"].values or []),
                out_features=P["out_features"].valueAsText,
                messages=messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class PatchAnalysis(object):
    """Patch Analysis -- the minimax stepping-stone route through a
    corridor: the path between every pair of termini that minimizes the
    LONGEST single gap of unsuitable habitat the species must cross,
    using patches of good habitat as stepping stones. A modernized port of
    the CorridorDesigner Evaluation Tools' Patch Analysis. See
    ``patch_analysis``."""

    def __init__(self):
        self.label = "Patch Analysis"
        self.description = (
            "Finds the route through a corridor polygon that minimizes "
            "the patch-to-patch gaps an animal must cross moving between "
            "termini, and reports the critical (longest) gap for every "
            "pair of termini -- the largest expanse of unsuitable habitat "
            "the species must cross in one push. Patches of good habitat "
            "(optional) serve as stepping stones. Distances are EXACT "
            "shortest paths inside the corridor polygon, computed with a "
            "visibility graph -- no Spatial Analyst, no raster "
            "approximation, no 45-degree bias -- and reported as geodesic "
            "meters. The route is the minimax path through a minimum "
            "spanning tree (a variation on Kruskal's algorithm, as in the "
            "original tool), which holds for every termini pair "
            "simultaneously: any number of termini are analyzed in one "
            "run. Outputs a polyline feature class of the gap crossings "
            "(named endpoints, lengths, ranks) stamped with the full "
            "analysis record for the Corridor Analysis Data Report.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        corridor = arcpy.Parameter(
            displayName="Corridor polygon",
            name="corridor", datatype=["GPFeatureLayer", "DEFeatureClass"],
            parameterType="Required", direction="Input")
        termini = arcpy.Parameter(
            displayName="Termini polygons (wildland blocks / corridor "
                        "endpoints)",
            name="termini", datatype=["GPFeatureLayer", "DEFeatureClass"],
            parameterType="Required", direction="Input")
        name_field = arcpy.Parameter(
            displayName="Terminus name field (features sharing a value "
                        "are ONE terminus)",
            name="name_field", datatype="Field",
            parameterType="Optional", direction="Input")
        name_field.parameterDependencies = ["termini"]
        name_field.filter.list = ["Text", "Short", "Long"]
        patches = arcpy.Parameter(
            displayName="Habitat patch polygons (stepping stones)",
            name="patches", datatype=["GPFeatureLayer", "DEFeatureClass"],
            parameterType="Optional", direction="Input")
        patch_where = arcpy.Parameter(
            displayName="Patch selection expression (e.g. only "
                        "population-sized patches)",
            name="patch_where", datatype="GPSQLExpression",
            parameterType="Optional", direction="Input")
        patch_where.parameterDependencies = ["patches"]
        out_fc = arcpy.Parameter(
            displayName="Output connector feature class",
            name="out_fc", datatype="DEFeatureClass",
            parameterType="Required", direction="Output")
        simplify = arcpy.Parameter(
            displayName="Corridor boundary simplification tolerance, "
                        "meters (blank = automatic; 0 = none)",
            name="simplify_tolerance", datatype="GPDouble",
            parameterType="Optional", direction="Input",
            category="Advanced")
        return [corridor, termini, name_field, patches, patch_where,
                out_fc, simplify]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        term, cor, outr = P["termini"], P["corridor"], P["out_fc"]
        # Suggest the grouping field once per termini pick (hasBeenValidated
        # gating -- a cleared field stays cleared). For an Identify Termini
        # output the right field is Block (each Wildland Block is one
        # terminus); Category there names the ANALYSIS SURFACE and is the
        # same value on every row (found from Jeff's real run, 2026-08-15).
        if term.valueAsText and not term.hasBeenValidated:
            if not P["name_field"].altered:
                try:
                    have = {f.name for f in arcpy.ListFields(
                        term.value or term.valueAsText)}
                    if {"Category", "CategoryID", "Block"} <= have:
                        P["name_field"].value = "Block"
                except Exception:
                    pass
        if cor.valueAsText and not cor.hasBeenValidated:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = os.path.splitext(os.path.basename(
                        str(cor.valueAsText)))[0]
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(cor.value or cor.valueAsText)
                        .catalogPath)
                    sug = _unique_name(
                        _legalize("PatchRoutes_" + base), ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}

        for name in ("corridor", "termini", "patches"):
            p = P[name]
            if not p.valueAsText:
                continue
            try:
                shp = arcpy.Describe(p.value or p.valueAsText).shapeType
            except Exception:
                continue
            if shp and shp.lower() != "polygon":
                p.setErrorMessage(
                    "A polygon feature class or layer is required "
                    "(this is a {} dataset).".format(shp))

        def _gcs_name(param):
            try:
                sr = arcpy.Describe(param.value or param.valueAsText) \
                    .spatialReference
                return sr.GCS.name if sr and sr.name else None
            except Exception:
                return None

        cor_gcs = _gcs_name(P["corridor"]) if P["corridor"].valueAsText \
            else None
        if cor_gcs:
            for name in ("termini", "patches"):
                if not P[name].valueAsText:
                    continue
                other = _gcs_name(P[name])
                if other and other != cor_gcs:
                    P[name].setWarningMessage(
                        "This layer's geographic coordinate system ({}) "
                        "differs from the corridor's ({}). It will be "
                        "projected WITHOUT a datum transformation, which "
                        "can shift features by tens of meters. For full "
                        "control, project it to the corridor's coordinate "
                        "system first (e.g. with the Project tool or Clip "
                        "Data to Analysis Area).".format(other, cor_gcs))
        tol = P["simplify_tolerance"]
        if tol.value is not None and tol.value < 0:
            tol.setErrorMessage("The simplification tolerance cannot be "
                                "negative.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(patch_analysis)
            P = {p.name: p for p in parameters}
            tol = P["simplify_tolerance"].value
            patch_analysis.run_patch_analysis(
                corridor=P["corridor"].value or P["corridor"].valueAsText,
                termini=P["termini"].value or P["termini"].valueAsText,
                out_fc=P["out_fc"].valueAsText,
                name_field=P["name_field"].valueAsText or None,
                patches=(P["patches"].value or P["patches"].valueAsText
                         or None),
                patch_where=P["patch_where"].valueAsText or None,
                simplify_tolerance=(None if tol is None else float(tol)),
                messages=messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class BottleneckAnalysis(object):
    """Bottleneck Analysis -- the widest route through a corridor between
    two wildland blocks (maximin on the corridor's medial axis) and the
    width profile along it. The scripting/batch surface of the
    interactive Bottleneck Analysis window. See ``bottleneck_analysis``."""

    def __init__(self):
        self.label = "Bottleneck Analysis"
        self.description = (
            "Finds the route through a corridor polygon between two "
            "wildland blocks that MAXIMIZES THE MINIMUM corridor width "
            "encountered -- the route with the widest bottleneck, chosen "
            "over every strand and branch of the corridor at once -- and "
            "measures the corridor width at ~1000 stations along it. "
            "Computed exactly in vector on the corridor's medial axis "
            "(a Voronoi construction), with no Spatial Analyst and no "
            "raster approximation; widths and lengths are meters. "
            "An optional width threshold classifies the route into "
            "above/below stretches, reported with lengths sorted "
            "decreasing. Outputs: the route polyline, optional station "
            "points (with widths), and optional above/below-threshold "
            "segments, each stamped with the analysis record. The "
            "interactive Bottleneck Analysis window (Evaluate Corridors "
            "menu) offers the same analysis with a live width graph and "
            "adjustable threshold; THIS tool is the scripting and batch "
            "surface -- iterate it in ModelBuilder to reproduce the "
            "original's Batch Bottleneck runs. A modernized port of the "
            "CorridorDesigner Evaluation Tools' Bottleneck Analysis.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        corridor = arcpy.Parameter(
            displayName="Corridor polygon",
            name="corridor", datatype=["GPFeatureLayer", "DEFeatureClass"],
            parameterType="Required", direction="Input")
        corridor_exp = arcpy.Parameter(
            displayName="Corridor selection expression (when the layer "
                        "holds several corridors)",
            name="corridor_expression", datatype="GPSQLExpression",
            parameterType="Optional", direction="Input")
        corridor_exp.parameterDependencies = ["corridor"]
        block_a = arcpy.Parameter(
            displayName="Wildland Block A",
            name="block_a", datatype=["GPFeatureLayer", "DEFeatureClass"],
            parameterType="Required", direction="Input")
        block_a_exp = arcpy.Parameter(
            displayName="Block A selection expression (when the layer "
                        "holds several blocks)",
            name="block_a_expression", datatype="GPSQLExpression",
            parameterType="Optional", direction="Input")
        block_a_exp.parameterDependencies = ["block_a"]
        block_b = arcpy.Parameter(
            displayName="Wildland Block B",
            name="block_b", datatype=["GPFeatureLayer", "DEFeatureClass"],
            parameterType="Required", direction="Input")
        block_b_exp = arcpy.Parameter(
            displayName="Block B selection expression (when the layer "
                        "holds several blocks)",
            name="block_b_expression", datatype="GPSQLExpression",
            parameterType="Optional", direction="Input")
        block_b_exp.parameterDependencies = ["block_b"]
        threshold = arcpy.Parameter(
            displayName="Width threshold, meters (optional; classifies "
                        "the route into above/below stretches)",
            name="threshold", datatype="GPDouble",
            parameterType="Optional", direction="Input")
        out_route = arcpy.Parameter(
            displayName="Output route feature class",
            name="out_route", datatype="DEFeatureClass",
            parameterType="Required", direction="Output")
        out_points = arcpy.Parameter(
            displayName="Output station points (with widths)",
            name="out_points", datatype="DEFeatureClass",
            parameterType="Optional", direction="Output")
        out_segments = arcpy.Parameter(
            displayName="Output above/below-threshold segments",
            name="out_segments", datatype="DEFeatureClass",
            parameterType="Optional", direction="Output")
        n_stations = arcpy.Parameter(
            displayName="Number of width stations along the route",
            name="n_stations", datatype="GPLong",
            parameterType="Optional", direction="Input",
            category="Advanced")
        n_stations.value = bottleneck_analysis.DEFAULT_STATIONS
        boundary_step = arcpy.Parameter(
            displayName="Boundary sampling spacing, meters (blank = "
                        "automatic)",
            name="boundary_step", datatype="GPDouble",
            parameterType="Optional", direction="Input",
            category="Advanced")
        simplify = arcpy.Parameter(
            displayName="Boundary simplification tolerance, meters "
                        "(blank = automatic; 0 = none)",
            name="simplify_tolerance", datatype="GPDouble",
            parameterType="Optional", direction="Input",
            category="Advanced")
        profile_json = arcpy.Parameter(
            displayName="Profile JSON path (used by the Bottleneck "
                        "Analysis window; leave blank)",
            name="profile_json", datatype="GPString",
            parameterType="Optional", direction="Input",
            category="Advanced")
        return [corridor, corridor_exp, block_a, block_a_exp, block_b,
                block_b_exp, threshold, out_route, out_points, out_segments,
                n_stations, boundary_step, simplify, profile_json]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        cor, outr = P["corridor"], P["out_route"]
        # Same-layer convenience: picking one blocks layer for BOTH slots
        # with a recognizable 2-value Block field fills the expressions.
        ba, bb = P["block_a"], P["block_b"]
        if (ba.valueAsText and bb.valueAsText
                and ba.valueAsText == bb.valueAsText
                and not ba.hasBeenValidated
                and not P["block_a_expression"].altered
                and not P["block_b_expression"].altered):
            try:
                src = ba.value or ba.valueAsText
                have = {f.name for f in arcpy.ListFields(src)}
                field = next((f for f in ("Block", "Category")
                              if f in have), None)
                if field:
                    vals = sorted({r[0] for r in
                                   arcpy.da.SearchCursor(src, [field])
                                   if r[0] is not None})
                    if len(vals) == 2:
                        def _lit(v):
                            return ("'{}'".format(v)
                                    if isinstance(v, str) else str(v))
                        P["block_a_expression"].value = "{} = {}".format(
                            field, _lit(vals[0]))
                        P["block_b_expression"].value = "{} = {}".format(
                            field, _lit(vals[1]))
            except Exception:
                pass
        if cor.valueAsText and not cor.hasBeenValidated:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = os.path.splitext(os.path.basename(
                        str(cor.valueAsText)))[0]
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(cor.value or cor.valueAsText)
                        .catalogPath)
                    sug = _unique_name(
                        _legalize("Bottleneck_" + base), ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        for name in ("corridor", "block_a", "block_b"):
            p = P[name]
            if not p.valueAsText:
                continue
            try:
                shp = arcpy.Describe(p.value or p.valueAsText).shapeType
            except Exception:
                continue
            if shp and shp.lower() != "polygon":
                p.setErrorMessage(
                    "A polygon feature class or layer is required "
                    "(this is a {} dataset).".format(shp))
        t = P["threshold"]
        if t.value is not None and t.value <= 0:
            t.setErrorMessage("The width threshold must be greater than "
                              "zero (meters).")
        if (P["out_segments"].valueAsText and t.value is None):
            P["out_segments"].setWarningMessage(
                "Segments are the above/below-threshold stretches -- "
                "without a threshold this output is skipped.")
        return

    @staticmethod
    def _label_for(layer_text, expression):
        base = os.path.splitext(os.path.basename(str(layer_text)))[0]
        if expression:
            text = str(expression).replace("=", " ").replace("'", "")
            text = " ".join(text.split())
            return text if len(text) <= 40 else base
        return base

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(patch_analysis)
            importlib.reload(bottleneck_analysis)
            P = {p.name: p for p in parameters}
            thr = P["threshold"].value
            bottleneck_analysis.run_bottleneck(
                corridor=P["corridor"].value or P["corridor"].valueAsText,
                block_a=P["block_a"].value or P["block_a"].valueAsText,
                block_b=P["block_b"].value or P["block_b"].valueAsText,
                corridor_where=P["corridor_expression"].valueAsText or None,
                block_a_where=P["block_a_expression"].valueAsText or None,
                block_b_where=P["block_b_expression"].valueAsText or None,
                block_a_label=self._label_for(
                    P["block_a"].valueAsText,
                    P["block_a_expression"].valueAsText),
                block_b_label=self._label_for(
                    P["block_b"].valueAsText,
                    P["block_b_expression"].valueAsText),
                out_route=P["out_route"].valueAsText,
                out_points=P["out_points"].valueAsText or None,
                out_segments=P["out_segments"].valueAsText or None,
                threshold=(float(thr) if thr is not None else None),
                n_stations=int(P["n_stations"].value or
                               bottleneck_analysis.DEFAULT_STATIONS),
                boundary_step=(float(P["boundary_step"].value)
                               if P["boundary_step"].value is not None
                               else None),
                simplify_tolerance=(float(P["simplify_tolerance"].value)
                                    if P["simplify_tolerance"].value
                                    is not None else None),
                profile_json=P["profile_json"].valueAsText or None,
                messages=messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class FindRasterExtremeValues(object):
    """Find Raster Extreme Values -- peaks and low points of a DEM as a point
    feature class, with topographic prominence / depression depth and an
    edge-of-data flag. See ``raster_extremes``."""

    def __init__(self):
        self.label = "Find Raster Extreme Values"
        self.description = (
            "Finds the regional extremes of ANY raster surface and writes one "
            "point per extreme. On a DEM those are summits and sinks -- a low "
            "point is a cell (or connected flat) with no lower neighbor, a "
            "peak the same on the inverted surface -- but the tool works "
            "identically on any continuous raster: a slope raster gives the "
            "steepest and flattest points on the landscape, a habitat-"
            "suitability surface gives the locations of best and worst "
            "habitat, and the same goes for temperature, wetness, or any "
            "other modeled value. Flat tops and flat-bottomed basins yield "
            "ONE point each; flats that drain (benches) are excluded. Each "
            "point carries the source raster name and workspace, its value, "
            "X and Y (in the output's coordinate system -- the raster's, "
            "unless the Output Coordinates environment overrides it, which "
            "this tool honors), its prominence (for a DEM: topographic "
            "prominence or depression depth; for other surfaces: how far the "
            "value stands above or below its surroundings), and a flag "
            "marking extremes on the edge of the data whose neighbors are "
            "unknown. Optional filters: minimum prominence/depth, and a "
            "dominance window keeping only the most extreme point within a "
            "radius. Needs no Spatial Analyst.")
        self.category = "Topographic Analysis Tools"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input raster surface (DEM)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset"],
            parameterType="Required", direction="Input")
        extreme_type = arcpy.Parameter(
            displayName="Extremes to find", name="extreme_type",
            datatype="GPString", parameterType="Required", direction="Input")
        extreme_type.filter.type = "ValueList"
        extreme_type.filter.list = raster_extremes.EXTREME_TYPES
        extreme_type.value = raster_extremes.EXTREME_TYPES[0]
        out_fc = arcpy.Parameter(
            displayName="Output point feature class", name="out_fc",
            datatype="DEFeatureClass", parameterType="Required",
            direction="Output")
        min_prom = arcpy.Parameter(
            displayName="Minimum prominence / depth (raster units)",
            name="min_prominence",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        min_prom.value = 0.0
        window = arcpy.Parameter(
            displayName="Dominance window radius (cells; 1 = every extreme)",
            name="window",
            datatype="GPLong", parameterType="Optional", direction="Input")
        window.value = 1
        return [in_raster, extreme_type, out_fc, min_prom, window]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr = P["in_raster"], P["out_fc"]
        if inr.valueAsText:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize(os.path.splitext(os.path.basename(
                        inr.valueAsText))[0] + "_Extremes")
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr = P["in_raster"]
        if inr.valueAsText:
            try:
                if arcpy.Describe(inr.valueAsText).bandCount != 1:
                    inr.setErrorMessage("The raster must be single band.")
            except Exception:
                pass
        mp = P["min_prominence"].value
        if mp is not None and mp < 0:
            P["min_prominence"].setErrorMessage(
                "The minimum prominence must be zero or positive.")
        w = P["window"].value
        if w is not None and w < 1:
            P["window"].setErrorMessage(
                "The dominance window radius must be at least 1 cell.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(raster_extremes)
            P = {p.name: p for p in parameters}
            in_path = (_resolve_raster_path(P["in_raster"])
                       or P["in_raster"].valueAsText)
            out_lyrx = raster_extremes.run_find_extremes(
                in_raster=in_path,
                out_fc=P["out_fc"].valueAsText,
                extreme_type=P["extreme_type"].valueAsText,
                min_prominence=P["min_prominence"].value or 0.0,
                window=P["window"].value or 1,
                messages=messages)
            if out_lyrx:
                try:
                    P["out_fc"].symbology = out_lyrx
                except Exception as e:
                    messages.addWarningMessage(
                        "Could not set output symbology: {}".format(e))
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class InvertRaster(object):
    """Invert Raster -- 1/(x + c), turning a high-is-good surface into a
    least-cost surface. The dialog follows the input's shape: a single-band
    input asks for one output raster; a multi-band input (a Land Facet Density
    raster) asks for a workspace + prefix and fans out one single-band cost
    raster per band. See ``invert_raster``."""

    def __init__(self):
        self.label = "Invert Raster"
        self.description = (
            "Numerically inverts a raster -- each output cell is 1/(x + c) -- "
            "so a surface in which HIGH values mean good habitat (a diversity "
            "index, a Land Facet Density raster) becomes a least-cost surface "
            "in which that good ground is CHEAP to cross. A single-band input "
            "produces one output raster; a multi-band Land Facet Density "
            "raster produces one single-band cost raster per facet, named from "
            "its own records, with the stored integer scaling converted back "
            "to 0-1 proportions first so the constant keeps its intended "
            "meaning. The small constant c (default 0.1, strongly recommended "
            "by the Land Facet manual) prevents zero cells from dividing to "
            "NoData. An alternative LINEAR method (100 - x) reproduces the "
            "ArcMap CorridorDesigner CorridorModel tool's internal "
            "suitability-to-cost conversion for 0-100 habitat suitability "
            "models, with a cost of exactly 0 bumped to 1 as that tool did. "
            "Every output carries a record of its place in the Land "
            "Facet chain. A modernized port of the Land Facet Corridor "
            "Designer's Invert Raster tool.")
        self.category = "Corridor Designer Tools\\Land Facet Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input raster (high values = good)", name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset", "DERasterBand"],
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output inverted raster (single-band input)",
            name="out_raster",
            datatype="DERasterDataset", parameterType="Optional",
            direction="Output")
        out_ws = arcpy.Parameter(
            displayName="Output workspace (multi-band input)",
            name="out_workspace", datatype="DEWorkspace",
            parameterType="Optional", direction="Input")
        out_prefix = arcpy.Parameter(
            displayName="Output name prefix (multi-band input)",
            name="out_prefix",
            datatype="GPString", parameterType="Optional", direction="Input")
        out_prefix.value = "Inv"
        method = arcpy.Parameter(
            displayName="Inversion method", name="method",
            datatype="GPString", parameterType="Required", direction="Input")
        method.filter.type = "ValueList"
        method.filter.list = [self._RECIP_LABEL, self._LINEAR_LABEL]
        method.value = self._RECIP_LABEL
        add_constant = arcpy.Parameter(
            displayName="Constant added before inverting (blank = none)",
            name="add_constant",
            datatype="GPDouble", parameterType="Optional", direction="Input")
        add_constant.value = invert_raster.DEFAULT_CONSTANT
        facets = arcpy.Parameter(
            displayName="Facets / bands to invert (default: all)",
            name="facets", datatype="GPString", parameterType="Optional",
            direction="Input", multiValue=True)
        return [in_raster, out_raster, out_ws, out_prefix, method,
                add_constant, facets]

    _RECIP_LABEL = "Reciprocal: 1 / (x + c)"
    _LINEAR_LABEL = "Linear: 100 - x (ArcMap CorridorModel, for 0-100 HSMs)"

    def _is_linear(self, P):
        return str(P["method"].valueAsText or "").startswith("Linear")

    def isLicensed(self):
        return True

    def _band_count(self, inr):
        try:
            return getattr(arcpy.Describe(inr.valueAsText), "bandCount", 1)
        except Exception:
            return 1

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr = P["in_raster"], P["out_raster"]
        multi = bool(inr.valueAsText) and self._band_count(inr) > 1

        # The dialog follows the input's shape: single-band -> one output
        # raster; multi-band -> workspace + prefix (+ facet picker).
        outr.enabled = not multi
        P["out_workspace"].enabled = multi
        P["out_prefix"].enabled = multi
        P["facets"].enabled = multi
        # The constant belongs to the reciprocal only; the linear method
        # (100 - x) ignores it. Input param, so disabling without emptying
        # is safe -- the value comes back if the user switches back.
        P["add_constant"].enabled = not self._is_linear(P)
        # A DISABLED output parameter still holding a value gets its dataset
        # PRE-DELETED by the GP framework on Run -- empty it unconditionally
        # (see pyt-dialog gotcha: the vanishing-raster bug).
        if not outr.enabled and outr.valueAsText:
            outr.value = None
            self._auto_out = None

        if inr.valueAsText and not inr.hasBeenValidated:
            try:
                path = _resolve_raster_path(inr) or inr.valueAsText
                rec, _bn = invert_raster.source_density_record(path)
                choices = []
                if rec:
                    for b in rec.get("bands") or []:
                        choices.append("{} ({})".format(
                            b.get("facet_id"),
                            land_facet_linkage.facet_label(b)))
                if choices:
                    P["facets"].filter.list = choices
            except Exception:
                pass

        if inr.valueAsText and not multi:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize("Inv_" + os.path.splitext(
                        os.path.basename(inr.valueAsText))[0])
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        inr = P["in_raster"]
        if inr.valueAsText:
            multi = self._band_count(inr) > 1
            if multi and not P["out_workspace"].valueAsText:
                P["out_workspace"].setErrorMessage(
                    "The input is multi-band, so an output workspace is "
                    "required; each band is written as its own single-band "
                    "cost raster.")
            if not multi and not P["out_raster"].valueAsText:
                P["out_raster"].setErrorMessage(
                    "The input is single band, so the output raster name is "
                    "required.")
        if not self._is_linear(P):
            c = P["add_constant"].value
            if c is not None and c < 0:
                P["add_constant"].setErrorMessage(
                    "The constant must be zero or positive.")
            if c is None or c == 0:
                P["add_constant"].setWarningMessage(
                    "Without a constant, any cell whose value is exactly "
                    "zero becomes NoData in the output (division by zero). "
                    "The Land Facet manual strongly recommends 0.1.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(land_facet_mahalanobis)
            importlib.reload(invert_raster)
            P = {p.name: p for p in parameters}
            in_path = (_resolve_raster_path(P["in_raster"])
                       or P["in_raster"].valueAsText)
            picked = P["facets"].valueAsText
            facet_values = None
            if picked:
                facet_values = [t.strip().strip("'").split("(", 1)[0].strip()
                                for t in picked.split(";") if t.strip()]
            invert_raster.run_invert(
                in_raster=in_path,
                out_raster=P["out_raster"].valueAsText,
                out_workspace=P["out_workspace"].valueAsText,
                out_prefix=P["out_prefix"].valueAsText or "Inv",
                add_constant=P["add_constant"].value,
                facet_values=facet_values,
                method=(invert_raster.METHOD_LINEAR if self._is_linear(P)
                        else invert_raster.METHOD_RECIPROCAL),
                messages=messages)
            if P["out_raster"].valueAsText:
                _writeback_resolved_raster(P, "out_raster")
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


# ---------------------------------------------------------------------------
# CorridorDesigner HSM-building chain (hsm_tools) -- original tools by Dan
# Majka (primary author); ArcGIS Pro versions by Jeff Jenness and Dan Majka.
# ---------------------------------------------------------------------------
def _vt_cell_text(v):
    """Human-readable text for a value-table cell.

    A cell picked from the dialog's DROPDOWN arrives as an arcpy wrapper
    object (MappingLayerObject / MappingTableObject) whose str() is a
    bare repr like '<MappingTableObject object at 0x...>' -- useless for
    name matching and ugly in messages (Jeff's Run-blocking error,
    2026-08-14). Prefer the object's own name-ish attributes, then
    Describe, then str()."""
    if v is None:
        return ""
    s = str(v)
    if not (s.startswith("<") and "object at 0x" in s):
        return s
    for attr in ("name", "longName", "dataSource", "value"):
        try:
            n = getattr(v, attr, None)
        except Exception:
            n = None
        if n:
            return str(n)
    try:
        d = arcpy.Describe(v)
        n = getattr(d, "catalogPath", None) or getattr(d, "name", None)
        if n:
            return str(n)
    except Exception:
        pass
    return s


def _vt_raster_path(v):
    """A usable catalog path for one raster/table cell of a value table
    (may be a Layer/Table object, a CIMPATH string, or a plain path).
    Describe first, then the object's own path-ish attributes, then the
    display text as a last resort."""
    try:
        cp = getattr(arcpy.Describe(v), "catalogPath", None)
        if cp:
            return cp
    except Exception:
        pass
    for attr in ("dataSource", "catalogPath"):
        try:
            p = getattr(v, attr, None)
            if p:
                return str(p)
        except Exception:
            pass
    return _vt_cell_text(v) or str(v)


def _hsm_lyrx():
    """The bundled default HSM symbology (Jeff's Black Bear HSI classified
    ramp, generalized to every 0-100 suitability surface), or None if the
    file is missing."""
    p = os.path.join(os.path.dirname(_PYT_PATH), "hsm_symbology.lyrx")
    return p if os.path.isfile(p) else None


def _apply_hsm_symbology(P, messages, name="out_raster"):
    """Set the bundled HSM lyrx on an output parameter (run-end pattern)."""
    lyrx = _hsm_lyrx()
    if not lyrx:
        return
    try:
        P[name].symbology = lyrx
    except Exception as e:
        messages.addWarningMessage(
            "Could not set output symbology: {}".format(e))


def _clear_factor_named_output(factors_param, out_param):
    """Undo Pro's auto-generated output name on the HSM build tools.

    When the first input of a GP tool changes, Pro's dialog fills an empty
    required output with ``<first input name>_<tool name>`` in the current
    workspace. For Create HSM / Combine Habitat Factors the first input is
    a habitat FACTOR (elevation, land cover...), never the species being
    modeled, so that guess is always wrong -- the user should name the
    model deliberately (Jeff, 2026-08-14). On the validation cycle where
    the factors just changed, an output whose base name starts with the
    first factor's base name is treated as that auto-guess and cleared.
    A user-typed name survives: it persists across factor changes, and
    matches only if deliberately named after the first factor."""
    if factors_param.hasBeenValidated or not out_param.valueAsText:
        return
    rows = factors_param.values or []
    if not rows or rows[0][0] in (None, ""):
        return
    base = os.path.splitext(
        os.path.basename(_vt_cell_text(rows[0][0])))[0].lower()
    out_base = os.path.splitext(
        os.path.basename(out_param.valueAsText))[0].lower()
    if base and out_base.startswith(base):
        out_param.value = None


def _hsm_weights_message(factors_param, weight_col):
    """Set an error on ``factors_param`` when complete rows' weights do not
    sum to 100 (the original tools' hard rule)."""
    rows = factors_param.values or []
    if not rows:
        return
    weights = []
    for row in rows:
        wt = row[weight_col]
        if wt in (None, ""):
            return                       # row still being edited; stay quiet
        try:
            weights.append(float(wt))
        except (TypeError, ValueError):
            return
        if weights[-1] <= 0:
            factors_param.setErrorMessage(
                "Every factor weight must be a positive number.")
            return
    total = sum(weights)
    if abs(total - 100) > 1e-9:
        factors_param.setErrorMessage(
            "The factor weights must sum to exactly 100 (they sum to "
            "{:g}). The weights are relative importances, normalized to "
            "w/100 when combined.".format(total))


class HabitatSuitabilityModel(object):
    """HSM 1 -- reclassify factor rasters via ASCII remap files and combine
    by weighted geometric or additive mean. See ``hsm_tools.run_hsm``."""

    def __init__(self):
        self.label = "Create Habitat Suitability Model"
        self.description = (
            "Builds a habitat suitability model (HSM) on the 0-100 scale "
            "(100 = best habitat) from any number of habitat factor rasters "
            "(land cover, elevation, distance to roads...). Each factor is "
            "first reclassified onto 0-100 with a remap source of either "
            "kind: a tab-delimited ASCII remap file (the original "
            "CorridorDesigner format: 'value : score' or 'from to : "
            "score', ascending) or a geodatabase remap table (FromValue/"
            "ToValue/NewValue fields, or OriginalValue/NewValue) -- build "
            "either with the Create HSM Remap Table button on the ribbon, "
            "which writes the kind matching where your raster lives. The "
            "factors are then combined "
            "by weighted GEOMETRIC mean (recommended: a 0 score in any "
            "factor zeroes the cell -- Liebig's Law of the Minimum) or "
            "weighted ADDITIVE mean (equivalent to Weighted Overlay). "
            "Weights are numbers summing to 100. The first factor defines "
            "the output grid; others are resampled onto it if needed. "
            "Optionally saves each reclassified factor (as <name>_r) for "
            "reuse in Combine Habitat Factors when experimenting with "
            "weights. A modernized port of the CorridorDesigner Habitat "
            "Suitability Model tool by Dan Majka; this ArcGIS Pro version "
            "by Jeff Jenness and Dan Majka. Needs no Spatial Analyst.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False

    def getParameterInfo(self):
        factors = arcpy.Parameter(
            displayName="Habitat factors (raster, remap file, weight; "
                        "weights must sum to 100)",
            name="factors", datatype="GPValueTable",
            parameterType="Required", direction="Input")
        factors.columns = [["GPRasterLayer", "Habitat factor raster"],
                           ["GPTableView", "Remap file or table"],
                           ["GPDouble", "Weight (sum to 100)"]]
        method = arcpy.Parameter(
            displayName="Method of combining factors", name="method",
            datatype="GPString", parameterType="Required",
            direction="Input")
        method.filter.list = [hsm_tools.GEOMETRIC, hsm_tools.ADDITIVE]
        method.value = hsm_tools.GEOMETRIC
        out_raster = arcpy.Parameter(
            displayName="Output habitat suitability model",
            name="out_raster", datatype="DERasterDataset",
            parameterType="Required", direction="Output")
        save_reclass = arcpy.Parameter(
            displayName="Save reclassified habitat factors (for Combine "
                        "Habitat Factors)",
            name="save_reclassified", datatype="GPBoolean",
            parameterType="Optional", direction="Input")
        save_reclass.value = False
        # Derived (never shown in the dialog): the <base>_r rasters written
        # when the save option is on. Handing them to a derived output is
        # what makes Pro add each one to the map, the same way the model
        # itself is added (Jeff, 2026-08-14); scripts get them on the
        # Result object.
        saved_out = arcpy.Parameter(
            displayName="Saved reclassified factors", name="saved_factors",
            datatype="DERasterDataset", parameterType="Derived",
            direction="Output", multiValue=True)
        return [factors, method, out_raster, save_reclass, saved_out]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        # NO remap auto-suggestion, by design (Jeff, 2026-08-14): one
        # habitat raster legitimately spawns several species-specific
        # remap tables (bear_elev, cougar_elev...), so exact-name matching
        # guesses wrong in the very workflows the tool exists for.
        _clear_factor_named_output(P["factors"], P["out_raster"])
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        _hsm_weights_message(P["factors"], weight_col=2)
        for row in (P["factors"].values or []):
            rf = row[1]
            if rf in (None, ""):
                continue
            # A dropdown-picked cell is a wrapper OBJECT: test it as-is
            # first (str() would be a repr and always "not exist" --
            # Jeff's Run-blocking false error, 2026-08-14).
            try:
                if arcpy.Exists(rf):
                    continue
            except Exception:
                pass
            rfs = _vt_raster_path(rf)
            if not os.path.isfile(rfs) and not arcpy.Exists(rfs):
                P["factors"].setErrorMessage(
                    "The remap file/table {} does not exist.".format(
                        _vt_cell_text(rf)))
                break
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(hsm_tools)
            P = {p.name: p for p in parameters}
            # both the raster and the remap cells may hold map layer/table
            # references -- resolve each to a catalog path
            facs = [(_vt_raster_path(row[0]), _vt_raster_path(row[1]),
                     float(row[2]))
                    for row in (P["factors"].values or [])]
            saved = []
            hsm_tools.run_hsm(
                factors=facs,
                method=P["method"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                save_reclassified=bool(P["save_reclassified"].value),
                messages=messages, saved_paths=saved)
            _writeback_resolved_raster(P, "out_raster")
            _apply_hsm_symbology(P, messages)
            if saved:
                # Derived multivalue output -> Pro adds each saved factor
                # to the map; they are 0-100 scores, so the HSM symbology
                # fits them too (best-effort: silently skipped if Pro
                # declines it on a multivalue parameter).
                P["saved_factors"].value = saved
                lyrx = _hsm_lyrx()
                if lyrx:
                    try:
                        P["saved_factors"].symbology = lyrx
                    except Exception:
                        pass
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class CombineHabitatFactors(object):
    """HSM 2 -- weighted combine of ALREADY-reclassified (0-100) factors.
    See ``hsm_tools.run_combine``."""

    def __init__(self):
        self.label = "Combine Habitat Factors"
        self.description = (
            "Combines habitat factor rasters that are ALREADY scored on the "
            "0-100 suitability scale into one habitat suitability model, by "
            "weighted geometric mean (recommended) or weighted additive "
            "mean, with weights summing to 100. Use this to "
            "experiment with different weights or combination methods "
            "without re-running the reclassification step -- the Create "
            "Habitat Suitability Model tool can save its reclassified "
            "factors (<name>_r) exactly for this. The first factor defines "
            "the output grid. A modernized port of the CorridorDesigner "
            "Combine Habitat Factors tool by Dan Majka; this ArcGIS Pro "
            "version by Jeff Jenness and Dan Majka. Needs no Spatial "
            "Analyst.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False

    def getParameterInfo(self):
        factors = arcpy.Parameter(
            displayName="Reclassified habitat factors (raster, weight; "
                        "weights must sum to 100)",
            name="factors", datatype="GPValueTable",
            parameterType="Required", direction="Input")
        factors.columns = [["GPRasterLayer",
                            "Reclassified factor raster (0-100)"],
                           ["GPDouble", "Weight (sum to 100)"]]
        method = arcpy.Parameter(
            displayName="Method of combining factors", name="method",
            datatype="GPString", parameterType="Required",
            direction="Input")
        method.filter.list = [hsm_tools.GEOMETRIC, hsm_tools.ADDITIVE]
        method.value = hsm_tools.GEOMETRIC
        out_raster = arcpy.Parameter(
            displayName="Output habitat suitability model",
            name="out_raster", datatype="DERasterDataset",
            parameterType="Required", direction="Output")
        return [factors, method, out_raster]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        _clear_factor_named_output(P["factors"], P["out_raster"])
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        _hsm_weights_message(P["factors"], weight_col=1)
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(hsm_tools)
            P = {p.name: p for p in parameters}
            facs = [(_vt_raster_path(row[0]), float(row[1]))
                    for row in (P["factors"].values or [])]
            hsm_tools.run_combine(
                factors=facs,
                method=P["method"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            _apply_hsm_symbology(P, messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class NormalizeExistingHSM(object):
    """HSM 3 -- linear stretch of an externally built HSM onto 0-100.
    See ``hsm_tools.run_normalize``."""

    def __init__(self):
        self.label = "Normalize Existing HSM"
        self.description = (
            "Linearly stretches a habitat suitability model built OUTSIDE "
            "CorridorDesigner (a logistic-regression surface, an expert "
            "model on some other scale...) onto the 0-100 suitability scale "
            "the HSM chain expects: you name the value meaning WORST "
            "habitat (becomes 0) and BEST habitat (becomes 100), and the "
            "stretch is ((x - worst) / (best - worst)) * 100. A model where "
            "LOW values mean good habitat inverts naturally (give worst > "
            "best). Values beyond the anchors are clamped to 0-100 by "
            "default (uncheck to write them as-is, as the original did). A "
            "modernized port of the CorridorDesigner Normalize Existing "
            "HSM tool by Dan Majka; this ArcGIS Pro version by Jeff "
            "Jenness and Dan Majka. Needs no Spatial Analyst.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_raster = arcpy.Parameter(
            displayName="Input existing habitat suitability model",
            name="in_raster",
            datatype=["GPRasterLayer", "DERasterDataset", "DERasterBand"],
            parameterType="Required", direction="Input")
        worst = arcpy.Parameter(
            displayName="Value for WORST habitat in your model",
            name="worst", datatype="GPDouble", parameterType="Required",
            direction="Input")
        best = arcpy.Parameter(
            displayName="Value for BEST habitat in your model",
            name="best", datatype="GPDouble", parameterType="Required",
            direction="Input")
        clamp = arcpy.Parameter(
            displayName="Clamp results to 0-100", name="clamp",
            datatype="GPBoolean", parameterType="Optional",
            direction="Input")
        clamp.value = True
        out_raster = arcpy.Parameter(
            displayName="Output normalized habitat suitability model",
            name="out_raster", datatype="DERasterDataset",
            parameterType="Required", direction="Output")
        return [in_raster, worst, best, clamp, out_raster]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr = P["in_raster"], P["out_raster"]
        if inr.valueAsText and not inr.hasBeenValidated:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize("HSM_Norm_" + os.path.splitext(
                        os.path.basename(inr.valueAsText))[0])
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        if (P["worst"].value is not None and P["best"].value is not None
                and float(P["worst"].value) == float(P["best"].value)):
            P["best"].setErrorMessage(
                "The worst and best habitat values are equal; the stretch "
                "would divide by zero.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(hsm_tools)
            P = {p.name: p for p in parameters}
            in_path = (_resolve_raster_path(P["in_raster"])
                       or P["in_raster"].valueAsText)
            hsm_tools.run_normalize(
                in_raster=in_path,
                worst=float(P["worst"].value),
                best=float(P["best"].value),
                out_raster=P["out_raster"].valueAsText,
                clamp=bool(P["clamp"].value),
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            _apply_hsm_symbology(P, messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class ReclassifyFeaturesInHSM(object):
    """Modify HSM 1 -- burn polygon features into an HSM at a fixed score.
    See ``hsm_tools.run_reclassify_features``."""

    def __init__(self):
        self.label = "Reclassify Features in HSM"
        self.description = (
            "Assigns a fixed suitability score (0-100) to every cell of a "
            "habitat suitability model that falls inside your polygon "
            "features -- for influences the base habitat factors could not "
            "capture: a fenced right-of-way scored 0, a wildlife-friendly "
            "easement scored higher, a mapped disturbance footprint... "
            "Cells outside the polygons keep their original scores; NoData "
            "stays NoData. If the polygon layer has an active selection in "
            "the map, only the selected features are used (a dataset "
            "picked via Browse has no selection and uses all features). A "
            "modernized port of the CorridorDesigner Reclassify Features "
            "tool by Dan Majka; this ArcGIS Pro version by Jeff Jenness "
            "and Dan Majka. Needs no Spatial Analyst.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_hsm = arcpy.Parameter(
            displayName="Input habitat suitability model",
            name="in_hsm",
            datatype=["GPRasterLayer", "DERasterDataset", "DERasterBand"],
            parameterType="Required", direction="Input")
        in_features = arcpy.Parameter(
            displayName="Polygon features to reclassify in the HSM",
            name="in_features",
            datatype=["GPFeatureLayer", "DEFeatureClass"],
            parameterType="Required", direction="Input")
        try:
            in_features.filter.list = ["Polygon"]
        except Exception:
            pass
        new_score = arcpy.Parameter(
            displayName="New habitat suitability score for those cells "
                        "(0-100)",
            name="new_score", datatype="GPDouble",
            parameterType="Required", direction="Input")
        out_raster = arcpy.Parameter(
            displayName="Output modified habitat suitability model",
            name="out_raster", datatype="DERasterDataset",
            parameterType="Required", direction="Output")
        return [in_hsm, in_features, new_score, out_raster]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr = P["in_hsm"], P["out_raster"]
        if inr.valueAsText and not inr.hasBeenValidated:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize("HSM_Reclass_" + os.path.splitext(
                        os.path.basename(inr.valueAsText))[0])
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        sc = P["new_score"].value
        if sc is not None and not (0 <= float(sc) <= 100):
            P["new_score"].setErrorMessage(
                "The suitability score must be between 0 and 100.")
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(hsm_tools)
            P = {p.name: p for p in parameters}
            in_path = (_resolve_raster_path(P["in_hsm"])
                       or P["in_hsm"].valueAsText)
            hsm_tools.run_reclassify_features(
                in_hsm=in_path,
                in_features=P["in_features"].value
                or P["in_features"].valueAsText,
                new_score=float(P["new_score"].value),
                out_raster=P["out_raster"].valueAsText,
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            _apply_hsm_symbology(P, messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class CriticalHabitatFeature(object):
    """Modify HSM 2 -- scale an HSM by distance-band percentages from a
    critical feature. See ``hsm_tools.run_critical_feature``."""

    def __init__(self):
        self.label = "Critical Habitat Feature"
        self.description = (
            "Rescales a habitat suitability model by DISTANCE from a "
            "critical habitat feature -- a resource the species must stay "
            "near (or a hazard it must stay away from). You provide "
            "distance bands in METERS (converted to the raster's "
            "coordinate system automatically, including true spheroidal "
            "cell sizes for geographic rasters) with a percentage "
            "for each; every cell's suitability is multiplied by the "
            "percentage of the band its distance to the nearest "
            "critical-feature cell falls in. The workshop book's example: "
            "a riparian species keeps 100% of its score within 30 m of "
            "water, 80% from 30-60 m, 60% from 60-100 m, and 0% beyond "
            "100 m. The critical feature can be a feature layer (an active "
            "map selection is honored) or a raster (every data cell is the "
            "feature). Cells beyond your last band are left unchanged, "
            "with a warning -- end with a band out to a very large "
            "distance (e.g. '100 999999 : 0') to control that explicitly. "
            "A modernized port of the CorridorDesigner Critical Feature "
            "tool by Dan Majka; this ArcGIS Pro version by Jeff Jenness "
            "and Dan Majka. Needs no Spatial Analyst.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_hsm = arcpy.Parameter(
            displayName="Input habitat suitability model",
            name="in_hsm",
            datatype=["GPRasterLayer", "DERasterDataset", "DERasterBand"],
            parameterType="Required", direction="Input")
        in_critical = arcpy.Parameter(
            displayName="Critical habitat feature (features or raster)",
            name="in_critical",
            datatype=["GPFeatureLayer", "DEFeatureClass", "GPRasterLayer",
                      "DERasterDataset"],
            parameterType="Required", direction="Input")
        bands = arcpy.Parameter(
            displayName="Distance bands (from, to, percent of score kept)",
            name="bands", datatype="GPValueTable",
            parameterType="Required", direction="Input")
        bands.columns = [["GPDouble", "From distance (meters)"],
                         ["GPDouble", "To distance (meters)"],
                         ["GPDouble", "Percent of score kept (0-100)"]]
        out_raster = arcpy.Parameter(
            displayName="Output modified habitat suitability model",
            name="out_raster", datatype="DERasterDataset",
            parameterType="Required", direction="Output")
        return [in_hsm, in_critical, bands, out_raster]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outr = P["in_hsm"], P["out_raster"]
        if inr.valueAsText and not inr.hasBeenValidated:
            if not outr.altered or outr.valueAsText == self._auto_out:
                try:
                    base = _legalize("HSM_Critical_" + os.path.splitext(
                        os.path.basename(inr.valueAsText))[0])
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outr.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        rows = P["bands"].values or []
        prev = None
        overlaps = []
        for row in rows:
            lo, hi, pc = row[0], row[1], row[2]
            if lo is None or hi is None or pc is None:
                continue
            lo, hi, pc = float(lo), float(hi), float(pc)
            if hi < lo:
                P["bands"].setErrorMessage(
                    "The band {:g} to {:g} runs backwards.".format(lo, hi))
                return
            if not (0 <= pc <= 100):
                P["bands"].setErrorMessage(
                    "The percentage for band {:g}-{:g} must be between 0 "
                    "and 100.".format(lo, hi))
                return
            if prev is not None and (lo < prev[0] or hi < prev[1]):
                P["bands"].setErrorMessage(
                    "The distance bands must be in ascending order.")
                return
            # A genuine range overlap (a shared endpoint is the normal
            # touching-bands case, not an overlap): allowed, first band
            # wins -- but say so, or the trimming is invisible.
            if prev is not None and lo < prev[1]:
                overlaps.append((prev, (lo, hi, pc)))
            prev = (lo, hi, pc)
        if overlaps:
            P["bands"].setWarningMessage(
                "Overlapping distance bands -- cells in a shared range "
                "take the EARLIER band's percentage: " + " ".join(
                    "{:g}-{:g} m overlaps {:g}-{:g} m, so {:g}-{:g} m "
                    "keeps {:g}% and the later band effectively starts "
                    "at {:g} m.".format(
                        b[0], b[1], a[0], a[1], b[0], a[1], a[2], a[1])
                    for a, b in overlaps))
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(hsm_tools)
            P = {p.name: p for p in parameters}
            in_path = (_resolve_raster_path(P["in_hsm"])
                       or P["in_hsm"].valueAsText)
            crit = P["in_critical"].value or P["in_critical"].valueAsText
            try:
                if arcpy.Describe(crit).dataType in (
                        "RasterDataset", "RasterLayer", "RasterBand"):
                    crit = (_resolve_raster_path(P["in_critical"])
                            or P["in_critical"].valueAsText)
            except Exception:
                pass
            bands = [(float(r[0]), float(r[1]), float(r[2]))
                     for r in (P["bands"].values or [])]
            hsm_tools.run_critical_feature(
                in_hsm=in_path,
                in_critical=crit,
                bands=bands,
                out_raster=P["out_raster"].valueAsText,
                messages=messages)
            _writeback_resolved_raster(P, "out_raster")
            _apply_hsm_symbology(P, messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class HabitatPatches(object):
    """Patch configuration dataset from an HSM. See
    ``hsm_tools.run_patch_map``."""

    def __init__(self):
        self.label = "Create Habitat Patches"
        self.description = (
            "Turns a habitat suitability model into habitat PATCH polygons "
            "classified by whether each is large enough to matter. "
            "The HSM is averaged over a circular moving window (so small "
            "inclusions and gaps do not fragment the patches), cells whose "
            "average meets your quality threshold become suitable habitat, "
            "touching suitable cells (edges or corners) are grouped into "
            "patches, and each patch is classified by its GEODESIC area in "
            "hectares: below the minimum breeding size = 'Too small', at "
            "least breeding size = 'Breeding patch' (supports at least one "
            "breeding event), at least population size = 'Population "
            "patch'. Output is one polygon per patch with the class, name "
            "and area, symbolized by class. A modernized port of the "
            "CorridorDesigner Patch Configuration tool by Dan Majka; this "
            "ArcGIS Pro version by Jeff Jenness and Dan Majka. Needs no "
            "Spatial Analyst.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False
        self._auto_out = None

    def getParameterInfo(self):
        in_hsm = arcpy.Parameter(
            displayName="Input habitat suitability model",
            name="in_hsm",
            datatype=["GPRasterLayer", "DERasterDataset", "DERasterBand"],
            parameterType="Required", direction="Input")
        radius = arcpy.Parameter(
            displayName="Moving-window radius (for averaging the HSM)",
            name="radius", datatype="GPDouble",
            parameterType="Required", direction="Input")
        radius_units = arcpy.Parameter(
            displayName="Radius units", name="radius_units",
            datatype="GPString", parameterType="Required",
            direction="Input")
        radius_units.filter.type = "ValueList"
        radius_units.filter.list = hillshade.RADIUS_UNITS
        radius_units.value = "Meters"
        threshold = arcpy.Parameter(
            displayName="Habitat quality threshold (0-100)",
            name="threshold", datatype="GPDouble",
            parameterType="Required", direction="Input")
        breeding = arcpy.Parameter(
            displayName="Minimum breeding patch size (hectares)",
            name="breeding_ha", datatype="GPDouble",
            parameterType="Required", direction="Input")
        population = arcpy.Parameter(
            displayName="Minimum population patch size (hectares)",
            name="population_ha", datatype="GPDouble",
            parameterType="Required", direction="Input")
        out_fc = arcpy.Parameter(
            displayName="Output habitat patches",
            name="out_fc", datatype="DEFeatureClass",
            parameterType="Required", direction="Output")
        return [in_hsm, radius, radius_units, threshold, breeding,
                population, out_fc]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        inr, outf = P["in_hsm"], P["out_fc"]
        if inr.valueAsText and not inr.hasBeenValidated:
            if not outf.altered or outf.valueAsText == self._auto_out:
                try:
                    base = _legalize("Patches_" + os.path.splitext(
                        os.path.basename(inr.valueAsText))[0])
                    ws = arcpy.env.workspace or os.path.dirname(
                        arcpy.Describe(inr.valueAsText).catalogPath)
                    sug = _unique_name(base, ws)
                    outf.value = sug
                    self._auto_out = sug
                except Exception:
                    pass
        return

    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        if P["radius"].value is not None and P["radius"].value <= 0:
            P["radius"].setErrorMessage(
                "The moving-window radius must be positive.")
        t = P["threshold"].value
        if t is not None and not (0 <= t <= 100):
            P["threshold"].setErrorMessage(
                "The threshold must be between 0 and 100 (the HSM "
                "suitability scale).")
        b = P["breeding_ha"].value
        p = P["population_ha"].value
        if b is not None and b <= 0:
            P["breeding_ha"].setErrorMessage(
                "The minimum breeding patch size must be positive.")
        if b is not None and p is not None and p < b:
            P["population_ha"].setErrorMessage(
                "The minimum population patch size cannot be smaller than "
                "the minimum breeding patch size ({:g} ha).".format(b))
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(land_facet_linkage)
            importlib.reload(hsm_tools)
            P = {p.name: p for p in parameters}
            in_path = (_resolve_raster_path(P["in_hsm"])
                       or P["in_hsm"].valueAsText)
            lyrx = hsm_tools.run_patch_map(
                in_hsm=in_path,
                out_fc=P["out_fc"].valueAsText,
                radius=P["radius"].value,
                radius_units=P["radius_units"].valueAsText,
                threshold=P["threshold"].value,
                breeding_ha=P["breeding_ha"].value,
                population_ha=P["population_ha"].value,
                messages=messages)
            if lyrx:
                try:
                    P["out_fc"].symbology = lyrx
                except Exception:
                    pass
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")


class ClipDataToAnalysisArea(object):
    """Batch-clip rasters and feature classes to the analysis area. See
    ``clip_tools.run_clip_data``."""

    def __init__(self):
        self.label = "Clip Data to Analysis Area"
        self.description = (
            "Clips any mix of RASTERS and FEATURE CLASSES to an analysis "
            "area polygon in one run -- drag layers in from the map, or "
            "browse and multi-select datasets from disk. The output format "
            "follows the workspace you pick: a FOLDER gets shapefiles and "
            "GeoTIFFs, a GEODATABASE gets native feature classes and "
            "rasters. Optionally projects every output to a chosen "
            "coordinate system; when datums differ, the dialog suggests "
            "geographic transformations the same way the Project tool "
            "does, and accepts several at once because different inputs "
            "may need different datum paths (WGS 1984 sources and NAD "
            "1927 sources both headed to NAD 1983, say) -- each dataset "
            "uses the chosen transformation that fits its own datum. Map "
            "layer selections are honored: only the selected features are "
            "clipped, and a selection on the analysis area layer limits "
            "the clip boundary. Optionally adds the clipped layers to the "
            "map, each symbolized like its source layer when the input "
            "came from the map. Each dataset is clipped independently -- "
            "one failure warns and the batch continues. A modernized port "
            "of the CorridorDesigner Clip Analysis Layers tool by Dan "
            "Majka; this ArcGIS Pro version by Jeff Jenness and Dan "
            "Majka. Needs no Spatial Analyst.")
        self.category = "Corridor Designer Tools\\Corridor Designer"
        self.canRunInBackground = False

    def getParameterInfo(self):
        datasets = arcpy.Parameter(
            displayName="Datasets to clip (rasters and feature classes)",
            name="datasets",
            datatype=["GPRasterLayer", "GPFeatureLayer", "DERasterDataset",
                      "DEFeatureClass"],
            parameterType="Required", direction="Input", multiValue=True)
        area = arcpy.Parameter(
            displayName="Analysis area (polygon features)",
            name="area",
            datatype=["GPFeatureLayer", "DEFeatureClass"],
            parameterType="Required", direction="Input")
        try:
            area.filter.list = ["Polygon"]
        except Exception:
            pass
        workspace = arcpy.Parameter(
            displayName="Output workspace (folder = shapefiles + "
                        "GeoTIFFs; geodatabase = native datasets)",
            name="workspace", datatype="DEWorkspace",
            parameterType="Required", direction="Input")
        try:
            # Suggest the project's default geodatabase; freely editable.
            if arcpy.env.workspace:
                workspace.value = arcpy.env.workspace
        except Exception:
            pass
        prefix = arcpy.Parameter(
            displayName="Output name prefix (optional)",
            name="prefix", datatype="GPString",
            parameterType="Optional", direction="Input")
        suffix = arcpy.Parameter(
            displayName="Output name suffix (optional)",
            name="suffix", datatype="GPString",
            parameterType="Optional", direction="Input")
        # Default to _clip so outputs never shadow same-named originals in
        # the destination workspace (clearable if unwanted).
        suffix.value = "_clip"
        out_cs = arcpy.Parameter(
            displayName="Output coordinate system (optional)",
            name="out_cs", datatype="GPCoordinateSystem",
            parameterType="Optional", direction="Input")
        transformations = arcpy.Parameter(
            displayName="Geographic transformations (one per source "
                        "datum; suggested automatically)",
            name="transformations", datatype="GPString",
            parameterType="Optional", direction="Input", multiValue=True)
        transformations.filter.type = "ValueList"
        transformations.filter.list = []
        add_to_map = arcpy.Parameter(
            displayName="Add clipped layer(s) to map",
            name="add_to_map", datatype="GPBoolean",
            parameterType="Optional", direction="Input")
        add_to_map.value = True
        return [datasets, area, workspace, prefix, suffix, out_cs,
                transformations, add_to_map]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        ds_p, cs_p, tr_p = (P["datasets"], P["out_cs"],
                            P["transformations"])
        if not cs_p.valueAsText or not ds_p.valueAsText:
            return
        # Recompute candidates only when the coordinate system or the
        # dataset list just changed (hasBeenValidated is False exactly on
        # that cycle); ListTransformations per datum is not free.
        if cs_p.hasBeenValidated and ds_p.hasBeenValidated:
            return
        try:
            target = arcpy.SpatialReference()
            target.loadFromString(cs_p.valueAsText)
        except Exception:
            return
        suggestions, options, seen = [], [], set()
        for v in (ds_p.values or []):
            try:
                # MultiValue values are GP wrappers Describe sometimes
                # rejects; their text (layer name / path) usually resolves
                # -- without this, that dataset's datum silently got no
                # suggestion (Jeff's first Pro run).
                try:
                    d = arcpy.Describe(v)
                except Exception:
                    d = arcpy.Describe(str(v))
                sr = d.spatialReference
                if sr is None or getattr(sr, "name", "Unknown") == "Unknown":
                    continue
                datum = sr.GCS.datumName
                if datum == target.GCS.datumName or datum in seen:
                    continue
                seen.add(datum)
                cands = arcpy.ListTransformations(
                    sr, target, getattr(d, "extent", None)) or []
                if cands:
                    # Esri returns them best-first for this extent: the
                    # first is the auto-suggestion for this datum pair.
                    suggestions.append(cands[0])
                for c in cands:
                    if c not in options:
                        options.append(c)
            except Exception:
                pass
        try:
            tr_p.filter.type = "ValueList"
            tr_p.filter.list = options
        except Exception:
            pass
        # Fill (or refresh) the suggestions when the target CS just
        # changed, or when the parameter is still empty -- a hand-edited
        # list survives dataset additions.
        if suggestions and (not cs_p.hasBeenValidated
                            or not tr_p.valueAsText):
            try:
                tr_p.values = suggestions
            except Exception:
                pass
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        try:
            _scripting_hint(messages, type(self).__name__)
            importlib.reload(clip_tools)
            P = {p.name: p for p in parameters}
            out_cs = None
            if P["out_cs"].valueAsText:
                out_cs = arcpy.SpatialReference()
                out_cs.loadFromString(P["out_cs"].valueAsText)
            trans = [str(t) for t in (P["transformations"].values or [])
                     if t not in (None, "")]
            fcs, ras, details = clip_tools.run_clip_data(
                datasets=list(P["datasets"].values or []),
                area=P["area"].value or P["area"].valueAsText,
                workspace=P["workspace"].valueAsText,
                out_cs=out_cs,
                transformations=trans,
                prefix=(P["prefix"].valueAsText or ""),
                suffix=(P["suffix"].valueAsText or ""),
                messages=messages)
            # Map-adding is explicit (not derived-output auto-add) so the
            # checkbox genuinely controls it AND each output whose input
            # was a MAP LAYER can inherit that layer's symbology.
            if bool(P["add_to_map"].value):
                clip_tools.add_outputs_to_map(details, messages)
        except arcpy.ExecuteError:
            raise
        except Exception:
            importlib.reload(error_report)
            error_report.report(messages, self.label, parameters)
            raise arcpy.ExecuteError(
                "The tool stopped because of an unexpected error; see the report above.")
