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
import mahalanobis    # noqa: E402
import kernel_density # noqa: E402
import cartography_tools  # noqa: E402
import topographic_tools  # noqa: E402
import hillshade      # noqa: E402
import lsri           # noqa: E402
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
                      BlurredHillshade, SurfaceAreaRatio, VRM, TRI,
                      LSRIOnDEM, LSRIOnContourPolylines]


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
            if cur_tbl == "" or cur_tname == tool._auto_tables.get(key):
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
        params = [in_raster, out_area, area_units, out_ratio, out_flat,
                  flat_units, elev_units, corner_method]
        # Pre-select the choices from the last run saved with this project.
        saved = _load_tool_defaults("SurfaceAreaRatio")
        for p in (area_units, flat_units, elev_units, corner_method):
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
        return

    def execute(self, parameters, messages):
        try:
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
        self.label = "VRM"
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
        params = [in_raster, out_raster, elev_units, radius, radius_units]
        # Pre-select the choices from the last run saved with this project.
        saved = _load_tool_defaults("VRM")
        for p in (radius, radius_units, elev_units):
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
        return

    def execute(self, parameters, messages):
        try:
            importlib.reload(topographic_tools)
            P = {p.name: p for p in parameters}
            out_lyrx = topographic_tools.run_vrm(
                in_raster=P["in_raster"].valueAsText,
                out_raster=P["out_raster"].valueAsText,
                elev_units=P["elev_units"].valueAsText or "Meters",
                radius=P["radius"].value,
                radius_units=P["radius_units"].valueAsText or "Cells",
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
        self.label = "TRI"
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

