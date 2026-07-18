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


class Toolbox(object):
    def __init__(self):
        self.label = "Jenness Enterprises Tools"
        self.alias = "jenness"
        self.tools = [LoCoHStandard, LoCoHAdaptive, LoCoHFixedSphere,
                      MahalanobisRaster, MahalanobisPoints, MahalanobisCovariance,
                      MahalanobisChiSquare,
                      KernelDensityGenerate, KernelDensityProportion, KernelDensityContours,
                      ShadowPolygons]


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
