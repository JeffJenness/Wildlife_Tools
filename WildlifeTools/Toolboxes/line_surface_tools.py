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

"""Surface Attributes along a line -- engine.

Adds topographic-roughness attribute fields to an existing polyline feature
class, computed by sampling elevation along each line and reducing the line to
a set of straight three-dimensional segments. Every statistic is derived from
those segments (see ``compute_line_statistics``). Elevation is taken either
from the polyline's own Z values or, more commonly, from a DEM sampled by
bilinear interpolation at a regular ground interval (plus the first and last
vertex of every part). Multipart lines are handled part-by-part -- disconnected
pieces are never bridged by an artificial segment.

The per-segment reduction is deliberately separated from the statistics so the
same core can feed a companion tool that writes the segments out as their own
polyline feature class.

DIRECTION CONVENTION: every direction-dependent statistic describes the line
travelling in the direction of its geometry, from the first vertex to the last
(a line that climbs 100 m west-to-east drops 100 m east-to-west).

Internally all lengths and elevations are carried in METERS; outputs are
converted at the end -- lengths to the chosen Length Unit, elevations to the
DEM's / polyline's elevation units. Slopes are computed in DEGREES and the
single-value slope stats (mean/min/max) are converted to the chosen slope unit;
slope dispersion (standard deviation, RMS) has no exact non-linear conversion
and is always reported in degrees. Fractal dimension uses the planimetric
(map-view) path so it keeps the classic 1..2 range; sinuosity is fully 3-D.

Citations (attached in the tool help/metadata):
  Directional statistics -- Mardia & Jupp (2000), Fisher (1993), Batschelet
    (1981); formulas follow Jenness, J. (2012) "Directional data" newsletter.
  Fractal dimension (divider method) -- Mandelbrot (1967); Nams (2005).
  Sinuosity / straightness / tortuosity -- Benhamou (2004); straightness index
    per Batschelet (1981).
"""

import math

import numpy as np

# --------------------------------------------------------------------------
#  Statistic catalogue (the "Property" value list, in dialog order)
# --------------------------------------------------------------------------
#   key                        label (shown in the dropdown)
STATS = [
    ("plan_length", "Planimetric Length (geodesic)"),
    ("surf_length", "Surface Length (geodesic)"),
    ("surf_ratio", "Surface Ratio"),
    ("length_uphill", "Length Uphill"),
    ("prop_uphill", "Proportion Uphill"),
    ("elev_gain", "Cumulative Elevation Gain"),
    ("length_downhill", "Length Downhill"),
    ("prop_downhill", "Proportion Downhill"),
    ("elev_loss", "Cumulative Elevation Loss"),
    ("length_flat", "Length Perfectly Flat"),
    ("prop_flat", "Proportion Perfectly Flat"),
    ("net_elev", "Net Elevation Change"),
    ("avg_slope", "Average Slope"),
    ("slope_sd", "Slope Standard Deviation"),
    ("min_slope", "Minimum Slope"),
    ("max_slope", "Maximum Slope"),
    ("rms_slope", "RMS Slope"),
    ("mean_dir", "Mean Direction"),
    ("resultant", "Mean Resultant Length"),
    ("circ_var", "Circular Variance"),
    ("ang_var", "Angular Variance"),
    ("circ_sd", "Circular Standard Deviation"),
    ("ang_dev", "Angular Deviation"),
    ("rayleigh_p", "Rayleigh p-value"),
    ("total_curv", "Total Curvature"),
    ("fractal_d", "Fractal Dimension"),
    ("sinuosity", "Sinuosity (3D)"),
    ("min_elev", "Minimum Elevation"),
    ("min_elev_x", "Minimum Elevation X"),
    ("min_elev_y", "Minimum Elevation Y"),
    ("max_elev", "Maximum Elevation"),
    ("max_elev_x", "Maximum Elevation X"),
    ("max_elev_y", "Maximum Elevation Y"),
    ("elev_range", "Elevation Range"),
    ("vert_relief", "Total Vertical Relief Traveled"),
    ("reversals", "Reversal Count"),
]
STAT_KEYS = [k for k, _ in STATS]
STAT_LABEL = dict(STATS)
LABEL_STAT = {v: k for k, v in STATS}

# Which "unit family" each statistic reports in -- drives the field metadata
# ("length" = the chosen Length Unit; "elevation" = the DEM/Z elevation units;
# "degrees" = degrees; "slope" = the chosen slope unit; "none" = unitless;
# "count" = an integer count).
STAT_UNIT_KIND = {
    "plan_length": "length", "surf_length": "length",
    "length_uphill": "length", "length_downhill": "length",
    "length_flat": "length",
    "surf_ratio": "none", "prop_uphill": "none", "prop_downhill": "none",
    "prop_flat": "none", "sinuosity": "none", "circ_var": "none",
    "ang_var": "none", "resultant": "none", "fractal_d": "none",
    "rayleigh_p": "none",
    "elev_gain": "elevation", "elev_loss": "elevation", "net_elev": "elevation",
    "min_elev": "elevation", "max_elev": "elevation", "elev_range": "elevation",
    "vert_relief": "elevation",
    "min_elev_x": "coord", "min_elev_y": "coord",
    "max_elev_x": "coord", "max_elev_y": "coord",
    "avg_slope": "slope", "min_slope": "slope", "max_slope": "slope",
    "slope_sd": "degrees", "rms_slope": "degrees",
    "mean_dir": "degrees", "circ_sd": "degrees", "ang_dev": "degrees",
    "total_curv": "degrees",
    "reversals": "count",
}

SLOPE_UNITS = ["Degrees", "Percent"]

# Statistics whose value is a whole number (safe in an integer output field).
# Everything else can be fractional and needs a floating-point field.
INTEGER_STATS = {"reversals"}

# Statistics whose value changes when the line is traversed in the opposite
# direction (climb becomes descent, mean heading flips 180 degrees). The
# circular/angular SPREAD measures are invariant (adding 180 to every bearing
# rotates the mean but not the dispersion), so only the mean direction and the
# elevation-sign statistics are direction-dependent.
DIRECTION_DEPENDENT = {
    "elev_gain", "elev_loss", "net_elev", "length_uphill", "prop_uphill",
    "length_downhill", "prop_downhill", "mean_dir",
}


# --------------------------------------------------------------------------
#  Per-SEGMENT field catalogue (the companion "segment feature class" tool)
# --------------------------------------------------------------------------
#   key                label                              field type  unit kind
SEG_STATS = [
    ("begin_x", "Begin X", "DOUBLE", "coord"),
    ("begin_y", "Begin Y", "DOUBLE", "coord"),
    ("begin_z", "Begin Z", "DOUBLE", "elevation"),
    ("end_x", "End X", "DOUBLE", "coord"),
    ("end_y", "End Y", "DOUBLE", "coord"),
    ("end_z", "End Z", "DOUBLE", "elevation"),
    ("plan_length", "Planimetric Length (geodesic)", "DOUBLE", "length"),
    ("surf_length", "Surface Length (geodesic)", "DOUBLE", "length"),
    ("surf_ratio", "Surface Ratio", "DOUBLE", "none"),
    ("rel_elev", "Relative Elevation Change", "DOUBLE", "elevation"),
    ("net_elev", "Net Elevation Change", "DOUBLE", "elevation"),
    ("rel_slope", "Relative Slope", "DOUBLE", "slope"),
    ("abs_slope", "Absolute Slope", "DOUBLE", "slope"),
    ("land_slope", "Average Landscape Slope", "DOUBLE", "slope"),
    ("land_aspect", "Average Landscape Compass Direction", "DOUBLE", "degrees"),
    ("traverse", "Traverse Angle", "DOUBLE", "degrees"),
    ("bearing", "Bearing", "DOUBLE", "degrees"),
    ("deflection", "Deflection Angle", "DOUBLE", "degrees"),
    ("abs_deflection", "Absolute Deflection Angle", "DOUBLE", "degrees"),
    ("inclination", "Inclination Angle", "DOUBLE", "degrees"),
    ("abs_inclination", "Absolute Inclination Angle", "DOUBLE", "degrees"),
    ("defl3d", "3D Deflection Angle", "DOUBLE", "degrees"),
    ("source_id", "Source Line ID", "LONG", "id"),
    ("seg_num", "Segment Number", "LONG", "count"),
    ("part_index", "Part Index", "LONG", "count"),
    ("slope_class", "Slope Direction Class", "TEXT", "text"),
    ("station", "Station (distance along line)", "DOUBLE", "length"),
    ("mid_x", "Midpoint X", "DOUBLE", "coord"),
    ("mid_y", "Midpoint Y", "DOUBLE", "coord"),
    ("mid_z", "Midpoint Z", "DOUBLE", "elevation"),
    ("mean_elev", "Mean Elevation", "DOUBLE", "elevation"),
]
SEG_KEYS = [k for k, _, _, _ in SEG_STATS]
SEG_LABEL = {k: lb for k, lb, _, _ in SEG_STATS}
SEG_LABEL_KEY = {lb: k for k, lb, _, _ in SEG_STATS}
SEG_FTYPE = {k: ft for k, _, ft, _ in SEG_STATS}
SEG_KIND = {k: kd for k, _, _, kd in SEG_STATS}
# Angle fields that need the previous segment -> null / -999 on the first
# segment of each part.
SEG_NEEDS_PREV = {"deflection", "abs_deflection", "inclination",
                  "abs_inclination", "defl3d"}
# Attributes read from the terrain around the segment rather than from the
# segment itself; they need a DEM even when the elevations come from the
# polylines' own Z values.
SEG_LANDSCAPE = {"land_slope", "land_aspect", "traverse"}

# What each segment attribute IS, for the field metadata written into the
# output feature class (same wording as the Split Lines into Topography
# Segments help page). Plain ASCII, as for STAT_DESC.
SEG_DESC = {
    "begin_x": "The X coordinate of the segment's start, in the dataset's coordinate system.",
    "begin_y": "The Y coordinate of the segment's start, in the dataset's coordinate system.",
    "begin_z": "The elevation at the segment's start.",
    "end_x": "The X coordinate of the segment's end, in the dataset's coordinate system.",
    "end_y": "The Y coordinate of the segment's end, in the dataset's coordinate system.",
    "end_z": "The elevation at the segment's end.",
    "mid_x": "The X coordinate of the segment's midpoint, in the dataset's coordinate system.",
    "mid_y": "The Y coordinate of the segment's midpoint, in the dataset's coordinate system.",
    "mid_z": "The elevation at the segment's midpoint (the mean of its two end elevations).",
    "mean_elev": "The mean of the segment's two end elevations.",
    "plan_length": "The horizontal length of the segment, measured on the spheroid.",
    "surf_length": "The true three-dimensional length of the segment: the hypotenuse of the planimetric length and the elevation change.",
    "surf_ratio": "Surface length divided by planimetric length; exactly 1 on a level segment.",
    "rel_elev": "End elevation minus start elevation: positive uphill, negative downhill, in the direction of travel.",
    "net_elev": "The absolute value of the elevation change over the segment.",
    "rel_slope": "The segment's slope with its sign: positive climbing, negative descending, in the direction of travel.",
    "abs_slope": "The segment's steepness regardless of direction.",
    "land_slope": "The slope of the terrain itself under the segment (not the slope of the segment): the DEM's slope from a 3 x 3 cell neighborhood, sampled along the segment (its two ends and points at the DEM's cell interval between them) and averaged.",
    "land_aspect": "The aspect of the terrain under the segment: the downslope compass direction of the DEM's surface, 0 to 360 degrees clockwise from north, from a 3 x 3 cell neighborhood sampled along the segment and averaged as directions. Undefined (null) where the terrain is flat.",
    "traverse": "The angle between the segment's direction of travel and the fall line of the terrain under it, 0 to 90 degrees: 0 when the segment runs straight up or down the slope, 90 when it follows the contour. Computed from the Bearing and the Average Landscape Compass Direction. Undefined (null) where the terrain is flat.",
    "bearing": "The segment's compass azimuth, 0 to 360 degrees clockwise from north, in the direction of travel.",
    "deflection": "The horizontal turn from the previous segment: this segment's bearing minus the previous one's, wrapped to -180 to +180 degrees; a right turn is positive, a left turn negative. Null on the first segment of a line, part or run.",
    "abs_deflection": "The absolute value of the Deflection Angle. Null on the first segment of a line, part or run.",
    "inclination": "The vertical turn from the previous segment: this segment's pitch (signed slope angle) minus the previous one's. Null on the first segment of a line, part or run.",
    "abs_inclination": "The absolute value of the Inclination Angle. Null on the first segment of a line, part or run.",
    "defl3d": "The full three-dimensional angle between the previous segment's vector and this one's: 0 when the route continues straight at the same pitch, 180 for an exact reversal. Always positive; null on the first segment of a line, part or run.",
    "source_id": "The ObjectID of the polyline this segment came from.",
    "seg_num": "The segment's order within its part, counted from 1.",
    "part_index": "Which part of a multipart line the segment belongs to, counted from 0.",
    "slope_class": "Uphill, Downhill or Flat (exactly zero elevation change), in the direction of travel.",
    "station": "The cumulative planimetric distance from the start of the part to the start of this segment.",
}
assert set(SEG_DESC) == set(SEG_LABEL)


def seg_units_phrase(key, length_unit, elev_unit, slope_unit):
    """Human-readable units for one segment attribute's output field."""
    kind = SEG_KIND[key]
    if kind == "length":
        return length_unit
    if kind == "elevation":
        return "{} (elevation)".format(elev_unit)
    if kind == "degrees":
        return "degrees (azimuth)" if key in ("bearing", "land_aspect") else "degrees"
    if kind == "slope":
        return "degrees" if slope_unit == "Degrees" else "percent"
    if kind in ("count", "id"):
        return "integer"
    if kind == "coord":
        return "coordinate system units of the polyline dataset"
    if kind == "text":
        return "text"
    return "unitless"


def seg_field_description(key, length_unit, elev_unit, slope_unit):
    """Field-metadata description for one segment attribute: what it is, its
    units, and the direction-of-travel convention where that matters."""
    txt = "{}. {} Units: {}. Computed by the Split Lines into Topography Segments tool.".format(
        SEG_LABEL[key], SEG_DESC[key],
        seg_units_phrase(key, length_unit, elev_unit, slope_unit))
    if key in ("rel_elev", "rel_slope", "bearing", "deflection", "inclination",
               "slope_class"):
        txt += (" The direction of travel is the direction of the line's geometry, "
                "from its first vertex to its last (reversing the line reverses "
                "this value).")
    return txt


def _label_to_field(label):
    """Sanitize a statistic label into a valid field-name stem (drop
    parentheticals, non-alphanumerics -> underscore)."""
    import re
    base = re.sub(r"\([^)]*\)", "", label)
    return re.sub(r"[^0-9A-Za-z]+", "_", base).strip("_")


# Compact default names for the min/max-coordinate statistics: the full
# label-derived names ("Minimum_Elevation_X") all truncate to "Minimum_El" on a
# SHAPEFILE (10-character field limit), colliding with each other AND with
# Minimum_Elevation -- the tool's duplicate-field validation then refuses to
# run. These are exactly 10 characters and distinct, so the defaults work on
# every output format.
_FIELD_NAME_OVERRIDE = {
    "min_elev_x": "Min_Elev_X", "min_elev_y": "Min_Elev_Y",
    "max_elev_x": "Max_Elev_X", "max_elev_y": "Max_Elev_Y",
}


# Default names for a SHAPEFILE (dBASE) target: at most 10 characters, all
# distinct, so nothing truncates and nothing collides. The geodatabase names
# above are the readable ones; these are what the dialog fills in instead
# when the input (or, for the segment tool, the output) is a shapefile, and
# the dialog swaps between the two sets when the dataset's format changes
# (Jeff, 2026-09-22: with a shapefile input, the long defaults truncated to
# duplicates and the tool refused to run on names the user never typed).
SHP_FIELD_NAME = {
    "plan_length": "PlanLen", "surf_length": "SurfLen", "surf_ratio": "SurfRatio",
    "length_uphill": "LenUp", "prop_uphill": "PropUp", "elev_gain": "ElevGain",
    "length_downhill": "LenDown", "prop_downhill": "PropDown", "elev_loss": "ElevLoss",
    "length_flat": "LenFlat", "prop_flat": "PropFlat", "net_elev": "NetElev",
    "avg_slope": "AvgSlope", "slope_sd": "SlopeSD", "min_slope": "MinSlope",
    "max_slope": "MaxSlope", "rms_slope": "RMSSlope",
    "mean_dir": "MeanDir", "resultant": "MeanResLen", "circ_var": "CircVar",
    "ang_var": "AngVar", "circ_sd": "CircSD", "ang_dev": "AngDev",
    "rayleigh_p": "RayleighP", "total_curv": "TotCurv", "fractal_d": "FractalD",
    "sinuosity": "Sinuosity", "min_elev": "MinElev", "min_elev_x": "MinElevX",
    "min_elev_y": "MinElevY", "max_elev": "MaxElev", "max_elev_x": "MaxElevX",
    "max_elev_y": "MaxElevY", "elev_range": "ElevRange", "vert_relief": "VertRelief",
    "reversals": "Reversals",
}
SEG_SHP_FIELD_NAME = {
    "begin_x": "SgBeginX", "begin_y": "SgBeginY", "begin_z": "SgBeginZ",
    "end_x": "SgEndX", "end_y": "SgEndY", "end_z": "SgEndZ",
    "plan_length": "SgPlanLen", "surf_length": "SgSurfLen", "surf_ratio": "SgSurfRat",
    "rel_elev": "SgRelElev", "net_elev": "SgNetElev", "rel_slope": "SgRelSlope",
    "abs_slope": "SgAbsSlope", "land_slope": "SgLandSlp", "land_aspect": "SgLandAsp",
    "traverse": "SgTravAng",
    "bearing": "SgBearing", "deflection": "SgDeflect",
    "abs_deflection": "SgAbsDefl", "inclination": "SgInclin",
    "abs_inclination": "SgAbsIncl", "defl3d": "SgDefl3D", "source_id": "SgSourceID",
    "seg_num": "SgSegNum", "part_index": "SgPartIdx", "slope_class": "SgSlopeCls",
    "station": "SgStation", "mid_x": "SgMidX", "mid_y": "SgMidY", "mid_z": "SgMidZ",
    "mean_elev": "SgMeanElev",
}
assert set(SHP_FIELD_NAME) == set(STAT_KEYS)
assert set(SEG_SHP_FIELD_NAME) == set(SEG_LABEL)
for _tbl in (SHP_FIELD_NAME, SEG_SHP_FIELD_NAME):
    assert all(len(v) <= 10 for v in _tbl.values())
    assert len({v.lower() for v in _tbl.values()}) == len(_tbl)


def default_field_name(key, shapefile=False):
    """Default output field name for an along-line statistic (e.g. 'Surface
    Length (geodesic)' -> 'Surface_Length'; -> 'SurfLen' for a shapefile)."""
    if shapefile:
        return SHP_FIELD_NAME[key]
    if key in _FIELD_NAME_OVERRIDE:
        return _FIELD_NAME_OVERRIDE[key]
    return _label_to_field(STAT_LABEL[key]) or key


def seg_default_field_name(key, shapefile=False):
    """Default output field name for a SEGMENT statistic -- prefixed 'Seg_' so it
    never collides with the along-line tool's field names or copied source
    fields (e.g. 'Surface Length (geodesic)' -> 'Seg_Surface_Length'; ->
    'SgSurfLen' for a shapefile)."""
    if shapefile:
        return SEG_SHP_FIELD_NAME[key]
    return "Seg_" + (_label_to_field(SEG_LABEL[key]) or key)


def is_shapefile_target(path):
    """True when ``path`` names a shapefile, or a dataset that will be created
    as one. The canonical test lives in mahalanobis (shared by every tool that
    chooses field names before creating a dataset)."""
    import mahalanobis as _mh
    return _mh.is_shapefile_target(path)


def field_units_phrase(key, length_unit, elev_unit, slope_unit):
    """Human-readable units for one statistic's output field."""
    kind = STAT_UNIT_KIND[key]
    if kind == "length":
        return length_unit
    if kind == "elevation":
        return "{} (elevation)".format(elev_unit)
    if kind == "degrees":
        return "degrees (azimuth)" if key == "mean_dir" else "degrees"
    if kind == "slope":
        return "degrees" if slope_unit == "Degrees" else "percent"
    if kind == "count":
        return "count (integer)"
    if kind == "coord":
        return "coordinate system units of the polyline dataset"
    return "unitless"


# What each statistic IS, for the field metadata written into the input feature
# class. The same wording as the Topography along Lines help page (Jeff,
# 2026-09-22: the label-plus-units description alone told a reader nothing).
# Plain ASCII: the metadata XML is fine with Unicode, but Pro's metadata view
# renders these in a plain font where R-bar and radicals read poorly.
STAT_DESC = {
    "plan_length": "The sum of the segments' horizontal lengths: the length of the line on the map, measured on the spheroid.",
    "surf_length": "The sum of the segments' three-dimensional lengths: the distance actually traveled over the ground.",
    "surf_ratio": "Surface length divided by planimetric length. Exactly 1 on flat ground, larger over rough or steep terrain; the line-based cousin of the surface ratio in the Surface Area and Ratio tool.",
    "length_uphill": "The summed surface length of the segments that climb, in the direction of travel.",
    "length_downhill": "The summed surface length of the segments that descend, in the direction of travel.",
    "length_flat": "The summed surface length of the segments with exactly zero elevation change.",
    "prop_uphill": "Length Uphill divided by the total surface length. The uphill, downhill and flat proportions sum to 1.",
    "prop_downhill": "Length Downhill divided by the total surface length. The uphill, downhill and flat proportions sum to 1.",
    "prop_flat": "Length Perfectly Flat divided by the total surface length. The uphill, downhill and flat proportions sum to 1.",
    "elev_gain": "The sum of every segment's rise, ignoring the falls: the total climbing.",
    "elev_loss": "The sum of every segment's fall, as a positive number: the total descending.",
    "net_elev": "Gain minus loss: the elevation of the last vertex minus the first, when the chain is unbroken. Otherwise the sum of all such net elevation changes in the multiple pieces of a multipart polyline, or of a line broken by a NoData gap.",
    "vert_relief": "Gain plus loss: all the climbing and all the descending together.",
    "min_elev": "The lowest sampled elevation along the line.",
    "max_elev": "The highest sampled elevation along the line.",
    "elev_range": "The highest sampled elevation along the line minus the lowest.",
    "min_elev_x": "The X coordinate of the lowest sampled point along the line, in the polyline dataset's own coordinate system even when the DEM is in another one.",
    "min_elev_y": "The Y coordinate of the lowest sampled point along the line, in the polyline dataset's own coordinate system even when the DEM is in another one.",
    "max_elev_x": "The X coordinate of the highest sampled point along the line, in the polyline dataset's own coordinate system even when the DEM is in another one.",
    "max_elev_y": "The Y coordinate of the highest sampled point along the line, in the polyline dataset's own coordinate system even when the DEM is in another one.",
    "reversals": "How many times the line switches between climbing and descending, counted within each part; perfectly flat segments are skipped rather than counted as a switch.",
    "avg_slope": "The mean of the segments' absolute slopes, each weighted by its surface length, so a long steep pitch counts for more than a short one.",
    "min_slope": "The shallowest segment slope. Absolute steepness: a segment falling at 20 degrees and one rising at 20 degrees have the same slope.",
    "max_slope": "The steepest segment slope. Absolute steepness: a segment falling at 20 degrees and one rising at 20 degrees have the same slope.",
    "slope_sd": "The surface-length-weighted standard deviation of the segment slopes: how variable the steepness is along the line.",
    "rms_slope": "The surface-length-weighted root mean square of the segment slopes, a measure that emphasizes the steep pitches more than the plain average does.",
    "mean_dir": "The bearing of the resultant of the segment headings (each weighted by its surface length), atan2(S, C), as a compass azimuth.",
    "resultant": "The mean resultant length R-bar of the segment headings, from 0 to 1. A value of 1 means every segment points the same way (a straight line); a value near 0 means the headings cancel out.",
    "circ_var": "Circular variance, 1 - R-bar (Mardia and Jupp 2000; Fisher 1993). 0 for a straight line, up to 1.",
    "ang_var": "Angular variance, 2(1 - R-bar) (Batschelet 1981). 0 up to 2.",
    "circ_sd": "Circular standard deviation, sqrt(-2 ln R-bar) (Mardia and Jupp 2000; Fisher 1993), converted from radians to degrees. Undefined when R-bar = 0.",
    "ang_dev": "Angular deviation, sqrt(2(1 - R-bar)) (Batschelet 1981), converted to degrees; it tops out at 81.03 degrees.",
    "rayleigh_p": "The Rayleigh test of whether the line has a preferred direction at all, calculated with equation 27.4 of Zar (1999) and using the segment count as the sample size. A small p-value means the headings are not uniformly scattered.",
    "total_curv": "The sum of the absolute turning angles between consecutive segments, within each part. A straight line has 0; a line that turns left 30 degrees and then right 30 degrees has 60.",
    "sinuosity": "Surface length divided by the straight three-dimensional distance between the part's first and last vertex (summed over parts for multipart lines). Exactly 1 for a straight line on any slope; larger the more the line meanders. It is the reciprocal of the straightness index of Batschelet (1981) discussed by Benhamou (2004).",
    "fractal_d": "How the measured length of the line grows as you measure it with a finer ruler, by the divider method (Mandelbrot 1967; Nams 2005). 1 for a straight line, approaching 2 for a path so tortuous it fills the plane.",
}
assert set(STAT_DESC) == set(STAT_KEYS)


def field_description(key, length_unit, elev_unit, slope_unit):
    """Field-metadata description for one statistic: what it is (the help
    page's definition), its units, and (for direction-dependent statistics)
    the direction-of-travel convention."""
    txt = "{}. {} Units: {}. Computed by the Topography along Lines tool.".format(
        STAT_LABEL[key], STAT_DESC[key],
        field_units_phrase(key, length_unit, elev_unit, slope_unit))
    if key in DIRECTION_DEPENDENT:
        txt += (" This value describes the line travelling in the direction of "
                "its geometry, from the first vertex to the last (reversing the "
                "line reverses this value).")
    if key in ("min_elev_x", "min_elev_y", "max_elev_x", "max_elev_y"):
        txt += (" If that elevation occurs at more than one sampled point, the "
                "first such point along the line is reported.")
    return txt

# Length units -> meters (the chosen Length Unit; matches Calculate Geometry's
# common set). Reported lengths are meters * (1 / factor)... stored here as
# meters-per-unit so length_out = length_m / METERS_PER_LENGTH[unit].
METERS_PER_LENGTH = {
    "Meters": 1.0,
    "Kilometers": 1000.0,
    "Feet": 0.3048,
    "Miles": 1609.344,
    "Yards": 0.9144,
    "Nautical Miles": 1852.0,
}
LENGTH_UNITS = list(METERS_PER_LENGTH.keys())

# Elevation unit -> meters.
ELEV_METERS = {"Meters": 1.0, "Feet": 0.3048}

# Generic vertical CS (used only when no real one can be inherited) so a
# PolylineZ output still draws correctly in a Scene instead of warning that it
# "has no vertical coordinate system." The unit matches the tool's elevation
# units; the datum is left unknown because we genuinely don't know it.


def _vertcs_wkt(sr):
    """Return the VERTCS[...] WKT of ``sr``'s vertical coordinate system, or
    None if it has none. Esri exports a compound SR as a single first token of
    the form ``PROJCS[...],VERTCS[...]`` (or ``GEOGCS[...],VERTCS[...]``), so the
    vertical part is lifted from there verbatim -- preserving the real datum."""
    try:
        if sr is None or sr.VCS is None:
            return None
        tok = sr.exportToString().split(";")[0]
        i = tok.find("VERTCS[")
        if i < 0:
            return None
        vert = tok[i:]
        # An "unknown" vertical CS carries no information, and a layer with
        # ANY vertical CS is filed by a map under a 3-D transformation, which
        # the map cannot build for an unknown vertical datum -- the layer then
        # draws with NO datum transformation at all (2026-09-23, NAD27 lines
        # in a NAD83 map: 200 m off). So an unknown VCS is treated as none.
        head = vert[:200].lower()
        if 'vertcs["unknown' in head or 'vdatum["unknown' in head:
            return None
        return vert
    except Exception:
        return None


def output_spatial_reference(source_sr, z_source_sr, elev_units):
    """Build the output SR for a PolylineZ result: the source polylines'
    horizontal CS plus a vertical CS. The vertical CS is inherited from
    ``z_source_sr`` (the DEM when the Z was sampled from a DEM, else the source
    lines themselves) so the output carries the elevations' true datum; if
    neither defines one, a generic VCS matching ``elev_units`` is attached so the
    output still displays correctly in a 3-D Scene."""
    import arcpy
    horiz = source_sr.exportToString().split(";")[0]
    # Drop any VERTCS already baked into the horizontal token (source was
    # already compound) so we don't end up with two.
    j = horiz.find(",VERTCS[")
    if j >= 0:
        horiz = horiz[:j]
    # A vertical CS is attached only when one is KNOWN (the DEM's, else the
    # source lines' own); an unknown one is dropped (see _vertcs_wkt), and
    # no generic stand-in is invented, because a layer with a vertical CS
    # that the map cannot transform draws with no datum shift at all.
    vert = _vertcs_wkt(z_source_sr) or _vertcs_wkt(source_sr)
    out = arcpy.SpatialReference()
    out.loadFromString(horiz + (";" + vert if vert else ""))
    return out


def _slope_convert(deg, slope_unit):
    """Convert a single slope value in DEGREES to the chosen slope unit."""
    if slope_unit == "Percent":
        return math.tan(math.radians(deg)) * 100.0
    return deg


def _fractal_dimension(pts_xy_m, base_step):
    """Planimetric fractal dimension of one part by the divider (compass) method
    (Mandelbrot 1967; Nams 2005): the length of the path is measured by walking
    dividers of size delta along it; D = 1 - slope of log(length) vs log(delta)
    over a range of divider sizes. ``pts_xy_m`` is the ordered list of sample
    points (projected meters); ``base_step`` is the smallest divider (the
    sampling interval). Returns D in [1, ~2], or None if the part is too short /
    the fit is undefined."""
    pts = np.asarray(pts_xy_m, dtype=np.float64)
    if pts.shape[0] < 3:
        return None
    seg = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
    total = float(seg.sum())
    if total <= 0:
        return None
    cum = np.concatenate(([0.0], np.cumsum(seg)))   # arc length at each vertex

    def walk_length(delta):
        """Total length measured with a divider of length ``delta`` (walking
        dividers along the arc-length parameterization, interpolating hit
        points between vertices)."""
        pos = 0.0
        x0, y0 = pts[0]
        measured = 0.0
        n = 0
        while pos + delta <= total + 1e-9:
            target = pos + delta
            j = int(np.searchsorted(cum, target))
            if j >= len(cum):
                break
            # interpolate the point at arc length ``target`` between vertex j-1,j
            t = (target - cum[j - 1]) / max(cum[j] - cum[j - 1], 1e-12)
            x1 = pts[j - 1, 0] + t * (pts[j, 0] - pts[j - 1, 0])
            y1 = pts[j - 1, 1] + t * (pts[j, 1] - pts[j - 1, 1])
            measured += math.hypot(x1 - x0, y1 - y0)
            x0, y0, pos = x1, y1, target
            n += 1
        # add the leftover stub back to the true endpoint (fractional divider)
        measured += math.hypot(pts[-1, 0] - x0, pts[-1, 1] - y0)
        return measured, n

    # Divider sizes: geometric progression from the base step up to ~1/4 of the
    # total path, at least 4 sizes needed for a stable regression.
    dmin = max(base_step, total / 512.0)
    dmax = total / 4.0
    if dmax <= dmin * 1.5:
        return None
    n_sizes = max(4, int(round(math.log(dmax / dmin) / math.log(1.5))) + 1)
    deltas = np.geomspace(dmin, dmax, n_sizes)
    logd, logl = [], []
    for d in deltas:
        L, n = walk_length(d)
        if n >= 1 and L > 0:
            logd.append(math.log(d))
            logl.append(math.log(L))
    if len(logd) < 4:
        return None
    slope = np.polyfit(logd, logl, 1)[0]
    D = 1.0 - float(slope)
    return float(min(max(D, 1.0), 2.0))


# --------------------------------------------------------------------------
#  Elevation sampling / segment building (arcpy + GDAL layer)
# --------------------------------------------------------------------------
#  Reduces one polyline feature to the per-part straight-segment ``parts`` that
#  compute_line_statistics consumes. DEM case: sample points are projected into
#  the DEM's coordinate system (never the DEM itself -- projecting a raster
#  resamples/damages it), densified at min(cell width, cell height) as a ground
#  interval plus each part's endpoints, and Z is bilinear-interpolated. Own-Z
#  case: the polyline's own vertices are used directly. Planimetric segment
#  lengths and bearings are geodesic (ellipsoidal), matching the "geodesic"
#  length labels and the rest of the suite.


def _gdal():
    from osgeo import gdal
    return gdal


def load_dem(dem_path, window=None):
    """Open a DEM and return a dict with its array (NaN at NoData), geotransform,
    spatial reference, ground cell sizes, and the sampling interval (meters).

    ``window`` (xmin, ymin, xmax, ymax) in the DEM's OWN coordinate system, when
    given, limits the read to the cells covering that extent (plus a small
    margin) instead of the whole raster -- essential for large regional DEMs, so
    memory scales with the analysis extent rather than the DEM. The returned
    geotransform is shifted to the window origin so sampling is unaffected."""
    import arcpy
    import mahalanobis as _mh
    gdal = _gdal()
    # GDAL cannot open every raster by its catalog path -- notably rasters inside
    # a file geodatabase (e.g. ...\DEM.gdb\name). Use the toolbox's proven
    # resolver, which returns a GDAL-openable source (OpenFileGDB subdataset
    # syntax, or a scratch GeoTIFF export) plus a temp copy to delete afterward.
    src, tmp = _mh._gdal_source_path(dem_path, arcpy.env.scratchFolder)
    ds = gdal.Open(src)
    if ds is None:
        if tmp:
            try:
                arcpy.management.Delete(tmp)
            except Exception:
                pass
        raise arcpy.ExecuteError("Could not open the DEM: {}".format(dem_path))
    band = ds.GetRasterBand(1)
    gt = ds.GetGeoTransform()
    full_w, full_h = ds.RasterXSize, ds.RasterYSize
    nodata = band.GetNoDataValue()

    xoff, yoff, xsize, ysize = 0, 0, full_w, full_h
    if window is not None and gt[2] == 0 and gt[4] == 0:
        # Cell window covering the requested extent, padded for bilinear reach.
        margin = 4
        xmin, ymin, xmax, ymax = window
        cols = [(xmin - gt[0]) / gt[1], (xmax - gt[0]) / gt[1]]
        rows = [(ymax - gt[3]) / gt[5], (ymin - gt[3]) / gt[5]]   # gt[5] < 0
        c0 = max(0, int(math.floor(min(cols))) - margin)
        c1 = min(full_w, int(math.ceil(max(cols))) + margin)
        r0 = max(0, int(math.floor(min(rows))) - margin)
        r1 = min(full_h, int(math.ceil(max(rows))) + margin)
        if c1 <= c0 or r1 <= r0:
            ds = None
            if tmp:
                try:
                    arcpy.management.Delete(tmp)
                except Exception:
                    pass
            raise arcpy.ExecuteError(
                "The polylines do not fall within the DEM's extent.")
        xoff, yoff, xsize, ysize = c0, r0, c1 - c0, r1 - r0
        gt = (gt[0] + c0 * gt[1], gt[1], 0.0, gt[3] + r0 * gt[5], 0.0, gt[5])

    try:
        arr = band.ReadAsArray(xoff, yoff, xsize, ysize).astype(np.float64)
    except (MemoryError, RuntimeError) as e:
        ds = None
        if tmp:
            try:
                arcpy.management.Delete(tmp)
            except Exception:
                pass
        raise arcpy.ExecuteError(
            "Could not read the DEM into memory over the analysis extent -- it "
            "may be too large. Try a smaller subset of lines or a coarser DEM. "
            "({})".format(e))
    if nodata is not None:
        arr = np.where(arr == nodata, np.nan, arr)
    wv = _mh.mask_band_valid(band, np.ones(arr.shape, bool), xoff, yoff)
    if not wv.all():
        arr[~wv] = np.nan       # mask-band-only NoData (gdb GRID fill trap)
    ds = None
    if tmp:                                    # scratch export no longer needed
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass
    sr = arcpy.Describe(dem_path).spatialReference
    nrows = arr.shape[0]
    if sr.type == "Projected":
        mpu = float(sr.metersPerUnit or 1.0)
        cellw_m = abs(gt[1]) * mpu
        cellh_m = abs(gt[5]) * mpu
    else:                                     # geographic: degrees -> ground m
        lat0 = math.radians(gt[3] + (nrows / 2.0) * gt[5])
        cellh_m = abs(gt[5]) * 111132.0
        cellw_m = abs(gt[1]) * 111320.0 * max(math.cos(lat0), 1e-6)
    return {"arr": arr, "gt": gt, "sr": sr, "nodata": nodata,
            "cellw_m": cellw_m, "cellh_m": cellh_m,
            "interval_m": max(min(cellw_m, cellh_m), 1e-6)}


def same_datum(a, b):
    """True when two spatial references share a datum."""
    try:
        return a.GCS.datumName == b.GCS.datumName
    except Exception:
        return True


def datum_transformation(src_sr, dst_sr, extent=None):
    """The datum transformation used between the lines and the DEM (None when
    they share a datum): the toolbox-wide choice from clip_tools."""
    import clip_tools as _ct
    return _ct.transformation_name(src_sr, dst_sr, extent)


def _same_sr(a, b):
    try:
        return a.name == b.name and a.factoryCode == b.factoryCode
    except Exception:
        return False


def project_geometry(geom, dst_sr, extent=None):
    """projectAs WITH the datum transformation (see clip_tools.project_geometry)."""
    import clip_tools as _ct
    return _ct.project_geometry(geom, dst_sr, extent)


def lines_window_in_dem_crs(in_lines, dem_sr):
    """Bounding box of the input lines' extent expressed in the DEM's coordinate
    system, for use as ``load_dem``'s window so only the relevant part of a
    large DEM is read. The extent rectangle is densified before projection so
    the projected bounding box truly contains the lines even across very
    different coordinate systems. Returns (xmin, ymin, xmax, ymax), or None if
    it can't be determined (the whole DEM is then read)."""
    import arcpy
    try:
        d = arcpy.Describe(in_lines)
        ext = d.extent
        if ext is None or ext.XMin is None:
            return None
        lsr = d.spatialReference
        if (lsr and dem_sr and lsr.name and dem_sr.name
                and lsr.name != dem_sr.name):
            # Pad the box so a single straight east-west or north-south
            # line (a zero-height or zero-width extent) still makes a real
            # polygon; a degenerate one projects to a NaN extent.
            pad = max(ext.width, ext.height, 1.0) * 0.01
            x0, y0, x1, y1 = ext.XMin - pad, ext.YMin - pad, ext.XMax + pad, ext.YMax + pad
            poly = arcpy.Polygon(arcpy.Array([
                arcpy.Point(x0, y0), arcpy.Point(x1, y0), arcpy.Point(x1, y1),
                arcpy.Point(x0, y1), arcpy.Point(x0, y0)]), lsr)
            step = max((x1 - x0 + y1 - y0) / 40.0, 1e-9)
            try:
                poly = poly.densify("DISTANCE", step, step)
            except Exception:
                pass
            ext = project_geometry(poly, dem_sr, ext).extent
        vals = (ext.XMin, ext.YMin, ext.XMax, ext.YMax)
        if any(v is None or not math.isfinite(v) for v in vals):
            return None
        return vals
    except Exception:
        return None


def _bilinear(arr, gt, x, y):
    """Bilinear-interpolate the raster ``arr`` (NaN = NoData) at map coordinate
    (x, y) in the raster's own CRS. Returns NaN if out of bounds or if any of
    the four surrounding cells is NoData."""
    fcol = (x - gt[0]) / gt[1] - 0.5          # fractional index in cell-centers
    frow = (y - gt[3]) / gt[5] - 0.5
    c0 = int(math.floor(fcol))
    r0 = int(math.floor(frow))
    tx = fcol - c0
    ty = frow - r0
    h, w = arr.shape
    if c0 < 0 or r0 < 0 or c0 + 1 >= w or r0 + 1 >= h:
        return float("nan")
    v00 = arr[r0, c0]
    v01 = arr[r0, c0 + 1]
    v10 = arr[r0 + 1, c0]
    v11 = arr[r0 + 1, c0 + 1]
    if not (np.isfinite(v00) and np.isfinite(v01) and np.isfinite(v10) and np.isfinite(v11)):
        return float("nan")
    top = v00 * (1 - tx) + v01 * tx
    bot = v10 * (1 - tx) + v11 * tx
    return float(top * (1 - ty) + bot * ty)


def _landscape_grids(dem, elev_m_per_unit):
    """Slope (degrees) and aspect (as unit-vector sin/cos grids) of the loaded
    DEM window by Horn's 3 x 3 method, the same formulation as Esri's Slope and
    Aspect tools: dz/dx = ((c + 2f + i) - (a + 2d + g)) / (8 * cell width),
    dz/dy = ((g + 2h + i) - (a + 2b + c)) / (8 * cell height), with a..c the
    northern row. Cell sizes are ground meters; for a geographic DEM the cell
    width is computed per row from its latitude. Cached on the ``dem`` dict.
    Aspect is undefined (NaN) where the surface is flat."""
    if "land_slope_deg" in dem:
        return
    arr, gt = dem["arr"], dem["gt"]
    H, W = arr.shape
    z = arr * float(elev_m_per_unit)          # NaN stays NaN
    if dem["sr"].type == "Projected":
        cw = np.full(H, float(dem["cellw_m"]))
    else:
        lat = gt[3] + (np.arange(H) + 0.5) * gt[5]
        cw = abs(gt[1]) * 111320.0 * np.maximum(np.cos(np.radians(lat)), 1e-6)
    ch = float(dem["cellh_m"])
    pad = np.pad(z, 1, mode="edge")
    a, b, c = pad[:-2, :-2], pad[:-2, 1:-1], pad[:-2, 2:]
    d, f = pad[1:-1, :-2], pad[1:-1, 2:]
    g, h, i = pad[2:, :-2], pad[2:, 1:-1], pad[2:, 2:]
    with np.errstate(invalid="ignore", divide="ignore"):
        dzdx = ((c + 2.0 * f + i) - (a + 2.0 * d + g)) / (8.0 * cw[:, None])
        dzdy = ((g + 2.0 * h + i) - (a + 2.0 * b + c)) / (8.0 * ch)
        slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
        asp = np.degrees(np.arctan2(dzdy, -dzdx))
        asp = np.where(asp < 0.0, 90.0 - asp,
                       np.where(asp > 90.0, 360.0 - asp + 90.0, 90.0 - asp))
        flat = (dzdx == 0.0) & (dzdy == 0.0)
        asp = np.where(flat, np.nan, asp)
        rad = np.radians(asp)
        dem["land_slope_deg"] = slope
        dem["land_asp_sin"] = np.sin(rad)
        dem["land_asp_cos"] = np.cos(rad)


def _landscape_at(dem, pts):
    """Average landscape slope (degrees) and compass aspect (degrees) over the
    given (x, y) points in the DEM's coordinate system: slope is the mean of
    the finite bilinear samples, aspect the vector mean of the finite samples.
    Either is None when no sample is defined."""
    gt = dem["gt"]
    slopes, sins, coss = [], [], []
    for x, y in pts:
        s = _bilinear(dem["land_slope_deg"], gt, x, y)
        if np.isfinite(s):
            slopes.append(s)
        sn = _bilinear(dem["land_asp_sin"], gt, x, y)
        cs = _bilinear(dem["land_asp_cos"], gt, x, y)
        if np.isfinite(sn) and np.isfinite(cs):
            sins.append(sn)
            coss.append(cs)
    slope = (sum(slopes) / len(slopes)) if slopes else None
    aspect = None
    if sins:
        ms, mc = sum(sins) / len(sins), sum(coss) / len(coss)
        if math.hypot(ms, mc) > 1e-9:
            aspect = math.degrees(math.atan2(ms, mc)) % 360.0
    return slope, aspect


def _local_xy_m(points, sr):
    """Approximate metric planar coordinates for the fractal divider walk. For a
    projected SR, scale to meters; for geographic, a local equirectangular
    projection about the first point."""
    import math as _m
    if sr.type == "Projected":
        mpu = float(sr.metersPerUnit or 1.0)
        return [(p.X * mpu, p.Y * mpu) for p in points]
    lon0 = math.radians(points[0].X)
    lat0 = math.radians(points[0].Y)
    R = 6378137.0
    out = []
    for p in points:
        out.append((R * (math.radians(p.X) - lon0) * _m.cos(lat0),
                    R * (math.radians(p.Y) - lat0)))
    return out


def _densify_geodesic(pts_in, sr, interval_m):
    """Sample points along the vertex sequence ``pts_in`` (arcpy Points), KEEPING
    every original vertex and inserting interior points so no gap exceeds
    ``interval_m`` (geodesic meters). Preserving the real vertices means a
    straight run stays collinear (no spurious turning) and the true bend shows
    up cleanly at each vertex -- important both for the segment tool's turn
    angles and for honest per-segment weighting of the along-line statistics."""
    import arcpy
    out = []
    for j in range(len(pts_in) - 1):
        a, b = pts_in[j], pts_in[j + 1]
        sub = arcpy.Polyline(arcpy.Array([a, b]), sr)
        d = sub.getLength("GEODESIC", "METERS")
        k = max(1, int(math.ceil(d / interval_m)))
        for m in range(k):                       # a + interior pts; b starts next
            out.append(sub.positionAlongLine(m / float(k), True).firstPoint)
    out.append(pts_in[-1])                        # final vertex
    return out


def _densify_if_curved(shape, interval_m=None):
    """TRUE-CURVE polylines (arcs) expose only their arc endpoints when the
    vertices are read, so downstream sampling would follow the CHORD of each
    arc. Replace curves with straight segments: at the DEM sampling interval
    when one is known (GEODESIC on geographic data), else at 1/200 of the
    line's length. No-op for ordinary vertex polylines."""
    if not getattr(shape, "hasCurves", False):
        return shape
    try:
        sr = shape.spatialReference
        if interval_m:
            method = "GEODESIC" if (sr and sr.type == "Geographic") else "DISTANCE"
            d = max(float(interval_m), 0.01)
        else:
            method = "DISTANCE"
            d = max(shape.length / 200.0, 1e-6)
        return shape.densify(method, d, d)
    except Exception:
        return shape


def build_parts(shape, source, dem=None, elev_m_per_unit=1.0):
    """Reduce one polyline ``shape`` (arcpy Polyline, SHAPE@) to the per-part
    segment dicts compute_line_statistics needs. ``source`` is "DEM" or "Z".
    Returns (parts, diag) where diag counts NoData drops and short-for-fractal
    parts. All returned lengths/elevations are in METERS."""
    import arcpy
    parts = []
    diag = {"nodata_segments": 0, "empty_parts": 0}

    shape = _densify_if_curved(
        shape, dem["interval_m"] if source == "DEM" and dem else None)
    if source == "DEM":
        work_sr = dem["sr"]
        interval_m = dem["interval_m"]
        geom = (project_geometry(shape, work_sr)
                if shape.spatialReference and not _same_sr(shape.spatialReference, work_sr)
                else shape)
    else:
        work_sr = shape.spatialReference
        interval_m = None

    for pi in range(geom.partCount if source == "DEM" else shape.partCount):
        src_geom = geom if source == "DEM" else shape
        part_arr = src_geom.getPart(pi)
        pts_in = [pt for pt in part_arr if pt is not None]
        if len(pts_in) < 2:
            diag["empty_parts"] += 1
            continue

        if source == "DEM":
            # Sample at the DEM interval BETWEEN original vertices while keeping
            # every vertex, so real bends are preserved (see _densify_geodesic).
            samples = _densify_geodesic(pts_in, work_sr, interval_m)
            z_raw = [_bilinear(dem["arr"], dem["gt"], p.X, p.Y) for p in samples]
        else:
            samples = pts_in
            z_raw = [p.Z for p in samples]

        z_m = [(zr * elev_m_per_unit if zr is not None and np.isfinite(zr)
                else float("nan")) for zr in z_raw]

        seg_plan, seg_dz, seg_bear, keep_pts, keep_z = [], [], [], [], []
        for i in range(len(samples) - 1):
            p0, p1 = samples[i], samples[i + 1]
            z0, z1 = z_m[i], z_m[i + 1]
            if not (np.isfinite(z0) and np.isfinite(z1)):
                diag["nodata_segments"] += 1
                continue
            g0 = arcpy.PointGeometry(p0, work_sr)
            g1 = arcpy.PointGeometry(p1, work_sr)
            ang, dist = g0.angleAndDistanceTo(g1, "GEODESIC")
            seg_plan.append(dist)
            seg_dz.append(z1 - z0)
            seg_bear.append(ang % 360.0)
            if not keep_pts:
                keep_pts.append(p0)
                keep_z.append(z0)
            keep_pts.append(p1)
            keep_z.append(z1)
        if not seg_plan:
            diag["empty_parts"] += 1
            continue

        g_first = arcpy.PointGeometry(keep_pts[0], work_sr)
        g_last = arcpy.PointGeometry(keep_pts[-1], work_sr)
        _a, chord_h = g_first.angleAndDistanceTo(g_last, "GEODESIC")
        parts.append({
            "seg_plan_m": np.array(seg_plan),
            "seg_dz_m": np.array(seg_dz),
            "seg_bearing": np.array(seg_bear),
            "z_m": np.array(keep_z),
            "pts_xy_m": _local_xy_m(keep_pts, work_sr),
            "chord_horiz_m": chord_h,
            "chord_dz_m": keep_z[-1] - keep_z[0],
            "base_step_m": (interval_m if source == "DEM"
                            else float(np.median(seg_plan))),
            # For the min/max-elevation coordinate statistics: the kept sample
            # points (in work_sr -- the DEM's SR when sampling a DEM) plus both
            # references, so the winning point can be reported in the POLYLINE
            # dataset's own coordinate system. Only the one winning point is
            # ever projected, so keeping the whole list costs nothing extra.
            "pts_work": keep_pts,
            "work_sr": work_sr,
            "dataset_sr": shape.spatialReference or work_sr,
        })
    return parts, diag


def compute_line_statistics(parts, length_unit="Meters", elev_unit="Meters",
                            slope_unit="Degrees", wanted=None):
    """Compute the selected surface statistics for ONE polyline feature.

    ``parts``: list of parts (disconnected pieces). Each part is a dict with
    per-segment arrays already reduced to straight 3-D segments, all in METERS:
        seg_plan_m   : geodesic horizontal length of each segment
        seg_dz_m     : signed elevation change of each segment (end - start)
        seg_bearing  : planimetric bearing of each segment, degrees (0=N, 90=E)
        z_m          : sampled elevation at each vertex (length = n_seg + 1)
        pts_xy_m     : projected planar sample points (for the fractal divider)
        chord_horiz_m: geodesic distance between the part's first & last vertex
        chord_dz_m   : elevation difference between first & last vertex
        base_step_m  : the sampling interval (smallest fractal divider)
    and, optionally (needed only for the min/max-elevation COORDINATE stats;
    absent -> those stats come back NaN):
        pts_work     : the kept sample points (arcpy Point, in work_sr)
        work_sr      : the SR the samples are in
        dataset_sr   : the polyline dataset's SR (coordinates are reported here)
    ``wanted``: iterable of stat keys to compute (default: all).

    Returns {stat_key: value} with values already in reporting units (NaN where
    undefined, e.g. a proportion of a zero-length line)."""
    keys = set(STAT_KEYS if wanted is None else wanted)
    lm = METERS_PER_LENGTH[length_unit]          # meters per length unit
    em = ELEV_METERS.get(elev_unit, 1.0)         # meters per elevation unit

    def to_len(x_m):
        return x_m / lm

    def to_elev(x_m):
        return x_m / em

    # Flatten segments across parts (statistics that don't care about part
    # boundaries) while keeping per-part structure for those that do.
    plan = np.concatenate([p["seg_plan_m"] for p in parts]) if parts else np.array([])
    dz = np.concatenate([p["seg_dz_m"] for p in parts]) if parts else np.array([])
    bearing = np.concatenate([p["seg_bearing"] for p in parts]) if parts else np.array([])
    plan = plan.astype(np.float64)
    dz = dz.astype(np.float64)
    bearing = bearing.astype(np.float64)
    surf = np.hypot(plan, dz)                     # surface (3-D) segment lengths
    allz = np.concatenate([np.asarray(p["z_m"], float) for p in parts]) if parts else np.array([])

    total_plan = float(plan.sum())
    total_surf = float(surf.sum())
    up = dz > 0
    down = dz < 0
    flat = dz == 0
    # Slope of each segment in degrees (unit-independent: dz/plan is a ratio).
    with np.errstate(invalid="ignore", divide="ignore"):
        slope_deg = np.degrees(np.arctan2(np.abs(dz), plan))   # 0..90, positive
    out = {}

    def has(k):
        return k in keys

    if has("plan_length"):
        out["plan_length"] = to_len(total_plan)
    if has("surf_length"):
        out["surf_length"] = to_len(total_surf)
    if has("surf_ratio"):
        out["surf_ratio"] = (total_surf / total_plan) if total_plan > 0 else float("nan")
    if has("length_uphill"):
        out["length_uphill"] = to_len(float(surf[up].sum()))
    if has("length_downhill"):
        out["length_downhill"] = to_len(float(surf[down].sum()))
    if has("length_flat"):
        out["length_flat"] = to_len(float(surf[flat].sum()))
    if has("prop_uphill"):
        out["prop_uphill"] = (float(surf[up].sum()) / total_surf) if total_surf > 0 else float("nan")
    if has("prop_downhill"):
        out["prop_downhill"] = (float(surf[down].sum()) / total_surf) if total_surf > 0 else float("nan")
    if has("prop_flat"):
        out["prop_flat"] = (float(surf[flat].sum()) / total_surf) if total_surf > 0 else float("nan")
    gain_m = float(dz[up].sum())
    loss_m = float(-dz[down].sum())
    if has("elev_gain"):
        out["elev_gain"] = to_elev(gain_m)
    if has("elev_loss"):
        out["elev_loss"] = to_elev(loss_m)
    if has("net_elev"):
        out["net_elev"] = to_elev(gain_m - loss_m)
    if has("vert_relief"):
        out["vert_relief"] = to_elev(gain_m + loss_m)
    if has("min_elev"):
        out["min_elev"] = to_elev(float(allz.min())) if allz.size else float("nan")
    if has("max_elev"):
        out["max_elev"] = to_elev(float(allz.max())) if allz.size else float("nan")
    if has("elev_range"):
        out["elev_range"] = to_elev(float(allz.max() - allz.min())) if allz.size else float("nan")

    # --- coordinates of the lowest / highest sampled point -----------------
    # np.argmin/argmax return the FIRST occurrence, so a tie reports the first
    # such point along the line (parts in order, vertices in travel order).
    # Coordinates are reported in the POLYLINE dataset's coordinate system;
    # samples live in work_sr (the DEM's SR when sampling a DEM), so only the
    # single winning point is projected back.
    if keys & {"min_elev_x", "min_elev_y", "max_elev_x", "max_elev_y"}:
        def _coords_at(flat_idx):
            off = 0
            for p in parts:
                n = len(p["z_m"])
                if flat_idx < off + n:
                    pts = p.get("pts_work")
                    if pts is None:
                        return float("nan"), float("nan")
                    pt = pts[flat_idx - off]
                    wsr, dsr = p.get("work_sr"), p.get("dataset_sr")
                    try:
                        if (wsr is not None and dsr is not None
                                and not _same_sr(wsr, dsr)):
                            import arcpy
                            g = project_geometry(arcpy.PointGeometry(pt, wsr), dsr)
                            return (float(g.centroid.X), float(g.centroid.Y))
                    except Exception:
                        pass
                    return float(pt.X), float(pt.Y)
                off += n
            return float("nan"), float("nan")

        if allz.size:
            min_xy = _coords_at(int(np.argmin(allz)))
            max_xy = _coords_at(int(np.argmax(allz)))
        else:
            min_xy = max_xy = (float("nan"), float("nan"))
        if has("min_elev_x"):
            out["min_elev_x"] = min_xy[0]
        if has("min_elev_y"):
            out["min_elev_y"] = min_xy[1]
        if has("max_elev_x"):
            out["max_elev_x"] = max_xy[0]
        if has("max_elev_y"):
            out["max_elev_y"] = max_xy[1]

    # --- slope statistics (surface-length weighted, |slope|) ---------------
    if keys & {"avg_slope", "slope_sd", "min_slope", "max_slope", "rms_slope"}:
        w = surf
        wsum = float(w.sum())
        if wsum > 0 and slope_deg.size:
            mean_deg = float(np.sum(w * slope_deg) / wsum)
            var_deg = float(np.sum(w * (slope_deg - mean_deg) ** 2) / wsum)
            rms_deg = float(math.sqrt(np.sum(w * slope_deg ** 2) / wsum))
            if has("avg_slope"):
                out["avg_slope"] = _slope_convert(mean_deg, slope_unit)
            if has("slope_sd"):
                out["slope_sd"] = math.sqrt(max(var_deg, 0.0))     # degrees only
            if has("rms_slope"):
                out["rms_slope"] = rms_deg                          # degrees only
            if has("min_slope"):
                out["min_slope"] = _slope_convert(float(slope_deg.min()), slope_unit)
            if has("max_slope"):
                out["max_slope"] = _slope_convert(float(slope_deg.max()), slope_unit)
        else:
            for k in ("avg_slope", "slope_sd", "min_slope", "max_slope", "rms_slope"):
                if has(k):
                    out[k] = float("nan")

    # --- directional statistics (surface-length weighted) ------------------
    if keys & {"mean_dir", "resultant", "circ_var", "ang_var", "circ_sd",
               "ang_dev", "rayleigh_p"}:
        br = np.radians(bearing)
        w = surf
        S = float(np.sum(w * np.sin(br)))
        C = float(np.sum(w * np.cos(br)))
        wsum = float(w.sum())
        R = math.hypot(S, C)
        # Mean resultant length is bounded to [0, 1]; floating-point can nudge a
        # perfectly focused set (e.g. a straight line, all bearings equal) a hair
        # above 1, which would make -2*ln(Rbar) negative below.
        Rbar = min(max(R / wsum, 0.0), 1.0) if wsum > 0 else float("nan")
        if has("mean_dir"):
            out["mean_dir"] = (math.degrees(math.atan2(S, C)) % 360.0) if wsum > 0 else float("nan")
        if has("resultant"):
            out["resultant"] = Rbar
        if has("circ_var"):
            out["circ_var"] = (1.0 - Rbar) if wsum > 0 else float("nan")
        if has("ang_var"):
            out["ang_var"] = (2.0 * (1.0 - Rbar)) if wsum > 0 else float("nan")
        if has("circ_sd"):
            out["circ_sd"] = (math.degrees(math.sqrt(max(-2.0 * math.log(Rbar), 0.0)))
                              if wsum > 0 and Rbar > 0 else float("nan"))
        if has("ang_dev"):
            out["ang_dev"] = (math.degrees(math.sqrt(max(2.0 * (1.0 - Rbar), 0.0)))
                              if wsum > 0 else float("nan"))
        if has("rayleigh_p"):
            # Rayleigh test of uniformity using the segment count as n. The
            # p-value is Zar 1999 eq. 27.4 (derived from Greenwood and
            # Durand 1955), with Rayleigh's R = n * Rbar:
            #   P = exp[ sqrt(1 + 4n + 4(n^2 - R^2)) - (1 + 2n) ]
            # Checked against simulation 2026-09-20: within 0.0002 of the
            # true p at p = 0.05 for n >= 8.
            n = int(plan.size)
            if n > 0 and wsum > 0:
                Rn = n * Rbar
                p = math.exp(math.sqrt(1.0 + 4.0 * n + 4.0 * (n * n - Rn * Rn))
                             - (1.0 + 2.0 * n))
                out["rayleigh_p"] = float(min(max(p, 0.0), 1.0))
            else:
                out["rayleigh_p"] = float("nan")

    # --- total curvature: sum of |turn angle| between consecutive segments,
    #     within each part only (never across a part break) -----------------
    if has("total_curv"):
        tot = 0.0
        any_turn = False
        for p in parts:
            b = np.asarray(p["seg_bearing"], float)
            if b.size >= 2:
                d = np.diff(b)
                d = (d + 180.0) % 360.0 - 180.0     # wrap to (-180, 180]
                tot += float(np.sum(np.abs(d)))
                any_turn = True
        out["total_curv"] = tot if any_turn else float("nan")

    # --- reversal count: uphill<->downhill switches, within each part ------
    if has("reversals"):
        cnt = 0
        for p in parts:
            d = np.asarray(p["seg_dz_m"], float)
            sign = np.sign(d)
            sign = sign[sign != 0]                  # ignore perfectly flat segs
            if sign.size >= 2:
                cnt += int(np.sum(sign[1:] != sign[:-1]))
        out["reversals"] = cnt

    # --- 3D sinuosity: total surface length / sum of per-part 3D chords ----
    if has("sinuosity"):
        chord = 0.0
        for p in parts:
            chord += math.hypot(float(p["chord_horiz_m"]), float(p["chord_dz_m"]))
        out["sinuosity"] = (total_surf / chord) if chord > 0 else float("nan")

    # --- fractal dimension: per part (divider), surface-length weighted ----
    if has("fractal_d"):
        num = 0.0
        den = 0.0
        for p in parts:
            D = _fractal_dimension(p["pts_xy_m"], float(p["base_step_m"]))
            if D is not None:
                wlen = float(np.hypot(np.asarray(p["seg_plan_m"], float),
                                      np.asarray(p["seg_dz_m"], float)).sum())
                num += D * wlen
                den += wlen
        out["fractal_d"] = (num / den) if den > 0 else float("nan")

    return out


# --------------------------------------------------------------------------
#  Per-segment records for the companion segment-feature-class tool
# --------------------------------------------------------------------------

def build_segments(shape, source, dem, elev_m_per_unit, source_sr, parent_oid,
                   landscape=False, vertex_only=False):
    """Ordered per-segment records for ONE polyline feature, for the segment
    tool. Coordinates are in the SOURCE dataset's coordinate system; lengths and
    elevation change are carried in METERS (converted at write time by
    ``seg_value``); the three angle fields (deflection, inclination, 3D
    deflection) are None on the first segment of each part (no predecessor).
    Each record's ``geom`` is [(x, y, z), (x, y, z)] for a Z-aware output line.
    Segments touching NoData elevation are dropped and break the angle chain.
    With ``landscape`` True (and a DEM given, whatever the elevation source),
    each record also carries ``land_slope`` / ``land_aspect`` / ``traverse``:
    the terrain's own slope and downslope direction under the segment,
    averaged over points along it (both ends plus interior points at the
    DEM's cell interval), and the angle between the travel direction and the
    fall line (0 straight up/down, 90 along the contour); None where undefined.
    With ``vertex_only`` True the DEM is sampled at the original vertices only,
    giving one segment per vertex pair (e.g. one per GPS step) instead of one
    per DEM cell crossed."""
    import arcpy
    if landscape and dem is not None:
        _landscape_grids(dem, elev_m_per_unit)
    if source == "DEM":
        work_sr = dem["sr"]
        interval_m = dem["interval_m"]
        geom = (project_geometry(shape, work_sr)
                if (shape.spatialReference
                    and not _same_sr(shape.spatialReference, work_sr))
                else shape)
    else:
        work_sr = source_sr
        interval_m = None
        geom = shape

    segs = []
    seg_num = 0
    geom = _densify_if_curved(
        geom, interval_m if source == "DEM" else None)
    for pi in range(geom.partCount):
        pts_in = [pt for pt in geom.getPart(pi) if pt is not None]
        if len(pts_in) < 2:
            continue
        if source == "DEM":
            samples_w = (list(pts_in) if vertex_only
                         else _densify_geodesic(pts_in, work_sr, interval_m))
            z_elev = [_bilinear(dem["arr"], dem["gt"], p.X, p.Y) for p in samples_w]
        else:
            samples_w = pts_in
            z_elev = [p.Z for p in samples_w]

        # Sample coordinates in the SOURCE CRS (project the whole vertex string
        # at once when the sampling frame differs).
        if not _same_sr(work_sr, source_sr):
            proj = project_geometry(arcpy.Polyline(arcpy.Array(samples_w), work_sr), source_sr)
            parr = proj.getPart(0)
            samples_s = [parr[k] for k in range(len(parr))]
        else:
            samples_s = samples_w
        # Sample coordinates in the DEM's CRS, for the landscape attributes
        # (identical to samples_w when the DEM is the sampling frame).
        samples_d = None
        if landscape and dem is not None:
            if not _same_sr(work_sr, dem["sr"]):
                projd = project_geometry(arcpy.Polyline(arcpy.Array(samples_w), work_sr), dem["sr"])
                darr = projd.getPart(0)
                samples_d = [(darr[k].X, darr[k].Y) for k in range(len(darr))]
            else:
                samples_d = [(p.X, p.Y) for p in samples_w]

        station_m = 0.0
        prev = None                       # (bearing, pitch, vx, vy, vz)
        for i in range(len(samples_s) - 1):
            z0, z1 = z_elev[i], z_elev[i + 1]
            if not (z0 is not None and z1 is not None
                    and np.isfinite(z0) and np.isfinite(z1)):
                prev = None               # gap breaks the angle chain
                continue
            p0, p1 = samples_s[i], samples_s[i + 1]
            g0 = arcpy.PointGeometry(p0, source_sr)
            g1 = arcpy.PointGeometry(p1, source_sr)
            bearing, plan_m = g0.angleAndDistanceTo(g1, "GEODESIC")
            bearing %= 360.0
            dz_m = (z1 - z0) * elev_m_per_unit
            surf_m = math.hypot(plan_m, dz_m)
            pitch = math.degrees(math.atan2(dz_m, plan_m))
            br = math.radians(bearing)
            vx, vy, vz = plan_m * math.sin(br), plan_m * math.cos(br), dz_m
            defl = incl = defl3d = None
            if prev is not None:
                pb, pp, pvx, pvy, pvz = prev
                defl = (bearing - pb + 180.0) % 360.0 - 180.0
                incl = pitch - pp
                m1 = math.sqrt(pvx * pvx + pvy * pvy + pvz * pvz)
                m2 = math.sqrt(vx * vx + vy * vy + vz * vz)
                if m1 > 0 and m2 > 0:
                    c = max(-1.0, min(1.0, (pvx * vx + pvy * vy + pvz * vz) / (m1 * m2)))
                    defl3d = math.degrees(math.acos(c))
            land_slope = land_aspect = traverse = None
            if samples_d is not None:
                (dx0, dy0), (dx1, dy1) = samples_d[i], samples_d[i + 1]
                # both ends plus interior points at the DEM interval (at least
                # the midpoint), so a long vertex-to-vertex step is averaged
                # along its length rather than judged by three points.
                n = max(2, int(math.ceil(plan_m / dem["interval_m"])))
                pts_l = [(dx0 + (dx1 - dx0) * k / n, dy0 + (dy1 - dy0) * k / n)
                         for k in range(n + 1)]
                land_slope, land_aspect = _landscape_at(dem, pts_l)
                if land_aspect is not None:
                    d = abs((bearing - land_aspect + 180.0) % 360.0 - 180.0)   # 0..180
                    traverse = min(d, 180.0 - d)                             # 0..90
            seg_num += 1
            segs.append({
                "land_slope": land_slope, "land_aspect": land_aspect,
                "traverse": traverse,
                "begin_x": p0.X, "begin_y": p0.Y, "begin_z": z0,
                "end_x": p1.X, "end_y": p1.Y, "end_z": z1,
                "plan_m": plan_m, "surf_m": surf_m, "dz_m": dz_m,
                "bearing": bearing, "pitch": pitch,
                "deflection": defl, "inclination": incl, "defl3d": defl3d,
                "seg_num": seg_num, "part_index": pi, "station_m": station_m,
                "source_id": parent_oid,
                "geom": [(p0.X, p0.Y, z0), (p1.X, p1.Y, z1)],
            })
            station_m += plan_m
            prev = (bearing, pitch, vx, vy, vz)
    return segs


def seg_value(rec, key, length_unit="Meters", elev_unit="Meters",
              slope_unit="Degrees"):
    """The reporting value of one segment field ``key`` for a record from
    ``build_segments`` (converted to the chosen units). Returns None where the
    value is undefined (e.g. an angle on the first segment of a part)."""
    if key in ("begin_x", "begin_y", "end_x", "end_y"):
        return rec[key]                           # source-CRS map units
    if key == "mid_x":
        return (rec["begin_x"] + rec["end_x"]) / 2.0
    if key == "mid_y":
        return (rec["begin_y"] + rec["end_y"]) / 2.0
    if key in ("begin_z", "end_z"):
        return rec[key]                           # elevation units (as sampled)
    if key == "mid_z":
        return (rec["begin_z"] + rec["end_z"]) / 2.0
    if key == "mean_elev":
        return (rec["begin_z"] + rec["end_z"]) / 2.0
    lm = METERS_PER_LENGTH.get(length_unit, 1.0)
    if key == "plan_length":
        return rec["plan_m"] / lm
    if key == "surf_length":
        return rec["surf_m"] / lm
    if key == "station":
        return rec["station_m"] / lm
    if key == "surf_ratio":
        return (rec["surf_m"] / rec["plan_m"]) if rec["plan_m"] > 0 else None
    em = ELEV_METERS.get(elev_unit, 1.0)
    if key == "rel_elev":
        return rec["dz_m"] / em
    if key == "net_elev":
        return abs(rec["dz_m"]) / em
    if key == "rel_slope":
        return _slope_convert(rec["pitch"], slope_unit)
    if key == "abs_slope":
        return _slope_convert(abs(rec["pitch"]), slope_unit)
    if key == "land_slope":
        v = rec.get("land_slope")
        return None if v is None else _slope_convert(v, slope_unit)
    if key == "land_aspect":
        return rec.get("land_aspect")
    if key == "traverse":
        return rec.get("traverse")
    if key == "bearing":
        return rec["bearing"]
    if key == "deflection":
        return rec["deflection"]
    if key == "abs_deflection":
        return None if rec["deflection"] is None else abs(rec["deflection"])
    if key == "inclination":
        return rec["inclination"]
    if key == "abs_inclination":
        return None if rec["inclination"] is None else abs(rec["inclination"])
    if key == "defl3d":
        return rec["defl3d"]
    if key in ("source_id", "seg_num", "part_index"):
        return rec[key]
    if key == "slope_class":
        dz = rec["dz_m"]
        return "Flat" if dz == 0 else ("Uphill" if dz > 0 else "Downhill")
    return None
